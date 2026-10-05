from __future__ import annotations

import hashlib
import imaplib
import re
import time
from datetime import date, datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import RawListing
from ..images import is_listing_image_url
from ..normalize import canonicalize_url, clean_text, infer_fields, parse_money

_SKIP_LINK_RE = re.compile(r"unsubscribe|privacy|terms|preferences|manage-alert", re.I)
_GENERIC_LINK_RE = re.compile(r"view|see|more|details|property", re.I)
_NOISE_TITLE_RE = re.compile(
    r"view all results|start new search|^for sale$|^to rent$|house prices|new homes|commercial|overseas|students|fca\.org",
    re.I,
)
_PROPERTY_TITLE_RE = re.compile(r"\b(?:\d+\s*(?:bed|bedroom)s?|flat|house|apartment)\b", re.I)
_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)
_AVAILABLE_DATE_RE = re.compile(r"\bavailable\s+(?:from|on)\s+(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})\b", re.I)


class EmailAlertSource:
    """Reads rental-alert messages from an IMAP mailbox.

    This adapter only consumes emails the user is already authorized to access;
    it does not fetch portal pages or bypass portal controls.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        mailbox: str = "INBOX",
        tls: bool = True,
        source_name: str = "rental-alert-email",
        search_since_days: int = 2,
        link_pattern: str | None = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.mailbox = mailbox
        self.tls = tls
        self.source_name = source_name
        self.search_since_days = search_since_days
        self.link_pattern = re.compile(link_pattern) if link_pattern else None

    def fetch(self) -> list[RawListing]:
        mailbox = self._connect()
        try:
            mailbox.select(self.mailbox, readonly=True)
            since = (date.today() - timedelta(days=self.search_since_days)).strftime("%d-%b-%Y")
            status, data = mailbox.search(None, f"SINCE {since}")
            if status != "OK":
                raise RuntimeError("IMAP search failed")
            listings: list[RawListing] = []
            for message_id in data[0].split():
                status, message_data = mailbox.fetch(message_id, "(RFC822)")
                if status != "OK":
                    continue
                message_bytes = next((part[1] for part in message_data if isinstance(part, tuple)), None)
                if message_bytes:
                    listings.extend(self._parse_message(message_from_bytes(message_bytes)))
            return listings
        finally:
            try:
                mailbox.logout()
            except imaplib.IMAP4.error:
                pass

    def check_connection(self) -> int:
        mailbox = self._connect()
        try:
            status, data = mailbox.select(self.mailbox, readonly=True)
            if status != "OK":
                raise RuntimeError(f"Could not select mailbox {self.mailbox!r}")
            return int(data[0] or 0)
        finally:
            try:
                mailbox.logout()
            except imaplib.IMAP4.error:
                pass

    def _connect(self) -> imaplib.IMAP4:
        connection = imaplib.IMAP4_SSL if self.tls else imaplib.IMAP4
        for attempt in range(3):
            try:
                mailbox = connection(self.host, self.port, timeout=20)
                try:
                    mailbox.login(self.username, self.password)
                except Exception:
                    try:
                        mailbox.logout()
                    except (imaplib.IMAP4.error, OSError):
                        pass
                    raise
                return mailbox
            except (imaplib.IMAP4.abort, OSError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("Mailbox connection attempts exhausted")

    def _parse_message(self, message: Message) -> list[RawListing]:
        html = _message_html(message)
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        listings: list[RawListing] = []
        seen_urls: set[str] = set()
        subject = _decoded_header(message.get("Subject", ""))
        message_id = clean_text(message.get("Message-ID", "")) or None
        received_at = _received_at(message)
        for anchor in soup.find_all("a", href=True):
            url = unescape(anchor["href"]).strip()
            if not url.startswith(("http://", "https://")) or _SKIP_LINK_RE.search(url):
                continue
            if not _is_property_url(url):
                continue
            if self.link_pattern and not self.link_pattern.search(url):
                continue
            if "/for-sale/" in url.casefold():
                continue
            canonical_url = canonicalize_url(url)
            if canonical_url in seen_urls:
                continue
            container = _find_listing_container(anchor)
            text = clean_text(container.get_text(" ", strip=True))
            title = _extract_title(anchor, container, text, canonical_url)
            if (
                len(text) < 12
                or not _looks_like_listing(text)
                or _NOISE_TITLE_RE.search(title)
                or not _PROPERTY_TITLE_RE.search(title)
            ):
                continue
            metadata = {
                "email_subject": subject,
                "email_message_id": message_id,
                "email_received_at": received_at,
                "source_link_text": clean_text(anchor.get_text(" ", strip=True)),
                "image_urls": _extract_image_urls(_find_image_container(anchor, canonical_url), url),
                "parser_version": "email-v3",
            }
            if re.search(r"\bavailable\s+now\b", text, re.I):
                metadata["availability_note"] = "Available now"
            listing = RawListing(
                    source=self.source_name,
                    source_listing_id=hashlib.sha256(canonical_url.encode()).hexdigest()[:32],
                    url=url,
                    title=title,
                    address=(_extract_card_address(container, title) if urlsplit(url).hostname in {"rightmove.co.uk", "www.rightmove.co.uk"} else None) or _extract_address(text),
                    description=text,
                    monthly_rent=parse_money(text),
                    available_from=_extract_available_from(text),
                    metadata=metadata,
                )
            _, listing.bedrooms, listing.bathrooms, _ = infer_fields(listing)
            listings.append(listing)
            seen_urls.add(canonical_url)
        return listings


def _message_html(message: Message) -> str | None:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace") if payload else None
        return None
    payload = message.get_payload(decode=True)
    return payload.decode(message.get_content_charset() or "utf-8", errors="replace") if payload else None


def _decoded_header(value: str) -> str:
    return str(make_header(decode_header(value)))


def _received_at(message: Message) -> str | None:
    value = message.get("Date")
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _is_property_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if host in {"rightmove.co.uk", "www.rightmove.co.uk"}:
        return bool(re.fullmatch(r"/properties/\d+", path))
    if host in {"zoopla.co.uk", "www.zoopla.co.uk"}:
        return bool(re.fullmatch(r"/to-rent/details/\d+", path))
    return False


def _extract_title(anchor: Message, container: Message, text: str, canonical_url: str) -> str:
    for heading in container.find_all(["h1", "h2", "h3", "h4"]):
        title = clean_text(heading.get_text(" ", strip=True))
        if title and not _GENERIC_LINK_RE.search(title) and not title.startswith("£"):
            return title
    for link in container.find_all("a", href=True):
        if canonicalize_url(str(link["href"])) != canonical_url:
            continue
        label = clean_text(link.get_text(" ", strip=True))
        if label and len(label) <= 100 and not _GENERIC_LINK_RE.search(label) and _PROPERTY_TITLE_RE.search(label):
            return label
    anchor_text = clean_text(anchor.get_text(" ", strip=True))
    if anchor_text and not _GENERIC_LINK_RE.search(anchor_text):
        return anchor_text
    property_label = re.search(r"\b\d+\s+bed(?:room)?s?\s+.*?(?=\s+View property details|$)", text, re.I)
    return clean_text(property_label.group(0)) if property_label else text[:140] or "Untitled listing"


def _extract_card_address(container: Message, title: str) -> str | None:
    strings = [clean_text(str(value)) for value in container.stripped_strings]
    for index, value in enumerate(strings[:-1]):
        if value != title:
            continue
        candidate = strings[index + 1]
        if candidate and 8 <= len(candidate) <= 120 and "£" not in candidate and not _GENERIC_LINK_RE.search(candidate) and not re.search(r"\b0\d{2,4}\s+\d{3,}\b", candidate):
            return candidate
    return None


def _find_listing_container(anchor: Message) -> Message:
    fallback = None
    priced = None
    for depth, candidate in enumerate(anchor.parents):
        if depth >= 8:
            break
        if candidate.name not in {"article", "li", "td", "tr", "table", "div"}:
            continue
        text = clean_text(candidate.get_text(" ", strip=True))
        listing_links = {
            canonicalize_url(str(link.get("href", "")))
            for link in candidate.find_all("a", href=True)
            if re.search(r"/(?:to-rent/details|properties)/\d+", str(link.get("href", "")), re.I)
        }
        if len(listing_links) > 1:
            break
        if len(text) >= 12 and _looks_like_listing(text):
            if fallback is None:
                fallback = candidate
            if parse_money(text) is not None:
                if priced is None:
                    priced = candidate
                if candidate.find("img"):
                    return candidate
    return priced or fallback or anchor


def _find_image_container(anchor: Message, canonical_url: str) -> Message:
    container = anchor
    for depth, candidate in enumerate(anchor.parents):
        if depth >= 10:
            break
        if candidate.name not in {"article", "li", "td", "tr", "table", "div"}:
            continue
        listing_links = {
            canonicalize_url(str(link["href"]))
            for link in candidate.find_all("a", href=True)
            if _is_property_url(str(link["href"]))
        }
        if listing_links == {canonical_url}:
            container = candidate
        elif listing_links:
            break
    return container


def _extract_image_urls(container: Message, base_url: str) -> list[str]:
    image_urls: list[str] = []
    for image in container.find_all("img"):
        for image_url in (image.get("data-src"), image.get("src")):
            if not image_url:
                continue
            absolute_url = urljoin(base_url, str(image_url).strip())
            if is_listing_image_url(absolute_url) and absolute_url not in image_urls:
                image_urls.append(absolute_url)
    return image_urls


def _looks_like_listing(text: str) -> bool:
    return bool(parse_money(text) or re.search(r"\b(?:bed|bedroom|flat|house|apartment)\b", text, re.I))


def _extract_address(text: str) -> str | None:
    postcode = _POSTCODE_RE.search(text)
    if postcode:
        start = max(0, text.rfind(" ", 0, postcode.start() - 60))
        return text[start : postcode.end()].strip(" ,-")
    address = re.search(r"(?:to rent|for rent)\s+(.+?)(?:\s+View property details|$)", text, re.I)
    return clean_text(address.group(1)).strip(" ,-") if address else None


def _extract_available_from(text: str) -> date | None:
    match = _AVAILABLE_DATE_RE.search(text)
    if not match:
        return None
    value = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", match.group(1), flags=re.I)
    for format_string in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, format_string).date()
        except ValueError:
            continue
    return None

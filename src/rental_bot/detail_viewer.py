from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from urllib.robotparser import RobotFileParser

from .images import is_listing_image_url
from .normalize import parse_money


_AVAILABLE_DATE = re.compile(
    r"\bavailable\s+(?:from|on)\s+(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})\b",
    re.I,
)


class DetailViewError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    requested_url: str
    final_url: str
    status_code: int
    title: str
    description: str | None
    text: str
    image_urls: tuple[str, ...]
    content_hash: str
    retrieved_at: datetime
    facts: dict[str, object]


class HttpDetailViewer:
    """Fetches one explicitly authorized listing page without browser automation."""

    def __init__(
        self,
        *,
        allowed_domains: Iterable[str],
        provider_authorized_domains: Iterable[str],
        timeout_seconds: float = 15,
        max_bytes: int = 2_000_000,
        max_redirects: int = 3,
        user_agent: str = "london-rental-intelligence-bot-detail-viewer/0.1",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.allowed_domains = _normalise_domains(allowed_domains)
        self.provider_authorized_domains = _normalise_domains(provider_authorized_domains)
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.user_agent = user_agent
        self.transport = transport

    def fetch(self, url: str) -> PageSnapshot:
        requested_url = self._validate_url(url)
        headers = {"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"}
        with httpx.Client(
            follow_redirects=False,
            timeout=self.timeout,
            headers=headers,
            transport=self.transport,
        ) as client:
            current_url = requested_url
            response: httpx.Response | None = None
            for redirect_count in range(self.max_redirects + 1):
                self._validate_url(current_url)
                self._check_robots(client, current_url)
                response = self._request_page(client, current_url)
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                if redirect_count == self.max_redirects:
                    raise DetailViewError("Page exceeded the configured redirect limit")
                location = response.headers.get("location")
                if not location:
                    raise DetailViewError("Page returned a redirect without a location")
                current_url = urljoin(current_url, location)

            if response is None:
                raise DetailViewError("Page request did not produce a response")
            if response.status_code >= 400:
                raise DetailViewError(f"Page returned HTTP {response.status_code}")
            if "text/html" not in response.headers.get("content-type", "").lower():
                raise DetailViewError("Page did not return HTML")

            html = response.content.decode(response.encoding or "utf-8", errors="replace")
            return _snapshot(requested_url, str(response.url), response.status_code, html)

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise DetailViewError("Only HTTPS listing URLs are supported")
        if not parsed.hostname or parsed.username or parsed.password:
            raise DetailViewError("Listing URL must contain a hostname and no credentials")
        try:
            if parsed.port not in {None, 443}:
                raise DetailViewError("Only the default HTTPS port is supported")
        except ValueError as error:
            raise DetailViewError("Listing URL has an invalid port") from error
        if not _matches_domain(parsed.hostname, self.allowed_domains):
            raise DetailViewError("Listing domain is not in DETAIL_ALLOWED_DOMAINS")
        if not _matches_domain(parsed.hostname, self.provider_authorized_domains):
            raise DetailViewError("Provider authorization is not configured for this domain")
        return url

    def _check_robots(self, client: httpx.Client, url: str) -> None:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        response = self._read_response(client, robots_url, 256_000)
        if response.status_code == 404:
            return
        if 300 <= response.status_code < 400:
            raise DetailViewError("Could not verify robots.txt (redirected)")
        if response.status_code >= 400:
            raise DetailViewError(f"Could not verify robots.txt (HTTP {response.status_code})")
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        if not parser.can_fetch(self.user_agent, url):
            raise DetailViewError("robots.txt disallows this page")

    def _request_page(self, client: httpx.Client, url: str) -> httpx.Response:
        response = self._read_response(client, url, self.max_bytes)
        return response

    def _read_response(self, client: httpx.Client, url: str, max_bytes: int) -> httpx.Response:
        try:
            with client.stream("GET", url) as response:
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            raise DetailViewError("Response exceeded the configured size limit")
                    except ValueError as error:
                        raise DetailViewError("Response has an invalid content length") from error
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise DetailViewError("Response exceeded the configured size limit")
                headers = response.headers.copy()
                headers.pop("content-encoding", None)
                headers["content-length"] = str(len(content))
                return httpx.Response(
                    response.status_code,
                    headers=headers,
                    content=bytes(content),
                    request=response.request,
                )
        except httpx.HTTPError as error:
            raise DetailViewError("Could not retrieve the listing page") from error


def _snapshot(requested_url: str, final_url: str, status_code: int, html: str) -> PageSnapshot:
    soup = BeautifulSoup(html, "html.parser")
    facts = _structured_facts(soup)
    for element in soup(["script", "style", "noscript", "svg", "template"]):
        element.decompose()
    heading = soup.find("h1")
    title = str(facts.get("title") or _clean(heading.get_text(" ", strip=True) if heading else "") or _clean(soup.title.get_text(" ", strip=True) if soup.title else ""))
    description = None
    description_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) or soup.find("meta", attrs={"property": "og:description"})
    if description_tag and description_tag.get("content"):
        description = _clean(str(description_tag["content"]))
    description = str(facts.get("description") or description) if facts.get("description") or description else None
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = _clean(main.get_text(" ", strip=True))
    if re.search(r"\b(?:captcha|verify you are human|access denied)\b", title, re.I):
        raise DetailViewError("Page returned an access challenge instead of a listing")
    image_candidates = facts.get("image_urls") if isinstance(facts.get("image_urls"), list) else []
    for image in main.find_all("img"):
        for attribute in ("src", "data-src", "data-original"):
            if image.get(attribute):
                image_candidates.append(str(image[attribute]))
        for attribute in ("srcset", "data-srcset"):
            if image.get(attribute):
                image_candidates.extend(part.strip().split()[0] for part in str(image[attribute]).split(",") if part.strip())
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        image_candidates.insert(0, str(og_image["content"]))
    image_urls = tuple(
        dict.fromkeys(
            urljoin(final_url, image)
            for image in image_candidates
            if isinstance(image, str) and urljoin(final_url, image).startswith("https://")
            and is_listing_image_url(urljoin(final_url, image))
        )
    )[:30]
    facts["title"] = title
    if description:
        facts["description"] = description
    if "monthly_rent" not in facts:
        rent = parse_money(text)
        if rent is not None:
            facts["monthly_rent"] = rent
    for field, pattern in (
        ("bedrooms", r"\b(\d+)\s*(?:bed(?:room)?s?|br)\b"),
        ("bathrooms", r"\b(\d+)\s*bath(?:room)?s?\b"),
    ):
        if field not in facts:
            match = re.search(pattern, text, re.I)
            if match:
                facts[field] = int(match.group(1))
    available = _AVAILABLE_DATE.search(text)
    if available:
        value = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", available.group(1), flags=re.I)
        for format_string in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y"):
            try:
                facts["available_from"] = datetime.strptime(value, format_string).date().isoformat()
                break
            except ValueError:
                continue
    elif re.search(r"\bavailable\s+now\b", text, re.I):
        facts["availability_note"] = "Available now"
    return PageSnapshot(
        requested_url=requested_url,
        final_url=final_url,
        status_code=status_code,
        title=title,
        description=description,
        text=text,
        image_urls=image_urls,
        content_hash=hashlib.sha256(html.encode()).hexdigest(),
        retrieved_at=datetime.now(timezone.utc),
        facts=facts,
    )


def _structured_facts(soup: BeautifulSoup) -> dict[str, object]:
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        try:
            data = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        for item in _jsonld_objects(data):
            types = item.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if not isinstance(types, list) or not any(
                kind in {"Apartment", "House", "Residence", "SingleFamilyResidence", "Accommodation", "RealEstateListing"}
                for kind in types
            ):
                continue
            facts: dict[str, object] = {}
            for source, target in (("name", "title"), ("description", "description")):
                if isinstance(item.get(source), str):
                    facts[target] = _clean(item[source])
            address = item.get("address")
            if isinstance(address, dict):
                parts = [address.get(key) for key in ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
                address = ", ".join(str(part) for part in parts if isinstance(part, str) and part.strip())
            if isinstance(address, str) and address.strip():
                facts["address"] = _clean(address)
            for source, target in (("numberOfBedrooms", "bedrooms"), ("numberOfBathroomsTotal", "bathrooms")):
                try:
                    if item.get(source) is not None:
                        facts[target] = int(item[source])
                except (TypeError, ValueError):
                    pass
            images = item.get("image", [])
            images = [images] if isinstance(images, str) else images
            if isinstance(images, list):
                facts["image_urls"] = [
                    image if isinstance(image, str) else image.get("url") or image.get("contentUrl")
                    for image in images if isinstance(image, str) or isinstance(image, dict)
                ]
            offer = item.get("offers")
            if isinstance(offer, list):
                offer = next((entry for entry in offer if isinstance(entry, dict)), None)
            if isinstance(offer, dict) and offer.get("priceCurrency", "GBP") == "GBP":
                price = offer.get("price")
                period = str(offer.get("unitText") or offer.get("unitCode") or "month").lower()
                try:
                    if price is not None:
                        amount = int(float(str(price).replace(",", "")))
                        if "week" in period or period in {"w", "wk"}:
                            amount = round(amount * 52 / 12)
                        if "month" in period or period in {"mon", "m", "w", "wk"} or "week" in period:
                            facts["monthly_rent"] = amount
                except (TypeError, ValueError):
                    pass
            return facts
    return {}


def _jsonld_objects(value: object) -> Iterable[dict[str, object]]:
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_objects(item)
    elif isinstance(value, dict):
        yield value
        for key in ("@graph", "mainEntity", "item", "about"):
            if key in value:
                yield from _jsonld_objects(value[key])


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalise_domains(domains: Iterable[str]) -> tuple[str, ...]:
    return tuple(domain.strip().lower().lstrip(".") for domain in domains if domain.strip())


def _matches_domain(hostname: str, domains: Iterable[str]) -> bool:
    host = hostname.lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)

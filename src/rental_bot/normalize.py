from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import NormalizedListing, RawListing

_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}
_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.I)
_OUTWARD_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d{1,2})\b", re.I)
_BEDROOM_RE = re.compile(r"\b(\d+)\s*(?:bed(?:room)?s?|br)\b", re.I)
_BATHROOM_RE = re.compile(r"\b(\d+)\s*(?:bath(?:room)?s?)\b", re.I)
_RENT_RE = re.compile(r"£\s*([\d,]+)(?:\s*(?:pcm|per\s+month|p\.m\.))?", re.I)


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    hostname = (parts.hostname or "").lower()
    path = parts.path.rstrip("/") or "/"
    if (
        hostname in {"rightmove.co.uk", "www.rightmove.co.uk"}
        and re.fullmatch(r"/properties/\d+", path)
    ) or (
        hostname in {"zoopla.co.uk", "www.zoopla.co.uk"}
        and re.fullmatch(r"/to-rent/details/\d+", path)
    ):
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in _TRACKING_PARAMS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def parse_money(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"£\s*([\d,]+)", value)
    if not match:
        return None
    amount = int(match.group(1).replace(",", ""))
    suffix = value[match.end() : match.end() + 32]
    if re.match(r"\s*(?:pw\b|p/w\b|per\s+week\b|weekly\b)", suffix, re.I):
        return round(amount * 52 / 12)
    return amount


def infer_fields(raw: RawListing) -> tuple[int | None, int | None, int | None, str | None]:
    searchable = " ".join(part for part in (raw.title, raw.address, raw.description) if part)
    rent = raw.monthly_rent or parse_money(searchable)
    bedrooms = raw.bedrooms or _match_int(_BEDROOM_RE, searchable)
    bathrooms = raw.bathrooms or _match_int(_BATHROOM_RE, searchable)
    postcode_match = _POSTCODE_RE.search(searchable)
    if postcode_match:
        postcode = re.sub(r"\s+", " ", postcode_match.group(1).upper()).strip()
    else:
        outward_matches = _OUTWARD_POSTCODE_RE.findall(searchable)
        postcode = outward_matches[-1].upper() if outward_matches else None
    return rent, bedrooms, bathrooms, postcode


def _match_int(pattern: re.Pattern[str], value: str) -> int | None:
    match = pattern.search(value)
    return int(match.group(1)) if match else None


def normalize(raw: RawListing) -> NormalizedListing:
    url = raw.url.strip()
    canonical_url = canonicalize_url(url)
    source_listing_id = raw.source_listing_id or hashlib.sha256(canonical_url.encode()).hexdigest()[:32]
    rent, bedrooms, bathrooms, postcode = infer_fields(raw)
    return NormalizedListing(
        source=clean_text(raw.source),
        source_listing_id=source_listing_id,
        url=url,
        canonical_url=canonical_url,
        title=clean_text(raw.title) or "Untitled listing",
        address=clean_text(raw.address) or None,
        postcode=postcode,
        description=clean_text(raw.description) or None,
        monthly_rent=rent,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        available_from=raw.available_from,
        metadata=raw.metadata,
    )

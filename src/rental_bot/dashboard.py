from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .matching import matches
from .models import MatchCriteria, StoredListing
from .images import is_listing_image_url


def criteria_from_filters(filters: dict[str, Any]) -> MatchCriteria:
    return MatchCriteria(
        max_monthly_rent=filters["max_rent"],
        min_bedrooms=filters["min_bedrooms"],
        areas=filters["areas"],
        max_commute_minutes=filters["max_commute"],
    )


def stored_listing(row: dict[str, Any]) -> StoredListing:
    first_seen_at = row["first_seen_at"]
    if not isinstance(first_seen_at, datetime):
        first_seen_at = datetime.now(timezone.utc)
    return StoredListing(
        id=int(row["id"]), source=str(row["source"]), url=str(row["url"]),
        title=str(row["title"]), address=row.get("address"), postcode=row.get("postcode"),
        monthly_rent=row.get("monthly_rent"), bedrooms=row.get("bedrooms"),
        bathrooms=row.get("bathrooms"), description=row.get("description"),
        first_seen_at=first_seen_at, available_from=row.get("available_from"),
        commute_minutes=row.get("commute_minutes"), latitude=row.get("latitude"),
        longitude=row.get("longitude"), metadata=row.get("metadata") or {},
    )


def matches_filters(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    if filters["only_unprocessed"] and row.get("digest_sent_at") is not None:
        return False
    if filters["status"] is not None and row.get("review_status", "unreviewed") != filters["status"]:
        return False
    return matches(stored_listing(row), criteria_from_filters(filters))


def filter_reasons(row: dict[str, Any], filters: dict[str, Any]) -> list[str]:
    listing = stored_listing(row)
    checks = (
        ("Rent too high or missing", MatchCriteria(max_monthly_rent=filters["max_rent"])),
        ("Bedrooms below minimum or missing", MatchCriteria(min_bedrooms=filters["min_bedrooms"])),
        ("Area not in selected areas", MatchCriteria(areas=filters["areas"])),
        ("Commute too long or missing", MatchCriteria(max_commute_minutes=filters["max_commute"])),
    )
    return [reason for reason, criterion in checks if not matches(listing, criterion)]


def reason_counts(rows: list[dict[str, Any]], filters: dict[str, Any]) -> dict[str, int]:
    counts = {
        "Rent too high or missing": 0,
        "Bedrooms below minimum or missing": 0,
        "Area not in selected areas": 0,
        "Commute too long or missing": 0,
    }
    for row in rows:
        for reason in filter_reasons(row, filters):
            counts[reason] += 1
    return counts


def safe_http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def image_urls(row: dict[str, Any]) -> list[str]:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return []
    page_detail = metadata.get("page_detail")
    page_images = page_detail.get("image_urls") if isinstance(page_detail, dict) else None
    email_images = metadata.get("image_urls")
    images = [
        *(page_images if isinstance(page_images, list) else []),
        *(email_images if isinstance(email_images, list) else []),
    ]
    return list(dict.fromkeys(
        safe for url in images
        if (safe := safe_http_url(url)) and is_listing_image_url(safe)
    ))

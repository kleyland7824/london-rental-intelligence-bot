from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(slots=True)
class RawListing:
    source: str
    url: str
    title: str = ""
    address: str | None = None
    description: str | None = None
    monthly_rent: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    available_from: date | None = None
    source_listing_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedListing:
    source: str
    source_listing_id: str
    url: str
    canonical_url: str
    title: str
    address: str | None
    postcode: str | None
    description: str | None
    monthly_rent: int | None
    bedrooms: int | None
    bathrooms: int | None
    available_from: date | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MatchCriteria:
    max_monthly_rent: int | None = None
    min_bedrooms: int | None = None
    areas: tuple[str, ...] = ()
    max_commute_minutes: int | None = None


@dataclass(slots=True)
class StoredListing:
    id: int
    source: str
    url: str
    title: str
    address: str | None
    postcode: str | None
    monthly_rent: int | None
    bedrooms: int | None
    bathrooms: int | None
    description: str | None
    first_seen_at: datetime
    available_from: date | None = None
    commute_minutes: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

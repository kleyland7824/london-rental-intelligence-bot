from __future__ import annotations

from dataclasses import dataclass

from .matching import matches
from .models import MatchCriteria, StoredListing


@dataclass(frozen=True, slots=True)
class ListingAssessment:
    matches_filters: bool
    fit_score: int | None
    score_reasons: tuple[str, ...]
    evidence_sources: dict[str, str]
    missing_fields: tuple[str, ...]

    @property
    def known_fields(self) -> int:
        return len(self.evidence_sources) - len(self.missing_fields)


def assess_listing(listing: StoredListing, criteria: MatchCriteria) -> ListingAssessment:
    metadata = listing.metadata if isinstance(listing.metadata, dict) else {}
    page_detail = metadata.get("page_detail")
    page_facts = page_detail.get("facts") if isinstance(page_detail, dict) else None
    page_facts = page_facts if isinstance(page_facts, dict) else {}
    values = {
        "Rent": listing.monthly_rent,
        "Bedrooms": listing.bedrooms,
        "Location": listing.address or listing.postcode,
        "Bathrooms": listing.bathrooms,
        "Availability": listing.available_from or (metadata.get("availability_note") if metadata.get("availability_note") == "Available now" else None),
        "Commute": listing.commute_minutes,
    }
    page_keys = {
        "Rent": ("monthly_rent",),
        "Bedrooms": ("bedrooms",),
        "Location": ("address", "postcode"),
        "Bathrooms": ("bathrooms",),
        "Availability": ("available_from",),
        "Commute": (),
    }
    evidence_sources: dict[str, str] = {}
    for field, value in values.items():
        if value is None:
            evidence_sources[field] = "Unknown"
        elif field == "Commute":
            evidence_sources[field] = "Calculated route"
        elif any(page_facts.get(key) is not None for key in page_keys[field]):
            evidence_sources[field] = "Listing page"
        else:
            evidence_sources[field] = "Alert email"

    factors: list[tuple[int, int, str]] = []
    if criteria.max_monthly_rent is not None:
        limit = criteria.max_monthly_rent
        rent = listing.monthly_rent
        points = _margin_points(rent, limit, weight=35, margin_fraction=0.25)
        detail = f"Rent: £{rent:,}/month against £{limit:,} maximum" if rent is not None else "Rent: unknown"
        factors.append((points, 35, detail))
    if criteria.min_bedrooms is not None:
        bedrooms = listing.bedrooms
        points = 20 if bedrooms is not None and bedrooms >= criteria.min_bedrooms else 0
        detail = f"Bedrooms: {bedrooms} against {criteria.min_bedrooms} minimum" if bedrooms is not None else "Bedrooms: unknown"
        factors.append((points, 20, detail))
    if criteria.areas:
        searchable = " ".join(filter(None, (listing.address, listing.postcode, listing.title))).casefold()
        area = next((area for area in criteria.areas if area.casefold() in searchable), None)
        factors.append((20 if area else 0, 20, f"Area: {area or 'no selected area found in listing text'}"))
    if criteria.max_commute_minutes is not None:
        limit = criteria.max_commute_minutes
        commute = listing.commute_minutes
        points = _margin_points(commute, limit, weight=25, margin_fraction=0.5)
        detail = f"Commute: {commute} minutes against {limit} maximum" if commute is not None else "Commute: unknown"
        factors.append((points, 25, detail))

    total = sum(weight for _, weight, _ in factors)
    score = round(100 * sum(points for points, _, _ in factors) / total) if total else None
    reasons = tuple(f"{detail} ({points}/{weight})" for points, weight, detail in factors)
    return ListingAssessment(
        matches_filters=matches(listing, criteria),
        fit_score=score,
        score_reasons=reasons,
        evidence_sources=evidence_sources,
        missing_fields=tuple(field for field, source in evidence_sources.items() if source == "Unknown"),
    )


def _margin_points(value: int | None, limit: int, *, weight: int, margin_fraction: float) -> int:
    if value is None or value > limit or limit <= 0:
        return 0
    headroom = min(1.0, max(0.0, (limit - value) / (limit * margin_fraction)))
    return round(weight * (0.7 + 0.3 * headroom))


def rank_listings(listings: list[StoredListing], criteria: MatchCriteria) -> list[StoredListing]:
    return sorted(
        listings,
        key=lambda listing: (
            listing.commute_minutes is None,
            listing.commute_minutes if listing.commute_minutes is not None else float("inf"),
            -(assess_listing(listing, criteria).fit_score or 0),
            -listing.first_seen_at.timestamp(),
        ),
    )

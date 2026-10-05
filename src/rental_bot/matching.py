from __future__ import annotations

from .models import MatchCriteria, StoredListing


def matches(listing: StoredListing, criteria: MatchCriteria) -> bool:
    if criteria.max_monthly_rent is not None and (
        listing.monthly_rent is None or listing.monthly_rent > criteria.max_monthly_rent
    ):
        return False
    if criteria.min_bedrooms is not None and (
        listing.bedrooms is None or listing.bedrooms < criteria.min_bedrooms
    ):
        return False
    if criteria.areas:
        searchable = " ".join(filter(None, (listing.address, listing.postcode, listing.title))).casefold()
        if not any(area.casefold() in searchable for area in criteria.areas):
            return False
    if criteria.max_commute_minutes is not None and (
        listing.commute_minutes is None or listing.commute_minutes > criteria.max_commute_minutes
    ):
        return False
    return True

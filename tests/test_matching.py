from datetime import datetime, timezone

from rental_bot.matching import matches
from rental_bot.models import MatchCriteria, StoredListing


def listing(**overrides):
    values = {
        "id": 1,
        "source": "test",
        "url": "https://example.test/1",
        "title": "Flat in Hackney",
        "address": "Hackney, London",
        "postcode": "E8 2AB",
        "monthly_rent": 2100,
        "bedrooms": 2,
        "bathrooms": 1,
        "description": None,
        "first_seen_at": datetime.now(timezone.utc),
        "commute_minutes": 35,
    }
    values.update(overrides)
    return StoredListing(**values)


def test_matching_accepts_listing_inside_all_limits():
    assert matches(listing(), MatchCriteria(max_monthly_rent=2200, min_bedrooms=1, areas=("Hackney",), max_commute_minutes=45))


def test_matching_rejects_unknown_commute_when_limit_is_configured():
    assert not matches(listing(commute_minutes=None), MatchCriteria(max_commute_minutes=45))


def test_matching_rejects_over_budget():
    assert not matches(listing(monthly_rent=2300), MatchCriteria(max_monthly_rent=2200))

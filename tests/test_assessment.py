from datetime import datetime, timezone

from rental_bot.assessment import assess_listing, rank_listings
from rental_bot.models import MatchCriteria, StoredListing


def listing(**overrides):
    values = {
        "id": 1,
        "source": "rental-alert-email",
        "url": "https://example.test/listing/1",
        "title": "Two bedroom flat in Hackney",
        "address": "Hackney E8 2AB",
        "postcode": "E8 2AB",
        "monthly_rent": 2000,
        "bedrooms": 2,
        "bathrooms": None,
        "description": None,
        "first_seen_at": datetime.now(timezone.utc),
        "commute_minutes": 30,
    }
    values.update(overrides)
    return StoredListing(**values)


def test_assessment_ranks_better_rent_and_commute_without_changing_match():
    criteria = MatchCriteria(max_monthly_rent=2500, min_bedrooms=2, areas=("Hackney",), max_commute_minutes=45)

    stronger = assess_listing(listing(), criteria)
    weaker = assess_listing(listing(monthly_rent=2450, commute_minutes=44), criteria)

    assert stronger.matches_filters
    assert weaker.matches_filters
    assert stronger.fit_score > weaker.fit_score
    assert stronger.fit_score <= 100
    assert stronger.known_fields == 4
    assert stronger.missing_fields == ("Bathrooms", "Availability")
    assert any("£2,000/month against £2,500" in reason for reason in stronger.score_reasons)


def test_assessment_keeps_unknown_and_excluded_facts_explicit():
    criteria = MatchCriteria(max_monthly_rent=2500, max_commute_minutes=45)
    assessment = assess_listing(listing(monthly_rent=None, commute_minutes=None), criteria)

    assert not assessment.matches_filters
    assert assessment.fit_score == 0
    assert assessment.evidence_sources["Rent"] == "Unknown"
    assert "Commute" in assessment.missing_fields


def test_assessment_labels_page_facts_without_calling_them_verified():
    assessment = assess_listing(
        listing(metadata={"page_detail": {"facts": {"monthly_rent": 2000, "address": "Hackney E8 2AB"}}}),
        MatchCriteria(),
    )

    assert assessment.fit_score is None
    assert assessment.evidence_sources["Rent"] == "Listing page"
    assert assessment.evidence_sources["Location"] == "Listing page"
    assert assessment.evidence_sources["Bedrooms"] == "Alert email"
    assert assessment.evidence_sources["Commute"] == "Calculated route"


def test_ranking_prioritizes_slowest_transit_commute_over_fit_score():
    criteria = MatchCriteria(max_monthly_rent=2500, max_commute_minutes=45)
    fast = listing(id=1, commute_minutes=20, monthly_rent=2450)
    slow = listing(id=2, commute_minutes=40, monthly_rent=1800)
    unknown = listing(id=3, commute_minutes=None, monthly_rent=1500)

    assert [item.id for item in rank_listings([slow, unknown, fast], criteria)] == [1, 2, 3]


def test_available_now_is_known_without_fabricating_a_date():
    assessment = assess_listing(listing(available_from=None, metadata={"availability_note": "Available now"}), MatchCriteria())

    assert assessment.evidence_sources["Availability"] == "Alert email"
    assert "Availability" not in assessment.missing_fields

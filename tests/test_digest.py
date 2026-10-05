from datetime import datetime, timezone
from types import SimpleNamespace

from rental_bot.cli import notify_new_matches
from rental_bot.digest import render_digest, render_telegram_digest
from rental_bot.models import MatchCriteria, StoredListing


def test_digest_contains_actionable_listing_details():
    listing = StoredListing(
        id=1,
        source="email",
        url="https://example.test/1",
        title="Sunny Hackney flat",
        address="Hackney, London",
        postcode="E8 2AB",
        monthly_rent=2100,
        bedrooms=2,
        bathrooms=1,
        description=None,
        first_seen_at=datetime.now(timezone.utc),
    )
    digest = render_digest([listing])
    assert "£2,100/month" in digest
    assert "Sunny Hackney flat" in digest
    assert "https://example.test/1" in digest


def test_digest_shows_fit_and_unknown_fields_when_criteria_are_given():
    listing = StoredListing(
        id=2,
        source="email",
        url="https://example.test/2",
        title="Hackney flat",
        address="Hackney",
        postcode=None,
        monthly_rent=2000,
        bedrooms=2,
        bathrooms=None,
        description=None,
        first_seen_at=datetime.now(timezone.utc),
    )

    digest = render_digest([listing], MatchCriteria(max_monthly_rent=2500, areas=("Hackney",)))

    assert "Fit:" in digest
    assert "not verified" in digest
    assert "Unknown: Bathrooms, Availability, Commute" in digest


def test_telegram_digest_has_one_listing_link_and_short_transit_summary():
    listing = StoredListing(
        id=3, source="email", url="https://www.rightmove.co.uk/properties/12345",
        title="One bed flat", address="Hackney", postcode="E8", monthly_rent=2100,
        bedrooms=1, bathrooms=None, description=None, first_seen_at=datetime.now(timezone.utc),
        commute_minutes=35, metadata={"email_received_at": "2026-10-03T10:00:00+00:00", "commute": {
            "provider": "google-routes", "destinations": {"Office": {
                "transit": {"minutes": 35, "maps_url": "https://maps.example.test/route"},
                "walking": {"minutes": 70, "maps_url": "https://maps.example.test/walk"},
            }},
        }},
    )

    message = render_telegram_digest([listing], MatchCriteria(max_monthly_rent=2500, max_commute_minutes=45))

    assert "Transit (Google Maps): Office 35m" in message
    assert "Alert received: 2026-10-03" in message
    assert message.count("https://") == 1
    assert "Walking" not in message
    assert "not independently verified" in message


def test_telegram_notifies_only_matches_in_ranked_batches(monkeypatch):
    class Database:
        def __init__(self):
            self.marked = []

        def unnotified_listings(self, since):
            return [StoredListing(
                id=index, source="email", url=f"https://example.test/{index}", title=f"Flat {index}",
                address="Hackney", postcode=None, monthly_rent=2000 if index < 6 else 3000,
                bedrooms=1, bathrooms=None, description=None, first_seen_at=datetime.now(timezone.utc),
                commute_minutes=40 - index,
            ) for index in range(1, 7)]

        def mark_notified(self, listing_ids):
            self.marked.append(listing_ids)

    sent = []
    monkeypatch.setattr("rental_bot.cli.send_telegram", lambda **kwargs: sent.append(kwargs["body"]))
    database = Database()
    settings = SimpleNamespace(
        telegram_bot_token="configured", telegram_chat_id="configured",
        criteria=MatchCriteria(max_monthly_rent=2500, max_commute_minutes=45),
    )

    assert notify_new_matches(database, settings, 48) == 5
    assert database.marked == [[5, 4, 3, 2], [1]]
    assert len(sent) == 2
    assert all(message.count("https://") <= 4 for message in sent)

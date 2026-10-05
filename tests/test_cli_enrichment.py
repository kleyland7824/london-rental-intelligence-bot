from datetime import datetime, timezone
from types import SimpleNamespace

from rental_bot.cli import enrich_listings
from rental_bot.models import MatchCriteria, StoredListing


def test_worker_skips_completed_routes_but_manual_enrich_can_refresh(monkeypatch):
    routes_called = []

    class Routes:
        def __init__(self, *args):
            pass

        def routes(self, **kwargs):
            routes_called.append(kwargs)
            return {"transit": {"minutes": 30}}

        def close(self):
            pass

    class Database:
        def listings_for_enrichment(self, since):
            return [StoredListing(
                id=7, source="email", url="https://example.test/7", title="Flat",
                address="London", postcode=None, monthly_rent=2000, bedrooms=1,
                bathrooms=None, description=None, first_seen_at=datetime.now(timezone.utc),
                latitude=51.5, longitude=-0.1, commute_minutes=30,
            )]

        def update_commute(self, *args, **kwargs):
            pass

        def update_enrichment(self, *args, **kwargs):
            pass

    monkeypatch.setattr("rental_bot.cli.GoogleRoutesClient", Routes)
    settings = SimpleNamespace(
        geocoding_enabled=False, google_maps_api_key="configured", commute_provider="google",
        google_transit_departure_time=None, google_transit_timezone="Europe/London",
        commute_destinations=(("Office", "London Bridge"),), commute_modes=("transit",),
        criteria=MatchCriteria(max_commute_minutes=45), commute_destination=None,
    )
    database = Database()

    assert enrich_listings(database, settings, 30, pending_only=True) == 0
    assert routes_called == []
    assert enrich_listings(database, settings, 30) == 1
    assert len(routes_called) == 1

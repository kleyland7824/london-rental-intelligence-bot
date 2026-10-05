from datetime import datetime, timezone
from types import SimpleNamespace

from rental_bot.cli import enrich_pages
from rental_bot.detail_viewer import PageSnapshot
from rental_bot.models import StoredListing


class FakeDatabase:
    def __init__(self, listing):
        self.listing = listing
        self.saved = []

    def listings_for_page_review(self, since, limit, domains=None):
        assert limit == 5
        assert domains == ("example.test",)
        return [self.listing]

    def listing_for_page_review(self, listing_id):
        return self.listing if listing_id == self.listing.id else None

    def save_page_detail(self, listing_id, *, facts, detail):
        self.saved.append((listing_id, facts, detail))


def test_enrich_pages_saves_structured_facts(monkeypatch):
    listing = StoredListing(
        id=7,
        source="rental-alert-email",
        url="https://example.test/listing/7?utm_source=email",
        title="Email title",
        address=None,
        postcode=None,
        monthly_rent=None,
        bedrooms=None,
        bathrooms=None,
        description=None,
        first_seen_at=datetime.now(timezone.utc),
    )
    database = FakeDatabase(listing)
    snapshot = PageSnapshot(
        requested_url=listing.url,
        final_url="https://example.test/listing/7",
        status_code=200,
        title="Bright flat",
        description="Two bedrooms",
        text="Bright flat £2,000 pcm 2 bedrooms E8 2AB",
        image_urls=("https://example.test/photo.jpg",),
        content_hash="example-hash",
        retrieved_at=datetime.now(timezone.utc),
        facts={"title": "Bright flat", "address": "Hackney E8 2AB", "monthly_rent": 2000, "bedrooms": 2, "available_from": "2026-10-12"},
    )
    monkeypatch.setattr("rental_bot.cli.HttpDetailViewer", lambda **kwargs: SimpleNamespace(fetch=lambda url: snapshot))
    settings = SimpleNamespace(
        detail_review_enabled=True,
        detail_allowed_domains=("example.test",),
        detail_provider_authorized_domains=("example.test",),
        detail_timeout_seconds=15,
        detail_max_bytes=2_000_000,
        detail_max_redirects=3,
        detail_user_agent="test-agent",
    )

    assert enrich_pages(database, settings, 168, 5) == 1
    listing_id, facts, detail = database.saved[0]
    assert listing_id == 7
    assert facts["postcode"] == "E8 2AB"
    assert facts["available_from"] == "2026-10-12"
    assert detail["image_urls"] == ["https://example.test/photo.jpg"]
    assert enrich_pages(database, settings, 168, 5, listing_id=7) == 1
    assert enrich_pages(database, settings, 168, 5, listing_id=99) == 0
    database.saved.clear()

    redirected = PageSnapshot(
        requested_url=listing.url,
        final_url="https://example.test/listing/8",
        status_code=200,
        title="Different flat",
        description=None,
        text="Different flat",
        image_urls=(),
        content_hash="another-hash",
        retrieved_at=datetime.now(timezone.utc),
        facts={"title": "Different flat"},
    )
    monkeypatch.setattr("rental_bot.cli.HttpDetailViewer", lambda **kwargs: SimpleNamespace(fetch=lambda url: redirected))

    assert enrich_pages(database, settings, 168, 5) == 0
    assert len(database.saved) == 0

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from rental_bot.api import app, get_database, listing_dto
from rental_bot.dashboard import safe_http_url


def listing(**overrides):
    row = {
        "id": 1, "source": "rental-alert-email", "url": "https://example.test/listing/1",
        "title": "One bedroom flat in Islington", "address": "Islington N1", "postcode": "N1",
        "monthly_rent": 2000, "bedrooms": 1, "bathrooms": None, "description": "Bright flat",
        "available_from": None, "first_seen_at": datetime.now(timezone.utc),
        "commute_minutes": 35, "latitude": 51.53, "longitude": -0.1,
        "review_status": "unreviewed", "reviewed_at": None,
        "metadata": {"email_subject": "Private alert", "image_urls": ["https://example.test/image.jpg"]},
    }
    row.update(overrides)
    return row


class FakeDatabase:
    def __init__(self):
        self.rows = [listing()]

    def api_listings(self, _since, *, limit, offset, status, matching_only, sort, **_kwargs):
        self.last_sort = sort
        self.last_commute_at_most = _kwargs.get("commute_at_most")
        rows = [row for row in self.rows if status is None or row["review_status"] == status]
        if matching_only:
            rows = [row for row in rows if row["monthly_rent"] <= 2200]
        return rows[offset:offset + limit], len(rows)

    def api_summary_rows(self, _since):
        return self.rows

    def api_listing(self, listing_id):
        return next((row for row in self.rows if row["id"] == listing_id), None)

    def api_set_review_status(self, listing_id, status):
        row = self.api_listing(listing_id)
        if row is None:
            return None
        row["review_status"] = status
        row["reviewed_at"] = datetime.now(timezone.utc)
        return {"id": listing_id, "review_status": status, "reviewed_at": row["reviewed_at"]}


def test_dto_omits_private_email_metadata_and_reports_unknowns():
    result = listing_dto(listing(), {
        "max_rent": 2200, "min_bedrooms": 1, "areas": ("Islington",),
        "max_commute": 45, "status": None, "only_unprocessed": False,
    })
    assert result["assessment"]["matches_filters"]
    assert result["assessment"]["missing_fields"] == ("Bathrooms", "Availability")
    assert result["image_urls"] == ["https://example.test/image.jpg"]
    assert result["commute_provider"] is None
    assert result["email_received_at"] is None
    assert "email_subject" not in str(result)
    assert "metadata" not in result


def test_unsafe_or_malformed_urls_are_not_exposed():
    assert safe_http_url("javascript:alert(1)") is None
    assert safe_http_url("https://[broken") is None
    assert safe_http_url("https://user:pass@example.test/photo") is None


def test_missing_images_and_coordinates_remain_explicit():
    result = listing_dto(listing(metadata={}, latitude=None, longitude=None, bathrooms=None), {
        "max_rent": None, "min_bedrooms": None, "areas": (),
        "max_commute": None, "status": None, "only_unprocessed": False,
    })
    assert result["image_urls"] == []
    assert result["latitude"] is None
    assert result["longitude"] is None
    assert result["assessment"]["fit_score"] is None
    assert "Bathrooms" in result["assessment"]["missing_fields"]


def test_google_route_summary_has_provider_without_exposing_polyline():
    row = listing(metadata={"commute": {
        "provider": "google-routes", "destinations": {"Office": {
            "transit": {"status": "ok", "minutes": 36, "polyline": "private-geometry", "maps_url": "https://www.google.com/maps/dir/"},
        }},
    }})
    result = listing_dto(row, {
        "max_rent": None, "min_bedrooms": None, "areas": (),
        "max_commute": None, "status": None, "only_unprocessed": False,
    })
    assert result["commute_provider"] == "google-routes"
    assert result["routes"] == [{
        "destination": "Office", "mode": "transit", "minutes": 36,
        "url": "https://www.google.com/maps/dir/",
    }]
    assert "private-geometry" not in str(result)


def test_alert_received_date_is_exposed_without_email_headers():
    row = listing(metadata={"email_received_at": "2026-10-03T12:30:00+00:00", "email_subject": "Private alert", "availability_note": "Available now"})
    filters = {"max_rent": None, "min_bedrooms": None, "areas": (), "max_commute": None, "status": None, "only_unprocessed": False}

    assert listing_dto(row, filters)["email_received_at"] == datetime(2026, 10, 3, 12, 30, tzinfo=timezone.utc)
    assert listing_dto(row, filters)["availability_note"] == "Available now"
    assert "Private alert" not in str(listing_dto(row, filters))
    assert listing_dto(listing(metadata={"email_received_at": "not a date"}), filters)["email_received_at"] is None


def test_ai_suggestion_exposes_only_display_fields():
    row = listing(metadata={"ai_review": {
        "summary": "Good transit time", "recommendation": "maybe", "strengths": ["Transit"],
        "concerns": [], "unknowns": ["Bathroom"], "model": "test-model",
        "reviewed_at": "2026-10-04T07:00:00+00:00", "input_hash": "private-hash",
    }})
    filters = {"max_rent": None, "min_bedrooms": None, "areas": (), "max_commute": None, "status": None, "only_unprocessed": False}

    result = listing_dto(row, filters)

    assert result["ai_review"]["summary"] == "Good transit time"
    assert "input_hash" not in str(result)


def test_api_review_and_missing_listing(monkeypatch):
    monkeypatch.setenv("MAX_MONTHLY_RENT", "2200")
    monkeypatch.setenv("MIN_BEDROOMS", "1")
    monkeypatch.setenv("AREAS", "Islington")
    monkeypatch.setenv("MAX_COMMUTE_MINUTES", "45")
    database = FakeDatabase()
    app.dependency_overrides[get_database] = lambda: database
    try:
        client = TestClient(app)
        listed = client.get("/api/v1/listings?matching_only=true").json()
        assert listed["total"] == 1
        assert client.get("/api/v1/listings?matching_only=true").headers["cache-control"] == "no-store"
        assert listed["items"][0]["assessment"]["fit_score"] is not None
        assert client.get("/api/v1/listings?sort=commute").status_code == 200
        assert database.last_sort == "commute"
        assert client.get("/api/v1/listings?sort=invalid").status_code == 422
        assert client.get("/api/v1/listings?status=reviewed&commute_at_most=35").status_code == 200
        assert database.last_commute_at_most == 35
        assert client.get("/api/v1/listings?commute_at_most=0").status_code == 422
        assert client.get("/api/v1/summary").json()["awaiting_review"] == 1
        assert client.patch("/api/v1/listings/1/review", json={"status": "favourite"}).status_code == 200
        assert client.get("/api/v1/summary").json()["favourites"] == 1
        assert client.get("/api/v1/listings?status=favourite").json()["total"] == 1
        assert client.patch("/api/v1/listings/1/review", json={"status": "invalid"}).status_code == 422
        assert client.patch("/api/v1/listings/99/review", json={"status": "left"}).status_code == 404
        assert client.get("/api/v1/listings/99").status_code == 404
        assert client.get("/api/v1/listings?limit=101").status_code == 422
        assert client.get("/api/v1/listings?areas=" + ",".join(str(index) for index in range(11))).status_code == 422
    finally:
        app.dependency_overrides.clear()

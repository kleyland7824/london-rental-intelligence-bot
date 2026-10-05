from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from rental_bot.ai_review import request_review, review_facts, review_input_hash
from rental_bot.cli import run_ai_reviews
from rental_bot.models import MatchCriteria, StoredListing


def test_ai_review_sends_minimal_redacted_facts_and_structured_nonstored_request(monkeypatch):
    listing = StoredListing(
        id=7, source="email", url="https://example.test/private-link", title="Flat in Hackney",
        address="Full private address", postcode="E8", monthly_rent=2000, bedrooms=1,
        bathrooms=None, description="Call 020 1234 5678 or agent@example.test about this flat.",
        first_seen_at=datetime.now(timezone.utc), commute_minutes=30,
        metadata={"email_subject": "Private subject", "commute": {"destinations": {"Office": {"transit": {"minutes": 30}}}}},
    )
    facts = review_facts(listing)
    assert "private-link" not in str(facts)
    assert "Full private address" not in str(facts)
    assert "Private subject" not in str(facts)
    assert "020 1234 5678" not in str(facts)
    assert "agent@example.test" not in str(facts)

    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        return httpx.Response(200, json={
            "status": "completed", "usage": {"input_tokens": 123, "output_tokens": 50},
            "output": [{"type": "message", "content": [{"type": "output_text", "text":
                '{"summary":"Short commute, unknown bathroom.","recommendation":"maybe","strengths":["Commute"],"concerns":[],"unknowns":["Bathroom"]}'
            }]}],
        }, request=httpx.Request("POST", url))

    monkeypatch.setattr("rental_bot.ai_review.httpx.post", fake_post)
    result = request_review(facts, api_key="test-only", model="chosen-model")

    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["payload"]["store"] is False
    assert captured["payload"]["text"]["format"]["strict"] is True
    assert result["recommendation"] == "maybe"
    assert result["input_hash"] == review_input_hash(facts, "chosen-model")
    assert result["input_tokens"] == 123


def test_daily_ai_review_is_disabled_without_key_and_capped_when_enabled(monkeypatch):
    class Database:
        def __init__(self):
            self.saved = []
            self.rows = [dict(
                id=index, source="email", url=f"https://example.test/{index}", title=f"Flat {index}",
                address="Hackney", postcode="E8", monthly_rent=2000, bedrooms=1,
                bathrooms=None, description=None, first_seen_at=datetime.now(timezone.utc),
                commute_minutes=minutes, metadata={},
            ) for index, minutes in ((1, 40), (2, 20))]

        def ai_reviews_today(self, day):
            return len(self.saved)

        def api_summary_rows(self, since):
            return self.rows

        def save_ai_review(self, listing_id, review):
            self.saved.append(listing_id)

    settings = SimpleNamespace(
        ai_review_enabled=False, openai_api_key=None, openai_model=None,
        ai_max_listings_per_day=1, criteria=MatchCriteria(max_monthly_rent=2500),
    )
    database = Database()
    assert run_ai_reviews(database, settings) == 0
    settings.ai_review_enabled = True
    settings.openai_api_key = "test-only"
    settings.openai_model = "chosen-model"
    monkeypatch.setattr("rental_bot.cli.request_review", lambda facts, **kwargs: {"summary": "test"})

    assert run_ai_reviews(database, settings) == 1
    assert database.saved == [2]
    assert run_ai_reviews(database, settings) == 0

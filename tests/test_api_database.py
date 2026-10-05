from contextlib import contextmanager
from datetime import datetime, timezone

from rental_bot.db import Database


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, parameters):
        self.calls.append((sql, parameters))
        return self

    def fetchone(self):
        if "COUNT(*)" in self.calls[-1][0]:
            return {"total": 201}
        return {"id": 7, "review_status": "favourite", "reviewed_at": datetime.now(timezone.utc)}

    def fetchall(self):
        return [{"id": 7}]


def database_with_fake_connection():
    connection = FakeConnection()
    database = Database("unused")

    @contextmanager
    def fake_connection():
        yield connection

    database.connection = fake_connection
    return database, connection


def test_api_paginates_after_parameterized_filtering():
    database, connection = database_with_fake_connection()
    since = datetime.now(timezone.utc)
    rows, total = database.api_listings(
        since, limit=20, offset=100, status="unreviewed", max_rent=2400,
        min_bedrooms=2, areas=("O'Brien", "Hackney"), max_commute=45,
        matching_only=True,
    )
    assert rows == [{"id": 7}]
    assert total == 201
    count_sql, count_params = connection.calls[0]
    page_sql, page_params = connection.calls[1]
    assert "duplicate_of" in count_sql
    assert "invalid_listing_link" in count_sql
    assert "O'Brien" not in count_sql
    assert "POSITION(LOWER(%s)" in count_sql
    assert count_params == [since, "unreviewed", 2400, 2, "O'Brien", "Hackney", 45]
    assert "ORDER BY first_seen_at DESC" in page_sql
    assert page_params[-2:] == [20, 100]


def test_review_update_excludes_hidden_duplicate():
    database, connection = database_with_fake_connection()
    result = database.api_set_review_status(7, "favourite")
    assert result["review_status"] == "favourite"
    assert "NOT (metadata ? 'duplicate_of')" in connection.calls[0][0]
    assert "NOT (metadata ? 'invalid_listing_link')" in connection.calls[0][0]
    assert connection.calls[0][1] == ("favourite", 7)


def test_commute_sort_is_applied_before_pagination():
    database, connection = database_with_fake_connection()
    database.api_listings(datetime.now(timezone.utc), limit=100, offset=100, sort="commute")
    page_sql, page_params = connection.calls[1]
    assert "ORDER BY commute_minutes ASC NULLS LAST, first_seen_at DESC, id DESC" in page_sql
    assert page_params[-2:] == [100, 100]


def test_browse_filters_reviewed_and_commute_without_requiring_a_search_match():
    database, connection = database_with_fake_connection()
    database.api_listings(datetime.now(timezone.utc), limit=20, status="reviewed", commute_at_most=35)
    count_sql, count_params = connection.calls[0]
    assert "review_status <> 'unreviewed'" in count_sql
    assert "commute_minutes <= %s" in count_sql
    assert "monthly_rent <= %s" not in count_sql
    assert count_params[-1] == 35

from __future__ import annotations

from contextlib import contextmanager
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .images import is_listing_image_url
from .models import NormalizedListing, StoredListing
from .normalize import canonicalize_url


class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            yield connection

    def ensure_schema(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text()
        with self.connection() as connection:
            connection.execute(schema)

    def consolidate_urls(self, *, apply: bool = False) -> tuple[int, int]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM listings WHERE NOT (metadata ? 'duplicate_of') ORDER BY id"
                + (" FOR UPDATE" if apply else "")
            ).fetchall()
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                groups[canonicalize_url(row["url"])].append(row)
            changed = [
                (canonical_url, group)
                for canonical_url, group in groups.items()
                if len(group) > 1 or group[0]["canonical_url"] != canonical_url
            ]
            duplicate_count = sum(len(group) - 1 for _, group in changed)
            if not apply:
                return len(changed), duplicate_count
            for canonical_url, group in changed:
                reviewed = [row for row in group if row["review_status"] != "unreviewed"]
                if len({row["review_status"] for row in reviewed}) > 1:
                    raise ValueError("Conflicting review decisions prevent URL consolidation")
                exact = [row for row in group if row["canonical_url"] == canonical_url]
                candidates = exact or reviewed or group
                winner = max(candidates, key=_listing_completeness)
                others = [row for row in group if row["id"] != winner["id"]]
                preferred = [winner, *others]
                metadata = dict(winner["metadata"] or {})
                metadata.pop("duplicate_of", None)
                images = list(metadata.get("image_urls") or [])
                for row in others:
                    other_metadata = row["metadata"] or {}
                    images.extend(image for image in other_metadata.get("image_urls", []) if image not in images)
                    if "page_detail" not in metadata and "page_detail" in other_metadata:
                        metadata["page_detail"] = other_metadata["page_detail"]
                if images:
                    metadata["image_urls"] = images
                chosen_review = reviewed[0] if reviewed else winner
                values = {
                    "id": winner["id"],
                    "canonical_url": canonical_url,
                    "address": _first_present(preferred, "address"),
                    "postcode": _first_present(preferred, "postcode"),
                    "description": max((row["description"] or "" for row in group), key=len) or None,
                    "monthly_rent": _first_present(preferred, "monthly_rent"),
                    "bedrooms": _first_present(preferred, "bedrooms"),
                    "bathrooms": _first_present(preferred, "bathrooms"),
                    "available_from": _first_present(preferred, "available_from"),
                    "latitude": _first_present(preferred, "latitude"),
                    "longitude": _first_present(preferred, "longitude"),
                    "commute_minutes": _first_present(preferred, "commute_minutes"),
                    "first_seen_at": min(row["first_seen_at"] for row in group),
                    "last_seen_at": max(row["last_seen_at"] for row in group),
                    "digest_sent_at": _first_present(group, "digest_sent_at"),
                    "notified_at": _first_present(group, "notified_at"),
                    "review_status": chosen_review["review_status"],
                    "reviewed_at": chosen_review["reviewed_at"],
                    "metadata": Jsonb(metadata),
                }
                connection.execute(
                    """UPDATE listings SET canonical_url = %(canonical_url)s, address = %(address)s,
                       postcode = %(postcode)s, description = %(description)s, monthly_rent = %(monthly_rent)s,
                       bedrooms = %(bedrooms)s, bathrooms = %(bathrooms)s, available_from = %(available_from)s,
                       latitude = %(latitude)s, longitude = %(longitude)s, commute_minutes = %(commute_minutes)s,
                       first_seen_at = %(first_seen_at)s, last_seen_at = %(last_seen_at)s,
                       digest_sent_at = %(digest_sent_at)s, notified_at = %(notified_at)s,
                       review_status = %(review_status)s, reviewed_at = %(reviewed_at)s,
                       metadata = %(metadata)s WHERE id = %(id)s""",
                    values,
                )
                for row in others:
                    connection.execute(
                        "UPDATE listings SET metadata = COALESCE(metadata, '{}'::jsonb) || %s WHERE id = %s",
                        (Jsonb({"duplicate_of": winner["id"]}), row["id"]),
                    )
            return len(changed), duplicate_count

    def upsert(self, listing: NormalizedListing) -> tuple[int, bool]:
        insert_sql = """
            INSERT INTO listings (
                source, source_listing_id, url, canonical_url, title, address, postcode,
                description, monthly_rent, bedrooms, bathrooms, available_from, metadata
            ) VALUES (%(source)s, %(source_listing_id)s, %(url)s, %(canonical_url)s, %(title)s,
                %(address)s, %(postcode)s, %(description)s, %(monthly_rent)s, %(bedrooms)s,
                %(bathrooms)s, %(available_from)s, %(metadata)s)
            ON CONFLICT (canonical_url) DO NOTHING
            RETURNING id
        """
        values = {
            "source": listing.source,
            "source_listing_id": listing.source_listing_id,
            "url": listing.url,
            "canonical_url": listing.canonical_url,
            "title": listing.title,
            "address": listing.address,
            "postcode": listing.postcode,
            "description": listing.description,
            "monthly_rent": listing.monthly_rent,
            "bedrooms": listing.bedrooms,
            "bathrooms": listing.bathrooms,
            "available_from": listing.available_from,
            "metadata": Jsonb(listing.metadata),
        }
        with self.connection() as connection:
            row = connection.execute(insert_sql, values).fetchone()
            if row:
                return int(row["id"]), True
            current = connection.execute(
                "SELECT metadata FROM listings WHERE canonical_url = %s FOR UPDATE",
                (listing.canonical_url,),
            ).fetchone()
            if not current:
                raise RuntimeError(f"Could not find listing {listing.canonical_url}")
            previous_metadata = current["metadata"] or {}
            values["metadata"] = Jsonb({
                **listing.metadata,
                "image_urls": _merge_image_urls(
                    previous_metadata.get("image_urls"), listing.metadata.get("image_urls")
                ),
            })
            existing = connection.execute(
                """UPDATE listings SET last_seen_at = NOW(), url = %(url)s,
                   title = CASE WHEN metadata ? 'page_detail' THEN title ELSE %(title)s END,
                   address = CASE WHEN metadata ? 'page_detail' THEN COALESCE(address, %(address)s)
                                  ELSE COALESCE(%(address)s, address) END,
                   postcode = CASE WHEN metadata ? 'page_detail' THEN COALESCE(postcode, %(postcode)s)
                                   ELSE COALESCE(%(postcode)s, postcode) END,
                   description = CASE WHEN metadata ? 'page_detail' THEN COALESCE(description, %(description)s)
                                      ELSE COALESCE(%(description)s, description) END,
                   monthly_rent = CASE WHEN metadata ? 'page_detail' THEN COALESCE(monthly_rent, %(monthly_rent)s)
                                       ELSE COALESCE(%(monthly_rent)s, monthly_rent) END,
                   bedrooms = CASE WHEN metadata ? 'page_detail' THEN COALESCE(bedrooms, %(bedrooms)s)
                                   ELSE COALESCE(%(bedrooms)s, bedrooms) END,
                   bathrooms = CASE WHEN metadata ? 'page_detail' THEN COALESCE(bathrooms, %(bathrooms)s)
                                    ELSE COALESCE(%(bathrooms)s, bathrooms) END,
                   available_from = COALESCE(%(available_from)s, available_from),
                   metadata = COALESCE(metadata, '{}'::jsonb) || %(metadata)s
                   WHERE canonical_url = %(canonical_url)s RETURNING id""",
                values,
            ).fetchone()
            if not existing:
                raise RuntimeError(f"Could not update listing {listing.canonical_url}")
            return int(existing["id"]), False


    def new_listings(self, since: datetime) -> list[StoredListing]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT id, source, url, title, address, postcode, monthly_rent, bedrooms,
                   bathrooms, description, first_seen_at, commute_minutes, latitude, longitude, metadata
                   FROM listings WHERE first_seen_at >= %s AND digest_sent_at IS NULL
                     AND NOT (metadata ? 'duplicate_of')
                     AND NOT (metadata ? 'invalid_listing_link')
                   ORDER BY first_seen_at DESC""",
                (since,),
            ).fetchall()
        return [_stored(row) for row in rows]

    def listings_for_enrichment(self, since: datetime) -> list[StoredListing]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT id, source, url, title, address, postcode, monthly_rent, bedrooms,
                   bathrooms, description, first_seen_at, commute_minutes, latitude, longitude, metadata
                   FROM listings
                   WHERE first_seen_at >= %s AND NOT (metadata ? 'duplicate_of')
                     AND NOT (metadata ? 'invalid_listing_link')
                     AND (digest_sent_at IS NULL OR latitude IS NULL OR longitude IS NULL)
                   ORDER BY first_seen_at DESC""",
                (since,),
            ).fetchall()
        return [_stored(row) for row in rows]

    def listings_for_page_review(self, since: datetime, limit: int, domains: tuple[str, ...] | None = None) -> list[StoredListing]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT id, source, url, title, address, postcode, monthly_rent, bedrooms,
                          bathrooms, description, first_seen_at, commute_minutes, latitude, longitude, metadata
                   FROM listings
                   WHERE first_seen_at >= %s AND NOT (metadata ? 'page_detail')
                     AND NOT (metadata ? 'duplicate_of')
                     AND NOT (metadata ? 'invalid_listing_link')
                     AND (%s::text[] IS NULL OR split_part(url, '/', 3) = ANY(%s::text[]))
                   ORDER BY first_seen_at DESC LIMIT %s""",
                (since, list(domains) if domains is not None else None, list(domains) if domains is not None else None, limit),
            ).fetchall()
        return [_stored(row) for row in rows]

    def listing_for_page_review(self, listing_id: int) -> StoredListing | None:
        with self.connection() as connection:
            row = connection.execute(
                """SELECT id, source, url, title, address, postcode, monthly_rent, bedrooms,
                          bathrooms, description, first_seen_at, commute_minutes, latitude, longitude, metadata
                   FROM listings WHERE id = %s AND NOT (metadata ? 'duplicate_of')
                     AND NOT (metadata ? 'invalid_listing_link')""",
                (listing_id,),
            ).fetchone()
        return _stored(row) if row else None

    def save_page_detail(self, listing_id: int, *, facts: dict[str, object], detail: dict[str, object]) -> None:
        with self.connection() as connection:
            connection.execute(
                """UPDATE listings SET
                     title = COALESCE(NULLIF(%(title)s, ''), title),
                     address = COALESCE(%(address)s, address),
                     postcode = COALESCE(%(postcode)s, postcode),
                     description = COALESCE(%(description)s, description),
                     monthly_rent = COALESCE(%(monthly_rent)s, monthly_rent),
                     bedrooms = COALESCE(%(bedrooms)s, bedrooms),
                     bathrooms = COALESCE(%(bathrooms)s, bathrooms),
                     available_from = COALESCE(%(available_from)s, available_from),
                     metadata = COALESCE(metadata, '{}'::jsonb) || %(detail)s
                   WHERE id = %(id)s""",
                {
                    "id": listing_id,
                    "title": facts.get("title"),
                    "address": facts.get("address"),
                    "postcode": facts.get("postcode"),
                    "description": facts.get("description"),
                    "monthly_rent": facts.get("monthly_rent"),
                    "bedrooms": facts.get("bedrooms"),
                    "bathrooms": facts.get("bathrooms"),
                    "available_from": facts.get("available_from"),
                    "detail": Jsonb({"page_detail": detail}),
                },
            )

    def unnotified_listings(self, since: datetime) -> list[StoredListing]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT id, source, url, title, address, postcode, monthly_rent, bedrooms,
                   bathrooms, description, first_seen_at, commute_minutes, latitude, longitude, metadata
                   FROM listings
                   WHERE first_seen_at >= %s AND digest_sent_at IS NULL AND notified_at IS NULL
                     AND NOT (metadata ? 'duplicate_of')
                     AND NOT (metadata ? 'invalid_listing_link')
                   ORDER BY first_seen_at DESC""",
                (since,),
            ).fetchall()
        return [_stored(row) for row in rows]

    def dashboard_listings(self, since: datetime, limit: int = 200) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT id, source, url, title, address, postcode, monthly_rent, bedrooms,
                   bathrooms, description, available_from, first_seen_at, commute_minutes,
                   latitude, longitude, digest_sent_at, review_status, reviewed_at, metadata
                   FROM listings WHERE first_seen_at >= %s AND NOT (metadata ? 'duplicate_of')
                     AND NOT (metadata ? 'invalid_listing_link')
                   ORDER BY first_seen_at DESC LIMIT %s""",
                (since, limit),
            ).fetchall()
        return list(rows)

    def api_listings(
        self, since: datetime, *, limit: int, offset: int = 0, status: str | None = None,
        max_rent: int | None = None, min_bedrooms: int | None = None,
        areas: tuple[str, ...] = (), max_commute: int | None = None,
        matching_only: bool = False, commute_at_most: int | None = None, sort: str = "newest",
    ) -> tuple[list[dict[str, Any]], int]:
        if sort not in {"newest", "commute"}:
            raise ValueError(f"Unsupported listing sort: {sort}")
        conditions = ["first_seen_at >= %s", "NOT (metadata ? 'duplicate_of')", "NOT (metadata ? 'invalid_listing_link')"]
        parameters: list[Any] = [since]
        if status is not None:
            if status == "reviewed":
                conditions.append("review_status <> 'unreviewed'")
            else:
                conditions.append("review_status = %s")
                parameters.append(status)
        if commute_at_most is not None:
            conditions.append("commute_minutes <= %s")
            parameters.append(commute_at_most)
        if matching_only:
            if max_rent is not None:
                conditions.append("monthly_rent <= %s")
                parameters.append(max_rent)
            if min_bedrooms is not None:
                conditions.append("bedrooms >= %s")
                parameters.append(min_bedrooms)
            if areas:
                conditions.append(
                    "(" + " OR ".join(
                        "POSITION(LOWER(%s) IN LOWER(CONCAT_WS(' ', address, postcode, title))) > 0"
                        for _ in areas
                    ) + ")"
                )
                parameters.extend(areas)
            if max_commute is not None:
                conditions.append("commute_minutes <= %s")
                parameters.append(max_commute)
        where = " AND ".join(conditions)
        order_by = (
            "commute_minutes ASC NULLS LAST, first_seen_at DESC, id DESC"
            if sort == "commute" else "first_seen_at DESC, id DESC"
        )
        with self.connection() as connection:
            total = connection.execute(f"SELECT COUNT(*) AS total FROM listings WHERE {where}", parameters).fetchone()["total"]
            rows = connection.execute(
                """SELECT id, source, url, title, address, postcode, monthly_rent, bedrooms,
                          bathrooms, description, available_from, first_seen_at, commute_minutes,
                          latitude, longitude, digest_sent_at, review_status, reviewed_at, metadata
                   FROM listings WHERE """ + where + f" ORDER BY {order_by} LIMIT %s OFFSET %s",
                [*parameters, limit, offset],
            ).fetchall()
        return list(rows), int(total)

    def api_summary_rows(self, since: datetime) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return list(connection.execute(
                """SELECT id, source, url, title, address, postcode, monthly_rent, bedrooms,
                          bathrooms, description, available_from, first_seen_at, commute_minutes,
                          latitude, longitude, review_status, metadata
                   FROM listings WHERE first_seen_at >= %s AND NOT (metadata ? 'duplicate_of')
                     AND NOT (metadata ? 'invalid_listing_link')""",
                (since,),
            ).fetchall())

    def ai_reviews_today(self, day: str) -> int:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM listings WHERE metadata->'ai_review'->>'reviewed_at' LIKE %s",
                (f"{day}%",),
            ).fetchone()
        return int(row["total"])

    def save_ai_review(self, listing_id: int, review: dict[str, object]) -> None:
        with self.connection() as connection:
            connection.execute(
                """UPDATE listings SET metadata = metadata || %s
                   WHERE id = %s AND NOT (metadata ? 'duplicate_of')
                     AND NOT (metadata ? 'invalid_listing_link')""",
                (Jsonb({"ai_review": review}), listing_id),
            )

    def api_listing(self, listing_id: int) -> dict[str, Any] | None:
        with self.connection() as connection:
            return connection.execute(
                """SELECT id, source, url, title, address, postcode, monthly_rent, bedrooms,
                          bathrooms, description, available_from, first_seen_at, commute_minutes,
                          latitude, longitude, review_status, reviewed_at, metadata
                   FROM listings WHERE id = %s AND NOT (metadata ? 'duplicate_of')
                     AND NOT (metadata ? 'invalid_listing_link')""",
                (listing_id,),
            ).fetchone()

    def api_set_review_status(self, listing_id: int, status: str) -> dict[str, Any] | None:
        if status not in {"unreviewed", "rejected", "left", "favourite"}:
            raise ValueError(f"Unsupported review status: {status}")
        with self.connection() as connection:
            return connection.execute(
                """UPDATE listings SET review_status = %s, reviewed_at = NOW()
                   WHERE id = %s AND NOT (metadata ? 'duplicate_of')
                     AND NOT (metadata ? 'invalid_listing_link')
                   RETURNING id, review_status, reviewed_at""",
                (status, listing_id),
            ).fetchone()

    def set_review_status(self, listing_id: int, status: str) -> None:
        allowed_statuses = {"unreviewed", "rejected", "left", "favourite"}
        if status not in allowed_statuses:
            raise ValueError(f"Unsupported review status: {status}")
        with self.connection() as connection:
            connection.execute(
                "UPDATE listings SET review_status = %s, reviewed_at = NOW() WHERE id = %s",
                (status, listing_id),
            )

    def mark_digest_sent(self, listing_ids: list[int]) -> None:
        if not listing_ids:
            return
        with self.connection() as connection:
            connection.execute("UPDATE listings SET digest_sent_at = NOW() WHERE id = ANY(%s)", (listing_ids,))

    def mark_notified(self, listing_ids: list[int]) -> None:
        if not listing_ids:
            return
        with self.connection() as connection:
            connection.execute("UPDATE listings SET notified_at = NOW() WHERE id = ANY(%s)", (listing_ids,))

    def update_enrichment(self, listing_id: int, *, latitude: float | None, longitude: float | None, commute_minutes: int | None) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE listings SET latitude = %s, longitude = %s, commute_minutes = %s WHERE id = %s",
                (latitude, longitude, commute_minutes, listing_id),
            )

    def update_commute(self, listing_id: int, *, commute_minutes: int | None, commute: dict[str, object]) -> None:
        with self.connection() as connection:
            connection.execute(
                """UPDATE listings
                   SET commute_minutes = %s,
                       metadata = COALESCE(metadata, '{}'::jsonb) || %s
                   WHERE id = %s""",
                (commute_minutes, Jsonb({"commute": commute}), listing_id),
            )


def _merge_image_urls(previous: object, incoming: object) -> list[str]:
    previous_urls = previous if isinstance(previous, list) else []
    incoming_urls = incoming if isinstance(incoming, list) else []
    return list(dict.fromkeys(
        url for url in [*incoming_urls, *previous_urls]
        if isinstance(url, str) and is_listing_image_url(url)
    ))


def _stored(row: dict[str, Any]) -> StoredListing:
    return StoredListing(
        id=int(row["id"]),
        source=row["source"],
        url=row["url"],
        title=row["title"],
        address=row["address"],
        postcode=row["postcode"],
        monthly_rent=row["monthly_rent"],
        bedrooms=row["bedrooms"],
        bathrooms=row["bathrooms"],
        description=row["description"],
        first_seen_at=row["first_seen_at"].astimezone(timezone.utc),
        available_from=row.get("available_from"),
        commute_minutes=row["commute_minutes"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        metadata=row["metadata"] or {},
    )


def _listing_completeness(row: dict[str, Any]) -> tuple[int, int, int]:
    fields = ("address", "postcode", "description", "monthly_rent", "bedrooms", "bathrooms")
    metadata = row["metadata"] or {}
    return (
        sum(row[field] is not None for field in fields),
        len(metadata.get("image_urls") or []),
        -int(row["id"]),
    )


def _first_present(rows: list[dict[str, Any]], field: str) -> Any:
    return next((row[field] for row in rows if row[field] is not None), None)

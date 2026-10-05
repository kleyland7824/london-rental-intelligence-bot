from __future__ import annotations

import argparse
import imaplib
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
import psycopg

from .db import Database
from .dashboard import stored_listing
from .assessment import rank_listings
from .ai_review import request_review, review_facts, review_input_hash
from .detail_viewer import DetailViewError, HttpDetailViewer
from .digest import render_digest, render_telegram_digest, send_email, send_telegram
from .enrichment import Coordinates, GoogleGeocoder, GoogleRoutesClient, PostcodesIoGeocoder, TfLJourneyPlanner
from .matching import matches
from .models import RawListing
from .normalize import infer_fields, normalize
from .settings import Settings
from .sources.email import EmailAlertSource


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal London rental intelligence bot")
    parser.add_argument("command", choices=["init-db", "check-mail", "ingest-email", "enrich", "enrich-pages", "migrate-urls", "digest", "run-once", "worker", "schedule", "view-url"])
    parser.add_argument("--url")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--listing-id", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--since-hours", type=int, default=30)
    args = parser.parse_args()
    settings = Settings.from_env(require_email=args.command not in {"init-db", "view-url", "enrich-pages", "migrate-urls"})
    if args.command == "view-url":
        if not args.url:
            parser.error("view-url requires --url")
        print(view_url(args.url, settings))
        return
    database = Database(settings.database_url)
    database.ensure_schema()

    if args.command == "init-db":
        print("Database schema is ready.")
    elif args.command == "check-mail":
        print(f"Mailbox connection succeeded; {check_mail(settings)} message(s) in {settings.mailbox!r}.")
    elif args.command == "ingest-email":
        print(f"Ingested {ingest_email(database, settings)} listing(s).")
    elif args.command == "enrich":
        print(f"Enriched {enrich_listings(database, settings, args.since_hours)} listing(s).")
    elif args.command == "enrich-pages":
        print(f"Reviewed {enrich_pages(database, settings, args.since_hours, args.limit, listing_id=args.listing_id)} listing page(s).")
    elif args.command == "migrate-urls":
        groups, duplicates = database.consolidate_urls(apply=args.apply)
        print(f"{'Consolidated' if args.apply else 'Would consolidate'} {groups} URL group(s); {duplicates} duplicate record(s) retained as hidden history.")
    elif args.command == "digest":
        enrich_listings(database, settings, args.since_hours)
        print(deliver_digest(database, settings, args.since_hours))
    elif args.command == "run-once":
        ingest_email(database, settings)
        enrich_listings(database, settings, args.since_hours)
        print(deliver_digest(database, settings, args.since_hours))
    elif args.command in {"worker", "schedule"}:
        worker(database, settings)


def ingest_email(database: Database, settings: Settings) -> int:
    source = EmailAlertSource(
        host=settings.mail_host,
        port=settings.mail_port,
        username=settings.mail_username,
        password=settings.mail_password,
        mailbox=settings.mailbox,
        tls=settings.mail_tls,
        source_name=settings.email_source_name,
        search_since_days=settings.email_search_since_days,
        link_pattern=settings.email_link_pattern,
    )
    count = 0
    for raw_listing in source.fetch():
        database.upsert(normalize(raw_listing))
        count += 1
    return count


def check_mail(settings: Settings) -> int:
    source = EmailAlertSource(
        host=settings.mail_host,
        port=settings.mail_port,
        username=settings.mail_username,
        password=settings.mail_password,
        mailbox=settings.mailbox,
        tls=settings.mail_tls,
    )
    return source.check_connection()


def view_url(url: str, settings: Settings) -> str:
    if not settings.detail_review_enabled:
        raise DetailViewError("Detail review is disabled; set DETAIL_REVIEW_ENABLED=true")
    viewer = HttpDetailViewer(
        allowed_domains=settings.detail_allowed_domains,
        provider_authorized_domains=settings.detail_provider_authorized_domains,
        timeout_seconds=settings.detail_timeout_seconds,
        max_bytes=settings.detail_max_bytes,
        max_redirects=settings.detail_max_redirects,
        user_agent=settings.detail_user_agent,
    )
    snapshot = viewer.fetch(url)
    lines = [
        f"Requested URL: {snapshot.requested_url}",
        f"Final URL: {snapshot.final_url}",
        f"HTTP status: {snapshot.status_code}",
        f"Title: {snapshot.title or '(missing)'}",
        f"Description: {snapshot.description or '(missing)'}",
        f"Images: {len(snapshot.image_urls)}",
        f"Content hash: {snapshot.content_hash}",
        "",
        "Visible text:",
        snapshot.text[:12_000],
    ]
    return "\n".join(lines)


def enrich_pages(database: Database, settings: Settings, since_hours: int, limit: int, *, listing_id: int | None = None) -> int:
    if not settings.detail_review_enabled:
        raise DetailViewError("Detail review is disabled; set DETAIL_REVIEW_ENABLED=true")
    if limit < 1 or limit > 20:
        raise ValueError("--limit must be between 1 and 20")
    viewer = HttpDetailViewer(
        allowed_domains=settings.detail_allowed_domains,
        provider_authorized_domains=settings.detail_provider_authorized_domains,
        timeout_seconds=settings.detail_timeout_seconds,
        max_bytes=settings.detail_max_bytes,
        max_redirects=settings.detail_max_redirects,
        user_agent=settings.detail_user_agent,
    )
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    reviewed = 0
    if listing_id is not None:
        if listing_id < 1:
            raise ValueError("--listing-id must be positive")
        selected = database.listing_for_page_review(listing_id)
        listings = [selected] if selected else []
    else:
        listings = database.listings_for_page_review(since, limit, settings.detail_allowed_domains)
    for listing in listings:
        try:
            snapshot = viewer.fetch(listing.url)
        except DetailViewError as error:
            print(f"Listing {listing.id}: page review skipped ({error}).", flush=True)
            continue
        original = urlsplit(listing.url)
        final = urlsplit(snapshot.final_url)
        if (original.hostname, original.path.rstrip("/")) != (final.hostname, final.path.rstrip("/")):
            print(f"Listing {listing.id}: page review skipped (redirected to a different listing).", flush=True)
            continue
        facts = dict(snapshot.facts)
        raw = RawListing(
            source=listing.source,
            url=listing.url,
            title=str(facts.get("title") or ""),
            address=str(facts.get("address") or "") or None,
            description=str(facts.get("description") or snapshot.text[:2000]),
            monthly_rent=facts.get("monthly_rent"),
            bedrooms=facts.get("bedrooms"),
            bathrooms=facts.get("bathrooms"),
        )
        rent, bedrooms, bathrooms, postcode = infer_fields(raw)
        facts.update(monthly_rent=rent, bedrooms=bedrooms, bathrooms=bathrooms, postcode=postcode)
        available_from = facts.get("available_from")
        if isinstance(available_from, str):
            try:
                facts["available_from"] = date.fromisoformat(available_from).isoformat()
            except ValueError:
                facts.pop("available_from", None)
        database.save_page_detail(
            listing.id,
            facts=facts,
            detail={
                "final_url": snapshot.final_url,
                "retrieved_at": snapshot.retrieved_at.isoformat(),
                "content_hash": snapshot.content_hash,
                "image_urls": list(snapshot.image_urls),
                "facts": facts,
            },
        )
        reviewed += 1
    return reviewed


def deliver_digest(database: Database, settings: Settings, since_hours: int) -> str:
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    candidates = database.new_listings(since)
    matching = rank_listings([listing for listing in candidates if matches(listing, settings.criteria)], settings.criteria)
    body = render_digest(matching, settings.criteria)
    if matching and settings.smtp_host and settings.digest_from and settings.digest_to:
        send_email(
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            sender=settings.digest_from,
            recipient=settings.digest_to,
            body=body,
        )
    database.mark_digest_sent([listing.id for listing in candidates])
    return body


def notify_new_matches(database: Database, settings: Settings, since_hours: int) -> int:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return 0
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    candidates = database.unnotified_listings(since)
    matching = rank_listings([listing for listing in candidates if matches(listing, settings.criteria)], settings.criteria)
    if not matching:
        return 0
    for start in range(0, len(matching), 4):
        batch = matching[start:start + 4]
        send_telegram(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            body=render_telegram_digest(batch, settings.criteria),
        )
        database.mark_notified([listing.id for listing in batch])
    return len(matching)


def run_ai_reviews(database: Database, settings: Settings) -> int:
    if not settings.ai_review_enabled:
        return 0
    if not settings.openai_api_key or not settings.openai_model:
        raise ValueError("AI review needs OPENAI_API_KEY and OPENAI_MODEL")
    if not 1 <= settings.ai_max_listings_per_day <= 20:
        raise ValueError("AI_MAX_LISTINGS_PER_DAY must be between 1 and 20")
    today = datetime.now(timezone.utc).date().isoformat()
    remaining = settings.ai_max_listings_per_day - database.ai_reviews_today(today)
    if remaining <= 0:
        return 0
    since = datetime.now(timezone.utc) - timedelta(days=7)
    rows = database.api_summary_rows(since)
    candidates = rank_listings(
        [stored_listing(row) for row in rows if matches(stored_listing(row), settings.criteria)],
        settings.criteria,
    )
    existing = {row["id"]: row.get("metadata", {}).get("ai_review", {}) for row in rows}
    reviewed = 0
    for listing in candidates:
        facts = review_facts(listing)
        if existing[listing.id].get("input_hash") == review_input_hash(facts, settings.openai_model):
            continue
        result = request_review(facts, api_key=settings.openai_api_key, model=settings.openai_model)
        database.save_ai_review(listing.id, result)
        reviewed += 1
        if reviewed >= remaining:
            break
    return reviewed


def enrich_listings(database: Database, settings: Settings, since_hours: int, *, pending_only: bool = False) -> int:
    if not settings.geocoding_enabled and not settings.commute_destinations:
        return 0
    if settings.geocoding_enabled and settings.google_maps_api_key:
        geocoder = GoogleGeocoder(settings.google_maps_api_key)
    elif settings.geocoding_enabled:
        geocoder = PostcodesIoGeocoder(settings.geocoding_user_agent)
    else:
        geocoder = None
    commute = None
    google_routes = None
    if settings.commute_provider == "google":
        if not settings.google_maps_api_key:
            raise ValueError("COMMUTE_PROVIDER=google requires GOOGLE_MAPS_API_KEY")
        google_routes = GoogleRoutesClient(
            settings.google_maps_api_key,
            settings.google_transit_departure_time,
            settings.google_transit_timezone,
        )
    elif settings.criteria.max_commute_minutes and settings.commute_destination:
        commute = TfLJourneyPlanner(settings.commute_destination)
    elif settings.commute_provider not in {"tfl", "google", "none"}:
        raise ValueError(f"Unsupported commute provider: {settings.commute_provider}")
    count = 0
    coordinates_by_location: dict[str, Coordinates | None] = {}
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        for listing in database.listings_for_enrichment(since):
            if pending_only and listing.latitude is not None and listing.longitude is not None and (
                not (google_routes or commute) or listing.commute_minutes is not None
            ):
                continue
            coordinates = None
            location = listing.address or listing.postcode
            if geocoder and location and (listing.latitude is None or listing.longitude is None):
                location_key = location.casefold().strip()
                if location_key not in coordinates_by_location:
                    try:
                        coordinates_by_location[location_key] = geocoder.geocode(location)
                    except httpx.HTTPError:
                        coordinates_by_location[location_key] = None
                coordinates = coordinates_by_location[location_key]
            commute_minutes = None
            if commute and location:
                try:
                    commute_minutes = commute.minutes(location)
                except httpx.HTTPError:
                    commute_minutes = None
            if google_routes and location and settings.commute_destinations:
                commute_data = {
                    "provider": "google-routes",
                    "calculated_at": datetime.now(timezone.utc).isoformat(),
                    "destinations": {},
                }
                destination_minutes: list[int] = []
                for name, destination in settings.commute_destinations:
                    try:
                        routes = google_routes.routes(
                            origin=location,
                            destination=destination,
                            modes=settings.commute_modes,
                            destination_name=name,
                        )
                    except httpx.HTTPError:
                        routes = {"status": "request_failed", "destination": name}
                    commute_data["destinations"][name] = routes
                    transit = routes.get("transit") if isinstance(routes, dict) else None
                    if isinstance(transit, dict) and isinstance(transit.get("minutes"), int):
                        destination_minutes.append(transit["minutes"])
                commute_minutes = max(destination_minutes) if destination_minutes else None
                database.update_commute(listing.id, commute_minutes=commute_minutes, commute=commute_data)
            if coordinates or commute_minutes is not None:
                database.update_enrichment(
                    listing.id,
                    latitude=coordinates.latitude if coordinates else listing.latitude,
                    longitude=coordinates.longitude if coordinates else listing.longitude,
                    commute_minutes=commute_minutes,
                )
                count += 1
    finally:
        if geocoder:
            geocoder.close()
        if commute:
            commute.close()
        if google_routes:
            google_routes.close()
    return count


def worker(database: Database, settings: Settings) -> None:
    last_digest_date = None
    monitor = WorkerMonitor(settings)
    while True:
        stage = "mail_ingest"
        try:
            processed = ingest_email(database, settings)
            stage = "enrichment"
            enriched = enrich_listings(database, settings, 30, pending_only=True)
            stage = "page_enrichment"
            page_reviewed = 0
            if settings.detail_review_enabled and settings.detail_pages_per_cycle:
                if not 0 <= settings.detail_pages_per_cycle <= 5:
                    raise ValueError("DETAIL_PAGES_PER_CYCLE must be between 0 and 5")
                page_reviewed = enrich_pages(database, settings, 168, settings.detail_pages_per_cycle)
            stage = "notification"
            notified = notify_new_matches(database, settings, 48)
            stage = "digest"
            now = datetime.now(timezone.utc)
            if now.hour == settings.digest_hour_utc and last_digest_date != now.date():
                print(deliver_digest(database, settings, 48), flush=True)
                last_digest_date = now.date()
            stage = "ai_review"
            ai_reviewed = run_ai_reviews(database, settings) if now.hour == settings.ai_review_hour_utc else 0
            monitor.recovered()
            print(f"Worker cycle complete; processed {processed} alert listing(s), enriched {enriched}, page reviewed {page_reviewed}, notified {notified} matching listing(s), AI reviewed {ai_reviewed}.", flush=True)
        except Exception as error:
            monitor.failed(stage, error)
        time.sleep(settings.worker_interval_seconds)


def _worker_error_category(error: Exception) -> str:
    if isinstance(error, imaplib.IMAP4.abort):
        return "mail_connection"
    if isinstance(error, imaplib.IMAP4.error):
        detail = error.args[0] if error.args else ""
        if b"invalid credentials" in (detail if isinstance(detail, bytes) else str(detail).encode()).lower():
            return "mail_auth_rejected"
        return "mail_protocol"
    if isinstance(error, (OSError, TimeoutError)):
        return "network"
    if isinstance(error, psycopg.Error):
        return "database"
    if isinstance(error, httpx.HTTPError):
        return "external_service"
    return "unexpected"


@dataclass
class WorkerMonitor:
    settings: Settings
    failure_streak: int = 0
    alerted: bool = False
    last_alert_at: datetime | None = None

    def failed(self, stage: str, error: Exception) -> None:
        self.failure_streak += 1
        category = _worker_error_category(error)
        print(f"Worker cycle failed; stage={stage}; category={category}; consecutive_failures={self.failure_streak}.", flush=True)
        now = datetime.now(timezone.utc)
        due = self.last_alert_at is None or now - self.last_alert_at >= timedelta(hours=24)
        threshold = 1 if category == "mail_auth_rejected" else 2
        if self.failure_streak >= threshold and due:
            body = (
                "Rental bot needs attention: processing has failed on consecutive cycles.\n"
                f"Stage: {stage}; category: {category}; failed cycles: {self.failure_streak}.\n"
                "Rental alerts or notifications may be incomplete until the next successful cycle. "
                "Check worker logs and mailbox/account status."
            )
            if self._notify(body):
                self.alerted = True
                self.last_alert_at = now

    def recovered(self) -> None:
        if self.failure_streak and self.alerted:
            self._notify(
                f"Rental bot recovered after {self.failure_streak} failed cycle(s). "
                "Alert processing resumed; review the lookback window for missed emails."
            )
        self.failure_streak = 0
        self.alerted = False
        self.last_alert_at = None

    def _notify(self, body: str) -> bool:
        if not self.settings.telegram_bot_token or not self.settings.telegram_chat_id:
            return False
        try:
            send_telegram(
                bot_token=self.settings.telegram_bot_token,
                chat_id=self.settings.telegram_chat_id,
                body=body,
            )
            return True
        except Exception:
            print("Worker health alert delivery failed; check Telegram configuration.", flush=True)
            return False

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .models import MatchCriteria


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    mail_provider: str
    mail_host: str
    mail_port: int
    mail_username: str
    mail_password: str
    mailbox: str
    mail_tls: bool
    email_source_name: str
    email_search_since_days: int
    email_link_pattern: str | None
    criteria: MatchCriteria
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    digest_from: str | None
    digest_to: str | None
    digest_hour_utc: int
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    worker_interval_seconds: int
    detail_review_enabled: bool
    detail_pages_per_cycle: int
    detail_allowed_domains: tuple[str, ...]
    detail_provider_authorized_domains: tuple[str, ...]
    detail_timeout_seconds: float
    detail_max_bytes: int
    detail_max_redirects: int
    detail_user_agent: str
    geocoding_enabled: bool
    geocoding_user_agent: str
    commute_destination: str | None
    commute_provider: str
    commute_destinations: tuple[tuple[str, str], ...]
    commute_modes: tuple[str, ...]
    google_maps_api_key: str | None
    google_transit_departure_time: str | None
    google_transit_timezone: str
    ai_review_enabled: bool
    openai_api_key: str | None
    openai_model: str | None
    ai_review_hour_utc: int
    ai_max_listings_per_day: int

    @classmethod
    def from_env(cls, *, require_email: bool = True) -> "Settings":
        load_dotenv()
        mail_provider = os.getenv("MAIL_PROVIDER", "gmail").strip().lower()
        if mail_provider != "gmail":
            raise ValueError(f"Unsupported mail provider: {mail_provider}. Expected gmail.")
        return cls(
            database_url=_required("DATABASE_URL"),
            mail_provider=mail_provider,
            mail_host=os.getenv("MAIL_HOST", "imap.gmail.com"),
            mail_port=int(os.getenv("MAIL_PORT", "993")),
            mail_username=_required_or_empty("MAIL_USERNAME", require_email),
            mail_password=_required_or_empty("MAIL_PASSWORD", require_email),
            mailbox=os.getenv("MAILBOX", "INBOX"),
            mail_tls=os.getenv("MAIL_TLS", "true").lower() in {"1", "true", "yes"},
            email_source_name=os.getenv("EMAIL_SOURCE_NAME", "rental-alert-email"),
            email_search_since_days=int(os.getenv("EMAIL_SEARCH_SINCE_DAYS", "2")),
            email_link_pattern=os.getenv("EMAIL_LINK_PATTERN") or None,
            criteria=MatchCriteria(
                max_monthly_rent=_optional_int("MAX_MONTHLY_RENT"),
                min_bedrooms=_optional_int("MIN_BEDROOMS"),
                areas=tuple(item.strip() for item in os.getenv("AREAS", "").split(",") if item.strip()),
                max_commute_minutes=_optional_int("MAX_COMMUTE_MINUTES"),
            ),
            smtp_host=os.getenv("SMTP_HOST") or None,
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME") or None,
            smtp_password=os.getenv("SMTP_PASSWORD") or None,
            digest_from=os.getenv("DIGEST_FROM") or None,
            digest_to=os.getenv("DIGEST_TO") or None,
            digest_hour_utc=int(os.getenv("DIGEST_HOUR_UTC", "8")),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            worker_interval_seconds=int(os.getenv("WORKER_INTERVAL_SECONDS", "1800")),
            detail_review_enabled=os.getenv("DETAIL_REVIEW_ENABLED", "false").lower() in {"1", "true", "yes"},
            detail_pages_per_cycle=int(os.getenv("DETAIL_PAGES_PER_CYCLE", "0")),
            detail_allowed_domains=_csv("DETAIL_ALLOWED_DOMAINS"),
            detail_provider_authorized_domains=_csv("DETAIL_PROVIDER_AUTHORIZED_DOMAINS"),
            detail_timeout_seconds=float(os.getenv("DETAIL_TIMEOUT_SECONDS", "15")),
            detail_max_bytes=int(os.getenv("DETAIL_MAX_BYTES", "2000000")),
            detail_max_redirects=int(os.getenv("DETAIL_MAX_REDIRECTS", "3")),
            detail_user_agent=os.getenv("DETAIL_USER_AGENT", "london-rental-intelligence-bot-detail-viewer/0.1"),
            geocoding_enabled=os.getenv("GEOCODING_ENABLED", "false").lower() in {"1", "true", "yes"},
            geocoding_user_agent=os.getenv("GEOCODING_USER_AGENT", "london-rental-intelligence-bot"),
            commute_destination=os.getenv("COMMUTE_DESTINATION") or None,
            commute_provider=os.getenv("COMMUTE_PROVIDER", "tfl").strip().lower(),
            commute_destinations=_destinations(),
            commute_modes=tuple(
                mode.strip().lower()
                for mode in os.getenv("COMMUTE_MODES", "walking,transit").split(",")
                if mode.strip()
            ),
            google_maps_api_key=os.getenv("GOOGLE_MAPS_API_KEY") or None,
            google_transit_departure_time=os.getenv("GOOGLE_TRANSIT_DEPARTURE_TIME") or None,
            google_transit_timezone=os.getenv("GOOGLE_TRANSIT_TIMEZONE", "Europe/London"),
            ai_review_enabled=os.getenv("AI_REVIEW_ENABLED", "false").lower() in {"1", "true", "yes"},
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL") or None,
            ai_review_hour_utc=int(os.getenv("AI_REVIEW_HOUR_UTC", "7")),
            ai_max_listings_per_day=int(os.getenv("AI_MAX_LISTINGS_PER_DAY", "5")),
        )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _required_or_empty(name: str, required: bool) -> str:
    return _required(name) if required else os.getenv(name, "")


def _optional_int(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value else None


def _csv(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


def _destinations() -> tuple[tuple[str, str], ...]:
    destinations: list[tuple[str, str]] = []
    for index in range(1, 5):
        address = os.getenv(f"COMMUTE_DESTINATION_{index}", "").strip()
        if address:
            name = os.getenv(f"COMMUTE_DESTINATION_{index}_NAME", f"Destination {index}").strip()
            destinations.append((name or f"Destination {index}", address))
    if destinations:
        return tuple(destinations)
    legacy = os.getenv("COMMUTE_DESTINATION", "").strip()
    return (("Office", legacy),) if legacy else ()

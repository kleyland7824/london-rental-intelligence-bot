from __future__ import annotations

import smtplib
from email.message import EmailMessage

import httpx

from .assessment import assess_listing
from .models import MatchCriteria, StoredListing


def render_digest(listings: list[StoredListing], criteria: MatchCriteria | None = None) -> str:
    if not listings:
        return "London rental digest\n\nNo new matching listings today."
    lines = [f"London rental digest — {len(listings)} new matching listing(s)", ""]
    for index, listing in enumerate(listings, start=1):
        rent = f"£{listing.monthly_rent:,}/month" if listing.monthly_rent else "rent unavailable"
        beds = f"{listing.bedrooms} bed" if listing.bedrooms is not None else "bedrooms unavailable"
        location = listing.address or listing.postcode or "London location unavailable"
        commute = f" · {listing.commute_minutes} min commute" if listing.commute_minutes else ""
        lines.extend([f"{index}. {listing.title}", f"   {rent} · {beds} · {location}{commute}"])
        if criteria is not None:
            assessment = assess_listing(listing, criteria)
            if assessment.fit_score is not None:
                lines.append(f"   Fit: {assessment.fit_score}/100 (configured criteria; not verified)")
            if assessment.missing_fields:
                lines.append(f"   Unknown: {', '.join(assessment.missing_fields)}")
        commute_lines = _commute_lines(listing.metadata.get("commute"))
        lines.extend(f"   {line}" for line in commute_lines)
        lines.extend([f"   {listing.url}", ""])
    return "\n".join(lines).rstrip()


def render_telegram_digest(listings: list[StoredListing], criteria: MatchCriteria) -> str:
    lines = [f"🏠 {len(listings)} rental match{'es' if len(listings) != 1 else ''} · shortest transit first", ""]
    for listing in listings:
        rent = f"£{listing.monthly_rent:,}/mo" if listing.monthly_rent is not None else "Rent unknown"
        beds = f"{listing.bedrooms} bed" if listing.bedrooms is not None else "Beds unknown"
        location = (listing.postcode or listing.address or "Location unknown")[:55]
        fit = assess_listing(listing, criteria).fit_score
        lines.append(f"• {listing.title[:80]}")
        lines.append(f"  {rent} · {beds} · {location}" + (f" · fit {fit}/100" if fit is not None else ""))
        lines.append(f"  {_telegram_transit(listing)}")
        received = listing.metadata.get("email_received_at") if isinstance(listing.metadata, dict) else None
        lines.append(f"  {'Alert received' if received else 'Imported'}: {str(received or listing.first_seen_at.date())[:10]}")
        availability = listing.available_from or (listing.metadata.get("availability_note") if isinstance(listing.metadata, dict) else None)
        if availability:
            lines.append(f"  Available: {availability}")
        lines.append(f"  {listing.url}")
        lines.append("")
    lines.append("Based on your filters; listing details are not independently verified.")
    return "\n".join(lines)


def _telegram_transit(listing: StoredListing) -> str:
    commute = listing.metadata.get("commute") if isinstance(listing.metadata, dict) else None
    destinations = commute.get("destinations") if isinstance(commute, dict) else None
    parts = []
    if isinstance(destinations, dict):
        for name, routes in destinations.items():
            transit = routes.get("transit") if isinstance(routes, dict) else None
            minutes = transit.get("minutes") if isinstance(transit, dict) else None
            if isinstance(minutes, int):
                parts.append(f"{str(name)[:25]} {minutes}m")
    if parts:
        provider = "Google Maps" if commute.get("provider") == "google-routes" else "calculated"
        return f"Transit ({provider}): {' · '.join(parts)}"
    return f"Transit: {listing.commute_minutes}m" if listing.commute_minutes is not None else "Transit: unknown"


def _commute_lines(commute: object) -> list[str]:
    if not isinstance(commute, dict) or commute.get("provider") != "google-routes":
        return []
    destinations = commute.get("destinations")
    if not isinstance(destinations, dict):
        return []
    lines: list[str] = ["Commute routes:"]
    for name, routes in destinations.items():
        if not isinstance(routes, dict):
            continue
        lines.append(f"  {name}:")
        for mode in ("walking", "transit"):
            route = routes.get(mode)
            if isinstance(route, dict) and route.get("status") == "ok":
                minutes = route.get("minutes")
                link = route.get("maps_url")
                if isinstance(minutes, int):
                    lines.append(
                        f"    {mode.title()}: {minutes} min"
                        + (f" — {link}" if isinstance(link, str) else "")
                    )
            if mode == "transit" and isinstance(route, dict):
                for index, step in enumerate(route.get("transit_steps", []), start=1):
                    summary = _transit_step_summary(step)
                    if summary:
                        lines.append(f"    {index}. {summary}")
    return lines


def _transit_step_summary(step: object) -> str:
    if not isinstance(step, dict):
        return ""
    line = step.get("line") or step.get("line_name") or "Transit"
    vehicle = step.get("vehicle")
    headsign = step.get("headsign")
    origin = step.get("from")
    destination = step.get("to")
    stops = step.get("stops")
    departure = _time_only(step.get("departure_time"))
    arrival = _time_only(step.get("arrival_time"))
    details = [str(line)]
    if vehicle:
        details.append(str(vehicle).replace("_", " ").title())
    if headsign:
        details.append(f"towards {headsign}")
    if origin and destination:
        details.append(f"{origin} → {destination}")
    if departure or arrival:
        details.append("–".join(filter(None, (departure, arrival))))
    if stops is not None:
        details.append(f"{stops} stops")
    return " · ".join(details)


def _time_only(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.split("T", 1)[1][:5] if "T" in value else value[:5]


def send_email(*, smtp_host: str, smtp_port: int, username: str | None, password: str | None, sender: str, recipient: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = "London rental digest"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)


def send_telegram(*, bot_token: str, chat_id: str, body: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    with httpx.Client(timeout=20) as client:
        for start in range(0, len(body), 4000):
            response = client.post(url, json={"chat_id": chat_id, "text": body[start : start + 4000]})
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError("Telegram rejected the notification")

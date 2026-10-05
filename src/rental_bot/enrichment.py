from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import time
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx


@dataclass(frozen=True, slots=True)
class Coordinates:
    latitude: float
    longitude: float


class GoogleRoutesClient:
    """Small, server-side client for Google Routes API walking/transit routes."""

    def __init__(self, api_key: str, departure_time: str | None = None, timezone_name: str = "Europe/London"):
        self.departure_time = departure_time
        self.timezone_name = timezone_name
        self.client = httpx.Client(
            base_url="https://routes.googleapis.com",
            headers={"X-Goog-Api-Key": api_key},
            timeout=20,
        )

    def routes(
        self,
        *,
        origin: str,
        destination: str,
        modes: tuple[str, ...],
        destination_name: str,
    ) -> dict[str, object]:
        results: dict[str, object] = {}
        for mode in modes:
            if mode not in {"walking", "transit"}:
                raise ValueError(f"Unsupported Google commute mode: {mode}")
            results[mode] = self._route(origin, destination, mode, destination_name)
        return results

    def _route(self, origin: str, destination: str, mode: str, destination_name: str) -> dict[str, object]:
        travel_mode = "WALK" if mode == "walking" else "TRANSIT"
        payload: dict[str, object] = {
            "origin": {"address": origin},
            "destination": {"address": destination},
            "travelMode": travel_mode,
            "computeAlternativeRoutes": True,
        }
        if mode == "transit" and self.departure_time:
            payload["departureTime"] = _departure_timestamp(self.departure_time, self.timezone_name)
            payload["transitPreferences"] = {"routingPreference": "FEWER_TRANSFERS"}
        response = self.client.post(
            "/directions/v2:computeRoutes",
            json=payload,
            headers={
                "X-Goog-FieldMask": (
                    "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,"
                    "routes.legs.steps.travelMode,routes.legs.steps.transitDetails"
                )
            },
        )
        response.raise_for_status()
        routes = response.json().get("routes", [])
        if not routes:
            return {"status": "no_route", "destination": destination_name}
        route = routes[0]
        return {
            "status": "ok",
            "destination": destination_name,
            "minutes": _duration_minutes(route.get("duration")),
            "distance_meters": route.get("distanceMeters"),
            "polyline": route.get("polyline", {}).get("encodedPolyline"),
            "transit_steps": _transit_steps(route) if mode == "transit" else [],
            "maps_url": _maps_url(origin, destination, mode),
        }

    def close(self) -> None:
        self.client.close()


class GoogleGeocoder:
    """Server-side geocoder using the configured Google Maps Platform key."""

    def __init__(self, api_key: str):
        self.client = httpx.Client(base_url="https://maps.googleapis.com/maps/api", timeout=20)
        self.api_key = api_key

    def geocode(self, query: str) -> Coordinates | None:
        response = self.client.get("/geocode/json", params={"address": query, "key": self.api_key})
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "OK":
            return None
        results = payload.get("results", [])
        if not results:
            return None
        location = results[0].get("geometry", {}).get("location", {})
        if location.get("lat") is None or location.get("lng") is None:
            return None
        return Coordinates(latitude=float(location["lat"]), longitude=float(location["lng"]))

    def close(self) -> None:
        self.client.close()


class NominatimGeocoder:
    def __init__(self, user_agent: str):
        self._last_request = 0.0
        self.client = httpx.Client(
            base_url="https://nominatim.openstreetmap.org",
            headers={"User-Agent": user_agent},
            timeout=15,
        )

    def geocode(self, query: str) -> Coordinates | None:
        wait = 1.0 - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        response = self.client.get("/search", params={"q": query, "format": "json", "limit": 1})
        response.raise_for_status()
        results = response.json()
        if not results:
            return None
        return Coordinates(latitude=float(results[0]["lat"]), longitude=float(results[0]["lon"]))

    def close(self) -> None:
        self.client.close()


class PostcodesIoGeocoder:
    """Geocodes full UK postcodes or outward codes using postcodes.io."""

    def __init__(self, user_agent: str):
        self.client = httpx.Client(
            base_url="https://api.postcodes.io",
            headers={"User-Agent": user_agent},
            timeout=15,
        )

    def geocode(self, query: str) -> Coordinates | None:
        full_postcode = re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", query, re.IGNORECASE)
        if full_postcode:
            endpoint = f"/postcodes/{quote(full_postcode.group(1))}"
        else:
            outward_code = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\b", query, re.IGNORECASE)
            if not outward_code:
                return None
            endpoint = f"/outcodes/{quote(outward_code.group(1).upper())}"
        response = self.client.get(endpoint)
        response.raise_for_status()
        result = response.json().get("result")
        if not isinstance(result, dict) or result.get("latitude") is None or result.get("longitude") is None:
            return None
        return Coordinates(latitude=float(result["latitude"]), longitude=float(result["longitude"]))

    def close(self) -> None:
        self.client.close()


class TfLJourneyPlanner:
    """Optional commute provider using the permitted TfL Journey Planner API."""

    def __init__(self, destination: str):
        self.destination = destination
        self.client = httpx.Client(base_url="https://api.tfl.gov.uk", timeout=20)

    def minutes(self, origin: str) -> int | None:
        response = self.client.get(
            f"/Journey/JourneyResults/{quote(origin)}/to/{quote(self.destination)}",
            params={"nationalSearch": "false"},
        )
        response.raise_for_status()
        journeys = response.json().get("journeys", [])
        durations = [journey.get("duration") for journey in journeys if journey.get("duration") is not None]
        return min(durations) if durations else None

    def close(self) -> None:
        self.client.close()


def _duration_minutes(value: object) -> int | None:
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        return round(float(value[:-1]) / 60)
    except ValueError:
        return None


def _departure_timestamp(value: str, timezone_name: str) -> str:
    """Accept RFC3339 or a daily HH:MM local time, choosing the next weekday."""
    if "T" in value:
        return value
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("GOOGLE_TRANSIT_DEPARTURE_TIME must be HH:MM or RFC3339") from None
    now = datetime.now(zone)
    departure = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if departure <= now:
        departure += timedelta(days=1)
    while departure.weekday() >= 5:
        departure += timedelta(days=1)
    return departure.isoformat()


def _transit_steps(route: dict[str, object]) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    for leg in route.get("legs", []) if isinstance(route.get("legs"), list) else []:
        if not isinstance(leg, dict):
            continue
        for step in leg.get("steps", []) if isinstance(leg.get("steps"), list) else []:
            if not isinstance(step, dict) or "transitDetails" not in step:
                continue
            details = step["transitDetails"]
            if isinstance(details, dict):
                stop_details = details.get("stopDetails") if isinstance(details.get("stopDetails"), dict) else {}
                departure_stop = stop_details.get("departureStop") if isinstance(stop_details.get("departureStop"), dict) else {}
                arrival_stop = stop_details.get("arrivalStop") if isinstance(stop_details.get("arrivalStop"), dict) else {}
                line = details.get("transitLine") if isinstance(details.get("transitLine"), dict) else {}
                vehicle = line.get("vehicle") if isinstance(line.get("vehicle"), dict) else {}
                steps.append({
                    "line": line.get("nameShort") or line.get("name"),
                    "line_name": line.get("name"),
                    "vehicle": vehicle.get("type") or vehicle.get("name"),
                    "headsign": details.get("headsign"),
                    "from": departure_stop.get("name"),
                    "to": arrival_stop.get("name"),
                    "departure_time": details.get("departureTime"),
                    "arrival_time": details.get("arrivalTime"),
                    "stops": details.get("stopCount"),
                })
    return steps


def _maps_url(origin: str, destination: str, mode: str) -> str:
    from urllib.parse import quote_plus

    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={quote_plus(origin)}&destination={quote_plus(destination)}&travelmode={mode}"
    )

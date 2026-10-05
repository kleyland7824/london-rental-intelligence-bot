from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .assessment import assess_listing
from .dashboard import criteria_from_filters, filter_reasons, image_urls, safe_http_url, stored_listing
from .db import Database


app = FastAPI(title="London Rental Intelligence API", docs_url=None, redoc_url=None)
DIST = Path(os.getenv("FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
ReviewStatus = Literal["unreviewed", "left", "favourite", "rejected"]
ReviewFilter = Literal["unreviewed", "left", "favourite", "rejected", "reviewed"]


class ReviewUpdate(BaseModel):
    status: ReviewStatus


class AssessmentResponse(BaseModel):
    matches_filters: bool
    fit_score: int | None
    score_reasons: list[str]
    evidence_sources: dict[str, str]
    missing_fields: list[str]
    known_fields: int
    exclusion_reasons: list[str]


class RouteResponse(BaseModel):
    destination: str
    mode: str
    minutes: int | None
    url: str | None


class AIReviewResponse(BaseModel):
    summary: str
    recommendation: Literal["shortlist", "maybe", "not_suitable"]
    strengths: list[str]
    concerns: list[str]
    unknowns: list[str]
    model: str
    reviewed_at: datetime


class ListingResponse(BaseModel):
    id: int
    source: str
    url: str | None
    title: str
    address: str | None
    postcode: str | None
    description: str | None
    monthly_rent: int | None
    bedrooms: int | None
    bathrooms: int | None
    available_from: date | None
    availability_note: str | None
    first_seen_at: datetime
    email_received_at: datetime | None
    latitude: float | None
    longitude: float | None
    commute_minutes: int | None
    review_status: ReviewStatus
    reviewed_at: datetime | None
    image_urls: list[str]
    routes: list[RouteResponse]
    commute_provider: str | None
    page_reviewed_at: str | None
    ai_review: AIReviewResponse | None
    assessment: AssessmentResponse


class ListingPageResponse(BaseModel):
    items: list[ListingResponse]
    total: int
    offset: int
    next_offset: int | None


class SummaryResponse(BaseModel):
    imported: int
    matched: int
    awaiting_review: int
    favourites: int


class ReviewResponse(BaseModel):
    id: int
    review_status: ReviewStatus
    reviewed_at: datetime


@app.middleware("http")
async def private_api_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def get_database() -> Database:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return Database(database_url)


def query_filters(
    days: int = Query(14, ge=1, le=365),
    max_rent: int | None = Query(None, ge=0),
    min_bedrooms: int | None = Query(None, ge=0),
    areas: str | None = Query(None, max_length=250),
    max_commute: int | None = Query(None, ge=0),
    status: ReviewFilter | None = None,
) -> dict[str, object]:
    def configured_int(name: str) -> int | None:
        value = os.getenv(name)
        return int(value) if value else None

    def criterion(value: int | None, name: str) -> int | None:
        configured = configured_int(name) if value is None else value
        return configured or None

    selected_areas = os.getenv("AREAS", "") if areas is None else areas
    parsed_areas = tuple(area.strip() for area in selected_areas.split(",") if area.strip())
    if len(parsed_areas) > 10:
        raise HTTPException(status_code=422, detail="Too many areas")
    return {
        "since": datetime.now(timezone.utc) - timedelta(days=days),
        "max_rent": criterion(max_rent, "MAX_MONTHLY_RENT"),
        "min_bedrooms": criterion(min_bedrooms, "MIN_BEDROOMS"),
        "areas": parsed_areas,
        "max_commute": criterion(max_commute, "MAX_COMMUTE_MINUTES"),
        "status": status,
        "only_unprocessed": False,
    }


def listing_dto(row: dict, filters: dict[str, object]) -> dict[str, object]:
    assessment = assess_listing(stored_listing(row), criteria_from_filters(filters))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    commute = metadata.get("commute") if isinstance(metadata.get("commute"), dict) else {}
    destinations = commute.get("destinations") if isinstance(commute.get("destinations"), dict) else {}
    routes: list[dict[str, object]] = []
    for name, modes in destinations.items():
        if not isinstance(modes, dict):
            continue
        for mode in ("walking", "transit"):
            route = modes.get(mode)
            if isinstance(route, dict) and route.get("status") == "ok":
                routes.append({
                    "destination": str(name), "mode": mode,
                    "minutes": route.get("minutes") if isinstance(route.get("minutes"), int) else None,
                    "url": safe_http_url(route.get("maps_url")),
                })
    page = metadata.get("page_detail") if isinstance(metadata.get("page_detail"), dict) else {}
    ai_review = metadata.get("ai_review") if isinstance(metadata.get("ai_review"), dict) else None
    received_at = metadata.get("email_received_at")
    if isinstance(received_at, str):
        try:
            received_at = datetime.fromisoformat(received_at)
        except ValueError:
            received_at = None
    else:
        received_at = None
    return {
        "id": row["id"], "source": row["source"], "url": safe_http_url(row["url"]),
        "title": row["title"], "address": row.get("address"), "postcode": row.get("postcode"),
        "description": row.get("description"), "monthly_rent": row.get("monthly_rent"),
        "bedrooms": row.get("bedrooms"), "bathrooms": row.get("bathrooms"),
        "available_from": row.get("available_from"),
        "availability_note": "Available now" if page.get("facts", {}).get("availability_note") == "Available now" or metadata.get("availability_note") == "Available now" else None,
        "first_seen_at": row["first_seen_at"],
        "email_received_at": received_at,
        "latitude": row.get("latitude"), "longitude": row.get("longitude"),
        "commute_minutes": row.get("commute_minutes"), "review_status": row.get("review_status", "unreviewed"),
        "reviewed_at": row.get("reviewed_at"), "image_urls": image_urls(row),
        "routes": routes,
        "commute_provider": commute.get("provider") if commute.get("provider") in {"google-routes", "tfl"} else None,
        "page_reviewed_at": page.get("retrieved_at"),
        "ai_review": {key: ai_review[key] for key in ("summary", "recommendation", "strengths", "concerns", "unknowns", "model", "reviewed_at")}
        if ai_review and all(key in ai_review for key in ("summary", "recommendation", "strengths", "concerns", "unknowns", "model", "reviewed_at")) else None,
        "assessment": {
            "matches_filters": assessment.matches_filters,
            "fit_score": assessment.fit_score,
            "score_reasons": assessment.score_reasons,
            "evidence_sources": assessment.evidence_sources,
            "missing_fields": assessment.missing_fields,
            "known_fields": assessment.known_fields,
            "exclusion_reasons": filter_reasons(row, filters),
        },
    }


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/listings", response_model=ListingPageResponse)
def listings(
    filters: dict[str, object] = Depends(query_filters),
    database: Database = Depends(get_database),
    matching_only: bool = False,
    commute_at_most: int | None = Query(None, ge=1),
    sort: Literal["newest", "commute"] = "newest",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    rows, total = database.api_listings(
        filters["since"], limit=limit, offset=offset, status=filters["status"],
        max_rent=filters["max_rent"], min_bedrooms=filters["min_bedrooms"],
        areas=filters["areas"], max_commute=filters["max_commute"],
        matching_only=matching_only, commute_at_most=commute_at_most, sort=sort,
    )
    return {
        "items": [listing_dto(row, filters) for row in rows],
        "total": total, "offset": offset,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
    }


@app.get("/api/v1/summary", response_model=SummaryResponse)
def summary(
    filters: dict[str, object] = Depends(query_filters),
    database: Database = Depends(get_database),
) -> dict[str, int]:
    rows = database.api_summary_rows(filters["since"])
    criteria = criteria_from_filters(filters)
    matched = [row for row in rows if assess_listing(stored_listing(row), criteria).matches_filters]
    return {
        "imported": len(rows), "matched": len(matched),
        "awaiting_review": sum(row["review_status"] == "unreviewed" for row in matched),
        "favourites": sum(row["review_status"] == "favourite" for row in rows),
    }


@app.get("/api/v1/listings/{listing_id}", response_model=ListingResponse)
def listing_detail(
    listing_id: int, filters: dict[str, object] = Depends(query_filters),
    database: Database = Depends(get_database),
) -> dict[str, object]:
    row = database.api_listing(listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing_dto(row, filters)


@app.patch("/api/v1/listings/{listing_id}/review", response_model=ReviewResponse)
def update_review(
    listing_id: int, update: ReviewUpdate, database: Database = Depends(get_database),
) -> dict[str, object]:
    row = database.api_set_review_status(listing_id, update.status)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return row


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str) -> FileResponse:
    if not DIST.is_dir() or path.startswith("api/"):
        raise HTTPException(status_code=404)
    target = (DIST / path).resolve()
    if target.is_file() and target.is_relative_to(DIST.resolve()):
        return FileResponse(target)
    if "." in Path(path).name:
        raise HTTPException(status_code=404)
    return FileResponse(DIST / "index.html")

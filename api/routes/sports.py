"""Read-only sports endpoints. Every handler reads saved artifacts through research.sports.service.

No handler fetches upstream data, captures forecasts, settles markets, writes reports or starts the
collector.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from api.schemas import (
    SportsCollectorResponse,
    SportsCoverageResponse,
    SportsEventDetailResponse,
    SportsEventListResponse,
    SportsHistoricalResponse,
    SportsResultsResponse,
    SportsSummaryResponse,
)
from research.sports import service

router = APIRouter(prefix="/sports", tags=["sports"])

EventStatus = Literal["all", "captured", "upcoming", "awaiting_settlement", "settled", "missed"]


@router.get("/summary", response_model=SportsSummaryResponse)
def sports_summary() -> dict:
    return service.get_summary()


@router.get("/events", response_model=SportsEventListResponse)
def sports_events(
    sport: str | None = None,
    competition: str | None = None,
    family: str | None = None,
    cohort: str | None = None,
    status: EventStatus | None = None,
    collection: Literal["automatic", "collector_manual", "manual_pilot"] | None = None,
    q: str | None = Query(None, max_length=120),
    sort: Literal["default", "start_asc", "start_desc", "capture_desc", "settlement_desc"] = "default",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=200),
) -> dict:
    filters = {"sport": sport, "competition": competition, "family": family, "cohort": cohort,
               "status": status, "collection": collection, "q": q}
    return service.list_events(filters, page, page_size, sort)


@router.get("/events/{event_id:path}", response_model=SportsEventDetailResponse)
def sports_event(event_id: str) -> dict:
    detail = service.get_event(event_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No saved capture for event {event_id}")
    return detail


@router.get("/results", response_model=SportsResultsResponse)
def sports_results(
    sport: str | None = None,
    competition: str | None = None,
    family: str | None = None,
    cohort: str | None = None,
    weighting: Literal["event", "contract"] = "event",
) -> dict:
    filters = {"sport": sport, "competition": competition, "family": family, "cohort": cohort}
    return service.get_results(filters, weighting)


@router.get("/coverage", response_model=SportsCoverageResponse)
def sports_coverage(
    sport: str | None = None,
    competition: str | None = None,
    family: str | None = None,
    status: Literal["supported", "unsupported", "unattempted"] | None = None,
    q: str | None = Query(None, max_length=120),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict:
    filters = {"sport": sport, "competition": competition, "family": family, "status": status, "q": q}
    return service.get_coverage(filters, page, page_size)


@router.get("/historical", response_model=SportsHistoricalResponse)
def sports_historical(
    protocol: str | None = None,
    level: str | None = None,
    key: str | None = None,
    family: str | None = None,
    cohort: str | None = None,
    comparison: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
) -> dict:
    filters = {"protocol": protocol, "level": level, "key": key, "family": family, "cohort": cohort,
               "comparison": comparison}
    return service.get_historical(filters, page, page_size)


@router.get("/collector", response_model=SportsCollectorResponse)
def sports_collector() -> dict:
    """Collector state from saved status files and a passive scheduler query; never starts a cycle."""
    return service.get_collector_status()

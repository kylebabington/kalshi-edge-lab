"""Market Explorer API — GETs read cache only; POST /refresh starts an explicit crawl."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from api.schemas import (
    ExploreEventDetailResponse,
    ExploreEventListResponse,
    ExploreRefreshRequest,
    ExploreRefreshResponse,
    ExploreSummaryResponse,
)
from research.explore import discovery, index

router = APIRouter(prefix="/explore", tags=["explore"])


@router.get("/summary", response_model=ExploreSummaryResponse)
def explore_summary() -> dict:
    return index.summary()


@router.get("/events", response_model=ExploreEventListResponse)
def explore_events(
    category: str | None = None,
    q: str | None = None,
    contract_status: Literal["open", "closed", "determined", "finalized", "other"] | None = None,
    lifecycle: Literal["open", "closed", "final", "unknown"] | None = None,
    listed_open: Literal["latest", "any"] | None = "latest",
    closes_after: str | None = None,
    closes_before: str | None = None,
    quote: Literal["two_sided", "one_sided", "none"] | None = None,
    min_volume_24h: float | None = None,
    coverage: Literal["model", "none"] | None = None,
    sort: Literal["close_asc", "close_desc", "volume_desc", "title", "retrieved_desc"] = "close_asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    return index.list_events(
        category=category,
        q=q,
        contract_status=contract_status,
        lifecycle=lifecycle,
        listed_open=listed_open,
        closes_after=closes_after,
        closes_before=closes_before,
        quote=quote,
        min_volume_24h=min_volume_24h,
        coverage=coverage,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("/events/{event_ticker}", response_model=ExploreEventDetailResponse)
def explore_event(event_ticker: str) -> dict:
    found = index.get_event(event_ticker)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Event {event_ticker} is not in the explorer cache")
    return found


@router.post("/refresh", response_model=ExploreRefreshResponse)
def explore_refresh(body: ExploreRefreshRequest | None = None) -> JSONResponse:
    watched = list((body.watched_event_tickers if body else None) or [])
    result = discovery.start_refresh(watched_event_tickers=watched)
    status_code = 409 if result.get("conflict") else 202
    return JSONResponse(content=result, status_code=status_code)


@router.get("/refresh", response_model=ExploreRefreshResponse)
def explore_refresh_status() -> dict:
    status = discovery.get_refresh_status()
    return {"accepted": False, "conflict": False, "status": status}

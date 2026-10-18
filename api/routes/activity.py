"""Activity Center API — GET-only, network-free, write-free."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from api.schemas import ActivityIdsResponse, ActivityListResponse, ActivitySummaryResponse
from research.activity import index as activity_index

router = APIRouter(prefix="/activity", tags=["activity"])

CategoryFilter = Literal["weather", "sports", "kalshi", "system"]
TypeFilter = Literal[
    "forecast_accepted",
    "settlement_confirmed",
    "collector_failure",
    "collector_recovery",
    "collector_skipped",
    "backup_failure",
    "backup_skipped",
    "backup_ok",
    "explore_discovered",
    "explore_status_change",
    "explore_quote_change",
]


def _watched_list(watched: str | None) -> list[str] | None:
    if not watched:
        return None
    return [p.strip() for p in watched.split(",") if p.strip()]


@router.get("/summary", response_model=ActivitySummaryResponse)
def activity_summary() -> dict:
    return activity_index.summary()


@router.get("", response_model=ActivityListResponse)
def activity_list(
    category: CategoryFilter | None = None,
    type: TypeFilter | None = None,
    q: str | None = None,
    watched: str | None = Query(
        None,
        description="Comma-separated watchlist ids (category:key) for subject filtering",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=200),
) -> dict:
    return activity_index.list_activity(
        category=category,
        type=type,
        q=q,
        watched=_watched_list(watched),
        page=page,
        page_size=page_size,
    )


@router.get("/ids", response_model=ActivityIdsResponse)
def activity_ids(
    category: CategoryFilter | None = None,
    type: TypeFilter | None = None,
    q: str | None = None,
    watched: str | None = None,
) -> dict:
    """Full id snapshot for the filtered feed — used by browser mark-all-read."""
    from datetime import datetime, timezone

    ids = activity_index.all_ids(
        category=category,
        type=type,
        q=q,
        watched=_watched_list(watched),
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ids": ids,
        "total": len(ids),
    }

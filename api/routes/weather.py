from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from api import weather_index
from api.schemas import (
    LiveWeatherResponse,
    ModelSummaryResponse,
    WeatherCollectorResponse,
    WeatherEventResponse,
    WeatherMetricsContextResponse,
    WeatherSavedEventDetailResponse,
    WeatherSavedEventListResponse,
)
from research.weather.service import (
    get_live_weather_events,
    get_model_summary,
    get_snapshot,
    get_snapshot_scores,
    get_weather_event_detail,
    list_snapshots_for_event,
    serialize_event_bundle,
)

router = APIRouter(prefix="/weather", tags=["weather"])


def _serialize_live(payload: dict) -> dict:
    events = []
    for event in payload.get("events") or []:
        events.append(serialize_event_bundle(event))
    out = dict(payload)
    out["events"] = events
    return out


@router.get("/live", response_model=LiveWeatherResponse)
def weather_live() -> dict:
    """Live open KXHIGHNY predictions. May fetch current markets / GFS / GEFS / obs."""
    payload = get_live_weather_events()
    return _serialize_live(payload)


@router.get("/events/{event_ticker}", response_model=WeatherEventResponse)
def weather_event(event_ticker: str) -> dict:
    detail = get_weather_event_detail(event_ticker)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Event not found or not open: {event_ticker}")
    return serialize_event_bundle(detail)


@router.get("/events/{event_ticker}/snapshots")
def weather_event_snapshots(event_ticker: str) -> dict:
    """Read-only list of immutable snapshot metadata. Does not create snapshots."""
    return list_snapshots_for_event(event_ticker)


@router.get("/snapshots/{snapshot_id}")
def weather_snapshot(snapshot_id: str) -> dict:
    """Read one historical PredictionSnapshot. Does not create or score."""
    payload = get_snapshot(snapshot_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Snapshot not found: {snapshot_id}")
    return payload


@router.get("/snapshot-scores")
def weather_snapshot_scores() -> dict:
    """Read existing score artifacts if present. Does not score."""
    return get_snapshot_scores()


@router.get("/saved-events", response_model=WeatherSavedEventListResponse)
def weather_saved_events(
    status: Literal["all", "upcoming", "awaiting_settlement", "settled"] | None = None,
    q: str | None = Query(None, max_length=120),
    sort: Literal["default", "target_asc", "target_desc", "capture_desc", "settlement_desc"] = "default",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=200),
) -> dict:
    """Saved forecasts grouped by event, from local snapshot and score files. Never fetches or writes."""
    return weather_index.saved_events({"status": status, "q": q}, sort, page, page_size)


@router.get("/saved-events/{event_ticker}", response_model=WeatherSavedEventDetailResponse)
def weather_saved_event(event_ticker: str) -> dict:
    detail = weather_index.saved_event(event_ticker)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No saved forecast for event {event_ticker}")
    return detail


@router.get("/collector", response_model=WeatherCollectorResponse)
def weather_collector() -> dict:
    """Prospective task health from its log and a passive scheduler query; never starts a cycle."""
    return weather_index.collector()


@router.get("/metrics-context", response_model=WeatherMetricsContextResponse)
def weather_metrics_context() -> dict:
    """Sample sizes and data periods for weather metrics, from saved artifacts only."""
    return weather_index.metrics_context()


@router.get("/model/summary", response_model=ModelSummaryResponse)
def weather_model_summary() -> dict:
    """Read existing calibration/backtest artifacts. Never rebuilds them."""
    return get_model_summary()

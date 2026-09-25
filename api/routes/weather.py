from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas import LiveWeatherResponse, ModelSummaryResponse, WeatherEventResponse
from research.weather.service import (
    get_live_weather_events,
    get_model_summary,
    get_weather_event_detail,
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


@router.get("/model/summary", response_model=ModelSummaryResponse)
def weather_model_summary() -> dict:
    """Read existing calibration/backtest artifacts. Never rebuilds them."""
    return get_model_summary()

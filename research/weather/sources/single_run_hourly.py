"""Hourly series for already-selected GFS / HRRR Single Runs (Phase 6).

Fetches the full hourly temperature_2m series of one exact run from the
Open-Meteo Single Runs API in UTC (timezone=GMT) so America/New_York
windows and DST are computed locally with zoneinfo. Raw responses are cached
per (model, run_init). Run selection and publication latency are NOT decided
here — callers pass the run Phase 5 already selected.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from kalshi import cache
from research.weather.asof_observations import local_day_bounds_utc

SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
HOURLY_CACHE_DIR = cache.CACHE_ROOT / "weather" / "single_runs_hourly"
NYC_LATITUDE = 40.77
NYC_LONGITUDE = -73.97
REQUEST_TIMEOUT_S = 30
REQUEST_PAUSE_S = 0.15

WINDOW_STATUS_OK = "OK"
WINDOW_STATUS_UNAVAILABLE = "UNAVAILABLE"
WINDOW_REASON_SERIES_UNAVAILABLE = "run_hourly_series_unavailable"
WINDOW_REASON_NO_VALUES = "no_series_values_in_remaining_window"
WINDOW_REASON_HORIZON_SHORT = "run_horizon_ends_before_end_of_target_date"
WINDOW_REASON_STARTS_LATE = "run_series_starts_after_window_start"
WINDOW_REASON_MISSING_HOURS = "missing_or_null_hours_in_remaining_window"
WINDOW_REASON_EMPTY = "remaining_window_empty"


def hourly_cache_path(model: str, run_init: datetime) -> Path:
    return HOURLY_CACHE_DIR / model / f"{run_init.astimezone(timezone.utc):%Y%m%dT%HZ}.json"


def _as_utc(run_init: datetime) -> datetime:
    if run_init.tzinfo is None:
        return run_init.replace(tzinfo=timezone.utc)
    return run_init.astimezone(timezone.utc)


def fetch_run_hourly(
    model: str,
    run_init: datetime,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    """Return {"status", "payload", "meta"}; successful responses are cached raw."""
    run_init = _as_utc(run_init)
    path = hourly_cache_path(model, run_init)
    if not refresh and path.exists():
        cached = cache.read_json(path, default=None)
        if isinstance(cached, dict) and isinstance(cached.get("response"), dict):
            meta = dict(cached.get("meta") or {})
            meta["from_cache"] = True
            return {"status": "ok", "payload": cached["response"], "meta": meta}

    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": "GMT",
        "models": model,
        "run": run_init.strftime("%Y-%m-%dT%H:%M"),
    }
    meta: dict[str, Any] = {
        "source": f"open-meteo:single-runs:{model}",
        "url": SINGLE_RUNS_URL,
        "params": params,
        "model": model,
        "run_init_utc": run_init.isoformat(),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "from_cache": False,
    }
    try:
        response = requests.get(SINGLE_RUNS_URL, params=params, timeout=REQUEST_TIMEOUT_S)
    except requests.RequestException as error:
        meta["error"] = f"{type(error).__name__}: {error}"
        return {"status": "error", "payload": None, "meta": meta}
    time.sleep(REQUEST_PAUSE_S)
    meta["http_status"] = response.status_code
    if not response.ok:
        meta["error"] = response.text[:300]
        return {"status": "error", "payload": None, "meta": meta}
    try:
        payload = response.json()
    except ValueError as error:
        meta["error"] = f"invalid_json: {error}"
        return {"status": "error", "payload": None, "meta": meta}
    path.parent.mkdir(parents=True, exist_ok=True)
    cache.write_json(path, {"meta": meta, "response": payload}, compact=True)
    return {"status": "ok", "payload": payload, "meta": meta}


def parse_hourly_series(payload: dict[str, Any] | None) -> list[tuple[datetime, float | None]]:
    """Parse a timezone=GMT Single Runs payload into (valid_utc, temp_f) pairs."""
    if not isinstance(payload, dict):
        return []
    if int(payload.get("utc_offset_seconds") or 0) != 0:
        raise ValueError("hourly payload must be requested in UTC (timezone=GMT)")
    hourly = payload.get("hourly") or {}
    out: list[tuple[datetime, float | None]] = []
    for stamp, temp in zip(hourly.get("time") or [], hourly.get("temperature_2m") or []):
        try:
            valid = datetime.fromisoformat(str(stamp)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        value: float | None
        try:
            value = None if temp is None else float(temp)
        except (TypeError, ValueError):
            value = None
        out.append((valid, value))
    out.sort(key=lambda item: item[0])
    return out


def _ceil_hour(dt: datetime) -> datetime:
    floored = dt.replace(minute=0, second=0, microsecond=0)
    return floored if floored == dt else floored + timedelta(hours=1)


def remaining_day_high(
    series: list[tuple[datetime, float | None]] | None,
    *,
    target_date: str,
    checkpoint_as_of: datetime,
) -> dict[str, Any]:
    """Max forecast temperature at valid times in [checkpoint, end of NY target date).

    Every hourly valid time in the window must be present with a value;
    otherwise the window is UNAVAILABLE with a reason (never a partial max).
    """
    as_of = checkpoint_as_of.astimezone(timezone.utc)
    day_start, day_end = local_day_bounds_utc(target_date)
    window_start = _ceil_hour(max(as_of, day_start))
    expected: list[datetime] = []
    cursor = window_start
    while cursor < day_end:
        expected.append(cursor)
        cursor += timedelta(hours=1)

    base: dict[str, Any] = {
        "status": WINDOW_STATUS_UNAVAILABLE,
        "reason": None,
        "model_remaining_day_high_f": None,
        "window_start_utc": window_start.isoformat(),
        "window_end_utc_exclusive": day_end.isoformat(),
        "expected_hours": len(expected),
        "covered_hours": 0,
    }
    if not expected:
        base["reason"] = WINDOW_REASON_EMPTY
        return base
    if not series:
        base["reason"] = WINDOW_REASON_SERIES_UNAVAILABLE
        return base

    values = {t: v for t, v in series}
    covered = [values[t] for t in expected if values.get(t) is not None]
    base["covered_hours"] = len(covered)
    if not covered:
        base["reason"] = WINDOW_REASON_NO_VALUES
        return base
    if len(covered) < len(expected):
        first_valid = min(t for t, v in series if v is not None)
        last_valid = max(t for t, v in series if v is not None)
        if last_valid < expected[-1]:
            base["reason"] = WINDOW_REASON_HORIZON_SHORT
        elif first_valid > expected[0]:
            base["reason"] = WINDOW_REASON_STARTS_LATE
        else:
            base["reason"] = WINDOW_REASON_MISSING_HOURS
        return base

    base["status"] = WINDOW_STATUS_OK
    base["model_remaining_day_high_f"] = max(covered)
    return base

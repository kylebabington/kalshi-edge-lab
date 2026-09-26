"""HRRR deterministic forecast stream via Open-Meteo (ncep_hrrr_conus).

Availability gate (mandatory):
    run_init + HRRR_PUBLICATION_LATENCY <= prediction_as_of

Residuals must never pool with GFS residuals.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from kalshi import cache
from research.weather.evidence import (
    FRESHNESS_UNAVAILABLE,
    QUALITY_MISSING,
    QUALITY_OK,
    QUALITY_PARTIAL,
    QUALITY_UNAVAILABLE,
    ForecastSourceSnapshot,
    apply_freshness,
)
from research.weather.models import (
    HRRR_MODEL,
    HRRR_OPERATIONAL_SELECTION_POLICY,
    HRRR_PUBLICATION_LATENCY,
    HRRR_STREAM_EXACT,
)
from weather import NYC_LATITUDE, NYC_LONGITUDE

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"

HRRR_CACHE_DIR = cache.CACHE_ROOT / "weather" / "hrrr"
HRRR_RUN_CACHE = HRRR_CACHE_DIR / "hrrr_run_cache.json"

# Extended cycles reach 48h; side cycles 18h. Prefer recent hours for live trend.
LIVE_RUN_LOOKBACK_HOURS = 6


def _ensure_cache() -> None:
    HRRR_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def hrrr_available_as_of(run_init: datetime) -> datetime:
    return run_init + HRRR_PUBLICATION_LATENCY


def is_hrrr_run_available(*, run_init: datetime, as_of: datetime) -> bool:
    """No-lookahead gate for HRRR."""
    if run_init.tzinfo is None:
        run_init = run_init.replace(tzinfo=timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    # Reject future runs relative to as_of (init after as_of).
    if run_init > as_of:
        return False
    return hrrr_available_as_of(run_init) <= as_of


def load_hrrr_cache() -> dict[str, Any]:
    _ensure_cache()
    data = cache.read_json(HRRR_RUN_CACHE, default={})
    return data if isinstance(data, dict) else {}


def save_hrrr_cache(payload: dict[str, Any]) -> None:
    _ensure_cache()
    cache.write_json(HRRR_RUN_CACHE, payload)


def _max_high_for_date(times: list[str], temps: list[Any], target_date: str) -> float | None:
    values: list[float] = []
    for stamp, temp in zip(times, temps):
        if temp is None:
            continue
        if not str(stamp).startswith(target_date):
            continue
        try:
            values.append(float(temp))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


def _hourly_series_for_date(
    times: list[str], temps: list[Any], target_date: str
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for stamp, temp in zip(times, temps):
        if temp is None or not str(stamp).startswith(target_date):
            continue
        try:
            series.append({"timestamp": stamp, "temperature_f": float(temp)})
        except (TypeError, ValueError):
            continue
    return series


def get_hrrr_run_high(
    target_date: str,
    run_init: datetime,
    *,
    as_of: datetime | None = None,
    allow_unavailable: bool = False,
) -> float | None:
    """Exact HRRR run high for target NY calendar date via Single Runs API.

    Returns None if the publication latency gate fails (unless allow_unavailable).
    """
    as_of = as_of or _now_utc()
    if run_init.tzinfo is None:
        run_init = run_init.replace(tzinfo=timezone.utc)
    if not allow_unavailable and not is_hrrr_run_available(run_init=run_init, as_of=as_of):
        return None

    run_key = run_init.strftime("%Y-%m-%dT%H:%M")
    cache_key = f"{HRRR_MODEL}|{target_date}|{run_key}"
    disk = load_hrrr_cache()
    if cache_key in disk and disk[cache_key] is not None:
        try:
            return float(disk[cache_key])
        except (TypeError, ValueError):
            pass

    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": "America/New_York",
        "models": HRRR_MODEL,
        "run": run_key,
    }
    try:
        response = requests.get(SINGLE_RUNS_URL, params=params, timeout=25)
        if not response.ok:
            return None
        data = response.json()
    except requests.RequestException:
        return None

    hourly = data.get("hourly") or {}
    high = _max_high_for_date(
        list(hourly.get("time") or []),
        list(hourly.get("temperature_2m") or []),
        target_date,
    )
    if high is not None:
        disk[cache_key] = high
        save_hrrr_cache(disk)
    return high


def get_hrrr_previous_day_high(target_date: str) -> float | None:
    """~24h-lead HRRR high via Previous Runs API (temperature_2m_previous_day1)."""
    cache_key = f"{HRRR_MODEL}|prev_day1|{target_date}"
    disk = load_hrrr_cache()
    if cache_key in disk and disk[cache_key] is not None:
        try:
            return float(disk[cache_key])
        except (TypeError, ValueError):
            pass

    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,
        "start_date": target_date,
        "end_date": target_date,
        "hourly": "temperature_2m_previous_day1",
        "temperature_unit": "fahrenheit",
        "timezone": "America/New_York",
        "models": HRRR_MODEL,
    }
    try:
        response = requests.get(PREVIOUS_RUNS_URL, params=params, timeout=25)
        if not response.ok:
            return None
        data = response.json()
    except requests.RequestException:
        return None

    hourly = data.get("hourly") or {}
    temps = [
        float(t)
        for t in (hourly.get("temperature_2m_previous_day1") or [])
        if t is not None
    ]
    if not temps:
        return None
    high = max(temps)
    disk[cache_key] = high
    save_hrrr_cache(disk)
    return high


def _candidate_run_inits(as_of: datetime, lookback_hours: int = LIVE_RUN_LOOKBACK_HOURS) -> list[datetime]:
    """Recent whole-hour inits that pass the publication gate."""
    # Floor as_of to hour, then walk back.
    cursor = as_of.replace(minute=0, second=0, microsecond=0)
    out: list[datetime] = []
    for hours_ago in range(0, lookback_hours + 12):
        init = cursor - timedelta(hours=hours_ago)
        if is_hrrr_run_available(run_init=init, as_of=as_of):
            out.append(init)
        if len(out) >= lookback_hours:
            break
    return out


def collect_hrrr_live(
    target_date: str,
    *,
    as_of: datetime | None = None,
    recent_runs: int = 4,
) -> ForecastSourceSnapshot:
    """Live HRRR evidence with recent-run trend. Does not enter WeatherPrediction."""
    as_of = as_of or _now_utc()
    retrieved_at = as_of.isoformat()
    warnings: list[str] = []

    # Prefer Forecast API with models=ncep_hrrr_conus for current run + previous offsets.
    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,
        "hourly": "temperature_2m",
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": "America/New_York",
        "models": HRRR_MODEL,
        "forecast_days": 3,
    }
    live_payload: dict[str, Any] | None = None
    try:
        response = requests.get(FORECAST_URL, params=params, timeout=20)
        if response.ok:
            live_payload = response.json()
            _ensure_cache()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = HRRR_CACHE_DIR / f"live_{stamp}.json"
            cache.write_json(path, live_payload)
        else:
            warnings.append(f"HRRR live forecast HTTP {response.status_code}")
    except requests.RequestException as error:
        warnings.append(f"HRRR live forecast failed: {error}")

    hourly_series: list[dict[str, Any]] = []
    forecast_high: float | None = None
    model_run_at: str | None = None
    raw_cache_path: str | None = None

    if live_payload:
        hourly = live_payload.get("hourly") or {}
        hourly_series = _hourly_series_for_date(
            list(hourly.get("time") or []),
            list(hourly.get("temperature_2m") or []),
            target_date,
        )
        if hourly_series:
            forecast_high = max(row["temperature_f"] for row in hourly_series)
        daily = live_payload.get("daily") or {}
        dates = list(daily.get("time") or [])
        highs = list(daily.get("temperature_2m_max") or [])
        for date_str, high in zip(dates, highs):
            if date_str == target_date and high is not None:
                try:
                    forecast_high = float(high)
                except (TypeError, ValueError):
                    pass
                break
        # Open-Meteo may expose generation time
        gen = live_payload.get("generationtime_ms")
        raw_cache_path = str(HRRR_CACHE_DIR / "latest_live.json")
        cache.write_json(Path(raw_cache_path), live_payload)
        _ = gen

    # Operational policy: latest available exact run by latency gate (not skill-tuned).
    # Fixed-cycle 00/06/12/18Z benchmarks are historical-only (see phase4.py).
    run_rows: list[dict[str, Any]] = []
    for init in _candidate_run_inits(as_of)[: max(recent_runs, 1)]:
        high = get_hrrr_run_high(target_date, init, as_of=as_of)
        run_rows.append(
            {
                "model": HRRR_MODEL,
                "forecast_stream": HRRR_STREAM_EXACT,
                "run_init": init.isoformat(),
                "available_at": hrrr_available_as_of(init).isoformat(),
                "forecast_high_f": high,
                "lead_hours": (
                    (
                        datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        - init
                    ).total_seconds()
                    / 3600.0
                ),
            }
        )

    usable = [r for r in run_rows if r.get("forecast_high_f") is not None]
    # Prefer the newest available exact run as the operational HRRR high.
    if usable:
        model_run_at = usable[0]["run_init"]
        forecast_high = float(usable[0]["forecast_high_f"])
    # Live Forecast API high retained only when exact-run archive is sparse.

    prior_high = float(usable[1]["forecast_high_f"]) if len(usable) > 1 else None
    trend = None
    if forecast_high is not None and prior_high is not None:
        trend = float(forecast_high) - prior_high
    spread = None
    if len(usable) >= 2:
        vals = [float(r["forecast_high_f"]) for r in usable]
        spread = max(vals) - min(vals)

    if model_run_at is None and live_payload is not None:
        # Approximate: latest available init under gate.
        candidates = _candidate_run_inits(as_of, lookback_hours=1)
        if candidates:
            model_run_at = candidates[0].isoformat()

    available_at = None
    if model_run_at:
        init_dt = datetime.fromisoformat(model_run_at.replace("Z", "+00:00"))
        available_at = hrrr_available_as_of(init_dt).isoformat()

    freshness_seconds, freshness_status = apply_freshness(
        model_run_at or available_at, as_of=as_of
    )

    quality = QUALITY_OK
    if forecast_high is None:
        quality = QUALITY_MISSING if not warnings else QUALITY_UNAVAILABLE
        freshness_status = FRESHNESS_UNAVAILABLE
    elif not usable:
        quality = QUALITY_PARTIAL
        warnings.append(
            "HRRR exact recent-run archive sparse; live forecast used without full trend"
        )

    lead = None
    if model_run_at:
        init_dt = datetime.fromisoformat(model_run_at.replace("Z", "+00:00"))
        lead = (
            datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) - init_dt
        ).total_seconds() / 3600.0

    return ForecastSourceSnapshot(
        source_id="hrrr",
        source_name="HRRR (ncep_hrrr_conus)",
        model=HRRR_MODEL,
        target_date=target_date,
        model_run_at=model_run_at,
        available_at=available_at,
        retrieved_at=retrieved_at,
        forecast_high_f=forecast_high,
        hourly_high_f=max((r["temperature_f"] for r in hourly_series), default=None),
        hourly_series=hourly_series,
        prior_forecast_high_f=prior_high,
        run_to_run_trend_f=trend,
        recent_run_spread_f=spread,
        recent_runs=run_rows,
        lead_hours=lead,
        coordinates={"latitude": NYC_LATITUDE, "longitude": NYC_LONGITUDE},
        grid_metadata={"model": HRRR_MODEL, "api": "open-meteo"},
        freshness_seconds=freshness_seconds,
        freshness_status=freshness_status,
        quality_status=quality,
        warnings=warnings,
        metadata={
            "publication_latency_hours": HRRR_PUBLICATION_LATENCY.total_seconds() / 3600.0,
            "operational_selection_policy": HRRR_OPERATIONAL_SELECTION_POLICY,
            "forecast_stream": HRRR_STREAM_EXACT,
            "not_blended_into_prediction": True,
            "phase5_candidate_nbm": True,
        },
        raw_cache_path=raw_cache_path,
    )


def parse_hrrr_hourly_payload(
    payload: dict[str, Any],
    target_date: str,
    *,
    run_init: datetime | None = None,
    as_of: datetime | None = None,
) -> ForecastSourceSnapshot:
    """Test helper: parse an Open-Meteo-like hourly payload into a snapshot."""
    as_of = as_of or _now_utc()
    hourly = payload.get("hourly") or {}
    series = _hourly_series_for_date(
        list(hourly.get("time") or []),
        list(hourly.get("temperature_2m") or []),
        target_date,
    )
    high = max((r["temperature_f"] for r in series), default=None)
    if run_init and not is_hrrr_run_available(run_init=run_init, as_of=as_of):
        return ForecastSourceSnapshot(
            source_id="hrrr",
            source_name="HRRR (ncep_hrrr_conus)",
            model=HRRR_MODEL,
            target_date=target_date,
            model_run_at=run_init.isoformat(),
            quality_status=QUALITY_UNAVAILABLE,
            freshness_status=FRESHNESS_UNAVAILABLE,
            warnings=["HRRR run rejected by publication latency / future-run gate"],
        )
    return ForecastSourceSnapshot(
        source_id="hrrr",
        source_name="HRRR (ncep_hrrr_conus)",
        model=HRRR_MODEL,
        target_date=target_date,
        model_run_at=run_init.isoformat() if run_init else None,
        forecast_high_f=high,
        hourly_series=series,
        quality_status=QUALITY_OK if high is not None else QUALITY_MISSING,
    )

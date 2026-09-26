"""NWS point / hourly / grid forecast collector (live evidence only).

Resolves WFO/grid via /points — never hardcodes OKX permanently.
Missing optional grid variables do not fail the bundle.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
from weather import NYC_LATITUDE, NYC_LONGITUDE

NWS_API_BASE = "https://api.weather.gov"
NWS_HEADERS = {
    "User-Agent": "KalshiEdgeLab/0.5 (personal research project)",
    "Accept": "application/geo+json",
}
NYC_TZ = ZoneInfo("America/New_York")
NWS_CACHE_DIR = cache.CACHE_ROOT / "weather" / "nws"
POINT_CACHE_PATH = NWS_CACHE_DIR / "point_grid_mapping.json"


def _ensure_cache() -> None:
    NWS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _c_to_f(celsius: float | None) -> float | None:
    if celsius is None:
        return None
    return (float(celsius) * 9.0 / 5.0) + 32.0


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _atomic_write(path: Path, payload: Any) -> None:
    cache.write_json(path, payload)


def load_point_mapping() -> dict[str, Any]:
    _ensure_cache()
    data = cache.read_json(POINT_CACHE_PATH, default={})
    return data if isinstance(data, dict) else {}


def resolve_nws_point(
    latitude: float = NYC_LATITUDE,
    longitude: float = NYC_LONGITUDE,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Resolve /points → forecast URLs + office/grid metadata. Cached."""
    _ensure_cache()
    key = f"{latitude:.4f},{longitude:.4f}"
    mapping = load_point_mapping()
    if not force_refresh and key in mapping and isinstance(mapping[key], dict):
        cached = dict(mapping[key])
        cached["from_cache"] = True
        return cached

    url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    response = requests.get(url, headers=NWS_HEADERS, timeout=15)
    response.raise_for_status()
    props = response.json().get("properties") or {}
    resolved = {
        "latitude": latitude,
        "longitude": longitude,
        "forecast": props.get("forecast"),
        "forecastHourly": props.get("forecastHourly"),
        "forecastGridData": props.get("forecastGridData"),
        "forecastOffice": props.get("forecastOffice"),
        "gridId": props.get("gridId"),
        "gridX": props.get("gridX"),
        "gridY": props.get("gridY"),
        "cwa": props.get("cwa"),
        "timeZone": props.get("timeZone"),
        "radarStation": props.get("radarStation"),
        "resolved_at": _now_iso(),
        "from_cache": False,
    }
    # Derive WFO from office URL or cwa/gridId — do not hardcode OKX.
    office_url = str(resolved.get("forecastOffice") or "")
    wfo = resolved.get("cwa") or resolved.get("gridId")
    if not wfo and "/offices/" in office_url:
        wfo = office_url.rstrip("/").split("/")[-1]
    resolved["wfo"] = wfo
    mapping[key] = {k: v for k, v in resolved.items() if k != "from_cache"}
    _atomic_write(POINT_CACHE_PATH, mapping)
    return resolved


def _cache_response(name: str, payload: Any) -> str:
    _ensure_cache()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = NWS_CACHE_DIR / f"{name}_{stamp}.json"
    _atomic_write(path, payload)
    return str(path)


def _fetch_json(url: str) -> dict[str, Any]:
    response = requests.get(url, headers=NWS_HEADERS, timeout=20)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def _period_covers_date(period: dict[str, Any], target_date: str) -> bool:
    start = _parse_iso(period.get("startTime"))
    end = _parse_iso(period.get("endTime"))
    if start is None:
        return False
    local_start = start.astimezone(NYC_TZ).date().isoformat()
    if local_start == target_date:
        return True
    if end is not None:
        local_end = end.astimezone(NYC_TZ).date().isoformat()
        if local_end == target_date and start.astimezone(NYC_TZ).date().isoformat() <= target_date:
            return True
    return False


def _daily_high_from_forecast(periods: list[dict[str, Any]], target_date: str) -> tuple[float | None, str | None]:
    """Prefer daytime high temperature for target calendar day."""
    candidates: list[tuple[float, str | None, bool]] = []
    for period in periods:
        if not _period_covers_date(period, target_date):
            continue
        temp = period.get("temperature")
        if temp is None:
            continue
        try:
            value = float(temp)
        except (TypeError, ValueError):
            continue
        unit = str(period.get("temperatureUnit") or "F").upper()
        if unit == "C":
            converted = _c_to_f(value)
            value = converted if converted is not None else value
        is_daytime = bool(period.get("isDaytime", True))
        candidates.append((value, period.get("startTime"), is_daytime))
    if not candidates:
        return None, None
    daytime = [c for c in candidates if c[2]]
    pool = daytime or candidates
    best = max(pool, key=lambda item: item[0])
    return best[0], best[1]


def _hourly_for_date(
    periods: list[dict[str, Any]], target_date: str
) -> tuple[list[dict[str, Any]], float | None]:
    series: list[dict[str, Any]] = []
    highs: list[float] = []
    for period in periods:
        start = _parse_iso(period.get("startTime"))
        if start is None:
            continue
        if start.astimezone(NYC_TZ).date().isoformat() != target_date:
            continue
        temp = period.get("temperature")
        if temp is None:
            continue
        try:
            value = float(temp)
        except (TypeError, ValueError):
            continue
        unit = str(period.get("temperatureUnit") or "F").upper()
        if unit == "C":
            converted = _c_to_f(value)
            value = converted if converted is not None else value
        series.append(
            {
                "timestamp": period.get("startTime"),
                "temperature_f": value,
                "windSpeed": period.get("windSpeed"),
                "shortForecast": period.get("shortForecast"),
            }
        )
        highs.append(value)
    return series, (max(highs) if highs else None)


def _grid_values_for_date(grid_props: dict[str, Any], target_date: str) -> dict[str, Any]:
    """Extract useful grid fields for target day; missing vars are omitted."""
    wanted = {
        "temperature": "temperature",
        "dewpoint": "dewpoint",
        "relativeHumidity": "relativeHumidity",
        "skyCover": "skyCover",
        "probabilityOfPrecipitation": "probabilityOfPrecipitation",
        "windSpeed": "windSpeed",
        "weather": "weather",
    }
    out: dict[str, Any] = {}
    for key, prop_name in wanted.items():
        block = grid_props.get(prop_name)
        if not isinstance(block, dict):
            continue
        values = block.get("values") or []
        day_vals: list[Any] = []
        for entry in values:
            valid = str(entry.get("validTime") or "")
            # validTime like 2026-09-25T14:00:00+00:00/PT1H
            stamp = valid.split("/")[0] if valid else ""
            parsed = _parse_iso(stamp)
            if parsed is None:
                continue
            if parsed.astimezone(NYC_TZ).date().isoformat() != target_date:
                continue
            day_vals.append(
                {
                    "valid_time": stamp,
                    "value": entry.get("value"),
                }
            )
        if day_vals:
            out[key] = day_vals
    # Derive max temperature from grid if present (usually Celsius).
    temps = out.get("temperature") or []
    if temps:
        numeric = []
        for row in temps:
            try:
                if row.get("value") is not None:
                    numeric.append(float(row["value"]))
            except (TypeError, ValueError):
                continue
        if numeric:
            # NWS grid temperature is Celsius.
            out["temperature_max_c"] = max(numeric)
            out["temperature_max_f"] = _c_to_f(max(numeric))
    return out


def parse_nws_forecast_payloads(
    *,
    target_date: str,
    point: dict[str, Any],
    forecast_payload: dict[str, Any] | None,
    hourly_payload: dict[str, Any] | None,
    grid_payload: dict[str, Any] | None,
    retrieved_at: str | None = None,
    as_of: datetime | None = None,
    cache_paths: dict[str, str] | None = None,
) -> ForecastSourceSnapshot:
    """Pure parser for tests — no network."""
    retrieved_at = retrieved_at or _now_iso()
    as_of = as_of or datetime.now(timezone.utc)
    warnings: list[str] = []
    cache_paths = cache_paths or {}

    forecast_periods = ((forecast_payload or {}).get("properties") or {}).get("periods") or []
    hourly_periods = ((hourly_payload or {}).get("properties") or {}).get("periods") or []
    grid_props = (grid_payload or {}).get("properties") or {}

    daily_high, daily_issued = _daily_high_from_forecast(forecast_periods, target_date)
    hourly_series, hourly_high = _hourly_for_date(hourly_periods, target_date)
    grid_day = _grid_values_for_date(grid_props, target_date) if grid_props else {}

    issued_candidates = [
        ((forecast_payload or {}).get("properties") or {}).get("updateTime"),
        ((hourly_payload or {}).get("properties") or {}).get("updateTime"),
        grid_props.get("updateTime"),
        daily_issued,
    ]
    issued_at = next((x for x in issued_candidates if x), None)
    freshness_seconds, freshness_status = apply_freshness(issued_at, as_of=as_of)

    quality = QUALITY_OK
    if daily_high is None and hourly_high is None:
        quality = QUALITY_MISSING
        warnings.append("NWS forecast high unavailable for target date")
    elif daily_high is None or hourly_high is None or not grid_day:
        quality = QUALITY_PARTIAL
        if not grid_day:
            warnings.append("NWS grid data partial or missing optional variables")

    return ForecastSourceSnapshot(
        source_id="nws_forecast",
        source_name="NWS Point/Hourly/Grid Forecast",
        model="nws_api",
        target_date=target_date,
        issued_at=issued_at,
        available_at=issued_at,
        retrieved_at=retrieved_at,
        forecast_high_f=daily_high,
        hourly_high_f=hourly_high,
        hourly_series=hourly_series,
        coordinates={
            "latitude": float(point.get("latitude") or NYC_LATITUDE),
            "longitude": float(point.get("longitude") or NYC_LONGITUDE),
        },
        grid_metadata={
            "wfo": point.get("wfo"),
            "gridId": point.get("gridId"),
            "gridX": point.get("gridX"),
            "gridY": point.get("gridY"),
            "cwa": point.get("cwa"),
            "forecastOffice": point.get("forecastOffice"),
            "grid_day_fields": list(grid_day.keys()),
            "grid_day": grid_day,
        },
        freshness_seconds=freshness_seconds,
        freshness_status=freshness_status,
        quality_status=quality,
        warnings=warnings,
        metadata={
            "live_only": True,
            "no_historical_backfill": True,
            "cache_paths": cache_paths,
        },
        raw_cache_path=cache_paths.get("forecast"),
    )


def collect_nws_forecast(
    target_date: str,
    *,
    latitude: float = NYC_LATITUDE,
    longitude: float = NYC_LONGITUDE,
    as_of: datetime | None = None,
) -> ForecastSourceSnapshot:
    """Live NWS forecast evidence. Failures become unavailable snapshots."""
    retrieved_at = _now_iso()
    as_of = as_of or datetime.now(timezone.utc)
    try:
        point = resolve_nws_point(latitude, longitude)
    except Exception as error:  # noqa: BLE001
        return ForecastSourceSnapshot(
            source_id="nws_forecast",
            source_name="NWS Point/Hourly/Grid Forecast",
            model="nws_api",
            target_date=target_date,
            retrieved_at=retrieved_at,
            freshness_status=FRESHNESS_UNAVAILABLE,
            quality_status=QUALITY_UNAVAILABLE,
            warnings=[f"NWS point resolution failed: {error}"],
        )

    cache_paths: dict[str, str] = {}
    forecast_payload = hourly_payload = grid_payload = None
    warnings: list[str] = []

    for label, url_key in (
        ("forecast", "forecast"),
        ("forecastHourly", "forecastHourly"),
        ("forecastGridData", "forecastGridData"),
    ):
        url = point.get(url_key)
        if not url:
            warnings.append(f"NWS {label} URL missing from point metadata")
            continue
        try:
            payload = _fetch_json(str(url))
            path = _cache_response(label, payload)
            cache_paths[label if label != "forecastHourly" else "hourly"] = path
            if label == "forecast":
                forecast_payload = payload
                cache_paths["forecast"] = path
            elif label == "forecastHourly":
                hourly_payload = payload
            else:
                grid_payload = payload
        except Exception as error:  # noqa: BLE001
            warnings.append(f"NWS {label} fetch failed: {error}")

    snap = parse_nws_forecast_payloads(
        target_date=target_date,
        point=point,
        forecast_payload=forecast_payload,
        hourly_payload=hourly_payload,
        grid_payload=grid_payload,
        retrieved_at=retrieved_at,
        as_of=as_of,
        cache_paths=cache_paths,
    )
    snap.warnings = list(snap.warnings) + warnings
    return snap

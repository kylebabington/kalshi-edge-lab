"""Model support vs saved-forecast availability — kept as separate fields."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

from kalshi.cache import REPO_ROOT

WEATHER_SERIES = "KXHIGHNY"
SNAPSHOT_ROOT = REPO_ROOT / "data" / "weather" / "snapshots"
SERIES_MATRIX = REPO_ROOT / "docs" / "research" / "sports_capture_v2_series_matrix.csv"


@lru_cache(maxsize=1)
def _supported_sports_series() -> frozenset[str]:
    if not SERIES_MATRIX.is_file():
        return frozenset()
    out: set[str] = set()
    try:
        with SERIES_MATRIX.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("live_status") or "").strip() == "supported":
                    t = (row.get("series_ticker") or "").strip()
                    if t:
                        out.add(t)
    except OSError:
        return frozenset()
    return frozenset(out)


def clear_coverage_caches() -> None:
    _supported_sports_series.cache_clear()


def weather_snapshot_exists(event_ticker: str) -> bool:
    return (SNAPSHOT_ROOT / event_ticker).is_dir()


def sports_index_ready() -> bool:
    """False while sports captures exist but have never been parsed (cold start)."""
    try:
        from research.sports import service as S
        from research.sports.live import config as LC
    except Exception:
        return False
    root = LC.CAPTURE_DIR / "accepted" / "checkpoint"
    first = next(root.glob("*/*.json"), None) if root.is_dir() else None
    return first is None or str(first) in S._records.files


def sports_ticker_map() -> dict[str, str]:
    """Kalshi event_ticker → sports event_id. Empty while the index is cold."""
    if not sports_index_ready():
        return {}
    try:
        from research.sports import service as S
    except Exception:
        return {}
    out: dict[str, str] = {}
    for rec in S.records():
        eid = rec.get("event_id")
        if not isinstance(eid, str) or not eid:
            continue
        for t in rec.get("kalshi_event_tickers") or []:
            if isinstance(t, str) and t and t not in out:
                out[t] = eid
    return out


def coverage_for(
    event_ticker: str,
    series_ticker: str | None,
    *,
    sports_ready: bool | None = None,
    sports_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return model_supported and saved_forecast as separate claims.

    Pass sports_ready/sports_map when covering many events so the sports index
    is not rebuilt once per ticker.
    """
    series = series_ticker or ""

    if series == WEATHER_SERIES or event_ticker.startswith(f"{WEATHER_SERIES}-"):
        saved = weather_snapshot_exists(event_ticker)
        return {
            "model_supported": "weather",
            "model_supported_basis": f"Series {WEATHER_SERIES} is the weather research series",
            "saved_forecast": "available" if saved else "unavailable",
            "saved_forecast_basis": (
                f"Snapshot directory present under data/weather/snapshots/{event_ticker}"
                if saved
                else f"No snapshot directory for {event_ticker}"
            ),
            "saved_forecast_href": f"/markets/{event_ticker}" if saved else None,
            "sports_event_id": None,
            "research_hrefs": [{"label": "Weather Phase 7", "href": "/research/weather.phase7_prospective_v1"}],
        }

    supported = series in _supported_sports_series()
    if not supported:
        return {
            "model_supported": "none",
            "model_supported_basis": "Series is not in the supported sports capture matrix and is not KXHIGHNY",
            "saved_forecast": "unavailable",
            "saved_forecast_basis": "No model is registered for this series",
            "saved_forecast_href": None,
            "sports_event_id": None,
            "research_hrefs": [],
        }

    # Supported sports series: never say "No model available".
    ready = sports_index_ready() if sports_ready is None else sports_ready
    if not ready:
        return {
            "model_supported": "sports",
            "model_supported_basis": f"Series {series} has live_status=supported in the capture matrix",
            "saved_forecast": "lookup_pending",
            "saved_forecast_basis": "Sports index is still loading; capture lookup is pending",
            "saved_forecast_href": None,
            "sports_event_id": None,
            "research_hrefs": [{"label": "Sports capture", "href": "/research/sports.phase2_capture_v2"}],
        }

    mapping = sports_map if sports_map is not None else sports_ticker_map()
    eid = mapping.get(event_ticker)
    if eid:
        return {
            "model_supported": "sports",
            "model_supported_basis": f"Series {series} has live_status=supported in the capture matrix",
            "saved_forecast": "available",
            "saved_forecast_basis": f"Matched sports event_id {eid} via kalshi_event_tickers",
            "saved_forecast_href": f"/sports/events/{eid}",
            "sports_event_id": eid,
            "research_hrefs": [{"label": "Sports capture", "href": "/research/sports.phase2_capture_v2"}],
        }
    return {
        "model_supported": "sports",
        "model_supported_basis": f"Series {series} has live_status=supported in the capture matrix",
        "saved_forecast": "unavailable",
        "saved_forecast_basis": "Supported series, but no captured sports event references this Kalshi ticker",
        "saved_forecast_href": None,
        "sports_event_id": None,
        "research_hrefs": [{"label": "Sports capture", "href": "/research/sports.phase2_capture_v2"}],
    }

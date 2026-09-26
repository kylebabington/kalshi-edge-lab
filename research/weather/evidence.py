"""Canonical external evidence types for multi-source weather research.

Timestamps are never conflated:
  issued_at / model_run_at  — when the source produced the information
  available_at              — when we consider it trader-available
  retrieved_at              — when we fetched it

Phase 4 does NOT blend sources into WeatherPrediction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from research.weather.models import (
    DISAGREEMENT_LOW_MAX_F,
    DISAGREEMENT_MODERATE_MAX_F,
    FRESHNESS_AGING_SECONDS,
    FRESHNESS_FRESH_SECONDS,
)


QUALITY_OK = "ok"
QUALITY_PARTIAL = "partial"
QUALITY_MISSING = "missing"
QUALITY_STALE = "stale"
QUALITY_UNAVAILABLE = "unavailable"

FRESHNESS_FRESH = "fresh"
FRESHNESS_AGING = "aging"
FRESHNESS_STALE = "stale"
FRESHNESS_UNAVAILABLE = "unavailable"

SOURCE_TYPE_MODEL = "model"
SOURCE_TYPE_ENSEMBLE = "ensemble"
SOURCE_TYPE_OFFICIAL_FORECAST = "official_forecast"
SOURCE_TYPE_DISCUSSION = "forecast_discussion"
SOURCE_TYPE_OBSERVATION = "observation"


@dataclass
class EvidenceItem:
    source_id: str
    source_name: str
    source_type: str

    target_date: str | None = None

    issued_at: str | None = None
    model_run_at: str | None = None
    available_at: str | None = None
    retrieved_at: str | None = None

    valid_from: str | None = None
    valid_to: str | None = None

    value: float | dict[str, Any] | list[Any] | str | None = None
    unit: str | None = None

    raw_cache_path: str | None = None
    source_identifier: str | None = None

    freshness_seconds: float | None = None
    freshness_status: str = FRESHNESS_UNAVAILABLE

    quality_status: str = QUALITY_OK
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ForecastSourceSnapshot:
    """Numeric forecast stream snapshot (GFS / HRRR / GEFS / NWS)."""

    source_id: str
    source_name: str
    model: str | None = None
    target_date: str | None = None

    model_run_at: str | None = None
    available_at: str | None = None
    retrieved_at: str | None = None
    issued_at: str | None = None

    forecast_high_f: float | None = None
    expected_high_f: float | None = None
    hourly_high_f: float | None = None
    hourly_series: list[dict[str, Any]] = field(default_factory=list)

    prior_forecast_high_f: float | None = None
    run_to_run_trend_f: float | None = None
    recent_run_spread_f: float | None = None
    recent_runs: list[dict[str, Any]] = field(default_factory=list)

    lead_hours: float | None = None
    coordinates: dict[str, float] | None = None
    grid_metadata: dict[str, Any] = field(default_factory=dict)

    residual_sample_size: int | None = None
    freshness_seconds: float | None = None
    freshness_status: str = FRESHNESS_UNAVAILABLE
    quality_status: str = QUALITY_OK
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_cache_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ObservationSnapshot:
    station_id: str = "KNYC"
    target_date: str | None = None
    retrieved_at: str | None = None

    latest_temperature_f: float | None = None
    latest_timestamp: str | None = None
    high_so_far_f: float | None = None
    time_of_high: str | None = None
    change_1h_f: float | None = None
    change_3h_f: float | None = None
    morning_warming_rate_f_per_hour: float | None = None

    trajectory: list[dict[str, Any]] = field(default_factory=list)

    freshness_seconds: float | None = None
    freshness_status: str = FRESHNESS_UNAVAILABLE
    quality_status: str = QUALITY_OK
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ForecastDiscussionSnapshot:
    wfo: str | None = None
    product_id: str | None = None
    issued_at: str | None = None
    retrieved_at: str | None = None
    raw_text: str | None = None
    raw_cache_path: str | None = None

    key_messages: list[str] = field(default_factory=list)
    confidence_wording: list[str] = field(default_factory=list)
    temperature_discussion: list[str] = field(default_factory=list)
    cloud_cover_discussion: list[str] = field(default_factory=list)
    precipitation_timing: list[str] = field(default_factory=list)
    frontal_timing: list[str] = field(default_factory=list)
    model_disagreement_mentions: list[str] = field(default_factory=list)
    model_references: list[str] = field(default_factory=list)
    forecast_change_reasons: list[str] = field(default_factory=list)

    freshness_seconds: float | None = None
    freshness_status: str = FRESHNESS_UNAVAILABLE
    quality_status: str = QUALITY_OK
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WeatherEvidenceBundle:
    """All independent external evidence for one event at one as-of time."""

    target_date: str
    as_of: str
    retrieved_at: str

    gfs: ForecastSourceSnapshot | None = None
    gefs: ForecastSourceSnapshot | None = None
    hrrr: ForecastSourceSnapshot | None = None
    nws_forecast: ForecastSourceSnapshot | None = None
    nws_discussion: ForecastDiscussionSnapshot | None = None
    observations: ObservationSnapshot | None = None

    agreement: dict[str, Any] = field(default_factory=dict)
    items: list[EvidenceItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_date": self.target_date,
            "as_of": self.as_of,
            "retrieved_at": self.retrieved_at,
            "gfs": self.gfs.to_dict() if self.gfs else None,
            "gefs": self.gefs.to_dict() if self.gefs else None,
            "hrrr": self.hrrr.to_dict() if self.hrrr else None,
            "nws_forecast": self.nws_forecast.to_dict() if self.nws_forecast else None,
            "nws_discussion": (
                self.nws_discussion.to_dict() if self.nws_discussion else None
            ),
            "observations": (
                self.observations.to_dict() if self.observations else None
            ),
            "agreement": self.agreement,
            "items": [i.to_dict() for i in self.items],
            "warnings": list(self.warnings),
        }


def compute_freshness_seconds(
    reference_at: str | datetime | None,
    *,
    as_of: datetime | None = None,
) -> float | None:
    if reference_at is None:
        return None
    as_of = as_of or datetime.now(timezone.utc)
    if isinstance(reference_at, str):
        try:
            reference_at = datetime.fromisoformat(reference_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    if reference_at.tzinfo is None:
        reference_at = reference_at.replace(tzinfo=timezone.utc)
    return max(0.0, (as_of - reference_at).total_seconds())


def freshness_status_from_seconds(seconds: float | None) -> str:
    if seconds is None:
        return FRESHNESS_UNAVAILABLE
    if seconds <= FRESHNESS_FRESH_SECONDS:
        return FRESHNESS_FRESH
    if seconds <= FRESHNESS_AGING_SECONDS:
        return FRESHNESS_AGING
    return FRESHNESS_STALE


def apply_freshness(
    issued_or_run_at: str | datetime | None,
    *,
    as_of: datetime | None = None,
) -> tuple[float | None, str]:
    seconds = compute_freshness_seconds(issued_or_run_at, as_of=as_of)
    return seconds, freshness_status_from_seconds(seconds)


def _parse_ts(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def evidence_available_at_gate(
    *,
    available_at: str | datetime | None = None,
    issued_at: str | datetime | None = None,
    prediction_as_of: str | datetime,
) -> bool:
    """No-lookahead gate on information availability — NOT forecast valid time.

    A forecast may legitimately have valid_to > prediction_as_of.
    What must hold is available_at (or issued_at) <= prediction_as_of.
    """
    as_of = _parse_ts(prediction_as_of)
    if as_of is None:
        return False
    gate = _parse_ts(available_at) or _parse_ts(issued_at)
    if gate is None:
        return False
    return gate <= as_of


def retrieved_at_gate(
    *,
    retrieved_at: str | datetime | None,
    snapshot_created_at: str | datetime,
) -> bool:
    """Live-capture gate: we cannot retrieve after the snapshot is created."""
    created = _parse_ts(snapshot_created_at)
    retrieved = _parse_ts(retrieved_at)
    if created is None or retrieved is None:
        return False
    return retrieved <= created


def disagreement_label(spread_f: float | None) -> str:
    if spread_f is None:
        return "UNKNOWN"
    if spread_f <= DISAGREEMENT_LOW_MAX_F:
        return "LOW DISAGREEMENT"
    if spread_f <= DISAGREEMENT_MODERATE_MAX_F:
        return "MODERATE DISAGREEMENT"
    return "HIGH DISAGREEMENT"


def compute_source_agreement(
    *,
    gfs_raw_high: float | None = None,
    gfs_expected_high: float | None = None,
    hrrr_raw_high: float | None = None,
    hrrr_expected_high: float | None = None,
    gefs_median: float | None = None,
    nws_forecast_high: float | None = None,
    high_so_far: float | None = None,
) -> dict[str, Any]:
    """Descriptive multi-source agreement. Does NOT blend probabilities."""
    named = {
        "gfs_raw": gfs_raw_high,
        "gfs_expected": gfs_expected_high,
        "hrrr_raw": hrrr_raw_high,
        "hrrr_expected": hrrr_expected_high,
        "gefs_median": gefs_median,
        "nws_forecast_high": nws_forecast_high,
        "high_so_far": high_so_far,
    }
    present = {k: float(v) for k, v in named.items() if v is not None}
    model_keys = [
        k
        for k in (
            "gfs_expected",
            "gfs_raw",
            "hrrr_expected",
            "hrrr_raw",
            "gefs_median",
            "nws_forecast_high",
        )
        if k in present
    ]
    model_vals = [present[k] for k in model_keys]
    spread = (max(model_vals) - min(model_vals)) if len(model_vals) >= 2 else None

    def _diff(a: str, b: str) -> float | None:
        if a in present and b in present:
            return present[a] - present[b]
        return None

    return {
        "sources": present,
        "model_high_range": (
            {"min": min(model_vals), "max": max(model_vals)} if model_vals else None
        ),
        "max_min_disagreement_f": spread,
        "disagreement_label": disagreement_label(spread),
        "gfs_hrrr_difference_f": _diff("gfs_raw", "hrrr_raw")
        if "gfs_raw" in present and "hrrr_raw" in present
        else _diff("gfs_expected", "hrrr_expected"),
        "gfs_nws_difference_f": _diff("gfs_raw", "nws_forecast_high")
        if "gfs_raw" in present
        else _diff("gfs_expected", "nws_forecast_high"),
        "hrrr_nws_difference_f": _diff("hrrr_raw", "nws_forecast_high")
        if "hrrr_raw" in present
        else _diff("hrrr_expected", "nws_forecast_high"),
        "policy": "descriptive_only_no_blend",
        "thresholds_f": {
            "low_max": DISAGREEMENT_LOW_MAX_F,
            "moderate_max": DISAGREEMENT_MODERATE_MAX_F,
        },
    }

"""Canonical weather prediction / trade-evaluation types and constants.

Residual convention (mandatory):
    residual_f = actual_high_f - forecast_high_f
    possible_actual = current_forecast + historical_residual

Do NOT reuse backtest.py signed_error (= forecast - actual) without
converting the sign.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any


# ---------------------------------------------------------------------------
# Methodology constants (NOT ROI-tuned)
# ---------------------------------------------------------------------------

GFS_PUBLICATION_LATENCY = timedelta(hours=6)

# Hierarchical residual pool — same GFS run only. Never mix runs.
MIN_N_MONTH = 30
MIN_N_SEASON = 50
MIN_N_RUN = 80

# Walk-forward burn-in: require this many prior same-run residuals
# before emitting a calibrated OOS prediction.
MIN_RUN_HISTORY = 20

# Unvalidated research threshold — Phase 1 decisions stay RESEARCH_ONLY.
MIN_REQUIRED_EDGE = 0.05

SERIES_TICKER = "KXHIGHNY"

# Standardized forecast snapshots (report each independently).
STANDARDIZED_RUNS: tuple[dict[str, Any], ...] = (
    {
        "run_id": "prev_12z",
        "label": "previous-day 12Z",
        "run_date_offset": -1,
        "run_hour": 12,
    },
    {
        "run_id": "prev_18z",
        "label": "previous-day 18Z",
        "run_date_offset": -1,
        "run_hour": 18,
    },
    {
        "run_id": "day_00z",
        "label": "event-day 00Z",
        "run_date_offset": 0,
        "run_hour": 0,
    },
    {
        "run_id": "day_06z",
        "label": "event-day 06Z",
        "run_date_offset": 0,
        "run_hour": 6,
    },
)

RESIDUAL_CONVENTION = "residual_f = actual_high_f - forecast_high_f"
BUCKET_BOUNDARY_ASSUMPTION = "discrete_integer_from_rules"

# Settlement regimes — never silently transfer residuals across these.
REGIME_NWS_CLI_KNYC = "nws_cli_knyc"
REGIME_WEATHER_COMPANY_CLINYC = "weather_company_clinyc"
REGIME_UNKNOWN = "unknown"
REGIME_CONFLICTING = "conflicting"

PREDICTION_STATUS_OK = "OK"
PREDICTION_STATUS_BURN_IN = "BURN_IN"
PREDICTION_STATUS_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
PREDICTION_STATUS_INSUFFICIENT_TARGET_REGIME_HISTORY = (
    "INSUFFICIENT_TARGET_REGIME_HISTORY"
)
PREDICTION_STATUS_MISSING_FORECAST = "MISSING_FORECAST"
PREDICTION_STATUS_REGIME_MISMATCH = "REGIME_MISMATCH"
PREDICTION_STATUS_UNKNOWN = "UNKNOWN"

CALIBRATION_METHOD_DIRECT_CLINYC = "direct_clinyc"
CALIBRATION_METHOD_NWS = "direct_nws_cli_knyc"
# Identity transfer: predicted CLINYC = predicted KNYC (no offset).
CALIBRATION_METHOD_TRANSFER = "knyc_to_clinyc_identity_transfer"
# Backward-compatible alias (pre-Phase-3 name).
CALIBRATION_METHOD_TRANSFER_LEGACY = "knyc_to_clinyc_transfer"

TARGET_REGIME_MATCH_DIRECT = "DIRECT_CLINYC_CALIBRATION"
TARGET_REGIME_MATCH_NWS = "DIRECT_NWS_CALIBRATION"
TARGET_REGIME_MATCH_VALIDATED_TRANSFER = "VALIDATED_TRANSFER"
TARGET_REGIME_MATCH_EXPERIMENTAL_TRANSFER = "EXPERIMENTAL_IDENTITY_TRANSFER"
TARGET_REGIME_MATCH_UNVALIDATED = "UNVALIDATED_SOURCE_MISMATCH"

TRANSFER_STATUS_EXPERIMENTAL = "experimental"
TRANSFER_STATUS_VALIDATED = "validated"
TRANSFER_STATUS_NONE = "none"

EVIDENCE_CLASS_DEVELOPMENT = "development"
EVIDENCE_CLASS_PROSPECTIVE = "prospective"

ACTUAL_SOURCE_IEM_NWS_CLI = "iem_nws_cli"
ACTUAL_SOURCE_KALSHI_EXPIRATION = "kalshi_expiration_value"

HYPOTHESIS_ID_CLINYC_TRANSFER_V1 = "CLINYC_TRANSFER_V1"

DECISION_RESEARCH_ONLY = "RESEARCH_ONLY"
DECISION_YES = "YES"
DECISION_NO = "NO"
DECISION_NO_BET = "NO_BET"


@dataclass(frozen=True)
class WeatherPrediction:
    """Independent weather prediction — NO Kalshi price fields."""

    event_ticker: str
    target_date: str
    as_of: str

    forecast_run: str
    run_id: str

    point_forecast_high: float | None
    residual_sample_size: int

    expected_high: float | None
    median_high: float | None

    p10_high: float | None
    p25_high: float | None
    p50_high: float | None
    p75_high: float | None
    p90_high: float | None

    bucket_probabilities: dict[str, float]

    confidence_score: float
    confidence_label: str

    model_agreement: str
    calibration_quality: str

    prediction_status: str

    calibration_target_regime: str
    live_target_regime: str
    settlement_source_transfer_validated: bool

    calibration_method: str | None = None
    transfer_status: str | None = None

    gefs_raw_probabilities: dict[str, float] | None = None
    gefs_mean: float | None = None
    gefs_median: float | None = None
    gefs_std: float | None = None
    gefs_min: float | None = None
    gefs_max: float | None = None
    gefs_member_count: int | None = None

    warnings: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WeatherTradeEvaluation:
    """Trading comparison stub — Phase 1 defaults to RESEARCH_ONLY."""

    prediction: WeatherPrediction
    p_yes: float | None = None
    p_no: float | None = None
    yes_ask: float | None = None
    no_ask: float | None = None
    yes_fee: float | None = None
    no_fee: float | None = None
    yes_break_even_probability: float | None = None
    no_break_even_probability: float | None = None
    yes_probability_edge: float | None = None
    no_probability_edge: float | None = None
    uncertainty_buffer: float | None = None
    decision: str = DECISION_RESEARCH_ONLY
    min_required_edge: float = MIN_REQUIRED_EDGE
    min_required_edge_label: str = "unvalidated research constant"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["prediction"] = self.prediction.to_dict()
        return payload

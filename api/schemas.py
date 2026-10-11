"""Pydantic response schemas — HTTP boundary only."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str
    mode: str


class ResearchStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: str | None = None
    decision: str | None = None
    trade_recommendations: str | None = None
    settlement_source_transfer_validated: bool | None = None
    transfer_status: str | None = None
    target_regime_match: str | None = None
    calibration_method: str | None = None


class WeatherResolutionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_ticker: str
    target_date: str
    settlement_source_regime: str
    resolution_source: str
    resolution_certainty: str
    station_id: str
    warnings: list[str] = Field(default_factory=list)


class WeatherPredictionResponse(BaseModel):
    """Weather-only prediction. Must not include Kalshi bid/ask fields."""

    model_config = ConfigDict(extra="allow")

    event_ticker: str
    target_date: str
    prediction_status: str
    expected_high: float | None = None
    median_high: float | None = None
    p10_high: float | None = None
    p25_high: float | None = None
    p50_high: float | None = None
    p75_high: float | None = None
    p90_high: float | None = None
    bucket_probabilities: dict[str, float] = Field(default_factory=dict)
    confidence_score: float | None = None
    confidence_label: str | None = None
    calibration_target_regime: str | None = None
    live_target_regime: str | None = None
    settlement_source_transfer_validated: bool | None = None
    calibration_method: str | None = None
    transfer_status: str | None = None
    residual_sample_size: int | None = None
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class KalshiMarketResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str | None = None
    label: str | None = None
    yes_bid_dollars: float | None = None
    yes_ask_dollars: float | None = None
    mid_dollars: float | None = None
    spread_dollars: float | None = None


class WeatherEventResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_ticker: str
    target_date: str | None = None
    resolution: dict[str, Any]
    prediction: dict[str, Any]
    evidence: dict[str, Any] | None = None
    kalshi_markets: list[dict[str, Any]] = Field(default_factory=list)
    research_status: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class LiveWeatherResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    mode: str
    calibration_csv_loaded: bool | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class SportsArtifacts(BaseModel):
    capture_records: str
    settlement_scores: str
    settlement_runs: str
    capture_runs: str
    raw_evidence: str


class SportsSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    label: str
    source: str
    mode: str
    protocol: str
    scoring_protocol: str
    artifacts: SportsArtifacts
    counts: dict[str, int]
    candidate_counts: dict[str, dict[str, int]]
    by_sport: dict[str, dict[str, int]] = Field(default_factory=dict)
    last_capture_at: str | None = None
    last_capture_run: dict[str, Any] | None = None
    last_settlement_run: dict[str, Any] | None = None
    integrity: dict[str, int]
    collector: dict[str, Any]


class SportsSettlementSummary(BaseModel):
    state: str
    contracts: int
    final: int
    labelled: int
    pending: int
    states: dict[str, int] = Field(default_factory=dict)


class SportsEventItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str
    competition: str
    competition_name: str | None = None
    sport: str | None = None
    source: str | None = None
    cluster: str
    title: str | None = None
    title_source: str | None = None
    participants: list[str] = Field(default_factory=list)
    kalshi_event_tickers: list[str] = Field(default_factory=list)
    event_start: str | None = None
    window_open: str | None = None
    scheduled_cutoff: str | None = None
    capture_status: str
    prediction_as_of: str | None = None
    finalized_at: str | None = None
    horizon_minutes: float | None = None
    missed_reason: str | None = None
    view: str
    settlement: SportsSettlementSummary
    families: list[str] = Field(default_factory=list)
    cohorts: list[str] = Field(default_factory=list)
    contracts_total: int
    contracts_ok: int
    candidate_available: dict[str, int]
    comparability: str | None = None
    integrity: dict[str, str]
    collection_mode: str | None = None


class SportsEventListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    label: str
    source: str
    artifacts: SportsArtifacts
    filters: dict[str, Any] = Field(default_factory=dict)
    total: int
    page: int
    page_size: int
    pages: int
    views: dict[str, int]
    facets: dict[str, list[str]]
    items: list[SportsEventItem]


class SportsEventDetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    label: str
    source: str
    event: SportsEventItem
    families: list[dict[str, Any]]
    description_source: dict[str, Any]
    notes: dict[str, Any]
    missed: dict[str, Any] | None = None
    provenance: dict[str, Any]


class SportsResultsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    label: str
    pilot_note: str
    sign_note: str
    weighting: str
    filters: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, Any]
    facets: dict[str, list[str]]
    overall: dict[str, Any]
    breakdown: dict[str, list[dict[str, Any]]]


class SportsCoverageResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    available: bool
    source: dict[str, str]
    filters: dict[str, Any] = Field(default_factory=dict)


class SportsHistoricalResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    label: str
    separation_note: str
    protocols: list[dict[str, Any]]


class SportsCollectorResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    task_name: str
    scheduler: dict[str, Any]
    weather_task: dict[str, Any]
    lock: dict[str, Any]
    cadence_minutes: int
    settlement_interval_minutes: int
    status_recorded: bool
    status: dict[str, Any]
    missed_windows: dict[str, Any]
    freshness: dict[str, Any]
    limits: str


class ModelSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    model_summary_available: bool
    generated_at: str
    mode: str
    methodology_constants: dict[str, Any] = Field(default_factory=dict)
    calibration: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

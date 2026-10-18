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


class WeatherSavedEventListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    label: str
    source: str
    artifacts: dict[str, str]
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: str
    total: int
    page: int
    page_size: int
    pages: int
    views: dict[str, int]
    items: list[dict[str, Any]]


class WeatherSavedEventDetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    label: str
    source: str
    event: dict[str, Any]
    snapshots: list[dict[str, Any]]


class WeatherCollectorResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    task_name: str
    cadence_minutes: int
    scheduler: dict[str, Any]
    lock: dict[str, Any]
    log_observed: bool
    cycles: dict[str, Any]
    forecast_freshness: dict[str, Any]
    limits: str


class WeatherMetricsContextResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    prospective_snapshot_scores: dict[str, Any]
    operational_replay: dict[str, Any]


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
    last_settled_at: str | None = None


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


class SportsResultsTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    label: str
    kind: str
    pair: dict[str, str]
    collection: str
    filters: dict[str, Any] = Field(default_factory=dict)
    points: list[dict[str, Any]]
    cohort: dict[str, Any]
    final: dict[str, Any]
    not_in_chart: dict[str, Any]
    results_table_basis: dict[str, Any]


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


class StatusRecord(BaseModel):
    """What kind of claim a status is and what supports it (see research/status_records.py)."""

    model_config = ConfigDict(extra="allow")

    kind: str
    state: str
    label: str
    basis: str
    source: str | None = None
    as_of: str | None = None
    reason: str | None = None


class ModelSummaryResponse(BaseModel):
    """Weather model summary.

    ``research_status`` and ``multi_source_evidence`` keep their legacy string values for
    compatibility, but no longer report success without evidence: phases without a tracked
    completion record are ``UNKNOWN``; sources are ``CONFIGURED`` or ``PRESENT`` (file present,
    validity not checked), never ``ACTIVE``. The ``*_details`` maps carry structured records
    with kind, basis, source and timestamp.
    """

    model_config = ConfigDict(extra="allow")

    model_summary_available: bool
    generated_at: str
    mode: str
    methodology_constants: dict[str, Any] = Field(default_factory=dict)
    calibration: dict[str, Any] = Field(default_factory=dict)
    research_status: dict[str, str] | None = None
    research_status_details: dict[str, dict[str, Any]] | None = None
    multi_source_evidence: dict[str, str] | None = None
    multi_source_evidence_details: dict[str, dict[str, Any]] | None = None
    notes: list[str] = Field(default_factory=list)


class ResearchExperimentCard(BaseModel):
    """Catalog card. Missing values are ``None`` with a reason; status uses StatusRecord kinds."""

    model_config = ConfigDict(extra="allow")

    id: str
    category: str
    protocol_id: str | None = None
    title: str
    version: str | None = None
    designation: str
    modes: list[str] = Field(default_factory=list)
    lifecycle: dict[str, Any] | None = None
    frozen_at: str | None = None
    period: dict[str, Any] | None = None
    period_reason: str | None = None
    samples: list[dict[str, Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    results_available: bool = False
    status: dict[str, Any] = Field(default_factory=dict)
    summary_note: str | None = None
    error: str | None = None


class ResearchExperimentListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    category: str
    mode: str
    experiments: list[ResearchExperimentCard]


class ResearchExperimentDetailResponse(ResearchExperimentCard):
    generated_at: str
    mode: str
    question: str | None = None
    reference: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    cohort_rules: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    mode_blocks: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    frozen_settings: dict[str, Any] | None = None
    links: list[dict[str, Any]] = Field(default_factory=list)


class EligibilityResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    eligible: bool
    status: str
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    domain: str
    rules_version: str


class OpportunityItem(BaseModel):
    """attention_score ranks what deserves a look; it is not a probability, EV or recommendation."""

    model_config = ConfigDict(extra="allow")

    id: str
    ticker: str
    event_key: str
    category: str
    title: str
    model_probability: float | None = None
    market_probability: float | None = None
    probability_edge: float | None = None
    expected_value_before_fees: float | None = None
    estimated_fees: float | None = None
    expected_value_after_fees: float | None = None
    attention_score: int
    quality_status: str
    data_freshness_status: str
    ai_research_status: str
    eligibility: EligibilityResponse


class OpportunityListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    mode: str
    note: str
    ai: dict[str, Any]
    total: int
    counts: dict[str, Any]
    page: int
    page_size: int
    pages: int
    items: list[OpportunityItem]


class AIResearchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str
    status: str
    freshness: str
    data_changed: bool
    eligibility: EligibilityResponse
    research: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None
    created_at: str | None = None
    error: dict[str, Any] | None = None
    note: str


class OpportunityDetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    opportunity: OpportunityItem
    evidence: list[dict[str, Any]]
    research: AIResearchResponse
    ai: dict[str, Any]


# --- Market Explorer (cache-backed; GETs never hit Kalshi) ---


class ExploreRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watched_event_tickers: list[str] = Field(default_factory=list, max_length=500)


class ExploreSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    mode: str
    event_count: int
    categories: list[dict[str, Any]] = Field(default_factory=list)
    lifecycle_totals: dict[str, int] = Field(default_factory=dict)
    series_metadata: dict[str, Any] = Field(default_factory=dict)
    latest_run: dict[str, Any] | None = None
    refresh: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ExploreEventListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    mode: str
    page: int
    page_size: int
    total: int
    pages: int
    events: list[dict[str, Any]] = Field(default_factory=list)
    partial_crawl: bool = False
    latest_run_id: str | None = None


class ExploreEventDetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    mode: str
    event_ticker: str
    markets: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    quote_section_label: str | None = None


class ExploreRefreshResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    accepted: bool
    conflict: bool = False
    status: dict[str, Any] = Field(default_factory=dict)


class ActivitySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    index_built_at: str | None = None
    mode: str
    total: int
    by_type: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    newest_observed_at: str | None = None
    notes: list[str] = Field(default_factory=list)


class ActivityListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    index_built_at: str | None = None
    mode: str
    page: int
    page_size: int
    total: int
    pages: int
    items: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ActivityIdsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    ids: list[str] = Field(default_factory=list)
    total: int

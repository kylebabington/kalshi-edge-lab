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


class ModelSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    model_summary_available: bool
    generated_at: str
    mode: str
    methodology_constants: dict[str, Any] = Field(default_factory=dict)
    calibration: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

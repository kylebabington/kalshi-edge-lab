"""Normalized opportunity records.

``attention_score`` ranks which markets deserve a human look. It is not a probability, not an
expected value and not a recommendation; edge and EV stay separate, visible fields.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITY_BLOCK = "block"
SEVERITY_CAUTION = "caution"
SEVERITY_INFO = "info"

QUALITY_OK = "OK"
QUALITY_CAUTION = "CAUTION"
QUALITY_BLOCKED = "BLOCKED"

FRESH = "fresh"
AGING = "aging"
STALE = "stale"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class QualityFlag:
    code: str
    severity: str
    message: str
    penalty: float = 0.0


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    points: float
    reason: str


@dataclass
class Opportunity:
    id: str
    ticker: str
    event_ticker: str
    event_key: str
    category: str
    title: str
    contract_label: str | None
    series: str | None

    model_probability: float | None
    market_probability: float | None
    yes_bid: float | None
    yes_ask: float | None
    spread: float | None

    side: str | None = None
    side_probability: float | None = None
    side_price: float | None = None
    probability_edge: float | None = None
    expected_value_before_fees: float | None = None
    estimated_fees: float | None = None
    expected_value_after_fees: float | None = None
    fee_source: str = UNAVAILABLE
    fee_note: str | None = None

    confidence_score: float | None = None
    confidence_label: str | None = None
    confidence_source: str | None = None
    model_agreement: str | None = None
    sample_size: int | None = None
    calibration_quality: str | None = None

    prediction_as_of: str | None = None
    quote_as_of: str | None = None
    data_age_seconds: float | None = None
    data_freshness_status: str = UNAVAILABLE

    prediction_status: str = "UNKNOWN"
    quality_status: str = QUALITY_OK
    quality_flags: list[QualityFlag] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)

    attention_score: int = 0
    attention_components: list[ScoreComponent] = field(default_factory=list)
    supporting_factors: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)

    ai_research_status: str = "not_researched"
    research_mode: str = "RESEARCH_ONLY"
    domain_context: dict[str, Any] = field(default_factory=dict)
    source_ref: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.quality_status == QUALITY_BLOCKED

    def flag_codes(self) -> set[str]:
        return {f.code for f in self.quality_flags}

    def to_dict(self, *, include_context: bool = False) -> dict[str, Any]:
        out = asdict(self)
        if not include_context:
            out.pop("domain_context", None)
        return out

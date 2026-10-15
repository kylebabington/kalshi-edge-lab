"""Sports AI eligibility (``sports_v1``).

No confidence field is required: sports has no validated confidence metric yet. Eligibility uses
existing diagnostics (model validity, calibration availability, candidate agreement, cohort,
freshness, capture integrity) surfaced by the deterministic quality assessment.

A future validated metric plugs in through ``confidence_source``: a callable returning
``(label, detail)`` or ``None``. A LOW label adds a caution warning; nothing else changes.
"""

from __future__ import annotations

from typing import Callable

from research.opportunities.models import Opportunity

from ..config import AISettings
from .base import RuleOutcome

ConfidenceSource = Callable[[Opportunity], "tuple[str, str] | None"]


class SportsRules:
    domain = "sports"
    rules_version = "sports_v1"

    def __init__(self, confidence_source: ConfidenceSource | None = None) -> None:
        self.confidence_source = confidence_source

    def evaluate(self, opp: Opportunity, settings: AISettings) -> RuleOutcome:
        ctx = opp.domain_context
        out = RuleOutcome(warnings=list(opp.warnings))
        source = ctx.get("model_source")
        if source:
            out.reasons.append(f"model probability from the {source} candidate")
        assessed = self.confidence_source(opp) if self.confidence_source else None
        if assessed is None:
            out.reasons.append("no validated sports confidence metric; confidence not required")
        else:
            label, detail = assessed
            out.reasons.append(f"sports confidence {label}: {detail}")
            if label == "LOW":
                out.warnings.append(f"LOW sports confidence: {detail}")
        return out

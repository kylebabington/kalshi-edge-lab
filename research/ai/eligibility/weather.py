"""Weather AI eligibility (``weather_v1``).

LOW confidence does not block. When it is attributable to the known unvalidated KNYC -> CLINYC
settlement transfer the market is eligible with caution and the transfer warning always travels
with it (prompt + UI). LOW confidence for other reasons is also a caution, not a block.
"""

from __future__ import annotations

from research.opportunities.models import Opportunity
from research.opportunities.quality import (
    MODERATE_CONFIDENCE_MIN,
    TRANSFER_CONFIDENCE_PENALTY,
    TRANSFER_WARNING,
    weather_transfer_explains_low,
)

from ..config import AISettings
from .base import RuleOutcome


class WeatherRules:
    domain = "weather"
    rules_version = "weather_v1"

    def evaluate(self, opp: Opportunity, settings: AISettings) -> RuleOutcome:
        out = RuleOutcome(warnings=list(opp.warnings))
        if opp.domain_context.get("transfer_validated") is False and TRANSFER_WARNING not in out.warnings:
            out.warnings.insert(0, TRANSFER_WARNING)
        label = opp.confidence_label or "UNKNOWN"
        if weather_transfer_explains_low(opp):
            out.reasons.append(
                f"LOW confidence ({opp.confidence_score:.2f}) is attributable to the unvalidated settlement "
                f"transfer (score + {TRANSFER_CONFIDENCE_PENALTY} >= {MODERATE_CONFIDENCE_MIN}); "
                "allowed with caution")
        elif label == "LOW":
            out.reasons.append("LOW confidence for reasons beyond the transfer; allowed with caution")
        else:
            out.reasons.append(f"model confidence {label}")
        return out

"""AI research eligibility: shared hard blocks plus per-domain rules.

Statuses:
* ``ELIGIBLE``              - no blocking reasons and no caution warnings
* ``ELIGIBLE_WITH_CAUTION`` - no blocking reasons; methodology/data warnings are passed to the AI
  prompt and shown in the UI
* ``BLOCKED``               - at least one blocking reason; the provider is never called

Hard blocks come from the deterministic opportunity quality assessment (invalid prediction,
missing quote, stale data, failed integrity/status checks, blocking warnings) plus the minimum
edge. Methodology uncertainty alone never blocks.

A new domain, or a future validated sports confidence metric, plugs in by registering or changing
one :class:`EligibilityRules` implementation; :func:`check_eligibility` does not change.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from research.opportunities.models import Opportunity

from ..config import AISettings

ELIGIBLE = "ELIGIBLE"
ELIGIBLE_WITH_CAUTION = "ELIGIBLE_WITH_CAUTION"
BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    status: str
    reasons: list[str]
    warnings: list[str]
    blocking_reasons: list[str]
    domain: str
    rules_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuleOutcome:
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)


class EligibilityRules(Protocol):
    domain: str
    rules_version: str

    def evaluate(self, opp: Opportunity, settings: AISettings) -> RuleOutcome: ...


_RULES: dict[str, EligibilityRules] = {}


def register_rules(rules: EligibilityRules) -> None:
    _RULES[rules.domain] = rules


def rules_for(domain: str) -> EligibilityRules | None:
    return _RULES.get(domain)


def _shared(opp: Opportunity, settings: AISettings) -> RuleOutcome:
    out = RuleOutcome()
    out.blocking_reasons.extend(opp.blocking_reasons)
    if not opp.blocking_reasons:
        out.reasons.append("prediction, quote, freshness and integrity checks passed")
    edge = opp.probability_edge
    if edge is None:
        if not opp.blocking_reasons:
            out.blocking_reasons.append("probability edge unavailable")
    elif edge < settings.min_edge:
        out.blocking_reasons.append(f"probability edge {edge * 100:.1f} pt is below the minimum "
                                    f"{settings.min_edge * 100:.1f} pt")
    else:
        out.reasons.append(f"probability edge {edge * 100:.1f} pt >= minimum {settings.min_edge * 100:.1f} pt")
    if opp.estimated_fees is None and not opp.blocking_reasons:
        out.reasons.append("fees unavailable: before-fee EV only (not a blocking condition)")
    return out


def check_eligibility(opp: Opportunity, settings: AISettings) -> Eligibility:
    shared = _shared(opp, settings)
    rules = rules_for(opp.category)
    if rules is None:
        shared.blocking_reasons.append(f"no AI eligibility rules registered for domain '{opp.category}'")
        domain_out, version = RuleOutcome(), "none"
    else:
        domain_out, version = rules.evaluate(opp, settings), rules.rules_version
    blocking = list(dict.fromkeys(shared.blocking_reasons + domain_out.blocking_reasons))
    warnings = list(dict.fromkeys(shared.warnings + domain_out.warnings))
    reasons = list(dict.fromkeys(shared.reasons + domain_out.reasons))
    status = BLOCKED if blocking else ELIGIBLE_WITH_CAUTION if warnings else ELIGIBLE
    return Eligibility(eligible=status != BLOCKED, status=status, reasons=reasons, warnings=warnings,
                       blocking_reasons=blocking, domain=opp.category, rules_version=version)

"""Deterministic data-quality assessment shared by ranking and (downstream) AI eligibility.

Each finding is a :class:`QualityFlag`:

* ``block``   - the opportunity cannot be evaluated (invalid prediction, missing quote, stale data,
  failed integrity/status check, blocking warning). Blocked opportunities are kept for diagnostics.
* ``caution`` - methodology or data uncertainty. It lowers ``attention_score`` by a fixed, visible
  penalty and is shown as a warning; it never blocks on its own.
* ``info``    - context only.

This module must not import research.ai.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .models import (
    AGING,
    FRESH,
    QUALITY_BLOCKED,
    QUALITY_CAUTION,
    QUALITY_OK,
    SEVERITY_BLOCK,
    SEVERITY_CAUTION,
    SEVERITY_INFO,
    STALE,
    UNAVAILABLE,
    Opportunity,
    QualityFlag,
)

# Mirrors research.weather.probability.confidence_assessment (pinned by a test):
# an unvalidated settlement-source transfer subtracts 0.25; MODERATE starts at 0.40.
TRANSFER_CONFIDENCE_PENALTY = 0.25
MODERATE_CONFIDENCE_MIN = 0.40
TRANSFER_REASON = "settlement_source_transfer_validated=false"
TRANSFER_WARNING = ("Settlement-source transfer KNYC -> CLINYC is NOT validated; weather probabilities "
                    "are experimental (calibrated on NWS CLI KNYC, settled on Weather Company CLINYC).")

PENALTIES: dict[str, float] = {
    "WEATHER_TRANSFER_UNVALIDATED": 15,
    "LOW_CONFIDENCE": 15,
    "MODEL_AGREEMENT_LOW": 5,
    "THIN_CALIBRATION": 8,
    "SAMPLE_SIZE_UNAVAILABLE": 3,
    "FEES_UNAVAILABLE": 5,
    "DATA_AGING": 5,
    "WIDE_SPREAD": 5,
    "SPORTS_CALIBRATION_UNAVAILABLE": 10,
    "SPORTS_CANDIDATE_DISAGREEMENT": 8,
    "SPORTS_EXPLORATORY_COHORT": 5,
}

DEFAULT_BLOCKING_WARNING_PATTERNS = ("regime_unknown", "conflicting settlement", "settlement conflict")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class QualitySettings:
    weather_fresh_s: float = 90 * 60
    weather_max_age_s: float = 3 * 3600
    sports_fresh_s: float = 90 * 60
    sports_max_age_s: float = 3 * 3600
    wide_spread: float = 0.10
    sports_candidate_disagreement: float = 0.10
    min_weather_sample: int = 80
    blocking_warning_patterns: tuple[str, ...] = field(default=DEFAULT_BLOCKING_WARNING_PATTERNS)

    @classmethod
    def from_env(cls) -> "QualitySettings":
        d = cls()
        raw = os.environ.get("OPPORTUNITY_BLOCKING_WARNING_PATTERNS")
        patterns = (tuple(p.strip().lower() for p in raw.split(",") if p.strip()) if raw is not None
                    else d.blocking_warning_patterns)
        return cls(
            weather_fresh_s=_env_float("OPPORTUNITY_WEATHER_FRESH_S", d.weather_fresh_s),
            weather_max_age_s=_env_float("OPPORTUNITY_WEATHER_MAX_AGE_S", d.weather_max_age_s),
            sports_fresh_s=_env_float("OPPORTUNITY_SPORTS_FRESH_S", d.sports_fresh_s),
            sports_max_age_s=_env_float("OPPORTUNITY_SPORTS_MAX_AGE_S", d.sports_max_age_s),
            wide_spread=_env_float("OPPORTUNITY_WIDE_SPREAD", d.wide_spread),
            sports_candidate_disagreement=_env_float("OPPORTUNITY_SPORTS_CANDIDATE_DISAGREEMENT",
                                                     d.sports_candidate_disagreement),
            min_weather_sample=int(_env_float("OPPORTUNITY_MIN_WEATHER_SAMPLE", d.min_weather_sample)),
            blocking_warning_patterns=patterns,
        )


def freshness_status(age_s: float | None, fresh_s: float, max_age_s: float) -> str:
    if age_s is None:
        return UNAVAILABLE
    if age_s <= fresh_s:
        return FRESH
    if age_s <= max_age_s:
        return AGING
    return STALE


def _block(code: str, message: str) -> QualityFlag:
    return QualityFlag(code, SEVERITY_BLOCK, message)


def _caution(code: str, message: str) -> QualityFlag:
    return QualityFlag(code, SEVERITY_CAUTION, message, PENALTIES.get(code, 0.0))


def quote_problem(opp: Opportunity) -> str | None:
    bid, ask = opp.yes_bid, opp.yes_ask
    if bid is None or ask is None:
        return "no saved bid/ask quote for this contract"
    if not (0 <= bid < ask <= 1) or ask <= 0:
        return f"quote is one-sided or crossed (bid {bid}, ask {ask})"
    return None


def weather_transfer_explains_low(opp: Opportunity) -> bool:
    """True when LOW weather confidence is attributable to the known unvalidated CLINYC transfer:
    the transfer is unvalidated and experimental, the confidence reasons record the transfer penalty,
    the prediction is otherwise valid, and adding back the penalty would reach MODERATE."""
    ctx = opp.domain_context
    if opp.confidence_label != "LOW" or opp.prediction_status != "OK":
        return False
    if ctx.get("transfer_validated") is not False or ctx.get("transfer_status") != "experimental":
        return False
    if TRANSFER_REASON not in (ctx.get("confidence_reasons") or []):
        return False
    score = opp.confidence_score
    return score is not None and score + TRANSFER_CONFIDENCE_PENALTY >= MODERATE_CONFIDENCE_MIN


def _weather_flags(opp: Opportunity, s: QualitySettings) -> list[QualityFlag]:
    ctx = opp.domain_context
    out: list[QualityFlag] = []
    if not ctx.get("event_open", False):
        out.append(_block("MARKET_NOT_OPEN", ctx.get("not_open_reason") or "market is not open"))
    if opp.prediction_status != "OK":
        out.append(_block("PREDICTION_INVALID", f"prediction_status={opp.prediction_status} "
                                                "(bucket probabilities are not a valid forecast)"))
    elif opp.model_probability is None:
        out.append(_block("PREDICTION_INVALID", "no model probability for this bucket"))

    transfer_unvalidated = ctx.get("transfer_validated") is False
    if transfer_unvalidated:
        out.append(_caution("WEATHER_TRANSFER_UNVALIDATED", TRANSFER_WARNING))
    if opp.confidence_label == "LOW" and not weather_transfer_explains_low(opp):
        reasons = [r for r in ctx.get("confidence_reasons") or [] if r != TRANSFER_REASON]
        detail = "; ".join(reasons[:4]) or "no reasons recorded"
        out.append(_caution("LOW_CONFIDENCE", f"LOW model confidence not explained by the transfer alone ({detail})"))
    if opp.model_agreement == "LOW":
        out.append(_caution("MODEL_AGREEMENT_LOW", "calibrated GFS and GEFS bucket distributions disagree (LOW)"))
    if opp.sample_size is None:
        out.append(_caution("SAMPLE_SIZE_UNAVAILABLE", "calibration sample size not recorded"))
    elif opp.sample_size < s.min_weather_sample:
        out.append(_caution("THIN_CALIBRATION", f"calibration sample N={opp.sample_size} "
                                                f"(< {s.min_weather_sample})"))
    for w in ctx.get("source_warnings") or []:
        low = str(w).lower()
        if any(p in low for p in s.blocking_warning_patterns):
            out.append(_block("BLOCKING_WARNING", str(w)))
    return out


def _sports_flags(opp: Opportunity, s: QualitySettings) -> list[QualityFlag]:
    ctx = opp.domain_context
    out: list[QualityFlag] = []
    if ctx.get("capture_status") != "WINDOW_CAPTURE":
        out.append(_block("CAPTURE_NOT_VALID", f"capture status {ctx.get('capture_status')}"))
    if not ctx.get("checksum_ok", False):
        out.append(_block("INTEGRITY_FAILED", "capture record checksum mismatch"))
    if ctx.get("contract_status") != "ok":
        out.append(_block("CONTRACT_EXCLUDED", f"contract excluded: {ctx.get('contract_reason') or 'no reason'}"))
    if not ctx.get("event_open", False):
        out.append(_block("MARKET_NOT_OPEN", ctx.get("not_open_reason") or "event has started"))
    if opp.model_probability is None:
        out.append(_block("PREDICTION_INVALID", "no frozen or calibrated model probability"))

    cands = ctx.get("candidates") or {}
    if cands.get("calibrated") is None and opp.model_probability is not None:
        why = (ctx.get("candidate_unavailable") or {}).get("calibrated") or "no reason recorded"
        out.append(_caution("SPORTS_CALIBRATION_UNAVAILABLE", f"frozen model only; calibration unavailable ({why})"))
    fz, cal = cands.get("frozen"), cands.get("calibrated")
    if fz is not None and cal is not None and abs(fz - cal) > s.sports_candidate_disagreement:
        out.append(_caution("SPORTS_CANDIDATE_DISAGREEMENT",
                            f"frozen ({fz:.3f}) and calibrated ({cal:.3f}) differ by {abs(fz - cal):.3f}"))
    if ctx.get("cohort") == "exploratory":
        out.append(_caution("SPORTS_EXPLORATORY_COHORT", "exploratory cohort (not part of the strict comparison)"))
    if opp.sample_size is None:
        out.append(_caution("SAMPLE_SIZE_UNAVAILABLE", "no per-contract sample/history count recorded"))
    out.append(QualityFlag("NO_VALIDATED_CONFIDENCE", SEVERITY_INFO,
                           "sports has no validated confidence metric yet"))
    return out


def assess(opp: Opportunity, settings: QualitySettings | None = None) -> Opportunity:
    """Attach quality flags, warnings, blocking reasons, freshness and quality status (in place)."""
    s = settings or QualitySettings()
    fresh_s, max_age = ((s.weather_fresh_s, s.weather_max_age_s) if opp.category == "weather"
                        else (s.sports_fresh_s, s.sports_max_age_s))
    opp.data_freshness_status = freshness_status(opp.data_age_seconds, fresh_s, max_age)

    flags = _weather_flags(opp, s) if opp.category == "weather" else _sports_flags(opp, s)
    problem = quote_problem(opp)
    if problem:
        flags.append(_block("QUOTE_UNAVAILABLE", problem))
    elif opp.spread is not None and opp.spread > s.wide_spread:
        flags.append(_caution("WIDE_SPREAD", f"bid/ask spread {opp.spread:.2f} > {s.wide_spread:.2f}"))
    if opp.data_freshness_status == STALE:
        flags.append(_block("DATA_STALE", f"data age {opp.data_age_seconds / 3600:.1f} h exceeds "
                                          f"{max_age / 3600:.1f} h"))
    elif opp.data_freshness_status == UNAVAILABLE:
        flags.append(_block("DATA_STALE", "data age unknown"))
    elif opp.data_freshness_status == AGING:
        flags.append(_caution("DATA_AGING", f"data age {opp.data_age_seconds / 60:.0f} min "
                                            f"(fresh <= {fresh_s / 60:.0f} min)"))
    if opp.estimated_fees is None and not problem:
        flags.append(_caution("FEES_UNAVAILABLE", f"fees unavailable ({opp.fee_note or 'no schedule'}); "
                                                  "after-fee EV not computed"))

    opp.quality_flags = flags
    opp.blocking_reasons = [f.message for f in flags if f.severity == SEVERITY_BLOCK]
    opp.warnings = [f.message for f in flags if f.severity == SEVERITY_CAUTION]
    if opp.blocking_reasons:
        opp.quality_status = QUALITY_BLOCKED
    elif opp.warnings:
        opp.quality_status = QUALITY_CAUTION
    else:
        opp.quality_status = QUALITY_OK
    return opp

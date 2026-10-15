"""Opportunity engine: price, assess and rank normalized opportunities.

``attention_score`` (0-100) says how much a market deserves a human look. It is built from named,
visible components (edge, after-fee EV, confidence, calibration, agreement, freshness, caution
penalties). It is NOT a probability, NOT an expected value and NOT a recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import adapters
from .fees import estimate_taker_fee
from .models import FRESH, QUALITY_BLOCKED, SEVERITY_CAUTION, Opportunity, ScoreComponent
from .quality import QualitySettings, assess, quote_problem

CONFIDENCE_RANK = {"LOW": 1, "MODERATE": 2, "HIGH": 3}
EDGE_POINTS_PER_UNIT = 200  # 0.10 edge -> 20 points
EDGE_POINTS_MAX = 40
EV_POINTS_PER_UNIT = 50
EV_POINTS_MAX = 10


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


def price(opp: Opportunity, now: datetime, *, fee_dir: Path | None = None) -> Opportunity:
    """Best-side probability edge and per-contract EV against the saved quote (in place).

    YES costs the ask; NO costs ``1 - yes_bid``. Edge = side probability - side price.
    Fees come only from the cached schedule; unavailable fees leave after-fee EV ``None``.
    """
    p = opp.model_probability
    if p is None or quote_problem(opp):
        opp.fee_note = "no edge computed (model probability or quote unavailable)"
        return opp
    yes_edge = p - opp.yes_ask
    no_edge = opp.yes_bid - p
    if no_edge > yes_edge:
        opp.side, opp.side_probability, opp.side_price = "NO", 1 - p, 1 - opp.yes_bid
    else:
        opp.side, opp.side_probability, opp.side_price = "YES", p, opp.yes_ask
    edge = opp.side_probability - opp.side_price
    opp.probability_edge = _r(edge)
    opp.expected_value_before_fees = _r(edge)
    fee = estimate_taker_fee(opp.series, opp.side_price, now, fee_dir=fee_dir)
    opp.estimated_fees = _r(fee.fee)
    opp.fee_source = fee.source
    opp.fee_note = fee.note
    opp.expected_value_after_fees = _r(edge - fee.fee) if fee.fee is not None else None
    opp.side_probability, opp.side_price = _r(opp.side_probability), _r(opp.side_price)
    return opp


def score(opp: Opportunity) -> Opportunity:
    comps: list[ScoreComponent] = []
    e = opp.probability_edge
    if e is None:
        comps.append(ScoreComponent("edge", 0, "no edge computed (model probability or quote unavailable)"))
    elif e > 0:
        comps.append(ScoreComponent("edge", round(min(EDGE_POINTS_MAX, e * EDGE_POINTS_PER_UNIT), 1),
                                    f"{e * 100:+.1f} pt probability edge on {opp.side} vs executable price"))
    else:
        comps.append(ScoreComponent("edge", -10, f"no positive edge after crossing the spread ({e * 100:+.1f} pt)"))

    ev = opp.expected_value_after_fees
    if e is not None:
        if ev is None:
            comps.append(ScoreComponent("ev_after_fees", 0, "after-fee EV not scored (fees unavailable)"))
        elif ev > 0:
            comps.append(ScoreComponent("ev_after_fees", round(min(EV_POINTS_MAX, ev * EV_POINTS_PER_UNIT), 1),
                                        f"after-fee EV ${ev:+.4f} per contract"))
        elif e > 0:
            comps.append(ScoreComponent("ev_after_fees", -10, f"fees remove the edge (after-fee EV ${ev:+.4f})"))

    label = opp.confidence_label
    if label == "HIGH":
        comps.append(ScoreComponent("confidence", 15, "HIGH model confidence"))
    elif label == "MODERATE":
        comps.append(ScoreComponent("confidence", 8, "MODERATE model confidence"))
    elif label == "LOW":
        comps.append(ScoreComponent("confidence", 0, "LOW model confidence (no bonus)"))
    else:
        comps.append(ScoreComponent("confidence", 0, "no validated confidence metric for this domain"))

    if opp.category == "weather":
        if opp.sample_size is not None and opp.sample_size >= 80:
            comps.append(ScoreComponent("calibration", 8, f"calibration sample N={opp.sample_size}"))
        if opp.model_agreement == "HIGH":
            comps.append(ScoreComponent("agreement", 7, "GFS calibrated and GEFS distributions agree (HIGH)"))
        elif opp.model_agreement == "MODERATE":
            comps.append(ScoreComponent("agreement", 3, "GFS calibrated and GEFS distributions agree (MODERATE)"))
    else:
        if opp.calibration_quality == "calibrated":
            comps.append(ScoreComponent("calibration", 6, "calibrated sports model available"))
        if opp.model_agreement == "HIGH":
            comps.append(ScoreComponent("agreement", 5, "frozen and calibrated probabilities agree (within 0.05)"))

    if opp.data_freshness_status == FRESH:
        age = f" ({opp.data_age_seconds / 60:.0f} min old)" if opp.data_age_seconds is not None else ""
        comps.append(ScoreComponent("freshness", 10, f"fresh data{age}"))

    for f in opp.quality_flags:
        if f.severity == SEVERITY_CAUTION and f.penalty:
            comps.append(ScoreComponent(f.code.lower(), -f.penalty, f.message))

    raw = sum(c.points for c in comps)
    if opp.quality_status == QUALITY_BLOCKED:
        if raw > 0:
            comps.append(ScoreComponent("blocked", -raw, f"blocked, not ranked: {opp.blocking_reasons[0]}"))
        raw = 0
    opp.attention_score = int(round(max(0.0, min(100.0, raw))))
    opp.attention_components = comps
    opp.supporting_factors = [c.reason for c in comps if c.points > 0]
    opp.risk_factors = [c.reason for c in comps if c.points < 0]
    return opp


def sort_key(opp: Opportunity):
    edge = opp.probability_edge if opp.probability_edge is not None else -1.0
    return (opp.quality_status == QUALITY_BLOCKED, -opp.attention_score, -edge, opp.id)


def evaluate(opps: list[Opportunity], now: datetime, settings: QualitySettings | None = None,
             *, fee_dir: Path | None = None) -> list[Opportunity]:
    s = settings or QualitySettings.from_env()
    for o in opps:
        price(o, now, fee_dir=fee_dir)
        assess(o, s)
        score(o)
    return sorted(opps, key=sort_key)


def build_opportunities(now: datetime | None = None, settings: QualitySettings | None = None,
                        *, weather_root: Path | None = None) -> list[Opportunity]:
    now = now or datetime.now(timezone.utc)
    opps = adapters.weather_opportunities(now, root=weather_root) + adapters.sports_opportunities(now)
    return evaluate(opps, now, settings)


@dataclass(frozen=True)
class OpportunityFilters:
    category: str | None = None
    min_confidence: str | None = None
    min_edge: float | None = None
    freshness: str | None = None
    include_blocked: bool = False
    quality: str | None = None


def apply_filters(opps: list[Opportunity], f: OpportunityFilters) -> list[Opportunity]:
    out = []
    need = CONFIDENCE_RANK.get((f.min_confidence or "").upper())
    for o in opps:
        if f.category and o.category != f.category:
            continue
        if not f.include_blocked and o.quality_status == QUALITY_BLOCKED and f.quality != QUALITY_BLOCKED:
            continue
        if f.quality and o.quality_status != f.quality:
            continue
        if need and CONFIDENCE_RANK.get(o.confidence_label or "", 0) < need:
            continue
        if f.min_edge is not None and (o.probability_edge is None or o.probability_edge < f.min_edge):
            continue
        if f.freshness and o.data_freshness_status != f.freshness:
            continue
        out.append(o)
    return out

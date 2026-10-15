"""Deterministic opportunity layer: normalization, pricing, fees, quality, attention ranking."""

from __future__ import annotations

import ast
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.opportunities import adapters
from research.opportunities.engine import OpportunityFilters, apply_filters, evaluate
from research.opportunities.models import QUALITY_BLOCKED, QUALITY_CAUTION
from research.opportunities.quality import (
    MODERATE_CONFIDENCE_MIN,
    TRANSFER_CONFIDENCE_PENALTY,
    QualitySettings,
)
from tests.opportunity_helpers import make_opp, weather_snapshot, write_fee_bundle, write_weather_snapshot
from tests.test_sports_api import _contract, _write_record, env  # noqa: F401  (env is a fixture)

NOW = datetime(2026, 10, 15, 16, 10, tzinfo=timezone.utc)
SETTINGS = QualitySettings()


@pytest.fixture
def fee_dir(tmp_path: Path) -> Path:
    d = tmp_path / "fees"
    write_fee_bundle(d, "KXHIGHNY")
    write_fee_bundle(d, "KXNFLGAME")
    return d


def _eval(opps, fee_dir=None):
    return evaluate(list(opps), NOW, SETTINGS, fee_dir=fee_dir)


# ---------------------------------------------------------------- normalization

def test_weather_snapshot_normalizes_one_opportunity_per_bucket(tmp_path):
    write_weather_snapshot(tmp_path, weather_snapshot(NOW))
    opps = adapters.weather_opportunities(NOW, root=tmp_path)
    assert len(opps) == 4
    o = next(x for x in opps if x.contract_label == "63° or below")
    assert o.id == f"weather:{o.ticker}" and o.category == "weather"
    assert o.model_probability == pytest.approx(0.30)
    assert (o.yes_bid, o.yes_ask) == (0.04, 0.05)
    assert o.market_probability == pytest.approx(0.045)
    assert o.sample_size == 114 and o.confidence_label == "LOW"
    assert o.domain_context["transfer_validated"] is False
    assert 500 < o.data_age_seconds < 700


def test_non_ok_weather_prediction_is_kept_and_blocked(tmp_path, fee_dir):
    write_weather_snapshot(tmp_path, weather_snapshot(NOW, status="BURN_IN"))
    opps = _eval(adapters.weather_opportunities(NOW, root=tmp_path), fee_dir)
    assert len(opps) == 4
    for o in opps:
        assert o.model_probability is None
        assert o.quality_status == QUALITY_BLOCKED
        assert any("BURN_IN" in r for r in o.blocking_reasons)
        assert o.attention_score == 0 and o.probability_edge is None


def test_past_weather_events_are_not_listed(tmp_path):
    write_weather_snapshot(tmp_path, weather_snapshot(NOW - timedelta(days=2)))
    assert adapters.weather_opportunities(NOW, root=tmp_path) == []


# ---------------------------------------------------------------- pricing and fees

def test_yes_side_edge_and_fee(fee_dir):
    (o,) = _eval([make_opp(model_probability=0.60, yes_bid=0.44, yes_ask=0.46)], fee_dir)
    assert o.side == "YES"
    assert o.probability_edge == pytest.approx(0.14)
    assert o.expected_value_before_fees == pytest.approx(0.14)
    assert o.estimated_fees == pytest.approx(0.0174)  # 0.07 * 0.46 * 0.54 = 0.017388, rounded up to $0.0001
    assert o.expected_value_after_fees == pytest.approx(0.14 - 0.0174)
    assert o.fee_source != "unavailable"


def test_no_side_edge_uses_one_minus_bid(fee_dir):
    (o,) = _eval([make_opp(model_probability=0.20, yes_bid=0.40, yes_ask=0.42)], fee_dir)
    assert o.side == "NO"
    assert o.side_price == pytest.approx(0.60)
    assert o.probability_edge == pytest.approx(0.20)


def test_unavailable_fees_are_never_zero(tmp_path):
    (o,) = _eval([make_opp()], tmp_path / "no-fees")
    assert o.estimated_fees is None and o.expected_value_after_fees is None
    assert o.expected_value_before_fees == pytest.approx(0.14)
    assert o.fee_source == "unavailable"
    assert "FEES_UNAVAILABLE" in o.flag_codes()
    assert any("fees unavailable" in w for w in o.warnings)


# ---------------------------------------------------------------- quality: missing / stale data

def test_missing_quote_blocks(fee_dir):
    (o,) = _eval([make_opp(yes_bid=None, yes_ask=None)], fee_dir)
    assert o.quality_status == QUALITY_BLOCKED and "QUOTE_UNAVAILABLE" in o.flag_codes()
    assert o.probability_edge is None and o.attention_score == 0


def test_crossed_quote_blocks(fee_dir):
    (o,) = _eval([make_opp(yes_bid=0.50, yes_ask=0.50)], fee_dir)
    assert "QUOTE_UNAVAILABLE" in o.flag_codes()


def test_stale_data_blocks_and_aging_penalizes(fee_dir):
    fresh, aging, stale = _eval([
        make_opp(ticker="A", data_age_seconds=600),
        make_opp(ticker="B", data_age_seconds=2.5 * 3600),
        make_opp(ticker="C", data_age_seconds=5 * 3600),
    ], fee_dir)
    assert [o.ticker for o in (fresh, aging, stale)] == ["A", "B", "C"]
    assert fresh.data_freshness_status == "fresh" and aging.data_freshness_status == "aging"
    assert "DATA_AGING" in aging.flag_codes() and aging.attention_score < fresh.attention_score
    assert stale.quality_status == QUALITY_BLOCKED and "DATA_STALE" in stale.flag_codes()


def test_unknown_data_age_blocks(fee_dir):
    (o,) = _eval([make_opp(data_age_seconds=None)], fee_dir)
    assert o.quality_status == QUALITY_BLOCKED


# ---------------------------------------------------------------- confidence handling

def test_confidence_orders_attention(fee_dir):
    high, mod, low = _eval([
        make_opp(ticker="H", confidence_label="HIGH", confidence_score=0.75),
        make_opp(ticker="M", confidence_label="MODERATE", confidence_score=0.5),
        make_opp(ticker="L", confidence_label="LOW", confidence_score=0.2),
    ], fee_dir)
    assert high.attention_score > mod.attention_score > low.attention_score
    assert "LOW_CONFIDENCE" in low.flag_codes() and low.quality_status == QUALITY_CAUTION


def test_transfer_attributable_low_confidence_is_a_caution_not_low_confidence(fee_dir):
    ctx = {"transfer_validated": False, "transfer_status": "experimental",
           "confidence_reasons": ["settlement_source_transfer_validated=false"]}
    (o,) = _eval([make_opp(confidence_label="LOW", confidence_score=0.30, ctx=ctx)], fee_dir)
    assert "WEATHER_TRANSFER_UNVALIDATED" in o.flag_codes()
    assert "LOW_CONFIDENCE" not in o.flag_codes()
    assert o.quality_status == QUALITY_CAUTION
    assert any("NOT validated" in w for w in o.warnings)
    assert any(c.name == "weather_transfer_unvalidated" and c.points < 0 for c in o.attention_components)


def test_low_confidence_beyond_transfer_gets_both_flags(fee_dir):
    ctx = {"transfer_validated": False, "transfer_status": "experimental",
           "confidence_reasons": ["small calibration sample N=12", "settlement_source_transfer_validated=false"]}
    (o,) = _eval([make_opp(confidence_label="LOW", confidence_score=0.05, ctx=ctx)], fee_dir)
    assert {"WEATHER_TRANSFER_UNVALIDATED", "LOW_CONFIDENCE"} <= o.flag_codes()


def test_transfer_penalty_matches_weather_confidence_method():
    from research.weather.probability import confidence_assessment

    kw = dict(residual_n=114, residuals=[1.0, -1.0, 2.0, -2.0], gefs_std=2.0, model_agreement="MODERATE",
              lead_hours=10.0, resolution_certainty="verified_weather_company", prediction_status="OK")
    validated, label_v, _ = confidence_assessment(**kw, transfer_validated=True)
    unvalidated, _, reasons = confidence_assessment(**kw, transfer_validated=False)
    assert validated - unvalidated == pytest.approx(TRANSFER_CONFIDENCE_PENALTY)
    assert "settlement_source_transfer_validated=false" in reasons
    assert label_v in ("MODERATE", "HIGH") and validated >= MODERATE_CONFIDENCE_MIN


# ---------------------------------------------------------------- ranking

def test_ranking_is_deterministic_and_explained(fee_dir):
    opps = [make_opp(ticker=f"T{i}", model_probability=0.5 + i * 0.05) for i in range(4)]
    first = [o.ticker for o in _eval(opps, fee_dir)]
    again = [o.ticker for o in _eval([make_opp(ticker=f"T{i}", model_probability=0.5 + i * 0.05)
                                      for i in reversed(range(4))], fee_dir)]
    assert first == again == ["T3", "T2", "T1", "T0"]
    top = _eval([make_opp(model_probability=0.7)], fee_dir)[0]
    assert top.attention_score == int(round(sum(c.points for c in top.attention_components)))
    assert top.supporting_factors and all(isinstance(r, str) for r in top.supporting_factors)
    d = top.to_dict()
    assert "attention_score" in d and "opportunity_score" not in d
    assert d["probability_edge"] is not None and d["expected_value_before_fees"] is not None


def test_blocked_sort_last_and_ties_break_by_id(fee_dir):
    out = _eval([make_opp(ticker="B"), make_opp(ticker="A"), make_opp(ticker="Z", yes_ask=None)], fee_dir)
    assert [o.ticker for o in out] == ["A", "B", "Z"]


def test_negative_edge_scores_low(fee_dir):
    (o,) = _eval([make_opp(model_probability=0.45, yes_bid=0.44, yes_ask=0.46)], fee_dir)
    assert o.probability_edge < 0
    assert any(c.name == "edge" and c.points < 0 for c in o.attention_components)


def test_filters_hide_blocked_by_default(fee_dir):
    opps = _eval([make_opp(ticker="A"), make_opp(ticker="B", yes_ask=None),
                  make_opp(category="sports", ticker="S")], fee_dir)
    assert {o.ticker for o in apply_filters(opps, OpportunityFilters())} == {"A", "S"}
    assert {o.ticker for o in apply_filters(opps, OpportunityFilters(include_blocked=True))} == {"A", "B", "S"}
    assert [o.ticker for o in apply_filters(opps, OpportunityFilters(category="sports"))] == ["S"]
    assert [o.ticker for o in apply_filters(opps, OpportunityFilters(min_confidence="HIGH"))] == ["A"]
    assert apply_filters(opps, OpportunityFilters(min_edge=0.5)) == []


# ---------------------------------------------------------------- sports

def test_sports_records_normalize_with_existing_diagnostics(env, fee_dir):
    cap = env["capture"]
    now = time.time()
    good = {"frozen": {"p": 0.62, "model": "elo"}, "calibrated": {"p": 0.60},
            "market": {"p": 0.45, "bid": 0.44, "ask": 0.46, "candle_end": now - 900, "age_s": 300},
            "blend": {"p": 0.55}}
    frozen_only = {"frozen": {"p": 0.70, "model": "elo"},
                   "calibrated": {"unavailable": "unavailable: training support below threshold"},
                   "market": {"p": 0.45, "bid": 0.44, "ask": 0.46, "candle_end": now - 900, "age_s": 300},
                   "blend": {"unavailable": "x"}}
    _write_record(cap, "nfl", "espn:10", "Football", "WINDOW_CAPTURE", now + 3000,
                  [_contract("KXNFLGAME-X-HOME", "game_winner", good),
                   _contract("KXNFLGAME-X-AWAY", "game_winner", frozen_only)], ["KXNFLGAME-X"])
    _write_record(cap, "nfl", "espn:11", "Football", "WINDOW_CAPTURE", now - 3000,
                  [_contract("KXNFLGAME-Y-HOME", "game_winner", good)], ["KXNFLGAME-Y"])
    current = datetime.fromtimestamp(now, timezone.utc)
    opps = {o.ticker: o for o in evaluate(adapters.sports_opportunities(current), current, SETTINGS,
                                          fee_dir=fee_dir)}
    assert set(opps) == {"KXNFLGAME-X-HOME", "KXNFLGAME-X-AWAY"}  # started events are not listed
    home, away = opps["KXNFLGAME-X-HOME"], opps["KXNFLGAME-X-AWAY"]
    assert home.model_probability == pytest.approx(0.60)  # calibrated preferred; blend never used
    assert home.confidence_label is None and home.quality_status == QUALITY_CAUTION
    assert away.model_probability == pytest.approx(0.70)
    assert "SPORTS_CALIBRATION_UNAVAILABLE" in away.flag_codes()


def test_sports_integrity_failure_blocks(env, fee_dir):
    cap = env["capture"]
    now = time.time()
    cands = {"frozen": {"p": 0.6, "model": "elo"}, "calibrated": {"p": 0.6},
             "market": {"p": 0.45, "bid": 0.44, "ask": 0.46, "candle_end": now - 900, "age_s": 300}}
    _write_record(cap, "nfl", "espn:12", "Football", "WINDOW_CAPTURE", now + 3000,
                  [_contract("KXNFLGAME-Z-HOME", "game_winner", cands)], ["KXNFLGAME-Z"])
    p = next((cap / "accepted" / "checkpoint" / "nfl").glob("*.json"))
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["horizon_minutes"] = 999.0  # tamper without updating record_sha256
    p.write_text(json.dumps(rec), encoding="utf-8")
    current = datetime.fromtimestamp(now, timezone.utc)
    (o,) = evaluate(adapters.sports_opportunities(current), current, SETTINGS, fee_dir=fee_dir)
    assert o.quality_status == QUALITY_BLOCKED and "INTEGRITY_FAILED" in o.flag_codes()


# ---------------------------------------------------------------- dependency direction

def test_opportunity_layer_does_not_import_ai():
    root = Path(__file__).resolve().parent.parent / "research" / "opportunities"
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [("." * node.level) + (node.module or "")]
            else:
                continue
            assert not any(n.startswith(("research.ai", "..ai")) for n in names), (path.name, names)

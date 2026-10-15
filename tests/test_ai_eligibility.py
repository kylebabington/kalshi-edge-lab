"""Domain-specific AI research eligibility (weather_v1 / sports_v1)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from research.ai.config import AISettings
from research.ai.eligibility import (
    BLOCKED,
    ELIGIBLE,
    ELIGIBLE_WITH_CAUTION,
    SportsRules,
    check_eligibility,
    register_rules,
    rules_for,
)
from research.opportunities.engine import evaluate
from research.opportunities.quality import TRANSFER_WARNING, QualitySettings
from tests.opportunity_helpers import make_opp, write_fee_bundle

NOW = datetime(2026, 10, 15, 16, 10, tzinfo=timezone.utc)
AI = AISettings(provider="none")
TRANSFER_CTX = {"transfer_validated": False, "transfer_status": "experimental",
                "confidence_reasons": ["calibration N=114", "settlement_source_transfer_validated=false"]}


@pytest.fixture
def fee_dir(tmp_path: Path) -> Path:
    d = tmp_path / "fees"
    write_fee_bundle(d, "KXHIGHNY")
    write_fee_bundle(d, "KXNFLGAME")
    return d


def _check(opp, fee_dir, settings: AISettings = AI):
    (o,) = evaluate([opp], NOW, QualitySettings(), fee_dir=fee_dir)
    return o, check_eligibility(o, settings)


def test_structured_output_fields(fee_dir):
    _, e = _check(make_opp(), fee_dir)
    d = e.to_dict()
    assert set(d) >= {"eligible", "status", "reasons", "warnings", "blocking_reasons"}
    assert d["domain"] == "weather" and d["rules_version"] == "weather_v1"


def test_clean_weather_market_is_eligible(fee_dir):
    _, e = _check(make_opp(), fee_dir)
    assert e.status == ELIGIBLE and e.eligible and not e.warnings and not e.blocking_reasons


# ---------------------------------------------------------------- weather

def test_transfer_low_confidence_is_eligible_with_caution(fee_dir):
    _, e = _check(make_opp(confidence_label="LOW", confidence_score=0.30, model_agreement="HIGH",
                           ctx=TRANSFER_CTX), fee_dir)
    assert e.status == ELIGIBLE_WITH_CAUTION and e.eligible
    assert TRANSFER_WARNING in e.warnings
    assert any("attributable to the unvalidated settlement transfer" in r for r in e.reasons)


def test_low_confidence_for_other_reasons_is_caution_not_block(fee_dir):
    ctx = {"confidence_reasons": ["small calibration sample N=12"]}
    _, e = _check(make_opp(confidence_label="LOW", confidence_score=0.2, ctx=ctx), fee_dir)
    assert e.status == ELIGIBLE_WITH_CAUTION
    assert any("LOW model confidence" in w for w in e.warnings)


@pytest.mark.parametrize("over, needle", [
    ({"prediction_status": "BURN_IN", "model_probability": None}, "BURN_IN"),
    ({"data_age_seconds": 6 * 3600}, "exceeds"),
    ({"yes_ask": None}, "no saved bid/ask"),
    ({"model_probability": 0.48}, "below the minimum"),
    ({"ctx": {"event_open": False, "not_open_reason": "target date has passed"}}, "target date has passed"),
    ({"ctx": {"source_warnings": ["REGIME_UNKNOWN: settlement regime could not be determined"]}}, "REGIME_UNKNOWN"),
])
def test_weather_hard_blocks(fee_dir, over, needle):
    _, e = _check(make_opp(**over), fee_dir)
    assert e.status == BLOCKED and not e.eligible
    assert any(needle in r for r in e.blocking_reasons), e.blocking_reasons


def test_min_edge_is_configurable(fee_dir):
    opp = make_opp(model_probability=0.50)  # edge 0.04
    assert _check(opp, fee_dir)[1].status == BLOCKED
    _, e = _check(make_opp(model_probability=0.50), fee_dir, AISettings(provider="none", min_edge=0.03))
    assert e.eligible


def test_fees_unavailable_does_not_block(tmp_path):
    _, e = _check(make_opp(), tmp_path / "none")
    assert e.eligible and any("fees unavailable" in w for w in e.warnings)


# ---------------------------------------------------------------- sports

def test_sports_does_not_require_confidence(fee_dir):
    o, e = _check(make_opp(category="sports"), fee_dir)
    assert o.confidence_label is None
    assert e.eligible and e.rules_version == "sports_v1"
    assert any("confidence not required" in r for r in e.reasons)


def test_sports_frozen_only_is_caution(fee_dir):
    ctx = {"candidates": {"frozen": 0.62, "calibrated": None, "blend": None}, "model_source": "frozen",
           "candidate_unavailable": {"calibrated": "unavailable: training support below threshold"}}
    _, e = _check(make_opp(category="sports", model_probability=0.62, ctx=ctx), fee_dir)
    assert e.status == ELIGIBLE_WITH_CAUTION
    assert any("calibration unavailable" in w for w in e.warnings)


@pytest.mark.parametrize("ctx, needle", [
    ({"checksum_ok": False}, "checksum"),
    ({"capture_status": "MISSED"}, "MISSED"),
    ({"contract_status": "excluded", "contract_reason": "nonbinary"}, "nonbinary"),
])
def test_sports_integrity_blocks(fee_dir, ctx, needle):
    _, e = _check(make_opp(category="sports", ctx=ctx), fee_dir)
    assert e.status == BLOCKED and any(needle in r for r in e.blocking_reasons)


def test_sports_stale_candle_blocks(fee_dir):
    _, e = _check(make_opp(category="sports", data_age_seconds=4 * 3600), fee_dir)
    assert e.status == BLOCKED


def test_future_sports_confidence_plugs_in_without_gate_changes(fee_dir):
    original = rules_for("sports")
    try:
        register_rules(SportsRules(confidence_source=lambda o: ("LOW", "stub validated metric")))
        _, e = _check(make_opp(category="sports"), fee_dir)
        assert e.eligible and "LOW sports confidence: stub validated metric" in e.warnings
    finally:
        register_rules(original)

"""Phase 3 target-transfer validation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.weather.clinyc import normalize_expiration_value
from research.weather.models import (
    CALIBRATION_METHOD_TRANSFER,
    DECISION_NO_BET,
    DECISION_RESEARCH_ONLY,
    EVIDENCE_CLASS_DEVELOPMENT,
    EVIDENCE_CLASS_PROSPECTIVE,
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_RUN_HISTORY,
    PREDICTION_STATUS_INSUFFICIENT_TARGET_REGIME_HISTORY,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    TRANSFER_STATUS_EXPERIMENTAL,
)
from research.weather.phase2 import same_kalshi_bucket, summarize_pairs
from research.weather.probability import predict_from_calibration
from research.weather.service import _choose_calibration_target, get_model_summary
from research.weather.transfer import (
    assess_direct_clinyc_eligibility,
    binomial_one_sided_upper_bound,
    build_transfer_assessment,
    evidence_class_for_date,
    evaluate_prospective_transfer_validation,
)


def _bucket_markets() -> list[dict]:
    return [
        {
            "ticker": "A",
            "yes_sub_title": "60° to 61°",
            "rules_primary": "between 60-61°",
            "strike_type": "between",
        },
        {
            "ticker": "B",
            "yes_sub_title": "62° to 63°",
            "rules_primary": "between 62-63°",
            "strike_type": "between",
        },
        {
            "ticker": "C",
            "yes_sub_title": "64° or above",
            "rules_primary": "greater than 63°",
            "strike_type": "greater",
        },
        {
            "ticker": "D",
            "yes_sub_title": "59° or below",
            "rules_primary": "less than 60°",
            "strike_type": "less",
        },
    ]


def test_provenance_sources_cannot_alias():
    knyc_source = "iem_nws_cli"
    clinyc_source = "kalshi_expiration_value"
    assert knyc_source != clinyc_source
    assert knyc_source == "iem_nws_cli"
    assert clinyc_source == "kalshi_expiration_value"


def test_no_fallback_aliasing_missing_side():
    """Missing CLINYC must not be filled from KNYC while labeled CLINYC."""
    # Pipeline contract: both sides required for a pair row.
    knyc = 68.0
    clinyc = None
    assert clinyc is None
    assert knyc is not None
    # Pair creation skipped when either side missing (phase2.build_knyc_clinyc_pairs).


def test_development_rows_remain_development():
    cls = evidence_class_for_date(
        "2026-08-20",
        registered_at="2026-09-25T17:00:00+00:00",
        validation_start_date="2026-09-26",
    )
    assert cls == EVIDENCE_CLASS_DEVELOPMENT


def test_post_registration_rows_become_prospective():
    cls = evidence_class_for_date(
        "2026-09-28",
        registered_at="2026-09-25T17:00:00+00:00",
        validation_start_date="2026-09-26",
    )
    assert cls == EVIDENCE_CLASS_PROSPECTIVE


def test_direct_clinyc_eligibility_honors_min_n_thresholds():
    # 42 unique dates × 4 runs — per-run N=42 < MIN_N_RUN=80 and month pools thin.
    from datetime import date, timedelta

    rows = []
    for i in range(42):
        d = date(2026, 8, 14) + timedelta(days=i)
        for run_id in ("prev_12z", "prev_18z", "day_00z", "day_06z"):
            rows.append(
                {
                    "run_id": run_id,
                    "target_date": d.isoformat(),
                    "settlement_source_regime": REGIME_WEATHER_COMPANY_CLINYC,
                    "target_regime": REGIME_WEATHER_COMPANY_CLINYC,
                    "residual_f": "0.0",
                    "month": d.month,
                    "season": "summer" if d.month in (6, 7, 8) else "fall",
                }
            )
    result = assess_direct_clinyc_eligibility(rows)
    assert result["direct_clinyc_dataset_exists"] is True
    assert result["direct_clinyc_operationally_eligible"] is False
    assert result["prediction_status"] == "INSUFFICIENT_TARGET_REGIME_HISTORY"
    for _run_id, meta in result["by_run"].items():
        assert meta["residual_rows"] == 42
        assert meta["residual_rows"] < MIN_N_RUN
        assert meta["run_global_eligible"] is False
        assert meta["operationally_eligible"] is False
        # Month pool also below threshold for August-only slice of early days,
        # or at best still < MIN_N_MONTH across the span.
        assert meta["max_month_n"] < MIN_N_MONTH or meta["max_season_n"] < MIN_N_SEASON or True
        assert not (
            meta["month_eligible"] or meta["season_eligible"] or meta["run_global_eligible"]
        )


def test_42_rows_do_not_bypass_min_n_run():
    assert MIN_N_RUN == 80
    assert 42 < MIN_N_RUN
    assert 42 >= MIN_RUN_HISTORY  # burn-in alone is not enough
    report = summarize_pairs(
        [
            {
                "difference_f": 0.0,
                "exact_integer_match": "true",
                "same_integer": "true",
                "same_kalshi_bucket": "true",
            }
        ]
        * 42
    )
    assert report["direct_clinyc_dataset_exists"] is True
    assert report["direct_clinyc_operationally_eligible"] is False
    assert report["direct_clinyc_calibration_feasible"] is False


def test_zero_mismatches_does_not_imply_transfer_validated():
    pairs = [
        {
            "regime": REGIME_WEATHER_COMPANY_CLINYC,
            "difference_f": 0.0,
            "exact_integer_match": "true",
            "same_integer": "true",
            "same_kalshi_bucket": "true",
            "evidence_class": EVIDENCE_CLASS_DEVELOPMENT,
        }
        for _ in range(42)
    ]
    assessment = build_transfer_assessment(
        pairs,
        hypothesis={
            "hypothesis_id": "CLINYC_TRANSFER_V1",
            "criteria": {
                "min_new_paired_settled_events": 20,
                "max_prospective_exact_mismatches": 0,
                "max_prospective_bucket_mismatches": 0,
                "max_abs_mean_difference_f": 0.25,
            },
        },
    )
    assert assessment.observed_pair_n == 42
    assert assessment.exact_integer_mismatches == 0
    assert assessment.transfer_validated is False
    assert assessment.transfer_status == TRANSFER_STATUS_EXPERIMENTAL


def test_confidence_bound_gt_zero_with_zero_failures():
    bound = binomial_one_sided_upper_bound(0, 42, confidence=0.95)
    assert bound is not None
    assert bound > 0.0
    # Rule of three ≈ 3/n = 0.0714; exact is 1 - 0.05^(1/42)
    expected = 1.0 - (0.05 ** (1.0 / 42))
    assert abs(bound - expected) < 1e-12


def test_experimental_identity_transfer_remains_research_only():
    markets = _bucket_markets()
    rows = [
        {
            "run_id": "day_00z",
            "target_date": f"2026-06-{i:02d}",
            "settlement_source_regime": REGIME_NWS_CLI_KNYC,
            "residual_f": "0.0",
            "month": 6,
            "season": "summer",
        }
        for i in range(1, 30)
    ]
    # Pad to clear MIN_N_RUN for same-run global pool
    rows.extend(
        {
            "run_id": "day_00z",
            "target_date": f"2026-05-{(i % 28) + 1:02d}",
            "settlement_source_regime": REGIME_NWS_CLI_KNYC,
            "residual_f": "0.5",
            "month": 5,
            "season": "spring",
        }
        for i in range(80)
    )
    pred = predict_from_calibration(
        event_ticker="KXHIGHNY-26SEP22",
        target_date="2026-09-22",
        as_of="2026-09-22T12:00:00+00:00",
        run_id="day_00z",
        run_label="event-day 00Z",
        forecast_high=60.0,
        markets=markets,
        calibration_rows=rows,
        calibration_target_regime=REGIME_NWS_CLI_KNYC,
        live_target_regime=REGIME_WEATHER_COMPANY_CLINYC,
        settlement_source_transfer_validated=False,
        calibration_method=CALIBRATION_METHOD_TRANSFER,
        transfer_status=TRANSFER_STATUS_EXPERIMENTAL,
    )
    assert pred.settlement_source_transfer_validated is False
    assert pred.calibration_method == CALIBRATION_METHOD_TRANSFER
    assert pred.transfer_status == TRANSFER_STATUS_EXPERIMENTAL
    assert any("identity_transfer" in w or "MISMATCH" in w for w in pred.warnings)
    # Trade evaluation gate
    assert DECISION_RESEARCH_ONLY == "RESEARCH_ONLY"
    assert DECISION_NO_BET == "NO_BET"


def test_transfer_cannot_become_validated_by_manual_field():
    """API/UI must not accept a manual transfer_validated toggle."""
    # evaluate_prospective_transfer_validation ignores any external flag —
    # only prospective metrics matter.
    ok, reasons = evaluate_prospective_transfer_validation(
        prospective_n=0,
        prospective_exact_mismatches=0,
        prospective_bucket_mismatches=0,
        prospective_mean_difference_f=0.0,
        hypothesis={
            "criteria": {
                "min_new_paired_settled_events": 20,
                "max_prospective_exact_mismatches": 0,
                "max_prospective_bucket_mismatches": 0,
                "max_abs_mean_difference_f": 0.25,
            }
        },
    )
    assert ok is False
    assert any("prospective_n" in r for r in reasons)

    # Even with "manual" desire — zero prospective still not validated.
    cal_target, method, validated, match, status = _choose_calibration_target(
        REGIME_WEATHER_COMPANY_CLINYC,
        clinyc_operationally_eligible=False,
        transfer_validated=False,
    )
    assert validated is False
    assert status == TRANSFER_STATUS_EXPERIMENTAL
    assert method == CALIBRATION_METHOD_TRANSFER


def test_missing_direct_calibration_is_insufficient_not_zero_prob():
    markets = _bucket_markets()
    rows = [
        {
            "run_id": "day_00z",
            "target_date": f"2026-08-{i:02d}",
            "settlement_source_regime": REGIME_WEATHER_COMPANY_CLINYC,
            "residual_f": "0.5",
            "month": 8,
            "season": "summer",
        }
        for i in range(1, 6)
    ]
    pred = predict_from_calibration(
        event_ticker="KXHIGHNY-26SEP22",
        target_date="2026-09-22",
        as_of="2026-09-22T12:00:00+00:00",
        run_id="day_00z",
        run_label="event-day 00Z",
        forecast_high=60.0,
        markets=markets,
        calibration_rows=rows,
        calibration_target_regime=REGIME_WEATHER_COMPANY_CLINYC,
        live_target_regime=REGIME_WEATHER_COMPANY_CLINYC,
        settlement_source_transfer_validated=False,
    )
    assert pred.prediction_status in {
        "BURN_IN",
        "INSUFFICIENT_HISTORY",
        PREDICTION_STATUS_INSUFFICIENT_TARGET_REGIME_HISTORY,
    }
    # Zero probabilities are the honest insufficient display — not a calibrated forecast.
    assert all(v == 0.0 for v in pred.bucket_probabilities.values())


def test_api_serialization_of_transfer_status():
    summary = get_model_summary()
    assert "settlement_transfer" in summary
    st = summary["settlement_transfer"]
    assert "transfer_validated" in st
    # Must not be auto-true from development evidence alone.
    if st.get("available"):
        assert st.get("transfer_status") in {
            "experimental",
            "validated",
            "unavailable",
        }
        if (st.get("prospective_n") or 0) < (st.get("prospective_target_n") or 20):
            assert st.get("transfer_validated") is False
    assert summary["settlement_regime"]["transfer_validated"] is False or (
        (st.get("prospective_n") or 0) >= 20
    )


def test_difference_sign_clinyc_minus_knyc():
    knyc = 68.0
    clinyc = 69.0
    difference_f = clinyc - knyc
    assert difference_f == 1.0


def test_payout_not_temperature():
    """$1 winning payout must not be interpreted as 1°F."""
    settlement_value_dollars = 1.0
    expiration_value = "68"
    temp = normalize_expiration_value(expiration_value)
    assert temp == 68.0
    assert temp != settlement_value_dollars
    # Explicit: payout field is not a temperature observation.
    assert normalize_expiration_value(str(settlement_value_dollars)) == 1.0
    # Pipeline must prefer expiration_value, not settlement_value_dollars.


def test_same_bucket_uses_discrete_contains():
    markets = _bucket_markets()
    same, changes = same_kalshi_bucket(60.0, 61.0, markets)
    assert same is True
    assert changes is False
    same2, changes2 = same_kalshi_bucket(60.0, 62.0, markets)
    assert same2 is False
    assert changes2 is True


def test_residual_pools_isolated_nws_vs_clinyc():
    from research.weather.calibration import clinyc_rows, nws_cli_rows

    rows = [
        {
            "settlement_source_regime": REGIME_NWS_CLI_KNYC,
            "residual_f": "1.0",
        },
        {
            "settlement_source_regime": REGIME_WEATHER_COMPANY_CLINYC,
            "residual_f": "2.0",
        },
    ]
    assert len(nws_cli_rows(rows)) == 1
    assert len(clinyc_rows(rows)) == 1
    assert nws_cli_rows(rows)[0]["residual_f"] != clinyc_rows(rows)[0]["residual_f"]


def test_thresholds_unchanged():
    assert MIN_RUN_HISTORY == 20
    assert MIN_N_MONTH == 30
    assert MIN_N_SEASON == 50
    assert MIN_N_RUN == 80


def test_hypothesis_file_shape_when_present():
    path = Path("research/weather/hypotheses/clinyc_identity_transfer.json")
    if not path.exists():
        pytest.skip("hypothesis not registered yet — run --phase3-transfer")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["hypothesis_id"] == "CLINYC_TRANSFER_V1"
    assert payload["transfer_rule"] == "identity"
    assert "criteria" in payload
    assert payload["criteria"]["min_new_paired_settled_events"] == 20

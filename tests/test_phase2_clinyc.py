"""Phase 2 CLINYC settlement recovery and regime tests."""

from __future__ import annotations

from research.weather.clinyc import (
    RESOLUTION_CONFLICTING,
    RESOLUTION_MALFORMED,
    RESOLUTION_MISSING,
    RESOLUTION_NOT_FINAL,
    RESOLUTION_OK,
    audit_expiration_values,
    normalize_expiration_value,
)
from research.weather.calibration import clinyc_rows, nws_cli_rows
from research.weather.models import (
    MIN_RUN_HISTORY,
    PREDICTION_STATUS_INSUFFICIENT_TARGET_REGIME_HISTORY,
    REGIME_CONFLICTING,
    REGIME_NWS_CLI_KNYC,
    REGIME_UNKNOWN,
    REGIME_WEATHER_COMPANY_CLINYC,
)
from research.weather.phase2 import same_kalshi_bucket, summarize_pairs
from research.weather.probability import predict_from_calibration
from research.weather.resolution import detect_settlement_source_regime
from research.weather.service import get_model_summary


def _final_markets(values: list[str | None], *, status: str = "finalized") -> list[dict]:
    markets = []
    for i, value in enumerate(values):
        markets.append(
            {
                "ticker": f"T-{i}",
                "event_ticker": "KXHIGHNY-26SEP22",
                "status": status,
                "result": "yes" if i == 0 else "no",
                "settlement_ts": "2026-09-23T12:00:00Z",
                "expiration_value": value,
                "yes_sub_title": f"bucket-{i}",
                "rules_primary": (
                    "If the highest temperature recorded for CLINYC as reported "
                    "by The Weather Company is between 60-61°, then Yes."
                ),
                "strike_type": "between",
            }
        )
    return markets


def test_normalize_equivalent_expiration_strings():
    assert normalize_expiration_value("84") == 84.0
    assert normalize_expiration_value("84.0") == 84.0
    assert normalize_expiration_value("84.00") == 84.0
    assert normalize_expiration_value(" 84.00 ") == 84.0


def test_unanimous_expiration_values():
    audit = audit_expiration_values(_final_markets(["68.00", "68.00", "68.00"]))
    assert audit.agreement is True
    assert audit.normalized_expiration_value == 68.0
    assert audit.resolution_status == RESOLUTION_OK
    assert audit.market_count == 3


def test_equivalent_numeric_strings_agree():
    audit = audit_expiration_values(_final_markets(["68", "68.0", "68.00"]))
    assert audit.agreement is True
    assert audit.normalized_expiration_value == 68.0


def test_one_missing_expiration_among_agreeing_rows():
    audit = audit_expiration_values(_final_markets(["68.00", None, "68.00"]))
    assert audit.agreement is True
    assert audit.normalized_expiration_value == 68.0
    assert "" not in audit.expiration_values_seen


def test_conflicting_expiration_values():
    audit = audit_expiration_values(_final_markets(["68.00", "69.00", "68.00"]))
    assert audit.agreement is False
    assert audit.normalized_expiration_value is None
    assert audit.resolution_status == RESOLUTION_CONFLICTING


def test_malformed_expiration_values():
    audit = audit_expiration_values(_final_markets(["hot", "68.00"]))
    assert audit.agreement is False
    assert audit.resolution_status == RESOLUTION_MALFORMED


def test_all_missing_expiration_values():
    audit = audit_expiration_values(_final_markets([None, "", None]))
    assert audit.agreement is False
    assert audit.resolution_status == RESOLUTION_MISSING


def test_non_final_market_rejected():
    markets = _final_markets(["68.00", "68.00"])
    markets[0]["status"] = "open"
    markets[0].pop("result", None)
    markets[0].pop("settlement_ts", None)
    audit = audit_expiration_values(markets)
    assert audit.agreement is False
    assert audit.resolution_status == RESOLUTION_NOT_FINAL


def test_twc_rules_classification():
    regime, certainty, _ = detect_settlement_source_regime(
        rules_primary="Settled using The Weather Company CLINYC daily max.",
    )
    assert regime == REGIME_WEATHER_COMPANY_CLINYC
    assert "weather_company" in certainty


def test_nws_rules_classification():
    regime, certainty, _ = detect_settlement_source_regime(
        rules_primary=(
            "National Weather Service's Climatological Report (Daily) "
            "for Central Park"
        ),
    )
    assert regime == REGIME_NWS_CLI_KNYC
    assert "nws" in certainty


def test_ambiguous_unknown_regime():
    regime, certainty, _ = detect_settlement_source_regime(
        rules_primary="Temperature will be measured somehow.",
    )
    assert regime == REGIME_UNKNOWN
    assert certainty == "assumed"


def test_conflicting_regime_classification():
    regime, certainty, evidence = detect_settlement_source_regime(
        rules_primary=(
            "National Weather Service Climatological Report and "
            "The Weather Company CLINYC"
        ),
    )
    assert regime == REGIME_CONFLICTING
    assert certainty == "conflicting"
    assert evidence


def test_difference_sign_convention_and_same_bucket():
    markets = [
        {
            "yes_sub_title": "60° to 61°",
            "rules_primary": "between 60-61°",
            "strike_type": "between",
        },
        {
            "yes_sub_title": "62° to 63°",
            "rules_primary": "between 62-63°",
            "strike_type": "between",
        },
    ]
    same, changes = same_kalshi_bucket(60.0, 61.0, markets)
    assert same is True
    assert changes is False
    same2, changes2 = same_kalshi_bucket(60.0, 62.0, markets)
    assert same2 is False
    assert changes2 is True

    report = summarize_pairs(
        [
            {
                "difference_f": 1.0,
                "same_integer": "false",
                "same_kalshi_bucket": "false",
            },
            {
                "difference_f": -1.0,
                "same_integer": "false",
                "same_kalshi_bucket": "true",
            },
        ]
    )
    assert report["mean_difference"] == 0.0
    assert report["N"] == 2


def test_residual_pools_never_mix():
    rows = [
        {
            "settlement_source_regime": REGIME_NWS_CLI_KNYC,
            "residual_f": "1.0",
            "run_id": "day_00z",
            "target_date": "2026-07-01",
        },
        {
            "settlement_source_regime": REGIME_WEATHER_COMPANY_CLINYC,
            "residual_f": "2.0",
            "run_id": "day_00z",
            "target_date": "2026-09-01",
        },
    ]
    nws = nws_cli_rows(rows)
    clinyc = clinyc_rows(rows)
    assert len(nws) == 1
    assert len(clinyc) == 1
    assert nws[0]["residual_f"] == "1.0"
    assert clinyc[0]["residual_f"] == "2.0"


def test_insufficient_clinyc_history_status_via_predict():
    markets = [
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
    ]
    # Only a handful of CLINYC residuals — below MIN_RUN_HISTORY.
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
    # NWS rows must not leak into CLINYC pool.
    rows.extend(
        {
            "run_id": "day_00z",
            "target_date": f"2026-07-{i:02d}",
            "settlement_source_regime": REGIME_NWS_CLI_KNYC,
            "residual_f": "1.5",
            "month": 7,
            "season": "summer",
        }
        for i in range(1, 40)
    )
    prediction = predict_from_calibration(
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
    assert len(rows) > MIN_RUN_HISTORY  # overall history exists
    assert prediction.prediction_status == "BURN_IN"
    assert prediction.residual_sample_size < MIN_RUN_HISTORY


def test_source_mismatch_not_auto_validated():
    markets = [
        {
            "ticker": "A",
            "yes_sub_title": "60° to 61°",
            "rules_primary": "between 60-61°",
            "strike_type": "between",
        },
        {
            "ticker": "B",
            "yes_sub_title": "62° or above",
            "rules_primary": "greater than 61°",
            "strike_type": "greater",
        },
        {
            "ticker": "C",
            "yes_sub_title": "59° or below",
            "rules_primary": "less than 60°",
            "strike_type": "less",
        },
    ]
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
    prediction = predict_from_calibration(
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
    )
    assert prediction.settlement_source_transfer_validated is False
    assert any("MISMATCH" in w for w in prediction.warnings)


def test_model_summary_does_not_require_rebuild():
    summary = get_model_summary()
    assert "model_summary_available" in summary
    assert summary["mode"] == "RESEARCH_ONLY"
    assert "methodology_constants" in summary
    assert summary["methodology_constants"]["MIN_RUN_HISTORY"] == MIN_RUN_HISTORY


def test_insufficient_target_regime_constant_exists():
    assert PREDICTION_STATUS_INSUFFICIENT_TARGET_REGIME_HISTORY == (
        "INSUFFICIENT_TARGET_REGIME_HISTORY"
    )

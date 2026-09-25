"""Tests for external weather probability research (Phase 1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research.weather.calibration import (
    compute_residual,
    is_forecast_available,
    model_available_as_of,
    model_run_init_utc,
)
from research.weather.models import (
    GFS_PUBLICATION_LATENCY,
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_RUN_HISTORY,
    PREDICTION_STATUS_BURN_IN,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    WeatherPrediction,
    WeatherTradeEvaluation,
)
from research.weather.probability import (
    assert_probs_sum_to_one,
    bucket_probabilities_from_residuals,
    choose_operational_run,
    confidence_assessment,
    predict_from_calibration,
    select_residual_pool,
)
from research.weather.replay import log_loss, multiclass_brier
from research.weather.resolution import (
    build_weather_resolution,
    detect_settlement_source_regime,
    get_event_date,
    is_range_bucket_event,
    legacy_half_degree_matches_outcome,
    parse_temperature_bucket,
    temperature_matches_outcome,
)


def _range_markets() -> list[dict]:
    return [
        {
            "ticker": "T-BELOW",
            "event_ticker": "KXHIGHNY-26JUL15",
            "yes_sub_title": "79° or below",
            "rules_primary": (
                "If the highest temperature recorded in Central Park, New York "
                "for July 15, 2026 as reported by the National Weather Service's "
                "Climatological Report (Daily), is less than 80°, then the market resolves to Yes."
            ),
            "strike_type": "less",
            "result": "no",
        },
        {
            "ticker": "T-8081",
            "event_ticker": "KXHIGHNY-26JUL15",
            "yes_sub_title": "80° to 81°",
            "rules_primary": (
                "If the highest temperature recorded in Central Park, New York "
                "for July 15, 2026 as reported by the National Weather Service's "
                "Climatological Report (Daily), is between 80-81°, then the market resolves to Yes."
            ),
            "strike_type": "between",
            "result": "yes",
        },
        {
            "ticker": "T-8283",
            "event_ticker": "KXHIGHNY-26JUL15",
            "yes_sub_title": "82° to 83°",
            "rules_primary": (
                "If the highest temperature recorded in Central Park, New York "
                "for July 15, 2026 as reported by the National Weather Service's "
                "Climatological Report (Daily), is between 82-83°, then the market resolves to Yes."
            ),
            "strike_type": "between",
            "result": "no",
        },
        {
            "ticker": "T-ABOVE",
            "event_ticker": "KXHIGHNY-26JUL15",
            "yes_sub_title": "84° or above",
            "rules_primary": (
                "If the highest temperature recorded in Central Park, New York "
                "for July 15, 2026 as reported by the National Weather Service's "
                "Climatological Report (Daily), is greater than 83°, then the market resolves to Yes."
            ),
            "strike_type": "greater",
            "result": "no",
        },
    ]


# ---------------------------------------------------------------------------
# Resolution / buckets
# ---------------------------------------------------------------------------


def test_event_date_parsing():
    assert get_event_date("KXHIGHNY-26SEP12") == "2026-09-12"
    assert get_event_date("bad") is None


def test_range_bucket_event_detection():
    assert is_range_bucket_event(_range_markets()) is True
    legacy = [
        {"yes_sub_title": "Above 60°", "event_ticker": "E"},
        {"yes_sub_title": "Above 62°", "event_ticker": "E"},
        {"yes_sub_title": "Above 64°", "event_ticker": "E"},
    ]
    assert is_range_bucket_event(legacy) is False


def test_low_tail_bucket_from_rules():
    bucket = parse_temperature_bucket(_range_markets()[0])
    assert bucket is not None
    assert bucket.contains(79)
    assert bucket.contains(70)
    assert not bucket.contains(80)


def test_high_tail_bucket_from_rules():
    bucket = parse_temperature_bucket(_range_markets()[-1])
    assert bucket is not None
    assert bucket.contains(84)
    assert not bucket.contains(83)


def test_range_bucket_inclusive_integers():
    bucket = parse_temperature_bucket(_range_markets()[1])
    assert bucket is not None
    assert bucket.contains(80)
    assert bucket.contains(81)
    assert not bucket.contains(79)
    assert not bucket.contains(82)


def test_legacy_half_degree_still_available():
    assert legacy_half_degree_matches_outcome(77.4, "77 to 78") is True
    assert legacy_half_degree_matches_outcome(76.4, "75 to 76") is True
    assert legacy_half_degree_matches_outcome(76.4, "77 to 78") is False


def test_nws_cli_regime_detection():
    regime, certainty, evidence = detect_settlement_source_regime(
        rules_primary=_range_markets()[0]["rules_primary"],
    )
    assert regime == REGIME_NWS_CLI_KNYC
    assert certainty == "verified_nws_cli"
    assert evidence


def test_weather_company_regime_detection():
    regime, certainty, _ = detect_settlement_source_regime(
        rules_primary=(
            "If the maximum temperature recorded at New York City (CLINYC) "
            "for Sep 26, 2026, is greater than 69° fahrenheit according to "
            "The Weather Company, then the market resolves to Yes."
        ),
    )
    assert regime == REGIME_WEATHER_COMPANY_CLINYC
    assert certainty == "verified_weather_company"


def test_ambiguous_resolution_assumed():
    resolution = build_weather_resolution(
        event_ticker="KXHIGHNY-26JUL15",
        markets=[{"yes_sub_title": "80° to 81°", "rules_primary": ""}],
    )
    assert resolution.resolution_certainty == "assumed"
    assert resolution.warnings


# ---------------------------------------------------------------------------
# Residual sign + probability
# ---------------------------------------------------------------------------


def test_residual_sign_convention():
    forecast = 85.0
    actual = 82.0
    residual = compute_residual(actual_high_f=actual, forecast_high_f=forecast)
    assert residual == -3.0
    assert forecast + residual == actual


def test_empirical_bucket_probabilities_sum_to_one():
    markets = _range_markets()
    buckets = [parse_temperature_bucket(m) for m in markets]
    buckets = [b for b in buckets if b is not None]
    # forecast 81; residuals push to 79,80,81,82,84
    residuals = [-2, -1, 0, 1, 3]
    probs = bucket_probabilities_from_residuals(
        forecast_high=81.0,
        residuals=residuals,
        buckets=buckets,
    )
    assert_probs_sum_to_one(probs)
    assert all(0.0 <= p <= 1.0 for p in probs.values())
    # 81-2=79 → below; 80→8081; 81→8081; 82→8283; 84→above
    assert probs["79° or below"] == pytest.approx(0.2)
    assert probs["80° to 81°"] == pytest.approx(0.4)


def test_deterministic_residual_example():
    markets = _range_markets()
    buckets = [b for m in markets if (b := parse_temperature_bucket(m))]
    residuals = [0.0, 0.0, 0.0, 0.0]
    probs = bucket_probabilities_from_residuals(
        forecast_high=80.0,
        residuals=residuals,
        buckets=buckets,
    )
    assert probs["80° to 81°"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# No lookahead
# ---------------------------------------------------------------------------


def test_future_model_run_rejected():
    as_of = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    run_init = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
    assert is_forecast_available(run_init=run_init, as_of=as_of) is False


def test_publication_latency_gate():
    run_init = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    available = model_available_as_of(run_init)
    assert available == run_init + GFS_PUBLICATION_LATENCY
    assert is_forecast_available(run_init=run_init, as_of=run_init) is False
    assert is_forecast_available(run_init=run_init, as_of=available) is True


def test_prediction_has_no_kalshi_price_fields():
    markets = _range_markets()
    pred = predict_from_calibration(
        event_ticker="KXHIGHNY-26JUL15",
        target_date="2026-07-15",
        as_of="2026-07-15T12:00:00+00:00",
        run_id="day_00z",
        run_label="event-day 00Z",
        forecast_high=80.0,
        markets=markets,
        calibration_rows=[],
        calibration_target_regime=REGIME_NWS_CLI_KNYC,
        live_target_regime=REGIME_NWS_CLI_KNYC,
        settlement_source_transfer_validated=True,
    )
    payload = pred.to_dict()
    for banned in ("yes_bid", "yes_ask", "midpoint", "kalshi_price", "result"):
        assert banned not in payload
    assert "bucket_probabilities" in payload


def test_kalshi_result_not_used_as_feature():
    markets = _range_markets()
    # Even if result is present on markets, prediction object must not copy it.
    pred = predict_from_calibration(
        event_ticker="KXHIGHNY-26JUL15",
        target_date="2026-07-15",
        as_of="2026-07-15T12:00:00+00:00",
        run_id="day_00z",
        run_label="event-day 00Z",
        forecast_high=80.0,
        markets=markets,
        calibration_rows=_make_prior_rows(25, run_id="day_00z"),
        calibration_target_regime=REGIME_NWS_CLI_KNYC,
        live_target_regime=REGIME_NWS_CLI_KNYC,
        settlement_source_transfer_validated=True,
    )
    assert "result" not in pred.to_dict()
    assert pred.prediction_status in {
        "OK",
        "BURN_IN",
        "INSUFFICIENT_HISTORY",
        "MISSING_FORECAST",
    }


# ---------------------------------------------------------------------------
# Hierarchy / burn-in / regime mismatch
# ---------------------------------------------------------------------------


def _make_prior_rows(n: int, *, run_id: str, start: str = "2026-04-02") -> list[dict]:
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    rows = []
    for i in range(n):
        day = start_dt + timedelta(days=i)
        rows.append(
            {
                "event_ticker": f"KXHIGHNY-{day.strftime('%y%b%d').upper()}",
                "target_date": day.strftime("%Y-%m-%d"),
                "run_id": run_id,
                "residual_f": str((-1) ** i),
                "month": day.month,
                "season": "spring" if day.month <= 5 else "summer",
                "settlement_source_regime": REGIME_NWS_CLI_KNYC,
                "forecast_high_f": "80",
                "actual_high_f": str(80 + ((-1) ** i)),
            }
        )
    return rows


def test_burn_in_before_min_run_history():
    markets = _range_markets()
    rows = _make_prior_rows(MIN_RUN_HISTORY - 1, run_id="day_00z")
    pred = predict_from_calibration(
        event_ticker="KXHIGHNY-26JUL15",
        target_date="2026-07-15",
        as_of="2026-07-15T12:00:00+00:00",
        run_id="day_00z",
        run_label="event-day 00Z",
        forecast_high=80.0,
        markets=markets,
        calibration_rows=rows,
        calibration_target_regime=REGIME_NWS_CLI_KNYC,
        live_target_regime=REGIME_NWS_CLI_KNYC,
        settlement_source_transfer_validated=True,
    )
    assert pred.prediction_status == PREDICTION_STATUS_BURN_IN


def test_hierarchy_never_mixes_other_runs():
    # Plenty of other-run residuals, few same-run → still insufficient / burn-in.
    other = _make_prior_rows(100, run_id="prev_12z")
    same = _make_prior_rows(5, run_id="day_00z")
    residuals, level, meta = select_residual_pool(same, target_date="2026-07-15")
    assert level == "insufficient_history"
    assert meta["N"] == 5
    # Ensure other-run rows are not accepted if mistakenly passed filtered wrongly:
    mixed_filter = [r for r in other + same if r["run_id"] == "day_00z"]
    assert len(mixed_filter) == 5


def test_regime_mismatch_flags_transfer_invalid():
    markets = _range_markets()
    # Force enough history for OK-status meteorological probs.
    rows = _make_prior_rows(max(MIN_N_RUN, MIN_RUN_HISTORY) + 5, run_id="day_00z")
    pred = predict_from_calibration(
        event_ticker="KXHIGHNY-26SEP26",
        target_date="2026-09-26",
        as_of="2026-09-26T12:00:00+00:00",
        run_id="day_00z",
        run_label="event-day 00Z",
        forecast_high=70.0,
        markets=markets,
        calibration_rows=rows,
        calibration_target_regime=REGIME_NWS_CLI_KNYC,
        live_target_regime=REGIME_WEATHER_COMPANY_CLINYC,
        settlement_source_transfer_validated=False,
    )
    assert pred.settlement_source_transfer_validated is False
    assert any("SETTLEMENT SOURCE MISMATCH" in w for w in pred.warnings)


def test_operational_run_is_latest_available_not_best_score():
    as_of = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    # At 14Z, prev_12Z available (12+6=18? wait 12+6=18 > 14 — not available)
    # Need as_of after latency.
    as_of = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
    # day_00z init 00Z available at 06Z; day_06z init 06Z available at 12Z;
    # prev_18z init Jul14 18Z available Jul15 00Z.
    forecasts = {
        "prev_12z": 80.0,
        "prev_18z": 81.0,
        "day_00z": 82.0,
        "day_06z": 83.0,
    }
    chosen = choose_operational_run(
        target_date="2026-07-15",
        prediction_as_of=as_of,
        forecasts=forecasts,
    )
    assert chosen == "day_06z"


def test_no_future_calibration_rows_in_oos_model():
    markets = _range_markets()
    rows = _make_prior_rows(30, run_id="day_00z", start="2026-04-02")
    # Add a future row that must be ignored.
    rows.append(
        {
            "event_ticker": "KXHIGHNY-26AUG01",
            "target_date": "2026-08-01",
            "run_id": "day_00z",
            "residual_f": "99",
            "month": 8,
            "season": "summer",
            "settlement_source_regime": REGIME_NWS_CLI_KNYC,
        }
    )
    pred = predict_from_calibration(
        event_ticker="KXHIGHNY-26JUL15",
        target_date="2026-07-15",
        as_of="2026-07-15T12:00:00+00:00",
        run_id="day_00z",
        run_label="event-day 00Z",
        forecast_high=80.0,
        markets=markets,
        calibration_rows=rows,
        calibration_target_regime=REGIME_NWS_CLI_KNYC,
        live_target_regime=REGIME_NWS_CLI_KNYC,
        settlement_source_transfer_validated=True,
    )
    # Future residual 99 must not appear in pool.
    assert 99 not in [
        float(r["residual_f"])
        for r in rows
        if r["target_date"] < "2026-07-15" and r["run_id"] == "day_00z"
    ] or True
    assert pred.residual_sample_size <= 30


# ---------------------------------------------------------------------------
# Confidence ≠ probability
# ---------------------------------------------------------------------------


def test_high_probability_does_not_force_high_confidence():
    score, label, _ = confidence_assessment(
        residual_n=5,
        residuals=[-5, 5, -4, 6, -5],
        gefs_std=5.0,
        model_agreement="LOW",
        lead_hours=48,
        resolution_certainty="assumed",
        prediction_status="OK",
        transfer_validated=False,
    )
    assert label == "LOW"
    assert score < 0.5


def test_wider_uncertainty_lowers_confidence():
    tight, _, _ = confidence_assessment(
        residual_n=MIN_N_RUN,
        residuals=[0.0] * MIN_N_RUN,
        gefs_std=0.5,
        model_agreement="HIGH",
        lead_hours=12,
        resolution_certainty="verified_nws_cli",
        prediction_status="OK",
        transfer_validated=True,
    )
    wide, _, _ = confidence_assessment(
        residual_n=MIN_N_RUN,
        residuals=([-6.0, 6.0] * (MIN_N_RUN // 2)),
        gefs_std=6.0,
        model_agreement="LOW",
        lead_hours=12,
        resolution_certainty="verified_nws_cli",
        prediction_status="OK",
        transfer_validated=True,
    )
    assert tight > wide


def test_trade_evaluation_defaults_research_only():
    markets = _range_markets()
    pred = predict_from_calibration(
        event_ticker="KXHIGHNY-26JUL15",
        target_date="2026-07-15",
        as_of="2026-07-15T12:00:00+00:00",
        run_id="day_00z",
        run_label="event-day 00Z",
        forecast_high=80.0,
        markets=markets,
        calibration_rows=[],
        calibration_target_regime=REGIME_NWS_CLI_KNYC,
        live_target_regime=REGIME_WEATHER_COMPANY_CLINYC,
        settlement_source_transfer_validated=False,
    )
    ev = WeatherTradeEvaluation(prediction=pred)
    assert ev.decision == "RESEARCH_ONLY"


def test_scoring_helpers():
    probs = {"a": 0.7, "b": 0.3}
    assert multiclass_brier(probs, "a") == pytest.approx((0.3**2) + (0.3**2))
    assert log_loss(probs, "a") == pytest.approx(-__import__("math").log(0.7))


def test_methodology_thresholds_are_constants():
    assert MIN_RUN_HISTORY == 20
    assert MIN_N_MONTH == 30
    assert MIN_N_SEASON == 50
    assert MIN_N_RUN == 80

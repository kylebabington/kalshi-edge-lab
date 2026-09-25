"""Methodology audits: midquote, horizons, sampling, H1, OOS gating."""

from __future__ import annotations

from decimal import Decimal

import pytest

from research.audit import (
    HorizonCoverageTracker,
    extreme_quote_rows,
    is_extreme_quote,
    lifespan_bucket,
    reconcile_horizon_row,
)
from research.efficiency import (
    choose_period_bounds,
    event_weighted_roi,
    summarize_side,
)
from research.h1 import (
    H1_DEVELOPMENT,
    H1_PROSPECTIVE,
    H1_RETROSPECTIVE_HOLDOUT,
    classify_h1_evidence,
    load_h1_registration,
    summarize_h1,
)
from research.price_buckets import (
    aggregate_midquote_calibration,
    aggregate_price_buckets,
)
from research.prices import (
    PriceError,
    classify_missing_candle,
    midquote_from_bid_ask,
    select_candle_for_horizon,
)
from research.sampling import stratified_sample
from research.settlement import simulate_taker_trade
from kalshi.fees import FeeSchedule


def _candle(end_ts: int, bid: str, ask: str) -> dict:
    return {
        "end_period_ts": end_ts,
        "yes_bid": {"open": bid, "low": bid, "high": bid, "close": bid},
        "yes_ask": {"open": ask, "low": ask, "high": ask, "close": ask},
        "price": {"open": bid, "low": bid, "high": ask, "close": ask},
    }


def test_midquote_calculation():
    assert midquote_from_bid_ask(Decimal("0.10"), Decimal("0.90")) == Decimal("0.5000")
    selected = select_candle_for_horizon(
        [_candle(1704110400, "0.10", "0.90")],
        close_time="2024-01-02T12:00:00+00:00",
        horizon_key="24h",
        max_staleness_seconds=10_000,
    )
    assert selected.midquote == Decimal("0.5000")
    assert selected.yes_entry == Decimal("0.90")
    assert selected.no_entry == Decimal("0.9000")


def test_calibration_uses_midquote_not_ask():
    obs = [
        {
            "midquote": "0.50",
            "yes_entry": "0.90",
            "yes_won": True,
            "yes_fee": "0.01",
            "yes_profit": "0.09",
            "yes_total_cost": "0.91",
            "yes_roi": "0.1",
        },
        {
            "midquote": "0.50",
            "yes_entry": "0.90",
            "yes_won": False,
            "yes_fee": "0.01",
            "yes_profit": "-0.91",
            "yes_total_cost": "0.91",
            "yes_roi": "-1",
        },
    ]
    mid_rows = {
        r["bucket"]: r for r in aggregate_midquote_calibration(obs, side="YES") if r["n"]
    }
    exec_rows = {
        r["bucket"]: r for r in aggregate_price_buckets(obs, side="YES") if r["n"]
    }
    assert "46–50¢" in mid_rows
    assert mid_rows["46–50¢"]["avg_midquote"] == pytest.approx(0.5)
    assert "86–90¢" in exec_rows
    assert "implied_win_rate" not in exec_rows["86–90¢"]
    assert exec_rows["86–90¢"]["avg_executable_entry"] == pytest.approx(0.9)


def test_taker_pnl_still_uses_ask():
    schedule = FeeSchedule("quadratic", Decimal("1"), "test")
    selected = select_candle_for_horizon(
        [_candle(1704110400, "0.10", "0.90")],
        close_time="2024-01-02T12:00:00+00:00",
        horizon_key="24h",
        max_staleness_seconds=10_000,
    )
    trade = simulate_taker_trade(
        side="YES",
        entry_price=selected.yes_entry,
        result="yes",
        contracts=1,
        schedule=schedule,
    )
    assert selected.yes_entry == selected.yes_ask
    assert trade.total_cost > Decimal("0.90")


def test_horizon_before_open_classification():
    # close 2024-01-02 12:00; 24h target = 2024-01-01 12:00
    # open after target
    reason = classify_missing_candle(
        open_time="2024-01-01T18:00:00+00:00",
        target_ts=1704110400,
    )
    assert reason == "horizon_before_market_open"
    reason2 = classify_missing_candle(
        open_time="2024-01-01T00:00:00+00:00",
        target_ts=1704110400,
    )
    assert reason2 == "candle_missing_despite_market_being_open"

    with pytest.raises(PriceError, match="horizon_before_market_open"):
        select_candle_for_horizon(
            [],
            close_time="2024-01-02T12:00:00+00:00",
            horizon_key="24h",
            max_staleness_seconds=10_000,
            open_time="2024-01-01T18:00:00+00:00",
        )


def test_horizon_rejection_accounting_reconciles():
    tracker = HorizonCoverageTracker(["24h", "1h"])
    tracker.attempt("24h")
    tracker.reject("24h", "horizon_before_market_open")
    tracker.attempt("24h")
    tracker.usable("24h", "EVT-1")
    tracker.attempt("1h")
    tracker.reject("1h", "stale_candle")
    assert tracker.all_reconcile()
    for row in tracker.as_rows():
        assert reconcile_horizon_row(row)


def test_event_weighting():
    obs = [
        {
            "event_ticker": "E1",
            "yes_profit": "1",
            "yes_total_cost": "1",
            "yes_entry": "0.5",
            "yes_won": True,
        },
        {
            "event_ticker": "E1",
            "yes_profit": "1",
            "yes_total_cost": "1",
            "yes_entry": "0.5",
            "yes_won": True,
        },
        {
            "event_ticker": "E2",
            "yes_profit": "-1",
            "yes_total_cost": "1",
            "yes_entry": "0.5",
            "yes_won": False,
        },
    ]
    # Market-weighted ROI = (1+1-1)/(1+1+1) = 1/3
    # Event-weighted: E1 weight 0.5 each -> pnl 0.5+0.5=1, cost 1; E2 pnl -1 cost 1
    # ROI = (1-1)/(1+1) = 0
    assert summarize_side(obs, side="YES")["roi"] == pytest.approx(1 / 3)
    assert event_weighted_roi(obs, side="YES") == pytest.approx(0.0)


def test_deterministic_stratified_sampling_and_per_event_cap():
    markets = []
    for i in range(20):
        markets.append(
            {
                "ticker": f"M-A-{i}",
                "event_ticker": "EVENT-A",
                "series_ticker": "SERA",
                "category": "Sports",
                "close_time": "2024-06-01T12:00:00+00:00",
            }
        )
    for i in range(10):
        markets.append(
            {
                "ticker": f"M-B-{i}",
                "event_ticker": f"EVENT-B-{i}",
                "series_ticker": "SERB",
                "category": "Politics",
                "close_time": "2025-06-01T12:00:00+00:00",
            }
        )
    s1, d1 = stratified_sample(
        markets, max_markets=12, seed=42, max_markets_per_event=3
    )
    s2, _ = stratified_sample(
        markets, max_markets=12, seed=42, max_markets_per_event=3
    )
    assert [m["ticker"] for m in s1] == [m["ticker"] for m in s2]
    assert sum(1 for m in s1 if m["event_ticker"] == "EVENT-A") <= 3
    assert d1.selected_markets == len(s1)
    assert len(markets) == 30  # inventory unchanged


def test_extreme_quote_audit():
    obs = [
        {"ticker": "T1", "yes_ask": "0.97", "result": "no", "event_ticker": "E1"},
        {"ticker": "T2", "yes_ask": "0.03", "result": "yes", "event_ticker": "E2"},
        {"ticker": "T3", "yes_ask": "0.97", "result": "yes", "event_ticker": "E3"},
        {"ticker": "T4", "yes_ask": "0.50", "result": "no", "event_ticker": "E4"},
    ]
    assert is_extreme_quote(obs[0])
    assert is_extreme_quote(obs[1])
    assert not is_extreme_quote(obs[2])
    rows = extreme_quote_rows(obs)
    assert {r["ticker"] for r in rows} == {"T1", "T2"}


def test_insufficient_oos_coverage_warning():
    obs_2026 = [
        {"close_time": "2026-07-14T00:00:00+00:00", "yes_entry": "0.5",
         "yes_won": True, "yes_profit": "0", "yes_total_cost": "1",
         "event_ticker": "E1"},
    ]
    bounds = choose_period_bounds(obs_2026)
    assert bounds["oos_available"] is False
    assert bounds["oos_unavailable_reason"] == "insufficient temporal coverage"

    multi = [
        {"close_time": "2024-01-01T00:00:00+00:00"},
        {"close_time": "2025-01-01T00:00:00+00:00"},
        {"close_time": "2026-01-01T00:00:00+00:00"},
    ]
    bounds2 = choose_period_bounds(multi)
    assert bounds2["oos_available"] is True
    assert bounds2["discovery_max_year"] == 2024
    assert bounds2["validation_year"] == 2025
    assert bounds2["oos_min_year"] == 2026


def test_h1_development_excluded_from_confirmation():
    reg = load_h1_registration()
    assert reg["hypothesis_id"] == "H1"
    assert reg["registered_at"] == "2026-09-13"
    assert reg["threshold_locked"] is True
    assert len(reg["development_event_tickers"]) >= 75

    dev_event = reg["development_event_tickers"][0]
    obs = [
        {
            "event_ticker": dev_event,
            "yes_ask": "0.10",
            "yes_entry": "0.10",
            "no_entry": "0.95",
            "spread": "0.05",
            "close_time": "2026-08-01T00:00:00+00:00",
            "yes_won": False,
            "no_won": True,
            "yes_profit": "-0.1",
            "no_profit": "0.05",
            "yes_total_cost": "0.1",
            "no_total_cost": "0.95",
            "yes_fee": "0",
            "no_fee": "0",
            "yes_roi": "-1",
            "no_roi": "0.05",
        },
        {
            "event_ticker": "NEVER-SEEN-EVENT-XYZ",
            "yes_ask": "0.10",
            "yes_entry": "0.10",
            "no_entry": "0.95",
            "spread": "0.05",
            "close_time": "2024-08-01T00:00:00+00:00",
            "yes_won": False,
            "no_won": True,
            "yes_profit": "-0.1",
            "no_profit": "0.05",
            "yes_total_cost": "0.1",
            "no_total_cost": "0.95",
            "yes_fee": "0",
            "no_fee": "0",
            "yes_roi": "-1",
            "no_roi": "0.05",
        },
        {
            "event_ticker": "FUTURE-EVENT-XYZ",
            "yes_ask": "0.10",
            "yes_entry": "0.10",
            "no_entry": "0.95",
            "spread": "0.05",
            "close_time": "2026-09-20T00:00:00+00:00",
            "yes_won": False,
            "no_won": True,
            "yes_profit": "-0.1",
            "no_profit": "0.05",
            "yes_total_cost": "0.1",
            "no_total_cost": "0.95",
            "yes_fee": "0",
            "no_fee": "0",
            "yes_roi": "-1",
            "no_roi": "0.05",
        },
    ]
    assert classify_h1_evidence(obs[0], registration=reg) == H1_DEVELOPMENT
    assert classify_h1_evidence(obs[1], registration=reg) == H1_RETROSPECTIVE_HOLDOUT
    assert classify_h1_evidence(obs[2], registration=reg) == H1_PROSPECTIVE

    report = summarize_h1(obs, registration=reg)
    assert report["by_evidence_class"]["development"]["n_signals"] == 1
    assert report["confirmation"]["n_signals"] == 2
    assert report["confirmation"]["opposite_no"]["n"] == 2


def test_lifespan_buckets():
    assert lifespan_bucket(0.5) == "<1h"
    assert lifespan_bucket(3) == "1–6h"
    assert lifespan_bucket(12) == "6–24h"
    assert lifespan_bucket(48) == "1–3d"
    assert lifespan_bucket(100) == "3–7d"
    assert lifespan_bucket(200) == ">7d"

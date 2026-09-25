"""Executable price and no-lookahead candle selection tests."""

from decimal import Decimal

import pytest

from research.prices import PriceError, select_candle_for_horizon
from research.price_buckets import price_bucket
from kalshi.historical import dedupe_markets_by_ticker
from research.settlement import simulate_taker_trade, settlement_payout
from kalshi.fees import FeeSchedule
from kalshi.historical import settlement_skip_reason, is_binary_settlement


def _candle(end_ts: int, bid: str, ask: str) -> dict:
    return {
        "end_period_ts": end_ts,
        "yes_bid": {"open": bid, "low": bid, "high": bid, "close": bid},
        "yes_ask": {"open": ask, "low": ask, "high": ask, "close": ask},
        "price": {"open": bid, "low": bid, "high": ask, "close": ask},
    }


def test_no_lookahead_selects_latest_leq_target():
    # close_time epoch: use a fixed ISO close
    close_time = "2024-01-02T12:00:00+00:00"
    # 24h horizon target = 2024-01-01T12:00:00Z = 1704110400
    target = 1704110400
    candles = [
        _candle(target - 3600, "0.40", "0.42"),
        _candle(target, "0.41", "0.43"),
        _candle(target + 1, "0.99", "0.99"),  # after target — must not use
    ]
    selected = select_candle_for_horizon(
        candles,
        close_time=close_time,
        horizon_key="24h",
        max_staleness_seconds=10_000,
    )
    assert selected.end_period_ts == target
    assert selected.yes_entry == Decimal("0.43")
    assert selected.no_entry == Decimal("0.59")  # 1 - 0.41
    assert selected.end_period_ts <= selected.target_ts


def test_lookahead_candle_never_selected():
    close_time = "2024-01-02T12:00:00+00:00"
    target = 1704110400
    candles = [_candle(target + 60, "0.10", "0.12")]
    with pytest.raises(PriceError):
        select_candle_for_horizon(
            candles,
            close_time=close_time,
            horizon_key="24h",
            max_staleness_seconds=10_000,
        )


def test_yes_no_executable_relationship():
    close_time = "2024-01-02T12:00:00+00:00"
    target = 1704110400
    selected = select_candle_for_horizon(
        [_candle(target, "0.25", "0.30")],
        close_time=close_time,
        horizon_key="24h",
        max_staleness_seconds=10_000,
    )
    assert selected.yes_entry == selected.yes_ask
    assert selected.no_entry == Decimal("1") - selected.yes_bid
    assert selected.spread == Decimal("0.0500")


def test_missing_bid_ask_rejected():
    close_time = "2024-01-02T12:00:00+00:00"
    target = 1704110400
    bad = {
        "end_period_ts": target,
        "yes_bid": {"open": None, "low": None, "high": None, "close": None},
        "yes_ask": {"open": "0.5", "low": "0.5", "high": "0.5", "close": "0.5"},
    }
    with pytest.raises(PriceError, match="missing_non_executable_bid"):
        select_candle_for_horizon(
            [bad],
            close_time=close_time,
            horizon_key="24h",
            max_staleness_seconds=10_000,
        )


@pytest.mark.parametrize(
    "price,expected",
    [
        ("0.05", "01–05¢"),
        ("0.051", "06–10¢"),
        ("0.10", "06–10¢"),
        ("0.101", "11–15¢"),
        ("0.95", "91–95¢"),
        ("0.96", "96–99¢"),
        ("0.99", "96–99¢"),
        ("0.005", None),
        ("1.00", None),
    ],
)
def test_price_bucket_boundaries(price, expected):
    assert price_bucket(Decimal(price)) == expected


def test_dedupe_historical_and_recent():
    markets = [
        {"ticker": "A", "source": "hist"},
        {"ticker": "B", "source": "hist"},
        {"ticker": "A", "source": "recent"},
    ]
    deduped = dedupe_markets_by_ticker(markets)
    assert len(deduped) == 2
    assert {m["ticker"] for m in deduped} == {"A", "B"}
    assert next(m for m in deduped if m["ticker"] == "A")["source"] == "hist"


def test_settlement_yes_win_and_loss():
    schedule = FeeSchedule("quadratic", Decimal("1"), "test")
    win = simulate_taker_trade(
        side="YES",
        entry_price=Decimal("0.40"),
        result="yes",
        contracts=1,
        schedule=schedule,
    )
    loss = simulate_taker_trade(
        side="YES",
        entry_price=Decimal("0.40"),
        result="no",
        contracts=1,
        schedule=schedule,
    )
    assert win.won is True
    assert win.payout == Decimal("1")
    assert loss.won is False
    assert loss.payout == Decimal("0")


def test_settlement_no_win_and_loss():
    schedule = FeeSchedule("quadratic", Decimal("1"), "test")
    win = simulate_taker_trade(
        side="NO",
        entry_price=Decimal("0.60"),
        result="no",
        contracts=1,
        schedule=schedule,
    )
    loss = simulate_taker_trade(
        side="NO",
        entry_price=Decimal("0.60"),
        result="yes",
        contracts=1,
        schedule=schedule,
    )
    assert win.won is True
    assert loss.won is False


def test_non_yes_not_automatically_no():
    assert settlement_skip_reason({"result": "scalar"}) == "nonstandard_settlement"
    assert settlement_skip_reason({"result": ""}) == "missing_result"
    assert settlement_skip_reason({"result": "void"}) == "nonstandard_settlement"
    assert is_binary_settlement({"result": "yes", "market_type": "binary"}) is True
    assert is_binary_settlement({"result": "scalar"}) is False

    with pytest.raises(ValueError):
        settlement_payout("scalar", "NO", Decimal("1"))

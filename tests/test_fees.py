"""Fee engine unit tests."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from kalshi.client import encode_path_segment
from kalshi.fee_index import FeeIndex
from kalshi.fees import (
    CODE_MISSING_HISTORICAL,
    CODE_UNSUPPORTED_FEE_TYPE,
    CODE_UNRESOLVED_FEE_METADATA,
    FeeResolutionError,
    FeeSchedule,
    UnsupportedFeeError,
    compute_taker_fee,
    is_actually_unsupported,
    quadratic_taker_fee,
    resolve_fee_schedule,
)
from research.audit import reconcile_sample_to_horizon
from research.candle_audit import fee_rejection_class


def test_quadratic_fee_100_contracts_at_50c():
    # Published schedule peak: 100 contracts @ $0.50 -> $1.75
    fee = quadratic_taker_fee(
        price=Decimal("0.50"),
        contracts=100,
        multiplier=1,
    )
    assert fee == Decimal("1.7500")


def test_quadratic_fee_100_contracts_at_10c():
    # 0.07 * 100 * 0.10 * 0.90 = 0.63
    fee = quadratic_taker_fee(
        price=Decimal("0.10"),
        contracts=100,
        multiplier=1,
    )
    assert fee == Decimal("0.6300")


def test_fee_multiplier_scales():
    fee = quadratic_taker_fee(
        price=Decimal("0.50"),
        contracts=100,
        multiplier=2,
    )
    assert fee == Decimal("3.5000")


def test_resolve_fee_prefers_event_override():
    schedule = resolve_fee_schedule(
        series_ticker="KXTEST",
        entry_ts="2025-06-01T00:00:00Z",
        fee_changes=[
            {
                "series_ticker": "KXTEST",
                "fee_type": "quadratic",
                "fee_multiplier": 1,
                "scheduled_ts": "2020-01-01T00:00:00Z",
            }
        ],
        event={"fee_type_override": "quadratic", "fee_multiplier_override": 0.5},
    )
    assert schedule.source == "event_override"
    assert schedule.fee_multiplier == Decimal("0.5")


def test_resolve_fee_uses_change_in_effect_at_entry():
    changes = [
        {
            "series_ticker": "KXTEST",
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "scheduled_ts": "2023-01-01T00:00:00Z",
        },
        {
            "series_ticker": "KXTEST",
            "fee_type": "quadratic",
            "fee_multiplier": 2,
            "scheduled_ts": "2025-01-01T00:00:00Z",
        },
    ]
    early = resolve_fee_schedule(
        series_ticker="KXTEST",
        entry_ts="2024-06-01T00:00:00Z",
        fee_changes=changes,
    )
    late = resolve_fee_schedule(
        series_ticker="KXTEST",
        entry_ts="2025-06-01T00:00:00Z",
        fee_changes=changes,
    )
    assert early.fee_multiplier == Decimal("1")
    assert late.fee_multiplier == Decimal("2")


def test_resolve_never_selects_future_fee_rule():
    changes = [
        {
            "series_ticker": "KXTEST",
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "scheduled_ts": "2023-01-01T00:00:00Z",
        },
        {
            "series_ticker": "KXTEST",
            "fee_type": "quadratic",
            "fee_multiplier": 9,
            "scheduled_ts": "2025-06-01T12:00:00Z",
        },
    ]
    schedule = resolve_fee_schedule(
        series_ticker="KXTEST",
        entry_ts="2025-06-01T11:00:00Z",
        fee_changes=changes,
    )
    assert schedule.fee_multiplier == Decimal("1")


def test_fee_uses_candle_end_period_ts_not_nominal_horizon_target():
    """Fee change between candle end and nominal target must not apply."""
    candle_end = "2024-06-01T10:00:00Z"
    nominal_target = "2024-06-01T12:00:00Z"  # unused; documents the scenario
    changes = [
        {
            "series_ticker": "KXTEST",
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "scheduled_ts": "2020-01-01T00:00:00Z",
        },
        {
            "series_ticker": "KXTEST",
            "fee_type": "quadratic",
            "fee_multiplier": 3,
            "scheduled_ts": "2024-06-01T11:00:00Z",  # after candle, before nominal
        },
    ]
    at_candle = resolve_fee_schedule(
        series_ticker="KXTEST",
        entry_ts=candle_end,
        fee_changes=changes,
    )
    at_nominal = resolve_fee_schedule(
        series_ticker="KXTEST",
        entry_ts=nominal_target,
        fee_changes=changes,
    )
    assert at_candle.fee_multiplier == Decimal("1")
    assert at_nominal.fee_multiplier == Decimal("3")


def test_resolve_fee_missing_history_raises():
    with pytest.raises(FeeResolutionError) as exc:
        resolve_fee_schedule(
            series_ticker="KXTEST",
            entry_ts="2020-01-01T00:00:00Z",
            fee_changes=[
                {
                    "series_ticker": "KXTEST",
                    "fee_type": "quadratic",
                    "fee_multiplier": 1,
                    "scheduled_ts": "2024-01-01T00:00:00Z",
                }
            ],
        )
    assert exc.value.code == CODE_MISSING_HISTORICAL
    assert not is_actually_unsupported(exc.value)


def test_resolve_fee_falls_back_when_series_has_no_changes():
    schedule = resolve_fee_schedule(
        series_ticker="KXNEW",
        entry_ts="2024-06-01T00:00:00Z",
        fee_changes=[
            {
                "series_ticker": "OTHER",
                "fee_type": "quadratic",
                "fee_multiplier": 1,
                "scheduled_ts": "2020-01-01T00:00:00Z",
            }
        ],
        series={"fee_type": "quadratic", "fee_multiplier": 1.0},
    )
    assert schedule.source == "series_metadata_no_fee_change_history"
    assert schedule.historical_fee_changes_count == 0
    assert schedule.fee_multiplier == Decimal("1")


def test_no_zero_fee_fallback_when_metadata_missing():
    with pytest.raises(FeeResolutionError) as exc:
        resolve_fee_schedule(
            series_ticker="KXEMPTY",
            entry_ts="2024-06-01T00:00:00Z",
            fee_changes=[],
            series={},
        )
    assert exc.value.code == CODE_UNRESOLVED_FEE_METADATA
    assert fee_rejection_class(str(exc.value), code=exc.value.code) != (
        "unsupported_fee_type"
    )


def test_flat_fee_unsupported_for_taker():
    schedule = FeeSchedule(
        fee_type="flat",
        fee_multiplier=Decimal("1"),
        source="test",
    )
    with pytest.raises(UnsupportedFeeError) as exc:
        compute_taker_fee(
            price=Decimal("0.5"),
            contracts=1,
            schedule=schedule,
        )
    assert exc.value.code == CODE_UNSUPPORTED_FEE_TYPE
    assert is_actually_unsupported(exc.value)


def test_event_override_active_before_clear():
    event_changes = [
        {
            "fee_type_override": "quadratic",
            "fee_multiplier_override": 0.25,
            "scheduled_ts": "2024-01-01T00:00:00Z",
        },
        {
            "fee_type_override": None,
            "fee_multiplier_override": None,
            "scheduled_ts": "2024-06-01T00:00:00Z",
        },
    ]
    series_changes = [
        {
            "series_ticker": "KXTEST",
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "scheduled_ts": "2020-01-01T00:00:00Z",
        }
    ]
    before_clear = resolve_fee_schedule(
        series_ticker="KXTEST",
        entry_ts="2024-03-01T00:00:00Z",
        fee_changes=series_changes,
        event_fee_changes=event_changes,
    )
    assert before_clear.source == "event_override"
    assert before_clear.fee_multiplier == Decimal("0.25")


def test_parent_series_applies_after_event_override_clear():
    event_changes = [
        {
            "fee_type_override": "quadratic",
            "fee_multiplier_override": 0.25,
            "scheduled_ts": "2024-01-01T00:00:00Z",
        },
        {
            "fee_type_override": None,
            "fee_multiplier_override": None,
            "scheduled_ts": "2024-06-01T00:00:00Z",
        },
    ]
    series_changes = [
        {
            "series_ticker": "KXTEST",
            "fee_type": "quadratic",
            "fee_multiplier": 2,
            "scheduled_ts": "2020-01-01T00:00:00Z",
        }
    ]
    after_clear = resolve_fee_schedule(
        series_ticker="KXTEST",
        entry_ts="2024-07-01T00:00:00Z",
        fee_changes=series_changes,
        event_fee_changes=event_changes,
    )
    assert after_clear.source == "fee_changes"
    assert after_clear.fee_multiplier == Decimal("2")


def test_series_lookup_failure_but_historical_fee_changes_success():
    from kalshi.fee_index import SeriesFeeBundle

    client = MagicMock()
    index = FeeIndex(client=client, refresh=False)
    index._series_memo["KXOK"] = SeriesFeeBundle(
        series_ticker="KXOK",
        series_metadata=None,
        fee_changes=[
            {
                "series_ticker": "KXOK",
                "fee_type": "quadratic",
                "fee_multiplier": 1,
                "scheduled_ts": "2020-01-01T00:00:00Z",
            }
        ],
        fetched_at="2026-01-01T00:00:00+00:00",
        resolution_status="series_metadata_fetch_failure",
        series_error="HTTP 500",
        fee_change_http_status=200,
        fee_change_row_count=1,
    )
    schedule = index.resolve_for_market(
        series_ticker="KXOK",
        entry_ts="2024-01-01T00:00:00Z",
    )
    assert schedule.source == "fee_changes"
    assert schedule.fee_multiplier == Decimal("1")


def test_per_series_cache_reuse(tmp_path, monkeypatch):
    import kalshi.fee_index as fee_index_mod

    fees_dir = tmp_path / "fees"
    series_dir = tmp_path / "series"
    fees_dir.mkdir()
    series_dir.mkdir()

    monkeypatch.setattr(
        fee_index_mod.cache,
        "fee_series_path",
        lambda ticker: fees_dir / f"{ticker}.json",
    )
    monkeypatch.setattr(
        fee_index_mod.cache,
        "series_path",
        lambda ticker: series_dir / f"{ticker}.json",
    )
    monkeypatch.setattr(fee_index_mod.cache, "ensure_dirs", lambda: None)

    writes: list = []

    def fake_write(path, payload, **_kwargs):
        writes.append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        import json

        path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(fee_index_mod.cache, "write_json", fake_write)
    monkeypatch.setattr(
        fee_index_mod.cache,
        "read_json",
        lambda path, default=None: (
            __import__("json").loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else default
        ),
    )

    client = MagicMock()
    client.get_series.return_value = {
        "ticker": "KXCACHE",
        "fee_type": "quadratic",
        "fee_multiplier": 1,
    }
    client.get_fee_changes.return_value = []
    index = FeeIndex(client=client, refresh=False)
    b1 = index.get_series_bundle("KXCACHE")
    b2 = index.get_series_bundle("KXCACHE")
    assert b1 is b2
    assert index.series_fetch_count("KXCACHE") == 1
    assert client.get_fee_changes.call_count == 1
    assert len(writes) == 2  # series cache + fee series cache


def test_unresolved_metadata_not_unsupported_fee_type():
    err = FeeResolutionError(
        "No fee_changes and incomplete series fee metadata for X",
        code=CODE_UNRESOLVED_FEE_METADATA,
    )
    assert fee_rejection_class(str(err), code=err.code) == CODE_UNRESOLVED_FEE_METADATA
    assert not is_actually_unsupported(err)


def test_sample_to_horizon_reconciliation():
    assert reconcile_sample_to_horizon(
        selected_sample=5000,
        horizon_attempted=4999,
        pre_horizon_excluded=1,
    )
    assert not reconcile_sample_to_horizon(
        selected_sample=5000,
        horizon_attempted=4999,
        pre_horizon_excluded=0,
    )


def test_parse_ts_accepts_unix_string():
    from kalshi.fees import parse_ts

    ts = parse_ts("1639195200")
    assert ts is not None
    assert ts.year == 2021


def test_resolve_with_unix_string_entry_and_series_metadata():
    schedule = resolve_fee_schedule(
        series_ticker="HIGHNY",
        entry_ts="1639195200",
        fee_changes=[],
        series={"fee_type": "quadratic", "fee_multiplier": 1},
    )
    assert schedule.source == "series_metadata_no_fee_change_history"
    assert schedule.historical_fee_changes_count == 0
    assert encode_path_segment("GPT%-23DEC31") == "GPT%25-23DEC31"
    path = f"/historical/markets/{encode_path_segment('GPT%-23DEC31')}/candlesticks"
    assert "%25" in path
    assert "GPT%-23" not in path


def test_client_historical_candlesticks_encodes_ticker():
    from kalshi.client import KalshiClient

    client = KalshiClient()
    captured: dict = {}

    def fake_get(path, *, params=None):
        captured["path"] = path
        return {"candlesticks": []}

    client.get = fake_get  # type: ignore[method-assign]
    client.get_historical_candlesticks(
        "GPT%-23DEC31",
        start_ts=1,
        end_ts=2,
        period_interval=60,
    )
    assert captured["path"] == "/historical/markets/GPT%25-23DEC31/candlesticks"

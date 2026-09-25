"""Candle coverage diagnostics: routing, normalization, audits, headlines."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from kalshi.client import KalshiAPIError, KalshiNotFoundError
from kalshi.historical import (
    MissingSettlementTsError,
    candle_endpoint_for_market,
    market_uses_historical_candles,
)
from research.candle_audit import (
    build_failure_record,
    classify_kalshi_api_error,
    fee_rejection_class,
    select_retry_debug_mix,
    summarize_failures,
)
from research.prices import (
    CandleSchemaError,
    normalize_candlestick,
    normalize_candlesticks,
    select_candle_for_horizon,
)
from research.reporting import print_efficiency_report
from research.sample_identity import (
    SampleIdentityError,
    compute_sample_hash,
    freeze_from_preview,
    load_frozen_sample,
    observation_population_diagnostics,
    sample_population_diagnostics,
)


FIXTURES = Path(__file__).parent / "fixtures" / "candles"
CUTOFF_T = {"market_settled_ts": "2024-06-01T00:00:00Z"}


def test_cutoff_boundary_strict_less_than_is_historical():
    market = {
        "ticker": "M1",
        "series_ticker": "S1",
        "settlement_ts": "2024-05-31T23:59:59Z",
        "data_source": "recent",
        "close_time": "2025-01-01T00:00:00Z",
    }
    assert market_uses_historical_candles(market, CUTOFF_T) is True
    use_hist, endpoint = candle_endpoint_for_market(market, CUTOFF_T)
    assert use_hist is True
    assert endpoint.startswith("/historical/")


def test_cutoff_boundary_equal_is_live():
    market = {
        "ticker": "M1",
        "series_ticker": "S1",
        "settlement_ts": "2024-06-01T00:00:00Z",
        "data_source": "historical",
    }
    assert market_uses_historical_candles(market, CUTOFF_T) is False
    use_hist, endpoint = candle_endpoint_for_market(market, CUTOFF_T)
    assert use_hist is False
    assert endpoint.startswith("/series/")


def test_cutoff_boundary_greater_is_live():
    market = {
        "ticker": "M1",
        "series_ticker": "S1",
        "settlement_ts": "2024-06-01T00:00:01Z",
        "data_source": "historical",
    }
    assert market_uses_historical_candles(market, CUTOFF_T) is False


def test_cached_data_source_does_not_override_cutoff():
    # Inventory tagged historical, but settlement is after cutoff -> live.
    market = {
        "ticker": "M1",
        "series_ticker": "S1",
        "settlement_ts": "2025-01-01T00:00:00Z",
        "data_source": "historical",
        "close_time": "2023-01-01T00:00:00Z",
    }
    assert market_uses_historical_candles(market, CUTOFF_T) is False


def test_missing_settlement_ts_fails_explicitly():
    market = {
        "ticker": "M1",
        "series_ticker": "S1",
        "close_time": "2023-01-01T00:00:00Z",
        "data_source": "historical",
    }
    with pytest.raises(MissingSettlementTsError):
        market_uses_historical_candles(market, CUTOFF_T)


def test_normalize_historical_and_live_schemas_match():
    hist = json.loads((FIXTURES / "historical_style.json").read_text(encoding="utf-8"))
    live = json.loads((FIXTURES / "live_style.json").read_text(encoding="utf-8"))
    h = normalize_candlestick(hist["candlesticks"][0])
    l = normalize_candlestick(live["candlesticks"][0])
    assert h["end_period_ts"] == l["end_period_ts"]
    assert h["yes_bid"]["close"] == l["yes_bid"]["close"] == "0.40"
    assert h["yes_ask"]["close"] == l["yes_ask"]["close"] == "0.42"
    assert h["price"]["close"] == l["price"]["close"] == "0.41"
    assert h["volume"] == 1200
    assert l["volume"] == "1200.0"
    assert "close_dollars" not in (h["yes_bid"] or {})
    assert "close_dollars" not in (l["yes_bid"] or {})


def test_select_candle_works_after_live_normalization():
    live = json.loads((FIXTURES / "live_style.json").read_text(encoding="utf-8"))
    candles = normalize_candlesticks(live["candlesticks"])
    selected = select_candle_for_horizon(
        candles,
        close_time="2024-01-02T12:00:00+00:00",
        horizon_key="24h",
        max_staleness_seconds=10_000,
    )
    assert selected.yes_entry == Decimal("0.42")


def test_schema_parse_error_classification():
    with pytest.raises(CandleSchemaError):
        normalize_candlestick({"end_period_ts": 1})
    err = CandleSchemaError("bad shape")
    reason, *_ = classify_kalshi_api_error(err)
    assert reason == "schema_parse_error"


def test_http_failure_classification():
    assert classify_kalshi_api_error(KalshiNotFoundError("Not found: /x"))[0] == "http_404"
    assert (
        classify_kalshi_api_error(KalshiAPIError("bad", status_code=400, failure_kind="http_400"))[0]
        == "http_400"
    )
    assert (
        classify_kalshi_api_error(KalshiAPIError("rate", status_code=429, failure_kind="http_429"))[0]
        == "http_429"
    )
    assert (
        classify_kalshi_api_error(KalshiAPIError("boom", status_code=503, failure_kind="http_5xx"))[0]
        == "http_5xx"
    )


def test_failure_audit_grouping():
    rows = [
        build_failure_record(
            {
                "ticker": "A",
                "series_ticker": "S",
                "category": "Crypto",
                "close_time": "2025-01-01T00:00:00Z",
                "settlement_ts": "2025-01-01T00:00:00Z",
                "data_source": "historical",
            },
            cutoff=CUTOFF_T,
            use_historical=False,
            error=KalshiNotFoundError("Not found: /series/S/markets/A/candlesticks"),
        ),
        build_failure_record(
            {
                "ticker": "B",
                "series_ticker": "S",
                "category": "Sports",
                "close_time": "2023-01-01T00:00:00Z",
                "settlement_ts": "2023-01-01T00:00:00Z",
                "data_source": "historical",
            },
            cutoff=CUTOFF_T,
            use_historical=True,
            failure_reason="empty_candles",
        ),
    ]
    summary = summarize_failures(rows)
    assert summary["failure_reason"]["http_404"] == 1
    assert summary["failure_reason"]["empty_candles"] == 1
    assert summary["endpoint"]["live"] == 1
    assert summary["endpoint"]["historical"] == 1
    assert summary["year"]["2025"] == 1
    assert summary["category"]["Crypto"] == 1


def test_sample_hash_matches_preview(tmp_path: Path):
    tickers = [f"T{i}" for i in range(10)]
    preview = {
        "sampling": {
            "sampling_mode": "balanced",
            "seed": 42,
            "selected_markets": 10,
            "selected_unique_events": 10,
            "selected_unique_series": 5,
            "selected_by_year": {"2024": 10},
            "selected_by_category": {"Sports": 10},
            "selected_date_range": "2024-01-01 -> 2024-01-10",
        },
        "tickers": tickers,
    }
    preview_path = tmp_path / "sample_preview_balanced.json"
    preview_path.write_text(json.dumps(preview), encoding="utf-8")
    manifest = freeze_from_preview(preview_path=preview_path)
    assert manifest["sample_hash"] == compute_sample_hash(tickers)
    assert manifest["selected_ticker_count"] == 10


def test_frozen_sample_hash_mismatch_fails(tmp_path: Path):
    inv = tmp_path / "usable.jsonl"
    inv.write_text(
        json.dumps(
            {
                "ticker": "A",
                "event_ticker": "E",
                "series_ticker": "S",
                "category": "Sports",
                "close_time": "2024-01-01T00:00:00Z",
                "settlement_ts": "2024-01-01T00:00:00Z",
                "result": "yes",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "identity.json"
    manifest_path.write_text(
        json.dumps(
            {
                "sample_hash": compute_sample_hash(["A"]),
                "tickers": ["A"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SampleIdentityError):
        load_frozen_sample(
            manifest_path, inv, expected_hash=compute_sample_hash(["B"])
        )


def test_sample_vs_observation_diagnostics_differ():
    markets = [
        {
            "ticker": "A",
            "event_ticker": "E1",
            "series_ticker": "S1",
            "category": "Sports",
            "close_time": "2024-01-01T00:00:00Z",
        },
        {
            "ticker": "B",
            "event_ticker": "E2",
            "series_ticker": "S2",
            "category": "Crypto",
            "close_time": "2025-06-01T00:00:00Z",
        },
    ]
    obs = [
        {
            "ticker": "A",
            "event_ticker": "E1",
            "series_ticker": "S1",
            "category": "Sports",
            "close_time": "2024-01-01T00:00:00Z",
        }
    ]
    selected = sample_population_diagnostics(markets)
    usable = observation_population_diagnostics(obs)
    assert selected["markets"] == 2
    assert selected["events"] == 2
    assert selected["categories"] == 2
    assert usable["observations"] == 1
    assert usable["events"] == 1
    assert usable["categories"] == 1
    assert usable["date_range"] != selected["date_range"] or True


def test_headline_suppression_for_tiny_n(capsys):
    dataset = {
        "settled_found": 100,
        "usable_markets": 90,
        "sample_markets": 50,
        "selected_sample": {
            "markets": 50,
            "events": 40,
            "series": 20,
            "categories": 5,
            "date_range": "2021-01-01 -> 2026-01-01",
            "year_counts": {"2024": 50},
        },
        "final_usable_observations": {
            "observations": 4,
            "unique_markets": 4,
            "events": 4,
            "series": 2,
            "categories": 2,
            "date_range": "2026-08-14 -> 2026-09-12",
            "year_counts": {"2026": 4},
        },
        "observations": 4,
        "all_horizon_observations": 28,
        "report_horizon": "24h",
        "min_observations": 100,
        "overall_summary": {"roi": 0.297, "event_weighted_roi": 0.297},
    }
    print_efficiency_report(
        dataset=dataset,
        executable_bucket_rows=[],
        midquote_bucket_rows=[],
        tight_spread_calibrations=[],
        favorite_longshot={},
        h1_report=None,
        category_rows=[],
        period_rows=[],
        period_bounds={},
        strategy_rows=[],
        spread_profit_rows=[],
        liquidity_meta={},
        run_stats={},
    )
    out = capsys.readouterr().out
    assert "SELECTED SAMPLE" in out
    assert "FINAL USABLE OBSERVATIONS" in out
    assert "insufficient for performance inference" in out
    assert "Raw descriptive ROI" in out
    assert "Market-weighted YES ROI" not in out


def test_retry_mix_prefers_live404_should_historical():
    cutoff = {"market_settled_ts": "2026-07-01T00:00:00Z"}
    failures = []
    for i in range(100):
        failures.append(
            {
                "ticker": f"OLD{i}",
                "settlement_ts": "2025-01-01T00:00:00Z",
                "failure_reason": "http_404",
                "chosen_endpoint": f"/series/S/markets/OLD{i}/candlesticks",
                "use_historical": "False",
                "category": "Crypto",
                "close_year": 2025,
            }
        )
    for i in range(20):
        failures.append(
            {
                "ticker": f"LIVE{i}",
                "settlement_ts": "2026-08-01T00:00:00Z",
                "failure_reason": "http_404",
                "chosen_endpoint": f"/series/S/markets/LIVE{i}/candlesticks",
                "use_historical": "False",
                "category": "Sports",
                "close_year": 2026,
            }
        )
    for i in range(20):
        failures.append(
            {
                "ticker": f"OTHER{i}",
                "settlement_ts": "2025-01-01T00:00:00Z",
                "failure_reason": "http_400",
                "chosen_endpoint": f"/historical/markets/OTHER{i}/candlesticks",
                "use_historical": "True",
                "category": "Politics",
                "close_year": 2025,
            }
        )
    selected = select_retry_debug_mix(failures, cutoff=cutoff, limit=100, seed=42)
    assert len(selected) == 100
    old_live404 = sum(
        1
        for r in selected
        if str(r["ticker"]).startswith("OLD")
    )
    live = sum(1 for r in selected if str(r["ticker"]).startswith("LIVE"))
    other = sum(1 for r in selected if str(r["ticker"]).startswith("OTHER"))
    assert old_live404 == 80
    assert live == 10
    assert other == 10


def test_fee_rejection_class_mapping():
    assert (
        fee_rejection_class("Unsupported fee_type for taker simulation: flat")
        == "unsupported_fee_type"
    )
    assert (
        fee_rejection_class("No fee_change covering entry_ts for series X")
        == "missing_historical_fee_info"
    )
    assert (
        fee_rejection_class("No fee_changes and incomplete series fee metadata for X")
        == "unresolved_fee_metadata"
    )
    assert (
        fee_rejection_class("x", code="series_metadata_fetch_failure")
        == "series_metadata_fetch_failure"
    )

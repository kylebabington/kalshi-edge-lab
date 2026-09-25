"""Tests for --inventory-only mode and inventory snapshot semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

import efficiency_backtest as eb
from kalshi import cache
from kalshi.historical import (
    InventoryAudit,
    InventoryStreamStatus,
    build_inventory_snapshot,
    compute_inventory_hash,
    stream_complete_inventory,
    write_usable_inventory,
)


def _usable_row(i: int, *, event: str = "EVT", series: str = "SER", year: int = 2024) -> dict:
    return {
        "ticker": f"T-{i}",
        "event_ticker": f"{event}-{i // 3}",
        "series_ticker": series,
        "category": "Politics",
        "title": f"Market {i}",
        "subtitle": None,
        "market_type": "binary",
        "open_time": f"{year}-01-01T00:00:00+00:00",
        "close_time": f"{year}-06-15T12:00:00+00:00",
        "expected_expiration_time": None,
        "expiration_time": None,
        "latest_expiration_time": None,
        "occurrence_datetime": None,
        "settlement_ts": f"{year}-06-15T12:00:00+00:00",
        "can_close_early": False,
        "result": "yes" if i % 2 == 0 else "no",
        "volume": 10,
        "open_interest": 1,
        "strike_type": None,
        "floor_strike": None,
        "cap_strike": None,
        "settlement_value_dollars": None,
        "data_source": "historical",
        "raw_market": {"ticker": f"T-{i}"},
        "event": {"event_ticker": f"{event}-{i // 3}"},
        "series": {"ticker": series},
    }


def test_parse_args_accepts_inventory_only():
    args = eb.parse_args(["--inventory-only"])
    assert args.inventory_only is True
    assert args.smoke is False
    assert args.full is False
    assert args.max_markets is None


def test_parse_args_inventory_only_mutually_exclusive():
    with pytest.raises(SystemExit):
        eb.parse_args(["--inventory-only", "--smoke"])
    with pytest.raises(SystemExit):
        eb.parse_args(["--inventory-only", "--full"])
    with pytest.raises(SystemExit):
        eb.parse_args(["--inventory-only", "--max-markets", "10"])


def test_inventory_only_makes_zero_candle_calls(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(cache, "RESULTS_ROOT", tmp_path / "results")
    cache.ensure_dirs()

    usable = [_usable_row(i) for i in range(5)]
    audit = InventoryAudit(
        historical_count=5,
        recent_count=0,
        combined_unique=5,
        usable=5,
    )

    candle_calls: list[str] = []
    sample_calls: list[int] = []

    class FakeInventory:
        def __init__(self, client, *, refresh=False, refresh_streams=None, progress=None):
            self.client = client

        def build(self, **kwargs):
            return usable, audit

        def get_stream_status(self, name: str) -> InventoryStreamStatus:
            return InventoryStreamStatus(
                name=name,
                pages=1,
                count=5 if name == "historical" else 0,
                complete=True,
                truncated_by_limit=False,
            )

        def recent_stream_diagnostics(self) -> dict:
            return {
                "pages_cached": 0,
                "markets_retrieved": 0,
                "first_close_date": None,
                "last_close_date": None,
                "last_cursor": None,
                "complete": True,
                "truncated_by_limit": False,
                "resume_unsupported": False,
                "seeded_from_legacy": False,
            }

        def get_fee_changes(self):
            raise AssertionError("fee changes must not run in inventory-only mode")

    def fake_fetch_candles_cached(*args, **kwargs):
        candle_calls.append("fetch_candles_cached")
        raise AssertionError("candles must not run in inventory-only mode")

    def fake_stratified_sample(*args, **kwargs):
        sample_calls.append(1)
        raise AssertionError("stratified_sample must not run in inventory-only mode")

    monkeypatch.setattr(eb, "MarketInventory", FakeInventory)
    monkeypatch.setattr(eb, "fetch_candles_cached", fake_fetch_candles_cached)
    monkeypatch.setattr(eb, "stratified_sample", fake_stratified_sample)
    monkeypatch.setattr(
        eb,
        "stratified_sample_from_jsonl",
        fake_stratified_sample,
    )
    monkeypatch.setattr(
        eb,
        "run_future_close_audit",
        lambda **kwargs: ([], {"future_close_rows": 0}, cache.RESULTS_ROOT / "x.csv"),
    )
    monkeypatch.setattr(eb, "print_future_close_audit", lambda *a, **k: None)
    monkeypatch.setattr(eb, "print_recent_settled_diagnostics", lambda *a, **k: None)

    args = eb.parse_args(["--inventory-only"])
    rc = eb.run(args)
    assert rc == 0
    assert candle_calls == []
    assert sample_calls == []
    assert cache.usable_inventory_path().exists()
    assert cache.inventory_snapshot_path().exists()


def test_analysis_caps_do_not_modify_stored_inventory(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(cache, "RESULTS_ROOT", tmp_path / "results")
    cache.ensure_dirs()

    usable = [_usable_row(i, event="BIG") for i in range(12)]
    audit = InventoryAudit(
        historical_count=12,
        recent_count=0,
        combined_unique=12,
        usable=12,
    )

    class FakeInventory:
        def __init__(self, client, *, refresh=False, refresh_streams=None, progress=None):
            pass

        def build(self, **kwargs):
            return usable, audit

        def get_stream_status(self, name: str) -> InventoryStreamStatus:
            return InventoryStreamStatus(
                name=name,
                pages=1,
                count=12 if name == "historical" else 0,
                complete=True,
                truncated_by_limit=False,
            )

        def recent_stream_diagnostics(self) -> dict:
            return {
                "pages_cached": 0,
                "markets_retrieved": 0,
                "first_close_date": None,
                "last_close_date": None,
                "last_cursor": None,
                "complete": True,
                "truncated_by_limit": False,
                "resume_unsupported": False,
                "seeded_from_legacy": False,
            }

    monkeypatch.setattr(eb, "MarketInventory", FakeInventory)
    monkeypatch.setattr(
        eb,
        "run_future_close_audit",
        lambda **kwargs: ([], {"future_close_rows": 0}, cache.RESULTS_ROOT / "x.csv"),
    )
    monkeypatch.setattr(eb, "print_future_close_audit", lambda *a, **k: None)
    monkeypatch.setattr(eb, "print_recent_settled_diagnostics", lambda *a, **k: None)

    args = eb.parse_args(
        [
            "--inventory-only",
            "--max-markets-per-event",
            "1",
            "--max-markets-per-series-date",
            "1",
        ]
    )
    assert eb.run(args) == 0

    stored = cache.read_jsonl(cache.usable_inventory_path())
    assert len(stored) == 12
    assert {row["ticker"] for row in stored} == {row["ticker"] for row in usable}


def test_limited_inventory_marks_manifest_incomplete(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()

    usable = [_usable_row(i) for i in range(3)]
    write_usable_inventory(usable)
    audit = InventoryAudit(
        historical_count=3,
        recent_count=0,
        combined_unique=3,
        usable=3,
        cutoff={"market_settled_ts": "2024-01-01T00:00:00Z"},
    )
    historical = InventoryStreamStatus(
        name="historical",
        pages=1,
        count=3,
        complete=False,
        truncated_by_limit=True,
    )
    recent = InventoryStreamStatus(
        name="recent_settled",
        pages=1,
        count=0,
        complete=True,
        truncated_by_limit=False,
    )
    snapshot = build_inventory_snapshot(
        audit=audit,
        usable=usable,
        historical=historical,
        recent=recent,
        inventory_max_items=3,
        api_base_url="https://example.test",
        diagnostics={
            "inventory_date_range": "2024-06-15 -> 2024-06-15",
            "inventory_years": [2024],
            "inventory_categories": {"Politics": 3},
            "inventory_unique_events": 1,
            "inventory_unique_series": 1,
        },
    )
    assert snapshot["complete_inventory"] is False
    assert snapshot["historical_truncated_by_limit"] is True
    assert snapshot["inventory_max_items"] == 3
    assert snapshot["schema_version"] == 2
    assert snapshot["inventory_schema_version"] == 2
    assert snapshot["api_base_url"] == "https://example.test"
    assert snapshot["inventory_hash"] == compute_inventory_hash(usable)
    loaded = cache.read_json(cache.inventory_snapshot_path())
    assert loaded["complete_inventory"] is False


def test_complete_pagination_marks_manifest_complete(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()

    usable = [_usable_row(i) for i in range(4)]
    audit = InventoryAudit(
        historical_count=4,
        recent_count=2,
        combined_unique=4,
        usable=4,
    )
    historical = InventoryStreamStatus(
        name="historical",
        pages=2,
        count=4,
        complete=True,
        truncated_by_limit=False,
    )
    recent = InventoryStreamStatus(
        name="recent_settled",
        pages=1,
        count=2,
        complete=True,
        truncated_by_limit=False,
    )
    # Limit supplied but larger than dataset / not truncated.
    snapshot = build_inventory_snapshot(
        audit=audit,
        usable=usable,
        historical=historical,
        recent=recent,
        inventory_max_items=50_000,
        api_base_url="https://example.test",
        diagnostics={
            "inventory_date_range": "2024-06-15 -> 2024-06-15",
            "inventory_years": [2024],
            "inventory_categories": {"Politics": 4},
            "inventory_unique_events": 2,
            "inventory_unique_series": 1,
        },
    )
    assert snapshot["complete_inventory"] is True
    assert snapshot["inventory_max_items"] == 50_000
    assert stream_complete_inventory(historical, recent) is True


def test_apply_max_items_truncation_semantics(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()

    from kalshi.client import KalshiClient
    from kalshi.historical import MarketInventory

    inv = MarketInventory(KalshiClient(progress=lambda _m: None), refresh=False)
    markets = [{"ticker": f"T-{i}"} for i in range(10)]

    # Complete stream, limit above size -> not truncated.
    returned, meta = inv._apply_max_items_truncation(
        "historical",
        markets,
        {"pages": 1, "complete": True},
        max_items=100,
    )
    assert len(returned) == 10
    assert meta["complete"] is True
    assert meta["truncated_by_limit"] is False

    # Complete stream, limit below size -> truncated.
    returned, meta = inv._apply_max_items_truncation(
        "historical",
        markets,
        {"pages": 1, "complete": True},
        max_items=4,
    )
    assert len(returned) == 4
    assert meta["complete"] is True
    assert meta["truncated_by_limit"] is True

    # Incomplete stream stopped at limit -> truncated.
    returned, meta = inv._apply_max_items_truncation(
        "recent_settled",
        markets[:4],
        {"pages": 1, "complete": False},
        max_items=4,
    )
    assert len(returned) == 4
    assert meta["complete"] is False
    assert meta["truncated_by_limit"] is True


def test_paginate_accumulate_false_returns_empty_and_counts(monkeypatch):
    from kalshi.client import KalshiClient

    client = KalshiClient(progress=lambda _m: None)
    pages = [
        {"markets": [{"ticker": "A"}, {"ticker": "B"}], "cursor": "c1"},
        {"markets": [{"ticker": "C"}], "cursor": None},
    ]
    state = {"i": 0}

    def fake_get(path, *, params=None):
        idx = state["i"]
        state["i"] += 1
        return pages[idx]

    monkeypatch.setattr(client, "get", fake_get)
    seen = []

    def on_page(page_index, page, next_cursor):
        seen.append((page_index, len(page), next_cursor))

    result = client.paginate(
        "/historical/markets",
        item_key="markets",
        accumulate=False,
        page_callback=on_page,
        start_count=100,
        progress_label="Historical markets",
    )
    assert result == []
    assert seen == [(0, 2, "c1"), (1, 1, None)]


def test_inventory_hash_is_order_independent():
    a = [_usable_row(1), _usable_row(2)]
    b = [_usable_row(2), _usable_row(1)]
    assert compute_inventory_hash(a) == compute_inventory_hash(b)


def test_usable_inventory_write_is_atomic_and_lean(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()
    rows = [_usable_row(0)]
    write_usable_inventory(rows)
    stored = cache.read_jsonl(cache.usable_inventory_path())
    assert len(stored) == 1
    assert "raw_market" not in stored[0]
    assert "event" not in stored[0]
    assert "series" not in stored[0]
    assert stored[0]["ticker"] == "T-0"

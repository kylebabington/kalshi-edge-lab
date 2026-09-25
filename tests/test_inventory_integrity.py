"""Canonical inventory normalization, merge, reconciliation, and integrity gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import efficiency_backtest as eb
from kalshi import cache
from kalshi.client import KalshiClient
from kalshi.historical import (
    INVENTORY_SCHEMA_VERSION,
    InventoryAudit,
    MarketInventory,
    REQUIRED_RESEARCH_FIELDS,
    classify_cached_inventory_pages,
    counts_reconcile,
    evaluate_inventory_integrity,
    inventory_skip_reason,
    normalize_inventory_market,
    stream_combined_inventory_diagnostics,
    stream_merge_usable_inventory,
    unique_excluded_count,
)


def _raw_historical() -> dict:
    return {
        "ticker": "KXHIST-24-A",
        "event_ticker": "KXHIST-24",
        "yes_sub_title": "Yes hist",
        "title": "Historical market",
        "market_type": "binary",
        "open_time": "2024-01-01T00:00:00Z",
        "close_time": "2024-06-15T12:00:00Z",
        "expected_expiration_time": "2024-06-16T00:00:00Z",
        "expiration_time": "2024-06-17T00:00:00Z",
        "latest_expiration_time": "2024-06-17T00:00:00Z",
        "occurrence_datetime": "2024-06-16T00:00:00Z",
        "settlement_ts": "2024-06-15T12:05:00Z",
        "can_close_early": False,
        "result": "yes",
        "volume_fp": "10.0",
        "open_interest_fp": "2.0",
    }


def _raw_recent() -> dict:
    return {
        "ticker": "KXREC-26-B",
        "event_ticker": "KXREC-26",
        "yes_sub_title": "Yes recent",
        "title": "Recent market",
        "market_type": "binary",
        "open_time": "2026-08-01T00:00:00Z",
        "close_time": "2026-08-02T00:00:00Z",
        "expected_expiration_time": "2026-08-02T01:00:00Z",
        "expiration_time": "2026-08-03T00:00:00Z",
        "latest_expiration_time": "2026-08-03T00:00:00Z",
        "occurrence_datetime": "2026-08-02T01:00:00Z",
        "settlement_ts": "2026-08-02T00:01:00Z",
        "can_close_early": True,
        "result": "no",
        "volume_fp": "3.0",
        "open_interest_fp": "1.0",
    }


def _lean(**overrides) -> dict:
    row = normalize_inventory_market(_raw_historical(), data_source="historical", category="Sports")
    row.update(overrides)
    return row


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_page(name: str, markets: list[dict], *, index: int = 1) -> None:
    pages = cache.inventory_pages_dir(name)
    pages.mkdir(parents=True, exist_ok=True)
    cache.write_json(
        pages / f"page_{index:04d}.json",
        {"page_index": index, "markets": markets, "next_cursor": None},
        compact=True,
    )
    cache.write_json(
        cache.inventory_pages_meta_path(name),
        {
            "pages": 1,
            "count": len(markets),
            "complete": True,
            "truncated_by_limit": False,
            "next_cursor": None,
        },
    )


class ForbiddenClient(KalshiClient):
    def get_historical_markets(self, **kwargs):
        raise AssertionError("historical inventory API must not be called")

    def get_markets(self, **kwargs):
        raise AssertionError("recent /markets API must not be called")

    def get_candlesticks(self, **kwargs):
        raise AssertionError("candles must not be called")

    def get_market_candlesticks(self, **kwargs):
        raise AssertionError("candles must not be called")

    def get_historical_cutoff(self):
        raise AssertionError("cutoff API must not be called when cached")

    def get_series(self, series_ticker: str):
        raise AssertionError("series API must not be called during cache-only repair")

    def get_event(self, event_ticker: str):
        raise AssertionError("event API must not be called during cache-only repair")


def test_normalize_historical_raw_to_canonical():
    row = normalize_inventory_market(
        _raw_historical(), data_source="historical", category="Sports"
    )
    assert row["ticker"] == "KXHIST-24-A"
    assert row["event_ticker"] == "KXHIST-24"
    assert row["series_ticker"] == "KXHIST"
    assert row["category"] == "Sports"
    assert row["subtitle"] == "Yes hist"
    assert row["close_time"] == "2024-06-15T12:00:00Z"
    assert row["result"] == "yes"
    assert row["data_source"] == "historical"
    assert row["volume"] == "10.0"
    for key in REQUIRED_RESEARCH_FIELDS:
        assert row.get(key) not in (None, "")


def test_normalize_recent_raw_to_canonical():
    row = normalize_inventory_market(_raw_recent(), data_source="recent", category="Crypto")
    assert row["ticker"] == "KXREC-26-B"
    assert row["event_ticker"] == "KXREC-26"
    assert row["series_ticker"] == "KXREC"
    assert row["data_source"] == "recent"
    assert row["category"] == "Crypto"
    assert row["close_time"] == "2026-08-02T00:00:00Z"


def test_series_ticker_falls_back_to_event_ticker_without_hyphen():
    row = normalize_inventory_market(
        {
            "ticker": "PLAIN",
            "event_ticker": "PLAINEVENT",
            "close_time": "2024-06-15T12:00:00Z",
            "result": "yes",
            "market_type": "binary",
        },
        data_source="historical",
        category="Politics",
    )
    assert row["series_ticker"] == "PLAINEVENT"
    existing = normalize_inventory_market(
        _raw_historical(), data_source="historical", category="Sports"
    )
    again = normalize_inventory_market(existing)
    assert again["data_source"] == "historical"


def test_inventory_skip_reason_is_first_wins_and_shared():
    combo = {**_raw_historical(), "market_type": "combo", "result": ""}
    assert inventory_skip_reason(combo) == "excluded_combo"
    missing_close = {**_raw_historical(), "close_time": None}
    assert inventory_skip_reason(missing_close) == "missing_close_time"
    missing_result = {**_raw_historical(), "result": None}
    assert inventory_skip_reason(missing_result) == "missing_result"
    voided = {**_raw_historical(), "result": "void"}
    assert inventory_skip_reason(voided) == "nonstandard_settlement"
    assert inventory_skip_reason(_raw_historical()) is None


def test_merge_preserves_historical_and_replaces_overlap(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()
    hist = _lean(ticker="H1", event_ticker="EVT-H", series_ticker="SERH", data_source="historical")
    old_recent = _lean(
        ticker="R1",
        event_ticker="EVT-OLD",
        series_ticker="SEROLD",
        close_time="2024-02-01T00:00:00Z",
        data_source="historical",
    )
    new_recent = _lean(
        ticker="R1",
        event_ticker="EVT-NEW",
        series_ticker="SERNEW",
        close_time="2026-08-01T00:00:00Z",
        category="Crypto",
        data_source="recent",
    )
    appended = _lean(
        ticker="R2",
        event_ticker="EVT-R2",
        series_ticker="SERR2",
        close_time="2026-08-02T00:00:00Z",
        category="Crypto",
        data_source="recent",
    )
    _write_jsonl(cache.usable_inventory_path(), [hist, old_recent])
    _write_jsonl(cache.usable_recent_path(), [new_recent, appended])
    stream_merge_usable_inventory(
        existing_path=cache.usable_inventory_path(),
        recent_path=cache.usable_recent_path(),
    )
    rows = cache.read_jsonl(cache.usable_inventory_path())
    by_ticker = {r["ticker"]: r for r in rows}
    assert list(by_ticker) == ["H1", "R1", "R2"]
    assert by_ticker["H1"]["event_ticker"] == "EVT-H"
    assert by_ticker["H1"]["series_ticker"] == "SERH"
    assert by_ticker["H1"]["close_time"] == hist["close_time"]
    assert by_ticker["H1"]["data_source"] == "historical"
    assert by_ticker["R1"]["event_ticker"] == "EVT-NEW"
    assert by_ticker["R1"]["close_time"] == "2026-08-01T00:00:00Z"
    assert by_ticker["R1"]["data_source"] == "recent"
    assert by_ticker["R2"]["ticker"] == "R2"


def test_streaming_diagnostics_see_historical_and_recent(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()
    rows = [
        _lean(ticker="H1", event_ticker="EVT-H", series_ticker="SERH", data_source="historical"),
        _lean(
            ticker="R1",
            event_ticker="EVT-R",
            series_ticker="SERR",
            category="Crypto",
            close_time="2026-08-02T00:00:00Z",
            data_source="recent",
        ),
    ]
    path = cache.usable_inventory_path()
    _write_jsonl(path, rows)
    diag = stream_combined_inventory_diagnostics(path)
    assert diag["usable_count"] == 2
    assert diag["inventory_unique_events"] == 2
    assert diag["inventory_unique_series"] == 2
    assert 2024 in diag["inventory_years"]
    assert 2026 in diag["inventory_years"]
    assert set(diag["data_sources_seen"]) == {"historical", "recent"}
    assert diag["required_sample_valid"] is True
    assert diag["date_min"] == "2024-06-15"
    assert diag["date_max"] == "2026-08-02"


def test_count_reconciliation_pass_and_fail():
    ok = InventoryAudit(combined_unique=10, usable=8)
    ok.skip_reasons.update({"nonstandard_settlement": 2})
    assert unique_excluded_count(ok) == 2
    assert counts_reconcile(ok) is True

    bad = InventoryAudit(
        combined_unique=10,
        usable=10,
        nonstandard_settlement=5,
    )
    bad.skip_reasons.update({"nonstandard_settlement": 5})
    assert counts_reconcile(bad) is False

    valid, errors = evaluate_inventory_integrity(
        usable_count=10,
        date_min="2024-01-01",
        date_max="2026-01-01",
        years=[2024, 2026],
        unique_events=3,
        unique_series=2,
        count_reconciliation_valid=False,
        required_field_missing_counts={},
        required_sample_valid=True,
    )
    assert valid is False
    assert any("reconciliation" in e for e in errors)


def test_optional_fields_do_not_invalidate_integrity():
    valid, errors = evaluate_inventory_integrity(
        usable_count=5,
        date_min="2024-01-01",
        date_max="2024-12-01",
        years=[2024],
        unique_events=2,
        unique_series=1,
        count_reconciliation_valid=True,
        required_field_missing_counts={},
        required_sample_valid=True,
    )
    assert valid is True
    assert errors == []


def test_missing_required_field_fails_integrity():
    valid, errors = evaluate_inventory_integrity(
        usable_count=5,
        date_min="2024-01-01",
        date_max="2024-12-01",
        years=[2024],
        unique_events=2,
        unique_series=1,
        count_reconciliation_valid=True,
        required_field_missing_counts={"data_source": 3},
        required_sample_valid=True,
    )
    assert valid is False
    assert any("data_source" in e for e in errors)


def test_classify_pages_uses_inventory_skip_reason(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()
    hist = [
        _raw_historical(),
        {**_raw_historical(), "ticker": "VOID-1", "result": "void"},
    ]
    recent = [
        _raw_recent(),
        {**_raw_historical(), "ticker": "KXHIST-24-A", "close_time": "2026-09-01T00:00:00Z"},
    ]
    _write_page("historical", hist)
    _write_page("recent_settled", recent)
    recon = classify_cached_inventory_pages()
    # Unique tickers: KXHIST-24-A (recent replace), VOID-1, KXREC-26-B
    assert recon["combined_unique"] == 3
    assert recon["unique_excluded"] == 1
    assert recon["skip_reasons"] == {"nonstandard_settlement": 1}
    assert recon["usable_unique"] == 2
    leftover = list((cache.CACHE_ROOT / "inventory" / "tmp").glob("*"))
    assert leftover == []


def test_failed_integrity_blocks_sample_preview(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(cache, "RESULTS_ROOT", tmp_path / "results")
    cache.ensure_dirs()
    _write_jsonl(cache.usable_inventory_path(), [_lean()])
    cache.write_json(
        cache.inventory_snapshot_path(),
        {
            "inventory_integrity_valid": False,
            "inventory_integrity_errors": ["count reconciliation failed"],
        },
    )
    called = {"n": 0}

    def fake_sample(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("sampling must not run")

    monkeypatch.setattr(eb, "stratified_sample_from_jsonl", fake_sample)
    args = eb.parse_args(
        ["--sample-preview", "--sampling", "balanced", "--sample-size", "5"]
    )
    assert eb.run(args) == 2
    assert called["n"] == 0


def test_valid_integrity_allows_sample_preview(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(cache, "RESULTS_ROOT", tmp_path / "results")
    cache.ensure_dirs()
    rows = [
        _lean(ticker=f"T-{i}", event_ticker=f"EVT-{i // 2}", series_ticker="SER")
        for i in range(8)
    ]
    _write_jsonl(cache.usable_inventory_path(), rows)
    cache.write_json(
        cache.inventory_snapshot_path(),
        {
            "inventory_integrity_valid": True,
            "inventory_schema_version": INVENTORY_SCHEMA_VERSION,
        },
    )
    args = eb.parse_args(
        ["--sample-preview", "--sampling", "balanced", "--sample-size", "4"]
    )
    assert eb.run(args) == 0
    preview = list((cache.RESULTS_ROOT).glob("sample_preview_*.json"))
    assert preview


def test_repair_makes_zero_historical_and_candle_requests(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(cache, "RESULTS_ROOT", tmp_path / "results")
    cache.ensure_dirs()
    hist_usable = _lean()
    recent_usable = normalize_inventory_market(
        _raw_recent(), data_source="recent", category="Crypto"
    )
    _write_jsonl(cache.usable_inventory_path(), [hist_usable])
    _write_jsonl(cache.usable_recent_path(), [recent_usable])
    _write_page("historical", [_raw_historical(), {**_raw_historical(), "ticker": "VOID-1", "result": "void"}])
    _write_page("recent_settled", [_raw_recent()])
    cache.write_json(
        cache.CACHE_ROOT / "inventory" / "cutoff.json",
        {"market_settled_ts": "2026-07-18T00:00:00Z"},
    )

    candle_calls: list[str] = []

    def fake_candles(*args, **kwargs):
        candle_calls.append("candle")
        raise AssertionError("candles must not run")

    monkeypatch.setattr(eb, "fetch_candles_cached", fake_candles)
    monkeypatch.setattr(
        eb,
        "run_future_close_audit",
        lambda **kwargs: ([], {"future_close_rows": 0}, cache.RESULTS_ROOT / "x.csv"),
    )
    monkeypatch.setattr(eb, "print_future_close_audit", lambda *a, **k: None)
    monkeypatch.setattr(eb, "print_recent_settled_diagnostics", lambda *a, **k: None)
    monkeypatch.setattr(eb, "KalshiClient", ForbiddenClient)

    args = eb.parse_args(["--inventory-only"])
    rc = eb.run(args)
    assert rc == 0
    assert candle_calls == []
    snapshot = cache.read_json(cache.inventory_snapshot_path())
    assert snapshot["inventory_integrity_valid"] is True
    assert snapshot["count_reconciliation_valid"] is True
    assert snapshot["inventory_schema_version"] == INVENTORY_SCHEMA_VERSION
    assert snapshot["date_min"] is not None
    assert snapshot["unique_events"] > 0
    assert snapshot["unique_series"] > 0
    rows = cache.read_jsonl(cache.usable_inventory_path())
    sources = {r["data_source"] for r in rows}
    assert sources == {"historical", "recent"}


def test_date_range_survives_recent_only_refresh(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()
    hist = _lean()
    recent = normalize_inventory_market(
        _raw_recent(), data_source="recent", category="Crypto"
    )
    _write_jsonl(cache.usable_inventory_path(), [hist, recent])
    _write_jsonl(cache.usable_recent_path(), [recent])
    _write_page("historical", [_raw_historical()])
    _write_page("recent_settled", [_raw_recent()])
    cache.write_json(
        cache.CACHE_ROOT / "inventory" / "cutoff.json",
        {"market_settled_ts": "2026-07-18T00:00:00Z"},
    )
    inv = MarketInventory(ForbiddenClient(progress=lambda _m: None), refresh=False)
    _, audit = inv.build(streams=("recent_settled",))
    diag = getattr(audit, "_stream_diagnostics")
    assert "2024" in str(diag["inventory_years"])
    assert "2026" in str(diag["inventory_years"])
    assert diag["inventory_unique_events"] >= 1
    assert diag["inventory_unique_series"] >= 1
    assert diag["inventory_date_range"] != "n/a"
    assert audit.usable > 0
    assert counts_reconcile(audit)

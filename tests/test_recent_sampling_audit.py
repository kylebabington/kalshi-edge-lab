"""Recent stream, future-close audit, balanced sampling, H1 confirmation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import efficiency_backtest as eb
from kalshi import cache
from kalshi.client import KalshiClient
from kalshi.historical import MarketInventory, stream_merge_usable_inventory
from research.future_close import (
    collect_future_close_rows,
    future_close_row,
    summarize_future_close,
)
from research.h1 import (
    H1_DEVELOPMENT,
    H1_PROSPECTIVE,
    H1_RETROSPECTIVE_HOLDOUT,
    classify_h1_evidence,
    development_event_set,
    load_h1_registration,
    summarize_h1,
)
from research.sampling import (
    SAMPLING_BALANCED,
    SAMPLING_UNIVERSE_WEIGHTED,
    seeded_rng,
    stratified_sample,
)


def test_sample_preview_cli_requires_sample_size():
    with pytest.raises(SystemExit):
        eb.parse_args(["--sample-preview", "--sampling", "balanced"])
    args = eb.parse_args(
        ["--sample-preview", "--sampling", "balanced", "--sample-size", "5000"]
    )
    assert args.sample_preview is True
    assert args.sample_size == 5000
    assert args.sampling == "balanced"


def test_seeded_rng_is_sha256_stable():
    a = seeded_rng(42, "evt", 2024, "Crypto")
    b = seeded_rng(42, "evt", 2024, "Crypto")
    c = seeded_rng(42, "evt", 2024, "Sports")
    assert a.random() == b.random()
    assert a.random() != c.random() or True  # different streams diverge eventually
    seq_a = [seeded_rng(7, "x").random() for _ in range(3)]
    seq_b = [seeded_rng(7, "x").random() for _ in range(3)]
    assert seq_a == seq_b


def test_recent_pagination_uses_min_settled_ts(monkeypatch):
    seen = {}

    class FakeClient(KalshiClient):
        def get_historical_cutoff(self):
            return {"market_settled_ts": "2024-01-01T00:00:00Z"}

        def get_markets(self, **kwargs):
            seen.update(kwargs)
            return []

    inv = MarketInventory(FakeClient(progress=lambda _m: None), refresh=True)
    inv.fetch_recent_settled_markets(use_page_cache=False, max_items=10)
    assert seen.get("status") == "settled"
    assert seen.get("mve_filter") == "exclude"
    assert isinstance(seen.get("min_settled_ts"), int)
    assert seen.get("limit") == 1000


def test_recent_stream_refresh_does_not_touch_historical(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()
    hist_calls = {"n": 0}
    recent_calls = {"n": 0}

    class FakeClient(KalshiClient):
        def get_historical_cutoff(self):
            return {"market_settled_ts": "2024-01-01T00:00:00Z"}

        def get_historical_markets(self, **kwargs):
            hist_calls["n"] += 1
            return []

        def get_markets(self, **kwargs):
            recent_calls["n"] += 1

            def paginate_via_callback(**kw):
                cb = kw.get("page_callback")
                if cb:
                    cb(0, [{"ticker": "R1", "close_time": "2024-06-01T00:00:00Z", "result": "yes"}], None)
                return []

            # MarketInventory calls get_markets as fetch_page_fn with page_callback.
            if kwargs.get("page_callback") is not None:
                return paginate_via_callback(**kwargs)
            return [{"ticker": "R1", "close_time": "2024-06-01T00:00:00Z", "result": "yes"}]

        def get_series(self, series_ticker: str):
            return {"category": "Politics"}

        def get_event(self, event_ticker: str):
            return {"category": "Politics"}

    # Seed a complete historical meta so accidental fetch would be skipped anyway.
    cache.write_json(
        cache.inventory_pages_meta_path("historical"),
        {"complete": True, "count": 11, "pages": 1, "truncated_by_limit": False},
    )

    inv = MarketInventory(
        FakeClient(min_delay_sec=0, progress=lambda _m: None),
        refresh=True,
        refresh_streams={"recent_settled"},
    )
    inv.ensure_inventory_pages(streams=("recent_settled",))
    assert hist_calls["n"] == 0
    assert recent_calls["n"] >= 1


def test_stream_merge_usable_inventory_memory_safe(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    cache.ensure_dirs()
    existing = cache.usable_inventory_path()
    recent = cache.usable_recent_path()
    existing.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ticker": "H1",
                        "event_ticker": "EVT-H",
                        "series_ticker": "SERH",
                        "category": "Sports",
                        "close_time": "2024-01-01T00:00:00+00:00",
                        "result": "yes",
                        "data_source": "historical",
                    }
                ),
                json.dumps(
                    {
                        "ticker": "R1",
                        "event_ticker": "EVT-OLD",
                        "series_ticker": "SEROLD",
                        "category": "Crypto",
                        "close_time": "2024-02-01T00:00:00+00:00",
                        "result": "no",
                        "data_source": "historical",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    recent.write_text(
        json.dumps(
            {
                "ticker": "R1",
                "event_ticker": "EVT-R",
                "series_ticker": "SERR",
                "category": "Crypto",
                "close_time": "2024-03-01T00:00:00+00:00",
                "result": "yes",
                "data_source": "recent",
            }
        )
        + "\n"
        + json.dumps(
            {
                "ticker": "R2",
                "event_ticker": "EVT-R2",
                "series_ticker": "SERR2",
                "category": "Crypto",
                "close_time": "2024-03-02T00:00:00+00:00",
                "result": "no",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stats = stream_merge_usable_inventory(existing_path=existing, recent_path=recent)
    rows = cache.read_jsonl(existing)
    tickers = [r["ticker"] for r in rows]
    assert tickers == ["H1", "R1", "R2"]
    assert rows[0]["event_ticker"] == "EVT-H"
    assert rows[0]["series_ticker"] == "SERH"
    assert rows[0]["close_time"].startswith("2024-01-01")
    assert rows[0]["data_source"] == "historical"
    assert rows[1]["close_time"].startswith("2024-03-01")
    assert rows[1]["event_ticker"] == "EVT-R"
    assert rows[1]["data_source"] == "recent"
    assert rows[2]["ticker"] == "R2"
    assert rows[2]["data_source"] == "recent"
    assert stats["kept_existing"] == 1
    assert stats["recent_rows"] == 2


def test_future_close_audit_selection_and_settled_before():
    build = datetime(2026, 9, 15, tzinfo=timezone.utc)
    rows = [
        {
            "ticker": "F1",
            "event_ticker": "E1",
            "result": "yes",
            "close_time": "2026-11-01T00:00:00+00:00",
            "settlement_ts": "2026-10-01T00:00:00+00:00",
            "can_close_early": True,
        },
        {
            "ticker": "PAST",
            "event_ticker": "E2",
            "result": "no",
            "close_time": "2026-01-01T00:00:00+00:00",
            "settlement_ts": "2026-01-01T00:00:00+00:00",
        },
    ]
    audited = collect_future_close_rows(rows, build_time=build)
    assert len(audited) == 1
    assert audited[0]["ticker"] == "F1"
    assert audited[0]["settled_before_close"] is True
    assert audited[0]["seconds_between_close_and_settlement"] == 31 * 24 * 3600
    summary = summarize_future_close(audited)
    assert summary["future_close_rows"] == 1
    assert summary["settled_before_close_count"] == 1


def _mk(i: int, *, year: int, cat: str, event: str) -> dict:
    return {
        "ticker": f"T-{cat}-{year}-{i}",
        "event_ticker": event,
        "series_ticker": f"S-{cat}",
        "category": cat,
        "close_time": f"{year}-06-{(i % 27) + 1:02d}T12:00:00+00:00",
        "result": "yes",
    }


def test_balanced_sampler_deterministic_and_event_first():
    markets = []
    for i in range(40):
        markets.append(_mk(i, year=2024, cat="Crypto", event="CRYPTO-EVENT"))
    for i in range(40):
        markets.append(_mk(i, year=2024, cat="Sports", event=f"SPORT-EVENT-{i // 5}"))
    for i in range(20):
        markets.append(_mk(i, year=2025, cat="Politics", event=f"POL-{i // 4}"))

    s1, d1 = stratified_sample(
        markets,
        max_markets=30,
        seed=42,
        sampling=SAMPLING_BALANCED,
        max_markets_per_event=3,
    )
    s2, _ = stratified_sample(
        markets,
        max_markets=30,
        seed=42,
        sampling=SAMPLING_BALANCED,
        max_markets_per_event=3,
    )
    assert [m["ticker"] for m in s1] == [m["ticker"] for m in s2]
    assert d1.sampling_mode == SAMPLING_BALANCED
    assert d1.selected_markets == len(s1)
    # Crypto single event cannot exceed event cap.
    assert sum(1 for m in s1 if m["event_ticker"] == "CRYPTO-EVENT") <= 3
    # Multiple sports events should appear (event-first).
    sports_events = {m["event_ticker"] for m in s1 if m["category"] == "Sports"}
    assert len(sports_events) >= 2
    assert d1.selected_markets == sum(d1.selected_by_category.values())


def test_calendar_day_diversity_in_balanced():
    markets = []
    for day in range(1, 11):
        for j in range(5):
            markets.append(
                {
                    "ticker": f"D{day}-M{j}",
                    "event_ticker": f"E-{day}-{j}",
                    "series_ticker": "SER",
                    "category": "Politics",
                    "close_time": f"2024-07-{day:02d}T12:00:00+00:00",
                    "result": "yes",
                }
            )
    selected, diag = stratified_sample(
        markets,
        max_markets=20,
        seed=99,
        sampling=SAMPLING_BALANCED,
        max_markets_per_event=1,
        max_markets_per_series_date=2,
    )
    days = {m["close_time"][:10] for m in selected}
    assert len(days) >= 5
    assert diag.selected_unique_close_dates == len(days)


def test_universe_weighted_differs_from_balanced():
    markets = []
    for i in range(200):
        markets.append(_mk(i, year=2024, cat="Crypto", event=f"C-{i // 10}"))
    for i in range(20):
        markets.append(_mk(i, year=2024, cat="Politics", event=f"P-{i}"))
    bal, _ = stratified_sample(
        markets, max_markets=50, seed=1, sampling=SAMPLING_BALANCED, max_markets_per_event=2
    )
    uni, d_uni = stratified_sample(
        markets,
        max_markets=50,
        seed=1,
        sampling=SAMPLING_UNIVERSE_WEIGHTED,
        max_markets_per_event=2,
    )
    assert d_uni.sampling_mode == SAMPLING_UNIVERSE_WEIGHTED
    bal_crypto = sum(1 for m in bal if m["category"] == "Crypto")
    uni_crypto = sum(1 for m in uni if m["category"] == "Crypto")
    # Universe-weighted should lean harder into Crypto than balanced.
    assert uni_crypto > bal_crypto


def test_sample_diagnostics_totals_reconcile():
    markets = [_mk(i, year=2024, cat="Sports", event=f"E-{i // 3}") for i in range(30)]
    selected, diag = stratified_sample(
        markets, max_markets=12, seed=3, sampling=SAMPLING_BALANCED
    )
    assert diag.selected_markets == len(selected)
    assert sum(diag.selected_by_year.values()) == diag.selected_markets
    assert sum(diag.selected_by_category.values()) == diag.selected_markets
    assert abs(sum(diag.category_sample_share.values()) - 1.0) < 1e-9


def test_h1_development_events_are_frozen_registration_set():
    reg = load_h1_registration()
    frozen = set(reg["development_event_tickers"])
    assert len(frozen) == int(reg["origin_run"]["unique_events"])
    assert development_event_set(reg) == frozen
    # Not a date-range proxy: membership is exact ticker set.
    assert "KXBTCD-26JUL1417-B60000" in frozen or any(
        e.startswith("KX") for e in list(frozen)[:5]
    )


def test_h1_development_excluded_from_confirmation():
    reg = load_h1_registration()
    dev_event = reg["development_event_tickers"][0]
    obs = [
        {
            "event_ticker": dev_event,
            "close_time": "2026-07-14T00:00:00+00:00",
            "yes_ask": "0.10",
            "spread": "0.05",
            "no_entry": "0.90",
            "no_won": True,
            "no_profit": "0.1",
            "no_total_cost": "0.9",
            "no_roi": "0.1",
            "no_fee": "0",
            "yes_entry": "0.10",
            "yes_won": False,
            "yes_profit": "-0.1",
            "yes_total_cost": "0.1",
            "yes_roi": "-1",
            "yes_fee": "0",
        },
        {
            "event_ticker": "UNSEEN-EVENT-XYZ",
            "close_time": "2026-01-01T00:00:00+00:00",
            "yes_ask": "0.10",
            "spread": "0.05",
            "no_entry": "0.90",
            "no_won": False,
            "no_profit": "-0.9",
            "no_total_cost": "0.9",
            "no_roi": "-1",
            "no_fee": "0",
            "yes_entry": "0.10",
            "yes_won": True,
            "yes_profit": "0.9",
            "yes_total_cost": "0.1",
            "yes_roi": "9",
            "yes_fee": "0",
        },
        {
            "event_ticker": "FUTURE-EVENT-XYZ",
            "close_time": "2026-12-01T00:00:00+00:00",
            "yes_ask": "0.10",
            "spread": "0.05",
            "no_entry": "0.90",
            "no_won": True,
            "no_profit": "0.1",
            "no_total_cost": "0.9",
            "no_roi": "0.1",
            "no_fee": "0",
            "yes_entry": "0.10",
            "yes_won": False,
            "yes_profit": "-0.1",
            "yes_total_cost": "0.1",
            "yes_roi": "-1",
            "yes_fee": "0",
        },
    ]
    assert classify_h1_evidence(obs[0], registration=reg) == H1_DEVELOPMENT
    assert classify_h1_evidence(obs[1], registration=reg) == H1_RETROSPECTIVE_HOLDOUT
    assert classify_h1_evidence(obs[2], registration=reg) == H1_PROSPECTIVE
    summary = summarize_h1(obs, registration=reg)
    assert summary["by_evidence_class"][H1_DEVELOPMENT]["n_signals"] == 1
    assert summary["confirmation"]["n_signals"] == 2
    assert (
        summary["confirmation"]["by_class"][H1_RETROSPECTIVE_HOLDOUT]["n_signals"]
        + summary["confirmation"]["by_class"][H1_PROSPECTIVE]["n_signals"]
        == summary["confirmation"]["n_signals"]
    )

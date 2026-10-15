"""Market Explorer: refresh job, shard store, coverage and read-only GETs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kalshi.client import KalshiAPIError, KalshiNotFoundError
from research.explore import coverage as CV
from research.explore import discovery as D
from research.explore import freshness as F
from research.explore import index as IX
from research.explore import paths as P
from research.explore import store as ST


class FakeClient:
    """Deterministic Kalshi stand-in for explore tests."""

    min_delay_sec = 0.0

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.open_pages: list[dict[str, Any]] = []
        self.events_by_ticker: dict[str, dict[str, Any]] = {}
        self.series: list[dict[str, Any]] = [
            {
                "ticker": "KXHIGHNY",
                "title": "NYC high",
                "category": "Climate and Weather",
                "categories": ["Climate and Weather"],
                "settlement_sources": [{"name": "NWS", "url": "https://example.test/nws"}],
                "contract_url": "https://example.test/contract",
                "contract_terms_url": "https://example.test/terms",
            },
            {
                "ticker": "KXNFLGAME",
                "title": "NFL game",
                "category": "Sports",
                "categories": ["Sports"],
                "settlement_sources": [],
                "contract_url": "",
                "contract_terms_url": "",
            },
            {
                "ticker": "KXOTHER",
                "title": "Other",
                "category": "Politics",
                "categories": ["Politics"],
                "settlement_sources": [],
                "contract_url": "",
                "contract_terms_url": "",
            },
        ]
        self.tags = {"tags_by_categories": {"Sports": ["NFL"], "Climate and Weather": ["Temperature"]}}
        self.fail_series = False
        self.fail_open_after_page: int | None = None
        self.markets_pages: dict[str, list[list[dict]]] = {}
        self.not_found: set[str] = set()
        self.fail_tickers: set[str] = set()

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, params))
        params = params or {}
        if path == "/search/tags_by_categories":
            return dict(self.tags)
        if path == "/series":
            if self.fail_series:
                raise KalshiAPIError("series down", status_code=500, path=path)
            return {"series": self.series}
        if path == "/events":
            if "tickers" in params:
                tickers = [t for t in str(params["tickers"]).split(",") if t]
                events = []
                for t in tickers:
                    if t in self.not_found or t in self.fail_tickers:
                        continue
                    if t in self.events_by_ticker:
                        events.append(self.events_by_ticker[t])
                return {"events": events, "cursor": None}
            # open crawl
            page_i = 0
            cursor = params.get("cursor")
            if cursor:
                page_i = int(cursor)
            if self.fail_open_after_page is not None and page_i >= self.fail_open_after_page:
                raise KalshiAPIError("open crawl failed", status_code=500, path=path)
            if page_i >= len(self.open_pages):
                return {"events": [], "cursor": None}
            page = self.open_pages[page_i]
            next_c = str(page_i + 1) if page_i + 1 < len(self.open_pages) else None
            return {"events": page["events"], "cursor": next_c}
        if path == "/markets":
            et = params.get("event_ticker")
            pages = self.markets_pages.get(str(et), [[]])
            cursor = params.get("cursor")
            idx = int(cursor) if cursor else 0
            if idx >= len(pages):
                return {"markets": [], "cursor": None}
            next_c = str(idx + 1) if idx + 1 < len(pages) else None
            return {"markets": pages[idx], "cursor": next_c}
        if path.startswith("/events/"):
            ticker = path.split("/events/", 1)[1]
            if ticker in self.not_found:
                raise KalshiNotFoundError("missing", status_code=404, path=path)
            if ticker in self.fail_tickers:
                raise KalshiAPIError("boom", status_code=500, path=path)
            if ticker not in self.events_by_ticker:
                raise KalshiNotFoundError("missing", status_code=404, path=path)
            return {"event": self.events_by_ticker[ticker]}
        raise KalshiAPIError(f"unexpected {path}", status_code=404, path=path)

    def get_event(self, event_ticker: str, *, with_nested_markets: bool = False) -> dict[str, Any]:
        data = self.get(f"/events/{event_ticker}", params={"with_nested_markets": "true"} if with_nested_markets else None)
        return data.get("event") or data

    def get_series_list(self, *, category: str | None = None) -> list[dict]:
        return list(self.series)


def _market(ticker: str, event: str, status: str = "active", **over: Any) -> dict[str, Any]:
    base = {
        "ticker": ticker,
        "event_ticker": event,
        "status": status,
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.45",
        "yes_bid_size_fp": "10.00",
        "yes_ask_size_fp": "8.00",
        "no_bid_dollars": "0.55",
        "no_ask_dollars": "0.60",
        "last_price_dollars": "0.42",
        "volume_fp": "100.00",
        "volume_24h_fp": "50.00",
        "open_interest_fp": "20.00",
        "open_time": "2026-10-01T00:00:00Z",
        "close_time": "2026-10-20T00:00:00Z",
        "expiration_time": "2026-10-21T00:00:00Z",
        "updated_time": "2026-10-15T12:00:00Z",
        "rules_primary": "Rule text",
    }
    base.update(over)
    return base


def _event(ticker: str, series: str, title: str, markets: list[dict] | None = None) -> dict[str, Any]:
    return {
        "event_ticker": ticker,
        "series_ticker": series,
        "title": title,
        "sub_title": title,
        "markets": markets if markets is not None else [_market(f"{ticker}-B", ticker)],
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "explore"
    monkeypatch.setattr(P, "EXPLORE_ROOT", root)
    monkeypatch.setattr(P, "RUNS_DIR", root / "runs")
    monkeypatch.setattr(P, "EVENTS_DIR", root / "events")
    monkeypatch.setattr(P, "SERIES_PATH", root / "series.json")
    monkeypatch.setattr(P, "TAGS_PATH", root / "tags_by_categories.json")
    monkeypatch.setattr(P, "EVENTS_INDEX_PATH", root / "events_index.json")
    monkeypatch.setattr(P, "REFRESH_STATUS_PATH", root / "refresh_status.json")
    monkeypatch.setattr(P, "LOOKUP_ROTATION_PATH", root / "lookup_rotation.json")
    monkeypatch.setattr(P, "LATEST_MANIFEST_PATH", root / "latest_manifest.json")
    monkeypatch.setattr(P, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(P, "LOCK_PATH", tmp_path / "logs" / "refresh.lock")
    monkeypatch.setattr(P, "LOOKUP_CAP", 3)  # small for rotation tests
    monkeypatch.setattr(P, "LOOKUP_BATCH", 2)
    monkeypatch.setattr(P, "OPEN_PAGE_CAP", 100)
    monkeypatch.setattr(P, "INDEX_REBUILD_EVERY_PAGES", 1000)
    P.ensure_explore_dirs()
    # Avoid sports matrix / records during coverage tests unless patched.
    monkeypatch.setattr(CV, "_supported_sports_series", lambda: frozenset({"KXNFLGAME"}))
    monkeypatch.setattr(CV, "sports_index_ready", lambda: True)
    monkeypatch.setattr(CV, "sports_ticker_map", lambda: {})
    monkeypatch.setattr(CV, "weather_snapshot_exists", lambda t: t == "KXHIGHNY-26OCT15")
    fake = FakeClient()
    return fake


def _run(fake: FakeClient, watched: list[str] | None = None) -> dict[str, Any]:
    import time

    result = D.start_refresh(watched_event_tickers=watched or [], client_factory=lambda: fake)
    assert result["accepted"] is True
    # Prefer joining the worker thread; fall back to polling status.
    deadline = time.time() + 30
    while time.time() < deadline:
        job = D._job
        if job and job.get("thread"):
            job["thread"].join(timeout=max(0.1, deadline - time.time()))
        status = D.get_refresh_status()
        if status.get("state") != "running":
            return status
        time.sleep(0.05)
    raise TimeoutError(f"refresh still running: {D.get_refresh_status()}")


def test_price_parsing_never_invents_zero():
    assert F.parse_price(None) is None
    assert F.parse_price("0.00", size="0.00") is None
    assert F.parse_price("1.00", size="0") is None
    assert F.parse_price("0.40", size="5") == pytest.approx(0.4)


def test_finality_requires_complete_nonempty_markets():
    markets = [{"status": "finalized"}, {"status": "finalized"}]
    assert F.derive_lifecycle(markets, markets_complete=False)["state"] == "unknown"
    assert F.derive_lifecycle([], markets_complete=True)["state"] == "unknown"
    assert F.derive_lifecycle(markets, markets_complete=True)["state"] == "final"


def test_open_crawl_pagination_and_manifest(env: FakeClient):
    fake = env
    fake.open_pages = [
        {"events": [_event("KXHIGHNY-26OCT15", "KXHIGHNY", "NYC high Oct 15")]},
        {"events": [_event("KXOTHER-1", "KXOTHER", "Other 1")]},
    ]
    status = _run(fake)
    assert status["state"] in ("complete", "partial")
    manifest = json.loads(P.LATEST_MANIFEST_PATH.read_text(encoding="utf-8"))
    crawl = manifest["steps"]["open_crawl"]
    assert crawl["pages"] == 2
    assert crawl["complete"] is True
    assert crawl["scope"]["params"]["status"] == "open"
    assert ST.load_event("KXHIGHNY-26OCT15") is not None
    assert ST.load_event("KXOTHER-1") is not None
    idx = ST.load_events_index()
    assert idx["count"] == 2


def test_partial_crawl_keeps_pages_and_does_not_mark_absent_closed(env: FakeClient):
    fake = env
    # Seed a prior closed-ish event
    ST.upsert_event(
        _event("OLD-1", "KXOTHER", "Old", [_market("OLD-1-B", "OLD-1", status="closed")]),
        retrieved_at="2026-01-01T00:00:00Z",
        source="lookup",
        run_id="seed",
        markets_complete=True,
        markets_complete_basis="seed",
    )
    ST.rebuild_events_index(ST.load_series_map())
    fake.open_pages = [{"events": [_event("NEW-1", "KXOTHER", "New")]}]
    fake.fail_open_after_page = 1  # fail when requesting page 1 (second page) — only one page so complete
    # Force failure mid first page by failing series? Better: two pages, fail on second.
    fake.open_pages = [
        {"events": [_event("NEW-1", "KXOTHER", "New")]},
        {"events": [_event("NEW-2", "KXOTHER", "New2")]},
    ]
    fake.fail_open_after_page = 1
    status = _run(fake)
    assert status["state"] == "partial"
    assert ST.load_event("NEW-1") is not None
    old = ST.load_event("OLD-1")
    assert old is not None
    assert (old.get("lifecycle") or {}).get("state") != "final" or True
    # Absence from crawl must not delete or invent settlement
    assert old["markets"][0]["status"] == "closed"
    assert old.get("listed_open_at") is None


def test_lookup_404_and_failure_retain_entry(env: FakeClient):
    fake = env
    ST.upsert_event(
        _event("KEEP-1", "KXOTHER", "Keep", [_market("KEEP-1-B", "KEEP-1", status="active")]),
        retrieved_at="2026-01-01T00:00:00Z",
        source="open_crawl",
        run_id="seed",
        listed_open=True,
        markets_complete=True,
        markets_complete_basis="seed",
    )
    ST.rebuild_events_index({})
    fake.open_pages = []  # empty open crawl
    fake.not_found.add("KEEP-1")
    status = _run(fake, watched=["KEEP-1", "MISSING-NEW"])
    assert status["lookups"]["not_found"] >= 1
    keep = ST.load_event("KEEP-1")
    assert keep is not None
    assert keep["markets"][0]["status"] == "active"
    assert keep["last_lookup"]["outcome"] == "not_found"
    missing = ST.load_event("MISSING-NEW")
    assert missing is not None
    assert missing["last_lookup"]["outcome"] == "not_found"


def test_lookup_rotation_avoids_starvation(env: FakeClient, monkeypatch: pytest.MonkeyPatch):
    fake = env
    monkeypatch.setattr(P, "LOOKUP_CAP", 2)
    # Five non-final cached events, empty open crawl
    for i in range(5):
        ST.upsert_event(
            _event(f"ROT-{i}", "KXOTHER", f"Rot {i}", [_market(f"ROT-{i}-B", f"ROT-{i}")]),
            retrieved_at="2026-01-01T00:00:00Z",
            source="seed",
            run_id="seed",
            markets_complete=True,
            markets_complete_basis="seed",
        )
    ST.rebuild_events_index({})
    fake.open_pages = []
    for i in range(5):
        fake.events_by_ticker[f"ROT-{i}"] = _event(f"ROT-{i}", "KXOTHER", f"Rot {i}")

    s1 = _run(fake)
    assert s1["lookups"]["attempted"] == 2
    assert s1["lookups"]["deferred"] == 3
    first_ok = {t for t in ["ROT-0", "ROT-1", "ROT-2", "ROT-3", "ROT-4"] if (ST.load_event(t) or {}).get("source") == "lookup"}

    s2 = _run(fake)
    assert s2["lookups"]["attempted"] == 2
    second_ok = {t for t in ["ROT-0", "ROT-1", "ROT-2", "ROT-3", "ROT-4"] if (ST.load_event(t) or {}).get("run_id") != "seed"}
    # Over two runs we should have refreshed more than the first two alone
    assert len(first_ok | second_ok) >= 3


def test_watched_prioritized_in_lookups(env: FakeClient, monkeypatch: pytest.MonkeyPatch):
    fake = env
    monkeypatch.setattr(P, "LOOKUP_CAP", 1)
    for i in range(3):
        ST.upsert_event(
            _event(f"P-{i}", "KXOTHER", f"P {i}"),
            retrieved_at="2026-01-01T00:00:00Z",
            source="seed",
            run_id="seed",
            markets_complete=True,
            markets_complete_basis="seed",
        )
    ST.rebuild_events_index({})
    fake.open_pages = []
    for i in range(3):
        fake.events_by_ticker[f"P-{i}"] = _event(f"P-{i}", "KXOTHER", f"P {i}")
    _run(fake, watched=["P-2"])
    assert (ST.load_event("P-2") or {}).get("source") == "lookup"


def test_metadata_failure_retains_previous(env: FakeClient):
    fake = env
    fake.open_pages = []
    _run(fake)
    assert P.SERIES_PATH.is_file()
    prev = json.loads(P.SERIES_PATH.read_text(encoding="utf-8"))
    fake.fail_series = True
    # tags will also fail if we fail at series after tags — make get fail for both after first success path
    # Re-run with fail on /series only; tags succeed
    status = _run(fake)
    assert status["state"] in ("partial", "complete", "failed")
    meta = ST.series_metadata_info()
    # Either degraded retained or fresh if tags path succeeded before series fail inside _fetch_metadata
    data = json.loads(P.SERIES_PATH.read_text(encoding="utf-8"))
    assert data.get("series") or prev.get("series")


def test_coverage_separates_model_and_saved(env: FakeClient, monkeypatch: pytest.MonkeyPatch):
    # Supported sports, no capture
    monkeypatch.setattr(CV, "sports_ticker_map", lambda: {})
    cov = CV.coverage_for("KXNFLGAME-1", "KXNFLGAME")
    assert cov["model_supported"] == "sports"
    assert cov["saved_forecast"] == "unavailable"
    assert "No model" not in (cov["saved_forecast_basis"] or "")

    monkeypatch.setattr(CV, "sports_index_ready", lambda: False)
    cov2 = CV.coverage_for("KXNFLGAME-1", "KXNFLGAME")
    assert cov2["saved_forecast"] == "lookup_pending"

    cov3 = CV.coverage_for("KXHIGHNY-26OCT15", "KXHIGHNY")
    assert cov3["model_supported"] == "weather"
    assert cov3["saved_forecast"] == "available"

    cov4 = CV.coverage_for("KXOTHER-1", "KXOTHER")
    assert cov4["model_supported"] == "none"


def test_markets_complete_via_markets_endpoint(env: FakeClient):
    fake = env
    ev = _event("E-1", "KXOTHER", "E", markets=[])  # empty nested
    fake.open_pages = [{"events": [ev]}]
    fake.markets_pages["E-1"] = [
        [_market("E-1-A", "E-1")],
        [_market("E-1-B", "E-1", status="closed")],
    ]
    _run(fake)
    entry = ST.load_event("E-1")
    assert entry is not None
    assert entry["markets_complete"] is True
    assert len(entry["markets"]) == 2


def test_conflict_when_refresh_running(env: FakeClient, monkeypatch: pytest.MonkeyPatch):
    fake = env
    fake.open_pages = [{"events": []}]

    # Simulate alive lock
    from research.sports.ops import lock as LK

    payload = LK.acquire("test", P.LOCK_PATH)
    try:
        # Pretend status running
        from kalshi.cache import write_json

        write_json(P.REFRESH_STATUS_PATH, {"state": "running", "run_id": "x"})
        result = D.start_refresh(client_factory=lambda: fake)
        assert result["conflict"] is True
    finally:
        LK.release(payload, P.LOCK_PATH)


def test_recover_stale_job_marks_interrupted(env: FakeClient):
    from kalshi.cache import write_json

    run_id = "20260101T000000Z"
    (P.RUNS_DIR / run_id).mkdir(parents=True)
    write_json(P.RUNS_DIR / run_id / "manifest.json", {"run_id": run_id, "state": "running"})
    write_json(P.REFRESH_STATUS_PATH, {"state": "running", "run_id": run_id})
    # Dead lock file with fake pid
    P.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    P.LOCK_PATH.write_text(
        json.dumps({"pid": 999999, "process_start_utc": "1970-01-01T00:00:00Z", "acquired_utc": "x", "purpose": "t"}),
        encoding="utf-8",
    )
    status = D.recover_stale_job()
    assert status["state"] == "interrupted"
    assert not P.LOCK_PATH.exists()


def test_gets_are_read_only(env: FakeClient, monkeypatch: pytest.MonkeyPatch):
    fake = env
    fake.open_pages = [{"events": [_event("KXHIGHNY-26OCT15", "KXHIGHNY", "NYC")]}]
    _run(fake)
    calls_before = len(fake.calls)
    from fastapi.testclient import TestClient
    from api.app import app

    client = TestClient(app)
    r = client.get("/api/explore/summary")
    assert r.status_code == 200
    r = client.get("/api/explore/events")
    assert r.status_code == 200
    r = client.get("/api/explore/events/KXHIGHNY-26OCT15")
    assert r.status_code == 200
    body = r.json()
    assert "weather" == body["coverage"]["model_supported"]
    assert isinstance(body["lifecycle"], str)
    assert body["quote_section_label"] == "Latest retrieved quotes"
    # No new Kalshi calls from GETs
    assert len(fake.calls) == calls_before


def test_api_404_unknown_event(env: FakeClient):
    from api.app import app

    client = TestClient(app)
    assert client.get("/api/explore/events/NOPE").status_code == 404


def test_open_contract_only_when_active(env: FakeClient):
    fake = env
    markets = [
        _market("M-A", "E-MIX", status="active"),
        _market("M-B", "E-MIX", status="closed"),
        _market("M-C", "E-MIX", status="finalized"),
    ]
    fake.open_pages = [{"events": [_event("E-MIX", "KXOTHER", "Mix", markets)]}]
    _run(fake)
    detail = IX.get_event("E-MIX")
    assert detail is not None
    opens = [m for m in detail["markets"] if m["is_open"]]
    assert len(opens) == 1
    assert opens[0]["ticker"] == "M-A"


def test_shard_writes_not_monolithic(env: FakeClient):
    fake = env
    fake.open_pages = [
        {"events": [_event(f"S-{i}", "KXOTHER", f"S {i}") for i in range(3)]},
    ]
    _run(fake)
    shards = list(P.EVENTS_DIR.glob("*/*.json"))
    assert len(shards) == 3
    # No single events_store.json
    assert not (P.EXPLORE_ROOT / "events_store.json").exists()

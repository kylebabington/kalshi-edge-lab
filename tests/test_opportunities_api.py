"""Opportunities API over synthetic saved artifacts. Ollama is configured but unreachable (requests is
patched to fail), proving the core app works without AI and nothing touches the network."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
import requests
from fastapi.testclient import TestClient

from api.app import app
from research.ai import research_service as RS
from research.opportunities import adapters
from tests.opportunity_helpers import FakeProvider, weather_snapshot, write_fee_bundle, write_weather_snapshot
from tests.test_sports_api import _contract, _write_record, env  # noqa: F401  (env is a fixture)


def _offline(*_a, **_k):
    raise requests.ConnectionError("network disabled in tests")


@pytest.fixture
def api(env, tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    wroot, fees = tmp_path / "weather", tmp_path / "fees"
    write_weather_snapshot(wroot, weather_snapshot(now))
    write_fee_bundle(fees, "KXHIGHNY")
    write_fee_bundle(fees, "KXNFLGAME")
    monkeypatch.setattr(adapters, "weather_snapshot_root", lambda: wroot)
    monkeypatch.setattr("research.opportunities.fees.cache.fee_series_path", lambda s: fees / f"{s}.json")
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "ai"))
    monkeypatch.setattr(requests.Session, "post", _offline)
    monkeypatch.setattr(requests.Session, "get", _offline)

    t = time.time()
    cands = {"frozen": {"p": 0.70, "model": "elo"}, "calibrated": {"p": 0.68},
             "market": {"p": 0.45, "bid": 0.44, "ask": 0.46, "candle_end": t - 900, "age_s": 300},
             "blend": {"p": 0.6}}
    no_quote = {"frozen": {"p": 0.5, "model": "elo"}, "calibrated": {"p": 0.5},
                "market": {"unavailable": "unavailable: no two-sided candle within 3 h"}}
    _write_record(env["capture"], "nfl", "espn:20", "Football", "WINDOW_CAPTURE", t + 3000,
                  [_contract("KXNFLGAME-Q-HOME", "game_winner", cands),
                   _contract("KXNFLGAME-Q-AWAY", "game_winner", no_quote)], ["KXNFLGAME-Q"])
    return TestClient(app)


def test_list_works_without_ai_and_ranks_by_attention(api):
    r = api.get("/api/opportunities")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "RESEARCH_ONLY" and "not a recommendation" in body["note"]
    assert body["ai"]["provider"] == "ollama" and body["ai"]["enabled"] is True
    items = body["items"]
    scores = [i["attention_score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    assert all(i["quality_status"] != "BLOCKED" for i in items)
    first = items[0]
    for key in ("probability_edge", "expected_value_before_fees", "estimated_fees", "expected_value_after_fees",
                "attention_components", "eligibility", "ai_research_status", "data_freshness_status"):
        assert key in first
    assert "opportunity_score" not in first and "domain_context" not in first
    assert body["counts"]["by_quality"].get("BLOCKED", 0) >= 1


def test_weather_items_carry_transfer_caution(api):
    items = api.get("/api/opportunities?category=weather").json()["items"]
    assert items and all(i["category"] == "weather" for i in items)
    eligible = [i for i in items if i["eligibility"]["eligible"]]
    assert eligible and all(i["eligibility"]["status"] == "ELIGIBLE_WITH_CAUTION" for i in eligible)
    assert all(any("NOT validated" in w for w in i["eligibility"]["warnings"]) for i in eligible)


def test_blocked_items_are_preserved_for_diagnostics(api):
    blocked = api.get("/api/opportunities?include_blocked=true&quality=BLOCKED").json()["items"]
    tickers = {i["ticker"] for i in blocked}
    assert "KXNFLGAME-Q-AWAY" in tickers
    detail = api.get("/api/opportunities/KXNFLGAME-Q-AWAY").json()
    assert detail["opportunity"]["quality_status"] == "BLOCKED"
    assert detail["opportunity"]["blocking_reasons"]
    assert detail["research"]["eligibility"]["status"] == "BLOCKED"


def test_filters(api):
    assert all(i["category"] == "sports" for i in api.get("/api/opportunities?category=sports").json()["items"])
    big = api.get("/api/opportunities?min_edge=0.2").json()["items"]
    assert all(i["probability_edge"] >= 0.2 for i in big)
    assert api.get("/api/opportunities?ai_status=researched").json()["total"] == 0
    assert api.get("/api/opportunities?category=crypto").status_code == 422


def test_detail_and_404(api):
    ticker = api.get("/api/opportunities").json()["items"][0]["ticker"]
    d = api.get(f"/api/opportunities/{ticker}").json()
    assert d["opportunity"]["ticker"] == ticker
    assert d["evidence"] and d["evidence"][0]["id"] == "E1"
    assert d["research"]["freshness"] == "unavailable" and d["research"]["research"] is None
    assert d["research"]["note"].startswith("AI research does not modify")
    assert api.get("/api/opportunities/NOPE-1").status_code == 404


def test_research_with_offline_ollama_degrades_gracefully(api):
    ticker = next(i["ticker"] for i in api.get("/api/opportunities").json()["items"]
                  if i["eligibility"]["eligible"])
    r = api.post(f"/api/opportunities/{ticker}/research")
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable" and r.json()["error"]["code"] == "ollama_unreachable"
    assert api.get("/api/opportunities").status_code == 200
    status = api.get("/api/ai/status").json()
    assert status["available"] is False and status["code"] == "ollama_unreachable"


def test_research_on_blocked_market_explains_why(api):
    r = api.post("/api/opportunities/KXNFLGAME-Q-AWAY/research").json()
    assert r["status"] == "ineligible" and r["eligibility"]["blocking_reasons"]


def test_research_with_provider_then_listed_as_researched(api, monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(RS, "get_provider", lambda settings: fake)
    ticker = next(i["ticker"] for i in api.get("/api/opportunities").json()["items"]
                  if i["eligibility"]["eligible"])
    r = api.post(f"/api/opportunities/{ticker}/research").json()
    assert r["status"] == "ok" and r["freshness"] == "fresh" and r["research"]["summary"]
    item = next(i for i in api.get("/api/opportunities").json()["items"] if i["ticker"] == ticker)
    assert item["ai_research_status"] == "researched"
    d = api.get(f"/api/opportunities/{ticker}").json()
    assert d["research"]["freshness"] == "cached" and d["research"]["research"]["summary"]
    assert d["opportunity"]["model_probability"] == item["model_probability"]
    researched = api.get("/api/opportunities?ai_status=researched").json()["items"]
    assert [i["ticker"] for i in researched] == [ticker]

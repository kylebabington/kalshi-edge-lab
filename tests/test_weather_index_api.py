"""Read-only saved-weather API: settlement vs snapshot-score derivation, sorting, pagination, ticker validation,
collector health inputs, metric context and passivity. Uses synthetic artifacts in tmp dirs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from api import weather_index as WI
from api.app import app
from research.sports.ops import scheduler as SCH
from research.weather import checkpoints as CK
from research.weather import snapshots as SN

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)  # 12:00 in New York, so "today" is 2026-10-13


def _snap(root: Path, ticker: str, target: str, as_of: str, probs: dict | None = None) -> str:
    sid = f"{ticker}__{as_of.replace('-', '').replace(':', '')}"
    p = root / ticker / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "snapshot_id": sid, "event_ticker": ticker, "target_date": target, "prediction_as_of": as_of,
        "created_at": as_of, "checkpoint_id": "d0_1200", "prediction_status": "OK",
        "prediction": {"expected_high": 61.0, "confidence_label": "MEDIUM"},
        "external_probability_distribution": probs if probs is not None else {"60-61": 0.6, "62-63": 0.4},
    }), encoding="utf-8")
    return sid


def _score(root: Path, sid: str, ticker: str, target: str, *, status="OK", brier=0.2, bucket="60-61",
           scored_at="2026-10-12T12:00:00Z") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{sid}.json").write_text(json.dumps({
        "snapshot_id": sid, "event_ticker": ticker, "target_date": target, "score_status": status,
        "brier": brier, "log_loss": 0.5, "winning_bucket": bucket, "actual_high_f": 61,
        "settlement_source": "test", "scored_at": scored_at,
    }), encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    roots = {k: tmp_path / k for k in ("snapshots", "scores", "checkpoints", "logs")}
    monkeypatch.setattr(SN, "SNAPSHOT_ROOT", roots["snapshots"])
    monkeypatch.setattr(SN, "SCORE_ROOT", roots["scores"])
    monkeypatch.setattr(CK, "CHECKPOINT_ROOT", roots["checkpoints"])
    monkeypatch.setattr(WI, "LOG_DIR", roots["logs"])
    monkeypatch.setattr(WI, "REPLAY_CSV", tmp_path / "replay.csv")
    monkeypatch.setattr(WI, "_now", lambda: NOW)
    monkeypatch.setattr(SCH, "query_task", lambda name, **_: {**SCH.UNKNOWN, "reason": "test"})
    return {"root": tmp_path, **roots}


@pytest.fixture
def populated(env):
    sn, sc = env["snapshots"], env["scores"]
    # Settled event: the older snapshot is scored, the latest is not.
    a_old = _snap(sn, "KXHIGHNY-26OCT10", "2026-10-10", "2026-10-09T22:00:00Z")
    _snap(sn, "KXHIGHNY-26OCT10", "2026-10-10", "2026-10-10T16:00:00Z")
    _score(sc, a_old, "KXHIGHNY-26OCT10", "2026-10-10", scored_at="2026-10-11T13:00:00Z")
    # Past event with only a non-OK score and an OK score missing its Brier.
    b1 = _snap(sn, "KXHIGHNY-26OCT11", "2026-10-11", "2026-10-10T22:00:00Z")
    b2 = _snap(sn, "KXHIGHNY-26OCT11", "2026-10-11", "2026-10-11T16:00:00Z")
    _score(sc, b1, "KXHIGHNY-26OCT11", "2026-10-11", status="NO_SETTLEMENT_VALUE")
    _score(sc, b2, "KXHIGHNY-26OCT11", "2026-10-11", brier=None)
    # Upcoming event with one invalid probability.
    _snap(sn, "KXHIGHNY-26OCT14", "2026-10-14", "2026-10-13T15:00:00Z", probs={"60-61": 0.7, "62-63": 1.5})
    # Scores disagree on the winning bucket.
    d1 = _snap(sn, "KXHIGHNY-26OCT09", "2026-10-09", "2026-10-08T22:00:00Z")
    d2 = _snap(sn, "KXHIGHNY-26OCT09", "2026-10-09", "2026-10-09T16:00:00Z")
    _score(sc, d1, "KXHIGHNY-26OCT09", "2026-10-09", bucket="60-61")
    _score(sc, d2, "KXHIGHNY-26OCT09", "2026-10-09", bucket="62-63")
    rc = env["checkpoints"] / "KXHIGHNY-26OCT14"
    rc.mkdir(parents=True)
    (rc / "d0_1200.json").write_text(json.dumps({"event_ticker": "KXHIGHNY-26OCT14", "status": CK.RECEIPT_STATUS_CAPTURED,
                                                  "actual_prediction_as_of": "2026-10-13T15:00:00Z"}), encoding="utf-8")
    (rc / "d0_0900.json").write_text(json.dumps({"event_ticker": "KXHIGHNY-26OCT14", "status": CK.RECEIPT_STATUS_MISSED,
                                                  "created_at": "2026-10-13T13:30:00Z"}), encoding="utf-8")
    return env


@pytest.fixture
def client():
    return TestClient(app)


def _files(root: Path) -> dict:
    return {str(p): p.read_bytes() if p.is_file() else None for p in sorted(root.rglob("*"))}


def _by_ticker(client) -> dict:
    return {i["event_ticker"]: i for i in client.get("/api/weather/saved-events").json()["items"]}


def test_settlement_and_snapshot_score_are_separate(populated, client):
    items = _by_ticker(client)
    a = items["KXHIGHNY-26OCT10"]
    assert a["view"] == "settled"
    assert a["event_settlement"]["state"] == "confirmed"
    assert a["event_settlement"]["winning_bucket"] == "60-61"
    assert a["displayed_snapshot"]["prediction_as_of"] == "2026-10-10T16:00:00Z"
    assert a["displayed_snapshot_score"]["state"] == "not_scored"
    assert (a["snapshot_count"], a["scored_snapshot_count"]) == (2, 1)

    b = items["KXHIGHNY-26OCT11"]
    assert b["view"] == "awaiting_settlement"
    assert b["event_settlement"]["state"] == "not_confirmed"
    assert b["displayed_snapshot_score"]["state"] == "invalid"
    detail = client.get("/api/weather/saved-events/KXHIGHNY-26OCT11").json()
    assert [s["score"]["state"] for s in detail["snapshots"]] == ["unavailable", "invalid"]
    assert "brier" not in detail["snapshots"][1]["score"]

    d = items["KXHIGHNY-26OCT09"]
    assert d["event_settlement"]["state"] == "conflict"
    assert d["view"] == "awaiting_settlement"

    c = items["KXHIGHNY-26OCT14"]
    assert c["view"] == "upcoming"
    assert c["displayed_snapshot"]["bucket_probabilities"] == {"60-61": 0.7}
    assert c["displayed_snapshot"]["invalid_probabilities"] == 1
    assert c["receipts"] == {"captured": 1, "missed": 1}


def test_score_for_a_different_event_is_invalid(env, client):
    sid = _snap(env["snapshots"], "KXHIGHNY-26OCT05", "2026-10-05", "2026-10-04T22:00:00Z")
    _score(env["scores"], sid, "KXHIGHNY-26OCT06", "2026-10-05")
    e = _by_ticker(client)["KXHIGHNY-26OCT05"]
    assert e["displayed_snapshot_score"]["state"] == "invalid"
    assert e["event_settlement"]["state"] == "not_confirmed"


@pytest.mark.parametrize("sort,expected", [
    ("default", ["KXHIGHNY-26OCT14", "KXHIGHNY-26OCT11", "KXHIGHNY-26OCT09", "KXHIGHNY-26OCT10"]),
    ("target_asc", ["KXHIGHNY-26OCT09", "KXHIGHNY-26OCT10", "KXHIGHNY-26OCT11", "KXHIGHNY-26OCT14"]),
    ("target_desc", ["KXHIGHNY-26OCT14", "KXHIGHNY-26OCT11", "KXHIGHNY-26OCT10", "KXHIGHNY-26OCT09"]),
    ("capture_desc", ["KXHIGHNY-26OCT14", "KXHIGHNY-26OCT11", "KXHIGHNY-26OCT10", "KXHIGHNY-26OCT09"]),
])
def test_sorts(populated, client, sort, expected):
    r = client.get("/api/weather/saved-events", params={"sort": sort}).json()
    assert [i["event_ticker"] for i in r["items"]] == expected
    assert r["sort"] == sort


def test_settlement_sort_puts_confirmed_first(populated, client):
    r = client.get("/api/weather/saved-events", params={"sort": "settlement_desc"}).json()
    assert r["items"][0]["event_ticker"] == "KXHIGHNY-26OCT10"
    assert all(i["event_settlement"]["state"] != "confirmed" for i in r["items"][1:])


def test_filters_pagination_and_validation(populated, client):
    r = client.get("/api/weather/saved-events", params={"status": "awaiting_settlement"}).json()
    assert {i["event_ticker"] for i in r["items"]} == {"KXHIGHNY-26OCT11", "KXHIGHNY-26OCT09"}
    assert r["views"] == {"upcoming": 1, "awaiting_settlement": 2, "settled": 1}
    assert [i["event_ticker"] for i in client.get("/api/weather/saved-events", params={"q": "oct14"}).json()["items"]] \
        == ["KXHIGHNY-26OCT14"]
    p2 = client.get("/api/weather/saved-events", params={"page_size": 3, "page": 2}).json()
    assert (p2["pages"], p2["total"], len(p2["items"])) == (2, 4, 1)
    assert client.get("/api/weather/saved-events", params={"page_size": 3, "page": 99}).json()["page"] == 2
    assert client.get("/api/weather/saved-events", params={"status": "bogus"}).status_code == 422
    assert client.get("/api/weather/saved-events", params={"sort": "best_bet"}).status_code == 422


def test_detail_and_not_found(populated, client):
    r = client.get("/api/weather/saved-events/KXHIGHNY-26OCT10")
    assert r.status_code == 200
    body = r.json()
    assert body["event"]["event_ticker"] == "KXHIGHNY-26OCT10"
    assert [s["score"]["state"] for s in body["snapshots"]] == ["scored", "not_scored"]
    assert client.get("/api/weather/saved-events/KXHIGHNY-99JAN01").status_code == 404
    assert client.get("/api/weather/saved-events/not_a_ticker").status_code == 404
    assert client.get("/api/weather/saved-events/..%5C..%5Csecrets").status_code == 404


def test_collector_health_inputs_come_from_cycle_times(populated, client):
    logs = populated["logs"]
    logs.mkdir()
    (logs / "prospective_20261013.log").write_text(
        "2026-10-13T14:00:05Z [pid 1] cycle start\n"
        "2026-10-13T14:01:00Z [pid 1] cycle end (exit 0)\n"
        "2026-10-13T15:00:05Z [pid 2] cycle start\n"
        "2026-10-13T15:01:00Z [pid 2] cycle end (exit 1)\n", encoding="utf-8")
    r = client.get("/api/weather/collector").json()
    cy = r["cycles"]
    assert cy["last_successful_cycle_at"] == "2026-10-13T14:01:00Z"
    assert cy["last_cycle_exit_code"] == 1
    assert (cy["cycles_24h"], cy["failures_24h"], cy["consecutive_failures"]) == (2, 1, 1)
    assert cy["minutes_since_last_successful_cycle"] == pytest.approx(119.0)
    fr = r["forecast_freshness"]
    assert fr["last_snapshot_prediction_as_of"] == "2026-10-13T15:00:00Z"
    assert fr["last_captured_checkpoint_at"] == "2026-10-13T15:00:00Z"
    assert fr["missed_checkpoints_24h"] == 1
    assert r["scheduler"]["reason"] == "test"
    assert r["lock"] == {"held": False, "state": "free"}


def test_collector_without_logs_reports_unknown_not_healthy(env, client):
    r = client.get("/api/weather/collector").json()
    assert r["log_observed"] is False
    assert r["cycles"]["last_successful_cycle_at"] is None
    assert r["cycles"]["minutes_since_last_successful_cycle"] is None


def test_metrics_context_separates_prospective_and_historical(populated, client):
    populated["root"].joinpath("replay.csv").write_text(
        "target_date,brier\n2026-04-02,0.1\n2026-04-03,0.2\n2026-07-25,0.3\n", encoding="utf-8")
    r = client.get("/api/weather/metrics-context").json()
    p = r["prospective_snapshot_scores"]
    assert p["kind"] == "prospective"
    assert (p["scored_snapshots"], p["events"]) == (3, 2)
    assert (p["first_target_date"], p["last_target_date"]) == ("2026-10-09", "2026-10-10")
    h = r["operational_replay"]
    assert h == {"kind": "historical", "available": True, "first_target_date": "2026-04-02",
                 "last_target_date": "2026-07-25", "target_dates": 3}


def test_missing_artifacts_are_reported_and_nothing_is_created(env, client):
    r = client.get("/api/weather/saved-events").json()
    assert r["artifacts"] == {"snapshots": "missing", "scores": "missing"}
    assert r["items"] == [] and r["total"] == 0
    assert client.get("/api/weather/metrics-context").json()["operational_replay"]["available"] is False
    client.get("/api/weather/collector")
    assert not any(env[k].exists() for k in ("snapshots", "scores", "checkpoints", "logs"))


def test_get_requests_are_passive(populated, client, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(requests.Session, "request", no_network)
    before = _files(populated["root"])
    for path in ("/api/weather/saved-events", "/api/weather/saved-events/KXHIGHNY-26OCT10",
                 "/api/weather/collector", "/api/weather/metrics-context"):
        assert client.get(path).status_code == 200
    assert _files(populated["root"]) == before

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


# ---------------------------------------------------------------- prediction history

def _history_snap(root: Path, ticker: str, target: str, as_of: str, *, probs: dict, tickers: dict | None = None,
                  obs: dict | None = None, checkpoint: str | None = "d0_0900") -> str:
    sid = f"{ticker}__{as_of.replace('-', '').replace(':', '')}"
    p = root / ticker / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"snapshot_id": sid, "event_ticker": ticker, "target_date": target, "prediction_as_of": as_of,
            "created_at": as_of, "prediction_status": "OK", "schema_version": "5.0.0",
            "prediction": {"run_id": "day_06z", "forecast_run": "event-day 06Z", "provenance": {"live_has_observations": obs is not None}},
            "evidence": {"gfs": {"model_run_at": "2026-07-15T06:00:00+00:00"}, "observations": obs},
            "external_probability_distribution": probs,
            "kalshi_quote_sidecar": {"markets": [{"label": k, "ticker": v} for k, v in (tickers or {}).items()]}}
    if checkpoint:
        body["checkpoint_id"] = checkpoint
    p.write_text(json.dumps(body), encoding="utf-8")
    return sid


def test_observation_day_uses_fixed_est_bounds_not_new_york_calendar_date():
    start, end = WI.climate_day_bounds_utc("2026-07-15")
    assert (start.isoformat(), end.isoformat()) == ("2026-07-15T05:00:00+00:00", "2026-07-16T05:00:00+00:00")
    # 00:30 EDT on Jul 15 (New York calendar date = target) is 23:30 EST on Jul 14: previous climate day.
    assert WI._classify_obs_time("2026-07-15T04:30:00Z", start, end) == "before_day"
    # 00:30 EDT on Jul 16 (New York calendar date = next day) is 23:30 EST on Jul 15: still the target day.
    assert WI._classify_obs_time("2026-07-16T04:30:00+00:00", start, end) == "in_day"
    assert WI._classify_obs_time("2026-07-16T05:00:00Z", start, end) == "after_day"
    assert WI._classify_obs_time("2026-07-15T05:00:00Z", start, end) == "in_day"
    for unknown in (None, "", "garbage", "2026-07-15T12:00:00"):
        assert WI._classify_obs_time(unknown, start, end) == "unknown"


def test_history_rows_report_run_observations_and_unknown_times(env):
    obs = {"station_id": "KNYC", "high_so_far_f": 80.1, "latest_timestamp": "2026-07-16T04:30:00+00:00",
           "trajectory": [{"timestamp": "2026-07-15T04:51:00+00:00", "temperature_f": 79.0},
                          {"timestamp": "2026-07-15T12:51:00+00:00", "temperature_f": 80.1},
                          {"timestamp": None, "temperature_f": 78.0}, {"temperature_f": 77.0}]}
    _history_snap(env["snapshots"], "KXHIGHNY-26JUL15", "2026-07-15", "2026-07-15T13:00:00Z",
                  probs={"80° to 81°": 0.5, "82° to 83°": 0.5}, obs=obs)
    _history_snap(env["snapshots"], "KXHIGHNY-26JUL15", "2026-07-15", "2026-07-14T22:00:00Z",
                  probs={"80° to 81°": 0.4, "82° to 83°": 0.6}, obs=None, checkpoint=None)
    d = WI.saved_event("KXHIGHNY-26JUL15")
    first, last = d["snapshots"]
    assert first["prediction_as_of"] < last["prediction_as_of"]
    assert first["checkpoint_id"] is None and first["observation"] == {"present": False, "used_by_prediction": False}
    o = last["observation"]
    assert o["latest_position"] == "in_day" and o["used_by_prediction"] is True
    assert o["trajectory_points"] == {"in_day": 1, "before_day": 1, "after_day": 0, "unknown": 2}
    assert o["climate_day_bounds_utc"] == ["2026-07-15T05:00:00Z", "2026-07-16T05:00:00Z"]
    assert last["run"]["forecast_run"] == "event-day 06Z"
    assert last["run"]["gfs_model_run_at"] == "2026-07-15T06:00:00+00:00"


def test_bucket_identity_is_label_plus_saved_definition(env):
    t = "KXHIGHNY-26JUL15"
    _history_snap(env["snapshots"], t, "2026-07-15", "2026-07-14T22:00:00Z",
                  probs={"80° to 81°": 0.4, "82° or above": "0.6"}, tickers={"80° to 81°": f"{t}-B80.5"})
    _history_snap(env["snapshots"], t, "2026-07-15", "2026-07-15T13:00:00Z",
                  probs={"80° to 81°": 0.7, "82° or above": float("nan")}, tickers={"80° to 81°": f"{t}-B80.6"})
    d = WI.saved_event(t)
    same = [b for b in d["buckets"] if b["label"] == "80° to 81°"]
    assert len(same) == 2 and {b["ticker"] for b in same} == {f"{t}-B80.5", f"{t}-B80.6"}
    assert all(b["definition_variants"] == 2 and (b["lower_f"], b["upper_f"]) == (80, 81) for b in same)
    a, b = (x["id"] for x in sorted(same, key=lambda x: x["ticker"]))
    first, last = d["snapshots"]
    assert first["probabilities"][a] == {"p": 0.4, "invalid": False} and b not in first["probabilities"]
    assert last["probabilities"][b] == {"p": 0.7, "invalid": False} and a not in last["probabilities"]
    above = next(x for x in d["buckets"] if x["label"] == "82° or above")
    assert (above["lower_f"], above["upper_f"], above["valid_points"], above["snapshots"]) == (82, None, 0, 2)
    assert first["probabilities"][above["id"]] == {"p": None, "invalid": True}
    assert last["probabilities"][above["id"]] == {"p": None, "invalid": True}


def test_history_is_served_by_the_existing_saved_event_route(env, client):
    _history_snap(env["snapshots"], "KXHIGHNY-26JUL15", "2026-07-15", "2026-07-15T13:00:00Z",
                  probs={"80° to 81°": 0.5})
    r = client.get("/api/weather/saved-events/KXHIGHNY-26JUL15").json()
    assert r["buckets"][0]["label"] == "80° to 81°" and "not interpolated" in r["history_note"]
    assert r["snapshots"][0]["probabilities"][r["buckets"][0]["id"]]["p"] == 0.5

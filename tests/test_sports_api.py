"""Read-only sports API: filtering, pagination, event identity, missing artifacts, unavailable candidates,
common-contract metrics, passivity and response hygiene. Uses synthetic saved artifacts in tmp dirs."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from api.app import app
from research.sports import evaluate as EV
from research.sports import service as S
from research.sports.core import sha256_json
from research.sports.live import config as LC
from research.sports.ops import backup as BK
from research.sports.ops import collector as CO
from research.sports.ops import lock as LK
from research.sports.ops import scheduler as SCH
from research.sports.scoring import config as SCC

NOW = time.time()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _contract(ticker, family, cands, cohort="strict"):
    return {"ticker": ticker, "series": ticker.split("-")[0], "family": family, "cohort": cohort, "status": "ok",
            "candidates": cands}


FULL = {"frozen": {"p": 0.6, "model": "elo"}, "calibrated": {"p": 0.58, "map": {"form": "identity"}},
        "market": {"p": 0.55, "bid": 0.54, "ask": 0.56, "candle_end": NOW - 7200, "age_s": 1800},
        "blend": {"p": 0.57, "map": {"w": 0.3}}}
FROZEN_ONLY = {"frozen": {"p": 0.3, "model": "score"},
               "calibrated": {"unavailable": "unavailable: matched training support below threshold"},
               "market": {"unavailable": "unavailable: no two-sided candle within 3 h"},
               "blend": {"unavailable": "market candidate unavailable"}}


def _write_record(root: Path, comp, cluster, sport, status, start, contracts, tickers, run_id="run-test"):
    cutoff = start - 3600
    rec = {"protocol": LC.PROTOCOL, "schema": "test", "competition": comp, "cluster": cluster, "sport": sport,
           "source": "espn_team", "status": status, "start": _iso(start), "window_open": _iso(cutoff - 1200),
           "scheduled_cutoff": _iso(cutoff), "run_id": run_id, "kalshi_event_tickers": tickers,
           "contracts": contracts, "evidence": [], "comparability": "exact"}
    if status == "WINDOW_CAPTURE":
        rec.update({"prediction_as_of": _iso(cutoff - 600), "prediction_as_of_ts": cutoff - 600,
                    "finalized_at": _iso(cutoff - 590), "horizon_minutes": 70.0})
    else:
        rec.update({"reason": "checkpoint window expired without an accepted capture",
                    "written_at": _iso(cutoff + 60), "contracts": []})
    rec["record_sha256"] = sha256_json(rec)
    p = root / "accepted" / "checkpoint" / comp / f"{cluster.replace(':', '_')}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def _write_score(root: Path, comp, cluster, sport, contract, y, settled_at=None, prediction_as_of=None,
                 source_run_id="run-test"):
    settlement = {"state": "NONBINARY" if y is None else "SETTLED_YES" if y else "SETTLED_NO", "detail": "test"}
    if settled_at:
        settlement["market"] = {"settlement_ts": settled_at}
    sc = {"ticker": contract["ticker"], "competition": comp, "sport": sport, "family": contract["family"],
          "prediction_as_of": prediction_as_of, "source_run_id": source_run_id,
          "cohort": contract["cohort"], "event_key": f"{comp}|{cluster}", "y": y,
          "candidates": contract["candidates"], "scored_at": _iso(NOW - 60),
          "settlement": settlement,
          "settlement_evidence": [{"url": "https://example.invalid/secret-payload"}]}
    sc["score_sha256"] = sha256_json(sc)
    p = root / "finalized" / comp / cluster.replace(":", "_") / f"{contract['ticker']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sc), encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    cap, sc, ev, logs = tmp_path / "capture", tmp_path / "scores", tmp_path / "evidence", tmp_path / "logs"
    for d in (cap, sc, ev, logs):
        d.mkdir()
    monkeypatch.setattr(LC, "CAPTURE_DIR", cap)
    monkeypatch.setattr(LC, "EVIDENCE_ROOT", ev)
    monkeypatch.setattr(SCC, "SCORE_DIR", sc)
    monkeypatch.setattr(CO, "STATUS_PATH", logs / "collector_status.json")
    monkeypatch.setattr(CO, "CYCLES_PATH", logs / "collector_cycles.jsonl")
    monkeypatch.setattr(BK, "STATUS_PATH", logs / "backup_status.json")
    monkeypatch.setattr(BK, "PAUSE_PATH", logs / "backup_paused.json")
    monkeypatch.setattr(LK, "LOCK_PATH", logs / "cycle.lock")
    monkeypatch.setattr(SCH, "query_task", lambda name, **_: {**SCH.UNKNOWN, "reason": "test"})
    S.clear_caches()
    return {"capture": cap, "scores": sc, "root": tmp_path}


@pytest.fixture
def populated(env):
    cap, sc = env["capture"], env["scores"]
    a1 = _contract("KXNFLGAME-A-HOME", "game_winner", FULL)
    a2 = _contract("KXNFLSPREAD-A-H7", "spread", FROZEN_ONLY, cohort="exploratory")
    _write_record(cap, "nfl", "espn:1", "Football", "WINDOW_CAPTURE", NOW - 86400, [a1, a2], ["KXNFLGAME-A"])
    _write_score(sc, "nfl", "espn:1", "Football", a1, 1)
    _write_score(sc, "nfl", "espn:1", "Football", a2, 0)
    b1 = _contract("KXNFLGAME-B-HOME", "game_winner", FULL)
    _write_record(cap, "nfl", "espn:2", "Football", "WINDOW_CAPTURE", NOW - 7200, [b1], ["KXNFLGAME-B"])
    c1 = _contract("KXEPLGAME-C-HOME", "game_winner", FROZEN_ONLY)
    _write_record(cap, "epl", "espn:3", "Soccer", "WINDOW_CAPTURE", NOW + 4000, [c1], ["KXEPLGAME-C"])
    _write_record(cap, "nfl", "espn:4", "Football", "MISSED", NOW - 3 * 86400, [], ["KXNFLGAME-D"])
    return env


@pytest.fixture
def client():
    return TestClient(app)


def _snapshot(root: Path) -> dict:
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_events_order_views_and_identity(populated, client):
    r = client.get("/api/sports/events").json()
    assert [i["event_id"] for i in r["items"]] == ["epl|espn:3", "nfl|espn:2", "nfl|espn:1", "nfl|espn:4"]
    assert [i["view"] for i in r["items"]] == ["upcoming", "awaiting_settlement", "settled", "missed"]
    assert r["views"] == {"upcoming": 1, "awaiting_settlement": 1, "settled": 1, "missed": 1}
    assert len({i["event_id"] for i in r["items"]}) == 4
    settled = r["items"][2]
    assert settled["contracts_ok"] == 2 and settled["families"] == ["game_winner", "spread"]
    assert settled["settlement"]["state"] == "settled" and settled["settlement"]["labelled"] == 2

    eid = "nfl|espn:1"
    d = client.get(f"/api/sports/events/{quote(eid, safe='')}")
    assert d.status_code == 200
    body = d.json()
    assert body["event"]["event_id"] == eid
    tickers = [c["ticker"] for f in body["families"] for c in f["contracts"]]
    assert sorted(tickers) == ["KXNFLGAME-A-HOME", "KXNFLSPREAD-A-H7"]
    assert client.get("/api/sports/events/nfl%7Cespn%3A999").status_code == 404


@pytest.mark.parametrize("params,expected", [
    ({"sport": "Soccer"}, ["epl|espn:3"]),
    ({"competition": "nfl"}, ["nfl|espn:2", "nfl|espn:1", "nfl|espn:4"]),
    ({"family": "spread"}, ["nfl|espn:1"]),
    ({"cohort": "exploratory"}, ["nfl|espn:1"]),
    ({"status": "captured"}, ["epl|espn:3", "nfl|espn:2", "nfl|espn:1"]),
    ({"status": "missed"}, ["nfl|espn:4"]),
    ({"status": "awaiting_settlement"}, ["nfl|espn:2"]),
    ({"q": "KXEPLGAME-C"}, ["epl|espn:3"]),
    ({"q": "nothing-matches"}, []),
])
def test_event_filters(populated, client, params, expected):
    r = client.get("/api/sports/events", params=params).json()
    assert [i["event_id"] for i in r["items"]] == expected
    assert r["total"] == len(expected)


def test_event_pagination_and_validation(populated, client):
    p1 = client.get("/api/sports/events", params={"page_size": 3}).json()
    p2 = client.get("/api/sports/events", params={"page_size": 3, "page": 2}).json()
    assert (p1["pages"], p1["total"], len(p1["items"]), len(p2["items"])) == (2, 4, 3, 1)
    assert p2["items"][0]["event_id"] == "nfl|espn:4"
    clamped = client.get("/api/sports/events", params={"page_size": 3, "page": 99}).json()
    assert clamped["page"] == 2
    assert client.get("/api/sports/events", params={"page_size": 0}).status_code == 422
    assert client.get("/api/sports/events", params={"status": "bogus"}).status_code == 422


def test_unavailable_candidates_carry_reasons_not_zero(populated, client):
    d = client.get("/api/sports/events/" + quote("nfl|espn:1", safe="")).json()
    spread = next(f for f in d["families"] if f["family"] == "spread")["contracts"][0]
    for k in ("calibrated", "market", "blend"):
        c = spread["candidates"][k]
        assert c["available"] is False and "p" not in c and "brier" not in c
        assert c["reason"] == FROZEN_ONLY[k]["unavailable"]
    assert spread["candidates"]["frozen"]["available"] is True
    winner = next(f for f in d["families"] if f["family"] == "game_winner")["contracts"][0]
    mref = winner["candidates"]["market"]["market_reference"]
    assert mref["mid"] == 0.55 and mref["candle_age_s_at_prediction"] == 1800 and mref["candle_end_utc"]
    b, ll = EV._losses({"p": 0.6, "y": 1})
    assert winner["candidates"]["frozen"]["brier"] == pytest.approx(b)
    assert winner["settlement"]["outcome"] == 1
    assert "do not sum to one" in d["notes"]["binary"]

    s = client.get("/api/sports/summary").json()
    assert s["candidate_counts"]["calibrated"] == {"available": 2, "unavailable": 2}
    assert s["counts"]["captured_events"] == 3 and s["counts"]["missed_events"] == 1


def test_missed_event_detail(populated, client):
    d = client.get("/api/sports/events/" + quote("nfl|espn:4", safe="")).json()
    assert d["event"]["view"] == "missed" and d["families"] == []
    assert d["missed"]["reason"].startswith("checkpoint window expired")


def test_results_common_contract_pairs_and_weighting(populated, client):
    r = client.get("/api/sports/results").json()
    cands = {c["candidate"]: c for c in r["overall"]["candidates"]}
    assert (cands["frozen"]["contracts"], cands["frozen"]["events"]) == (2, 1)
    assert (cands["market"]["contracts"], cands["market"]["events"]) == (1, 1)
    pair = next(p for p in r["overall"]["comparisons"] if (p["candidate"], p["reference"]) == ("frozen", "market"))
    assert (pair["matched_contracts"], pair["unique_events"]) == (1, 1)
    bf, _ = EV._losses({"p": 0.6, "y": 1})
    bm, _ = EV._losses({"p": 0.55, "y": 1})
    assert pair["d_brier"] == pytest.approx(bf - bm)
    assert r["weighting"] == "event" and "negative value" in r["sign_note"].lower()
    assert r["counts"]["labelled_contracts"] == 2 and r["counts"]["labelled_events"] == 1
    assert {g["group"] for g in r["breakdown"]["family"]} == {"game_winner", "spread"}

    c = client.get("/api/sports/results", params={"weighting": "contract"}).json()
    assert c["weighting"] == "contract"
    fz = {x["candidate"]: x for x in c["overall"]["candidates"]}["frozen"]
    b1, _ = EV._losses({"p": 0.6, "y": 1})
    b2, _ = EV._losses({"p": 0.3, "y": 0})
    assert fz["brier"] == pytest.approx((b1 + b2) / 2)
    only_spread = client.get("/api/sports/results", params={"family": "spread"}).json()
    assert {x["candidate"]: x["contracts"] for x in only_spread["overall"]["candidates"]}["market"] == 0


def test_missing_artifacts_are_reported(env, client):
    s = client.get("/api/sports/summary").json()
    assert s["artifacts"]["capture_records"] == "missing" and s["artifacts"]["settlement_scores"] == "missing"
    assert s["counts"]["captured_events"] == 0 and s["last_capture_at"] is None
    assert s["collector"]["recorded"] is False
    ev = client.get("/api/sports/events").json()
    assert ev["items"] == [] and ev["artifacts"]["capture_records"] == "missing"
    r = client.get("/api/sports/results").json()
    assert r["counts"]["labelled_contracts"] == 0
    assert all(c["contracts"] == 0 for c in r["overall"]["candidates"])
    col = client.get("/api/sports/collector").json()
    assert col["status_recorded"] is False and col["scheduler"]["configured"] == "unknown"
    assert col["lock"] == {"held": False, "state": "free"}


def test_record_checksum_problem_is_exposed(populated, client):
    p = next((populated["capture"] / "accepted" / "checkpoint" / "nfl").glob("espn_2.json"))
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["contracts"][0]["candidates"]["frozen"]["p"] = 0.99
    p.write_text(json.dumps(rec), encoding="utf-8")
    item = next(i for i in client.get("/api/sports/events").json()["items"] if i["event_id"] == "nfl|espn:2")
    assert item["integrity"]["record_checksum"] == "MISMATCH"
    assert client.get("/api/sports/summary").json()["integrity"]["record_checksum_mismatches"] == 1


def test_get_requests_are_passive_and_hide_paths(populated, client, monkeypatch):
    import requests

    def no_network(*a, **k):
        raise AssertionError("GET handler attempted a network request")

    monkeypatch.setattr(requests.Session, "get", no_network)
    monkeypatch.setattr(requests.Session, "request", no_network)
    monkeypatch.setattr(CO, "run_cycle", no_network)
    before = _snapshot(populated["root"])
    urls = ["/api/sports/summary", "/api/sports/events", "/api/sports/events/" + quote("nfl|espn:1", safe=""),
            "/api/sports/results", "/api/sports/results?weighting=contract", "/api/sports/coverage",
            "/api/sports/historical", "/api/sports/collector"]
    for u in urls:
        r = client.get(u)
        assert r.status_code == 200, u
        text = r.text
        assert str(populated["root"]) not in text and str(populated["root"]).replace("\\", "\\\\") not in text
        assert "secret-payload" not in text and "settlement_evidence" not in text
        assert '"_path"' not in text and ":\\\\" not in text
    assert _snapshot(populated["root"]) == before
    assert not LK.LOCK_PATH.exists()


def test_collection_mode_from_cycle_log_without_touching_records(populated, client):
    cap = populated["capture"]
    rec_path = next(cap.rglob("espn_2.json"))
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    rec["run_id"] = "run-auto"
    rec["record_sha256"] = sha256_json({k: v for k, v in rec.items() if k != "record_sha256"})
    rec_path.write_text(json.dumps(rec), encoding="utf-8")
    cycles = [{"started_at": _iso(NOW - 600), "purpose": "scheduled", "capture": {"run_id": "run-auto"}},
              {"started_at": _iso(NOW - 300), "purpose": "scheduled", "dry_run": True, "capture": {"run_id": "run-test"}}]
    CO.CYCLES_PATH.write_text("".join(json.dumps(c) + "\n" for c in cycles), encoding="utf-8")
    before = _snapshot(cap)
    items = {i["event_id"]: i["collection_mode"] for i in client.get("/api/sports/events").json()["items"]}
    assert items == {"epl|espn:3": "manual_pilot", "nfl|espn:2": "automatic", "nfl|espn:1": "manual_pilot",
                     "nfl|espn:4": "manual_pilot"}
    only = client.get("/api/sports/events", params={"collection": "automatic"}).json()
    assert [i["event_id"] for i in only["items"]] == ["nfl|espn:2"]
    assert client.get("/api/sports/events", params={"collection": "bogus"}).status_code == 422
    detail = client.get("/api/sports/events/" + quote("nfl|espn:2", safe="")).json()
    assert detail["provenance"]["collection"]["mode"] == "automatic"
    assert detail["provenance"]["collection"]["amendment"] == "sports_phase2_capture_v2_ops_amendment_1"
    by = client.get("/api/sports/summary").json()["collection"]["by_mode"]
    assert by["automatic"] == {"captured": 1, "missed": 0}
    assert by["manual_pilot"] == {"captured": 2, "missed": 1}
    assert _snapshot(cap) == before


def test_collector_reports_backup_without_paths(populated, client):
    assert client.get("/api/sports/collector").json()["backup"]["recorded"] is False
    BK.STATUS_PATH.write_text(json.dumps({"backup": "pushed", "at": _iso(NOW), "files": 3, "missing_count": 0,
                                          "backup_dir": "C:/secret/clone", "remote": "kel-state:x/y.git",
                                          "ref": "refs/heads/sports-state"}), encoding="utf-8")
    r = client.get("/api/sports/collector")
    b = r.json()["backup"]
    assert (b["recorded"], b["backup"], b["files"]) == (True, "pushed", 3)
    assert "secret" not in r.text and "kel-state" not in r.text
    assert b["paused"] is None
    BK.set_pause("repairing")
    S.clear_caches()
    assert client.get("/api/sports/collector").json()["backup"]["paused"]["reason"] == "repairing"


def test_scheduler_result_codes_are_decoded():
    assert SCH.decode_result("267009")["name"] == "SCHED_S_TASK_RUNNING"
    assert SCH.decode_result("267011")["name"] == "SCHED_S_TASK_HAS_NOT_RUN"
    refused = SCH.decode_result("2147946720")
    assert (refused["hex"], refused["name"]) == ("0x800710E0", "ERROR_REQUEST_REFUSED")
    assert SCH.decode_result("-2147020576")["hex"] == "0x800710E0"
    assert SCH.decode_result("0x2")["name"] == "EXIT_2"
    assert SCH.decode_result("12345")["name"] == "UNRECOGNIZED"
    assert SCH.decode_result("n/a")["name"] == "UNKNOWN"


def test_coverage_inventory_and_reasons(client):
    r = client.get("/api/sports/coverage", params={"page_size": 5}).json()
    assert r["available"] is True
    assert r["matrix_series_total"] == sum(r["status_counts"].values())
    assert set(r["status_counts"]) <= {"supported", "unsupported", "unattempted"}
    assert len(r["series"]["items"]) == 5
    un = client.get("/api/sports/coverage", params={"status": "unattempted", "page_size": 500}).json()
    assert un["series"]["total"] == r["status_counts"]["unattempted"]
    assert all(i["status"] == "unattempted" and i["reason"] for i in un["series"]["items"])


def test_historical_separated_and_labelled(client):
    r = client.get("/api/sports/historical", params={"page_size": 5}).json()
    protos = {p["protocol"]: p for p in r["protocols"]}
    assert set(protos) == {"sports_phase1_v1", "sports_phase1_v2", "sports_phase2_v1"}
    assert protos["sports_phase1_v1"]["status"].startswith("superseded by")
    assert all(p["source"]["label"].startswith("tracked summary") for p in protos.values())
    assert "never merged" in r["separation_note"]
    periods = [p["period"] for p in protos.values()]
    assert any(periods)
    assert all(p is None or (p["label"] in ("test period", "evaluation period") and p["start"] <= p["end"])
               for p in periods)


@pytest.fixture
def settled_times(env):
    cap, sc = env["capture"], env["scores"]
    a = _contract("KXNFLGAME-A-HOME", "game_winner", FULL)
    _write_record(cap, "nfl", "espn:1", "Football", "WINDOW_CAPTURE", NOW - 86400, [a], ["KXNFLGAME-A"])
    _write_score(sc, "nfl", "espn:1", "Football", a, 1, settled_at=_iso(NOW - 3600),
                 prediction_as_of=_iso(NOW - 86400 - 4200))
    b = _contract("KXNFLGAME-B-HOME", "game_winner", FULL)
    _write_record(cap, "nfl", "espn:2", "Football", "WINDOW_CAPTURE", NOW - 2 * 86400, [b], ["KXNFLGAME-B"])
    _write_score(sc, "nfl", "espn:2", "Football", b, 0, settled_at=_iso(NOW - 600),
                 prediction_as_of=_iso(NOW - 2 * 86400 - 4200))
    c = _contract("KXEPLGAME-C-HOME", "game_winner", FROZEN_ONLY)
    _write_record(cap, "epl", "espn:3", "Soccer", "WINDOW_CAPTURE", NOW + 4000, [c], ["KXEPLGAME-C"])
    return env


@pytest.mark.parametrize("sort,expected", [
    ("start_asc", ["nfl|espn:2", "nfl|espn:1", "epl|espn:3"]),
    ("start_desc", ["epl|espn:3", "nfl|espn:1", "nfl|espn:2"]),
    ("capture_desc", ["epl|espn:3", "nfl|espn:1", "nfl|espn:2"]),
    ("settlement_desc", ["nfl|espn:2", "nfl|espn:1", "epl|espn:3"]),
])
def test_event_sort_orders(settled_times, client, sort, expected):
    r = client.get("/api/sports/events", params={"sort": sort}).json()
    assert [i["event_id"] for i in r["items"]] == expected
    assert r["sort"] == sort


def test_event_sort_validation_settlement_time_and_labels(settled_times, client):
    assert client.get("/api/sports/events", params={"sort": "best_bet"}).status_code == 422
    r = client.get("/api/sports/events").json()
    by = {i["event_id"]: i for i in r["items"]}
    assert by["nfl|espn:2"]["settlement"]["last_settled_at"] == _iso(NOW - 600)
    assert by["epl|espn:3"]["settlement"]["last_settled_at"] is None
    assert set(r["facet_labels"]["competition"]) == set(r["facets"]["competition"])
    assert all(isinstance(v, str) and v for v in r["facet_labels"]["competition"].values())


def test_results_report_their_data_period(settled_times, client):
    period = client.get("/api/sports/results").json()["period"]
    assert (period["first_settled_at"], period["last_settled_at"]) == (_iso(NOW - 3600), _iso(NOW - 600))
    assert (period["first_prediction_as_of"], period["last_prediction_as_of"]) == (
        _iso(NOW - 2 * 86400 - 4200), _iso(NOW - 86400 - 4200))


def test_collector_history_reads_the_full_cycle_log(populated, client):
    cycles = [{"finished_at": _iso(NOW - 5 * 86400), "result": "ok"},
              {"finished_at": _iso(NOW - 4 * 86400), "result": "error"},
              {"finished_at": _iso(NOW - 3 * 86400), "result": "skipped_lock_busy"}]
    CO.CYCLES_PATH.write_text("".join(json.dumps(c) + "\n" for c in cycles) + "not json\n", encoding="utf-8")
    h = client.get("/api/sports/collector").json()["history"]
    assert h == {"recorded": True, "last_cycle_finished_at": _iso(NOW - 4 * 86400), "last_cycle_result": "error",
                 "last_successful_cycle_at": _iso(NOW - 5 * 86400)}
    CO.CYCLES_PATH.unlink()
    assert client.get("/api/sports/collector").json()["history"] == {"recorded": False}


def test_scheduler_query_parsing():
    class P:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    SCH._cache.clear()
    missing = SCH.query_task("t-missing", runner=lambda a: P(1, err="ERROR: The system cannot find the file specified."))
    assert (missing["observable"], missing["configured"]) == (True, False)
    SCH._cache.clear()

    def boom(a):
        raise OSError("no schtasks")

    assert SCH.query_task("t-err", runner=boom)["configured"] == "unknown"
    SCH._cache.clear()
    csv_out = ('"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time","Last Result",'
               '"Scheduled Task State","Repeat: Every"\n'
               '"PC","\\t-ok","10/11/2026 3:05:00 PM","Ready","Interactive only","10/11/2026 3:00:00 PM","0",'
               '"Enabled","0 Hour(s), 5 Minute(s)"\n')
    ok = SCH.query_task("t-ok", runner=lambda a: P(0, out=csv_out))
    assert ok["enabled"] is True and ok["last_result"] == "0" and ok["status"] == "Ready"
    SCH._cache.clear()


# ---------------------------------------------------------------- results over time

MARKETLESS = {"frozen": {"p": 0.7, "model": "elo"}, "calibrated": {"p": 0.65, "map": {"form": "identity"}},
              "market": {"unavailable": "unavailable: no two-sided candle within 3 h"},
              "blend": {"unavailable": "market candidate unavailable"}}
WIN, LOSS = (0.6 - 1) ** 2 - (0.55 - 1) ** 2, 0.6 ** 2 - 0.55 ** 2  # frozen minus market Brier on FULL


@pytest.fixture
def timeline(env):
    cap, sc = env["capture"], env["scores"]
    (env["root"] / "logs" / "collector_cycles.jsonl").write_text(
        json.dumps({"purpose": "scheduled", "capture": {"run_id": "run-auto"}}) + "\n", encoding="utf-8")
    t0 = NOW - 10 * 86400

    def event(cluster, contracts, run_id="run-test"):
        _write_record(cap, "nfl", cluster, "Football", "WINDOW_CAPTURE", t0, contracts, ["KX"], run_id=run_id)

    def score(cluster, c, y, settled, run_id="run-test"):
        _write_score(sc, "nfl", cluster, "Football", c, y, settled_at=_iso(settled) if settled else None,
                     source_run_id=run_id)

    # E1 (automatic): two FULL contracts settle at t0+200 and t0+300; a marketless contract is still pending.
    e1 = [_contract("KX-E1-A", "game_winner", FULL), _contract("KX-E1-B", "game_winner", FULL),
          _contract("KX-E1-C", "game_winner", MARKETLESS)]
    event("espn:e1", e1, "run-auto")
    score("espn:e1", e1[0], 1, t0 + 200, "run-auto")
    score("espn:e1", e1[1], 0, t0 + 300, "run-auto")
    # E2 (manual): completes first.
    e2 = [_contract("KX-E2-A", "game_winner", FULL)]
    event("espn:e2", e2)
    score("espn:e2", e2[0], 1, t0 + 100)
    # E3: partially settled.
    e3 = [_contract("KX-E3-A", "game_winner", FULL), _contract("KX-E3-B", "spread", FULL)]
    event("espn:e3", e3)
    score("espn:e3", e3[0], 1, t0 + 50)
    # E4: completed but the score records no settlement time.
    e4 = [_contract("KX-E4-A", "game_winner", FULL)]
    event("espn:e4", e4)
    score("espn:e4", e4[0], 1, None)
    # E5: settled without a YES/NO outcome.
    e5 = [_contract("KX-E5-A", "game_winner", FULL)]
    event("espn:e5", e5)
    score("espn:e5", e5[0], None, t0 + 400)
    # E6: no market reference at all, so never selected for frozen:market.
    e6 = [_contract("KX-E6-A", "game_winner", FROZEN_ONLY)]
    event("espn:e6", e6)
    score("espn:e6", e6[0], 1, t0 + 10)
    S.clear_caches()
    return env


def test_timeline_points_are_completed_events_in_completion_order(timeline):
    from research.sports.scoring import report as RE
    r = S.get_results_timeline({}, ("frozen", "market"))
    assert [p["event_id"].split("|")[1] for p in r["points"]] == ["espn:e2", "espn:e1"]
    assert r["points"][0]["event_d_brier"] == pytest.approx(WIN)
    assert r["points"][1]["event_d_brier"] == pytest.approx((WIN + LOSS) / 2)
    assert [p["cumulative_d_brier_event"] for p in r["points"]] == pytest.approx([WIN, (WIN + (WIN + LOSS) / 2) / 2])
    assert [p["collection_mode"] for p in r["points"]] == ["manual_pilot", "automatic"]
    cohort = [s for s in S.scores() if s["event_key"] in ("nfl|espn:e1", "nfl|espn:e2")]
    assert r["final"]["equal"] is True
    assert r["final"]["cumulative_d_brier_event"] == pytest.approx(RE.paired(cohort, "frozen", "market")["brier_event"],
                                                                   abs=1e-12)
    assert r["cohort"]["events"] == 2 and r["cohort"]["contracts"] == 3
    assert r["cohort"]["by_collection"] == {"manual_pilot": 1, "automatic": 1}
    assert r["points"][-1]["completed_at"] == r["cohort"]["last_completed_at"]


def test_timeline_reports_partial_undated_and_nonbinary_events_separately(timeline):
    r = S.get_results_timeline({}, ("frozen", "market"))
    nic = r["not_in_chart"]
    assert nic["partially_settled"] == {"events": 1, "selected_contracts": 2}
    assert nic["nonbinary"] == {"events": 1, "selected_contracts": 1}
    assert nic["undated_completed"]["events"] == 1
    assert nic["undated_completed"]["d_brier_event"] == pytest.approx(WIN)
    basis = r["results_table_basis"]
    # E1 (2) + E2 (1) + settled half of E3 (1) + undated E4 (1); E5 has no label; E6 has no market reference.
    assert (basis["contracts"], basis["events"]) == (5, 4)
    assert basis["d_brier_event"] != pytest.approx(r["final"]["cumulative_d_brier_event"])


def test_timeline_selection_is_candidate_specific(timeline):
    fm = S.get_results_timeline({}, ("frozen", "market"))
    cf = S.get_results_timeline({}, ("calibrated", "frozen"))
    assert "nfl|espn:e1" in {p["event_id"] for p in fm["points"]}
    # For calibrated:frozen E1 also selects its marketless contract, which has not settled.
    assert "nfl|espn:e1" not in {p["event_id"] for p in cf["points"]}
    assert cf["not_in_chart"]["partially_settled"]["events"] == 2
    # E6 (frozen only) is selected by neither pair.
    assert all("espn:e6" not in p["event_id"] for p in fm["points"] + cf["points"])


def test_timeline_filters_and_collection(timeline):
    auto = S.get_results_timeline({}, ("frozen", "market"), "automatic")
    assert [p["event_id"] for p in auto["points"]] == ["nfl|espn:e1"]
    assert auto["collection"] == "automatic" and auto["results_table_basis"]["events"] == 1
    gw = S.get_results_timeline({"family": "game_winner"}, ("frozen", "market"))
    # Only E3's game_winner contract is selected under this filter, and it is settled: E3 completes.
    assert "nfl|espn:e3" in {p["event_id"] for p in gw["points"]}
    assert gw["not_in_chart"]["partially_settled"]["events"] == 0
    assert S.get_results_timeline({"sport": "Soccer"}, ("frozen", "market"))["points"] == []


def test_timeline_route_validates_pair_and_is_empty_without_data(env, client):
    assert client.get("/api/sports/results/timeline?pair=frozen:blend").status_code == 400
    assert client.get("/api/sports/results/timeline?pair=market:frozen").status_code == 400
    assert client.get("/api/sports/results/timeline?collection=bogus").status_code == 422
    r = client.get("/api/sports/results/timeline").json()
    assert r["points"] == [] and r["final"]["cumulative_d_brier_event"] is None and r["kind"] == "prospective"
    assert r["pair"] == {"candidate": "frozen", "reference": "market"}

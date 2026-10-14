"""Sports presentation caches: reuse only while files are unchanged, integrity problems still surface,
counts never need evidence, only visible rows are enriched and concurrent loads parse each file once."""

from __future__ import annotations

import json
import threading

import pytest

from research.sports import service as S
from research.sports.adapters import KALSHI_BASE
from research.sports.core import request_key, sha256_bytes, sha256_json
from research.sports.live import config as LC
from tests.test_sports_api import FULL, NOW, _contract, _write_record, _write_score, client, env, populated  # noqa: F401

RUN = "run-test"


def _evidence(root, key: str, body: bytes, sha: str | None = None):
    p = root / RUN / "kalshi" / key[:2] / f"{key}.body"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    p.with_name(f"{key}.meta.json").write_text(json.dumps({"sha256": sha or sha256_bytes(body)}), encoding="utf-8")
    return p


def _milestone_key() -> str:
    params = {"limit": LC.PAGE_LIMITS["milestones"], "category": "Sports",
              "minimum_start_date": LC.MILESTONE_MIN_START}
    return request_key(f"{KALSHI_BASE}/milestones", params)


def _milestones(title: str) -> bytes:
    return json.dumps({"milestones": [{"title": title, "start_date": "2026-10-11T17:00:00Z",
                                       "details": {"league": "NFL"},
                                       "primary_event_tickers": ["KXNFLGAME-A"]}], "cursor": None}).encode()


def _markets(name: str) -> bytes:
    return json.dumps({"markets": [{"ticker": "KXNFLGAME-A-HOME", "title": "Who wins?", "yes_sub_title": name,
                                    "rules_primary": "rules"}]}).encode()


@pytest.fixture
def evidence(populated):
    ev = populated["root"] / "evidence"
    title_body = _evidence(ev, _milestone_key(), _milestones("Away at Home"))
    url, params = f"{KALSHI_BASE}/markets", {"event_ticker": "KXNFLGAME-A"}
    key = request_key(url, params)
    body = _markets("Home Team")
    market_body = _evidence(ev, key, body)
    rec_path = populated["capture"] / "accepted" / "checkpoint" / "nfl" / "espn_1.json"
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    rec.pop("record_sha256")
    rec["evidence"] = [{"url": url, "params": params, "source": "kalshi", "key": key, "sha256": sha256_bytes(body),
                        "status": 200, "bytes": len(body), "requested_at": rec["prediction_as_of"],
                        "received_at": rec["prediction_as_of"]}]
    rec["record_sha256"] = sha256_json(rec)
    rec_path.write_text(json.dumps(rec), encoding="utf-8")
    return {**populated, "title_body": title_body, "market_body": market_body, "record": rec_path}


def _item(client, eid="nfl|espn:1", **params):
    items = client.get("/api/sports/events", params=params).json()["items"]
    return next(i for i in items if i["event_id"] == eid)


def test_titles_and_participants_come_from_verified_evidence(evidence, client):
    i = _item(client)
    assert i["title"] == "Away at Home" and i["participants"] == ["Home Team"]
    assert _item(client, q="home team")["event_id"] == "nfl|espn:1"


def test_changed_milestone_evidence_is_rechecked_not_served_from_cache(evidence, client):
    assert _item(client)["title"] == "Away at Home"
    evidence["title_body"].write_bytes(_milestones("Tampered at Elsewhere"))  # meta sha no longer matches
    i = _item(client)
    assert i["title"] is None and i["title_source"].startswith("unavailable")
    _evidence(evidence["root"] / "evidence", _milestone_key(), _milestones("Visitors at Hosts"))
    assert _item(client)["title"] == "Visitors at Hosts"


def test_changed_market_evidence_reports_hash_mismatch(evidence, client):
    eid = "nfl%7Cespn%3A1"
    assert client.get(f"/api/sports/events/{eid}").json()["description_source"]["files"] == {"ok": 1}
    evidence["market_body"].write_bytes(_markets("Someone Else Entirely"))
    detail = client.get(f"/api/sports/events/{eid}").json()
    assert detail["description_source"]["files"] == {"hash_mismatch": 1}
    assert detail["provenance"]["integrity"]["evidence"]["status"] == "PROBLEM"
    assert _item(client)["participants"] == ["Away", "Home"]  # falls back to the verified title only


def test_new_and_corrupted_records_and_scores_are_picked_up(evidence, client):
    before = client.get("/api/sports/summary").json()
    assert before["integrity"]["record_checksum_mismatches"] == 0
    e1 = _contract("KXNFLGAME-E-HOME", "game_winner", FULL)
    _write_record(evidence["capture"], "nfl", "espn:5", "Football", "WINDOW_CAPTURE", NOW - 5 * 86400, [e1],
                  ["KXNFLGAME-E"])
    assert _item(client, "nfl|espn:5")["settlement"]["state"] == "pending"
    _write_score(evidence["scores"], "nfl", "espn:5", "Football", e1, 1)
    assert _item(client, "nfl|espn:5")["settlement"]["state"] == "settled"

    rec = json.loads(evidence["record"].read_text(encoding="utf-8"))
    rec["horizon_minutes"] = 999.0  # changed without updating record_sha256
    evidence["record"].write_text(json.dumps(rec), encoding="utf-8")
    after = client.get("/api/sports/summary").json()
    assert after["counts"]["captured_events"] == before["counts"]["captured_events"] + 1
    assert after["integrity"]["record_checksum_mismatches"] == 1
    assert _item(client)["integrity"]["record_checksum"] == "MISMATCH"

    score = next((evidence["scores"] / "finalized" / "nfl" / "espn_5").glob("*.json"))
    sc = json.loads(score.read_text(encoding="utf-8"))
    sc["y"] = 0  # flipped without updating score_sha256
    score.write_text(json.dumps(sc), encoding="utf-8")
    assert client.get("/api/sports/summary").json()["integrity"]["score_checksum_mismatches"] == 1
    assert client.get("/api/sports/results").json()["counts"]["excluded_score_checksum_problems"] == 1


def test_counts_never_read_evidence_and_lists_enrich_only_visible_rows(evidence, client, monkeypatch):
    full = {i["event_id"]: i for i in S.all_events()}
    calls = []
    real = S._enrich
    monkeypatch.setattr(S, "_enrich", lambda item, s, runs=None: calls.append(item["event_id"]) or real(item, s, runs))
    client.get("/api/sports/summary")
    assert calls == []
    page = client.get("/api/sports/events", params={"page_size": 2}).json()["items"]
    assert calls == [i["event_id"] for i in page]
    assert page == [full[i["event_id"]] for i in page]
    calls.clear()
    searched = client.get("/api/sports/events", params={"q": "home team"}).json()["items"]
    assert len(calls) == len(full) and [i["event_id"] for i in searched] == ["nfl|espn:1"]


def test_concurrent_requests_parse_each_file_once(evidence, client, monkeypatch):
    monkeypatch.setattr(S, "_records", S._Cache())
    monkeypatch.setattr(S, "_scores", S._Cache())
    S.clear_caches()
    parsed = []
    real = S._load_score
    monkeypatch.setattr(S, "_load_score", lambda p: parsed.append(p) or real(p))
    paths = ["/api/sports/summary", "/api/sports/events", "/api/sports/results", "/api/sports/collector"]
    out, barrier = {}, threading.Barrier(len(paths))

    def go(path):
        barrier.wait()
        out[path] = client.get(path).status_code

    threads = [threading.Thread(target=go, args=(p,)) for p in paths]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(out.values()) == {200}
    n_files = len(list((evidence["scores"] / "finalized").glob("*/*/*.json")))
    assert len(parsed) == len(set(parsed)) == n_files

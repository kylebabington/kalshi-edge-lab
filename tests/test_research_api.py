"""Research Hub API: read-only catalog, missing artifacts, independence, status semantics, passivity."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
import requests

from api import weather_index as WI
from research.hub import catalog as CAT
from research.sports import service as S
from research.sports.ops import collector as CO
from tests.test_sports_api import NOW, _snapshot, client, env, populated  # noqa: F401 - fixtures

IDS = {
    "weather": ["weather.phase7_prospective_v1", "weather.clinyc_transfer_v1",
                "weather.shadow_gfs_hrrr_equal_v1", "weather.phase6_v2_1"],
    "sports": ["sports.phase2_capture_v2", "sports.phase1_v1", "sports.phase1_v2", "sports.phase2_v1"],
}
SUCCESS_WORDS = ("COMPLETE", "ACTIVE")


@pytest.fixture
def hub(env, tmp_path, monkeypatch):  # noqa: F811
    results, phase7, logs = tmp_path / "results", tmp_path / "phase7", tmp_path / "wlogs"
    for d in (results, phase7, logs):
        d.mkdir()
    monkeypatch.setattr(CAT, "RESULTS_ROOT", results)
    monkeypatch.setattr(S, "RESULTS_ROOT", results)
    monkeypatch.setattr(CAT, "PHASE7_ROOT", phase7)
    monkeypatch.setattr(WI, "LOG_DIR", logs)
    CAT.clear_caches()
    return {**env, "results": results, "phase7": phase7, "wlogs": logs}


def _cards(client, category):  # noqa: F811
    r = client.get("/api/research/experiments", params={"category": category})
    assert r.status_code == 200
    return {c["id"]: c for c in r.json()["experiments"]}


def _walk_records(obj):
    if isinstance(obj, dict):
        if {"kind", "state", "basis"} <= set(obj):
            yield obj
        for v in obj.values():
            yield from _walk_records(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_records(v)


def test_stable_category_qualified_ids(hub, client):  # noqa: F811
    for category, ids in IDS.items():
        cards = _cards(client, category)
        assert list(cards) == ids
        assert all(c["category"] == category and c["id"].startswith(category + ".") for c in cards.values())
    assert client.get("/api/research/experiments").status_code == 422
    assert client.get("/api/research/experiments", params={"category": "market"}).status_code == 422
    assert client.get("/api/research/experiments/weather.nope").status_code == 404


def test_missing_local_artifacts_are_unknown_never_success(hub, client):  # noqa: F811
    cards = _cards(client, "weather")
    p7 = cards["weather.phase7_prospective_v1"]
    counts = {s["label"]: s for s in p7["samples"]}
    for label in ("Checkpoint receipts", "Prediction records", "Final scores"):
        assert counts[label]["value"] is None and counts[label]["reason"]
    assert counts["Registered target dates"]["value"] == 60
    assert p7["status"]["operations"]["state"] == "unknown"
    assert p7["lifecycle"]["value"] == "collector unknown"
    assert p7["results_available"] is False

    cl = cards["weather.clinyc_transfer_v1"]
    assert cl["lifecycle"]["value"] == "transfer status unknown"
    prospective = next(s for s in cl["samples"] if s["label"] == "Prospective paired events")
    assert prospective["value"] is None and prospective["reason"]
    ev = {e["label"]: e["state"] for e in cl["status"]["evidence"]}
    assert ev["Transfer assessment"] == "missing"
    assert ev["Registered hypothesis"] == "present"

    for card in cards.values():
        assert card["status"]["integrity"]["state"] == "not_checked"
        text = json.dumps(card)
        assert not any(f'"{w}"' in text for w in SUCCESS_WORDS)
        for rec in _walk_records(card):
            if rec["state"] in ("unknown", "not_checked", "missing", "invalid", "failed", "stale"):
                assert rec["reason"], rec


def test_present_artifact_is_presence_and_invalid_is_reported(hub, client):  # noqa: F811
    (hub["results"] / "settlement_transfer_assessment.json").write_text(
        json.dumps({"transfer_status": "experimental", "prospective_n": 5, "prospective_target_n": 20}))
    (hub["results"] / "shadow_model_evaluation.json").write_text("{not json")
    cards = _cards(client, "weather")
    cl = cards["weather.clinyc_transfer_v1"]
    ev = next(e for e in cl["status"]["evidence"] if e["label"] == "Transfer assessment")
    assert ev["state"] == "present" and "not re-verified" in ev["basis"] and ev["as_of"]
    assert {s["label"]: s["value"] for s in cl["samples"]}["Prospective paired events"] == 5
    sh = cards["weather.shadow_gfs_hrrr_equal_v1"]
    ev = next(e for e in sh["status"]["evidence"] if e["label"] == "Shadow evaluation")
    assert ev["state"] == "invalid"
    assert next(r for r in sh["results"] if r["label"].startswith("Shadow evaluation"))["available"] is False


def test_weather_operations_from_task_log(hub, client):  # noqa: F811
    now = datetime.now(timezone.utc)
    log = hub["wlogs"] / f"{now:%Y-%m-%d}.log"
    ok_at = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.write_text(f"{ok_at} [pid 1] cycle start\n{ok_at} [pid 1] cycle end (exit 0)\n")
    op = _cards(client, "weather")["weather.phase7_prospective_v1"]["status"]["operations"]
    assert op["state"] == "recent_success" and op["as_of"]
    fail_at = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.write_text(log.read_text() + f"{fail_at} [pid 2] cycle end (exit 1)\n")
    op = _cards(client, "weather")["weather.phase7_prospective_v1"]["status"]["operations"]
    assert op["state"] == "failed" and "exit code 1" in op["reason"]
    old = (now - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.write_text(f"{old} [pid 1] cycle end (exit 0)\n")
    op = _cards(client, "weather")["weather.phase7_prospective_v1"]["status"]["operations"]
    assert op["state"] == "stale"


def test_sports_operations_from_status_file(hub, client):  # noqa: F811
    cards = _cards(client, "sports")
    assert cards["sports.phase2_capture_v2"]["status"]["operations"]["state"] == "unknown"
    fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    CO.STATUS_PATH.write_text(json.dumps({"last_cycle": {"finished_at": fresh, "result": "ok"}}))
    CAT.clear_caches()
    assert _cards(client, "sports")["sports.phase2_capture_v2"]["status"]["operations"]["state"] == "recent_success"
    CO.STATUS_PATH.write_text(json.dumps({"last_cycle": {"finished_at": fresh, "result": "error"}}))
    CAT.clear_caches()
    op = _cards(client, "sports")["sports.phase2_capture_v2"]["status"]["operations"]
    assert op["state"] == "failed" and "error" in op["reason"]


def test_sports_capture_counts_from_saved_records(populated, hub, client):  # noqa: F811
    S.records()  # the API warm-up thread parses records at startup
    card = _cards(client, "sports")["sports.phase2_capture_v2"]
    counts = {s["label"]: s["value"] for s in card["samples"]}
    assert counts["Captured events"] == 3
    assert counts["Scored events"] == 1
    assert card["period"]["start"] and card["period"]["end"]
    d = client.get("/api/research/experiments/sports.phase2_capture_v2").json()
    blocks = {b["mode"]: b for b in d["mode_blocks"]}
    assert blocks["historical_replay"]["applies"] is False
    assert blocks["manual_pilot"]["applies"] and blocks["automatic_collection"]["applies"]
    assert d["status"]["integrity"]["state"] == "verified"


def test_empty_sports_is_unknown(hub, client):  # noqa: F811
    card = _cards(client, "sports")["sports.phase2_capture_v2"]
    assert all(s["value"] is None and s["reason"] for s in card["samples"])
    assert card["period"] is None and card["period_reason"]
    d = client.get("/api/research/experiments/sports.phase2_capture_v2").json()
    assert d["status"]["integrity"]["state"] == "not_checked"


def test_cold_sports_index_does_not_block_catalog(populated, hub, client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(S, "_records", S._Cache())

    def boom():
        raise AssertionError("catalog must not parse the whole sports index on a cold start")

    monkeypatch.setattr(S, "get_summary", boom)
    card = _cards(client, "sports")["sports.phase2_capture_v2"]
    assert card["loading"] is True
    assert all(s["value"] is None for s in card["samples"])


def test_categories_load_independently(hub, client, monkeypatch):  # noqa: F811
    def broken(_detail):
        raise RuntimeError("sports index broken")

    entry = next(e for e in CAT.REGISTRY if e["id"] == "sports.phase2_capture_v2")
    monkeypatch.setitem(entry, "build", broken)
    sports = _cards(client, "sports")
    assert "sports index broken" in sports["sports.phase2_capture_v2"]["error"]
    assert sports["sports.phase2_capture_v2"]["status"]["operations"]["state"] == "unknown"
    assert sports["sports.phase1_v2"]["error"] is None
    weather = _cards(client, "weather")
    assert all(c["error"] is None for c in weather.values())


def test_detail_sections_and_read_only_settings(hub, client):  # noqa: F811
    d = client.get("/api/research/experiments/sports.phase1_v2").json()
    assert d["question"] and d["reference"] and d["limitations"]
    assert d["cohort_rules"]
    assert [b["mode"] for b in d["mode_blocks"]] == list(CAT.MODES)
    assert d["frozen_settings"]["protocol"] == "sports_phase1_v2"
    assert d["frozen_settings"]["competitions"]["omitted_for_display"] is True
    tracked = [s for s in d["sources"] if s["tracked"]]
    local = [s for s in d["sources"] if not s["tracked"]]
    assert tracked and local and all(not s["exists"] for s in local)
    integ = d["status"]["integrity"]
    assert integ["kind"] == "integrity" and integ["state"] in ("verified", "failed", "not_checked")
    assert integ["checks"]["local_artifacts"] == "absent"

    p7 = client.get("/api/research/experiments/weather.phase7_prospective_v1").json()
    assert p7["status"]["integrity"]["state"] == "not_checked" and p7["status"]["integrity"]["reason"]
    assert {p["state"] for p in p7["status"]["policy"]} >= {"RESEARCH_ONLY", "NO_BET", "SHADOW_ONLY"}


def test_every_tracked_source_is_in_git():
    listed = set(subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True,
                                cwd=CAT.REPO_ROOT).stdout.splitlines())
    declared = set(CAT.tracked_paths())
    assert declared
    assert declared <= listed, sorted(declared - listed)
    for e in CAT.REGISTRY:
        impl = CAT._card(e, False)["status"]["implementation"]
        if impl["state"] == "implemented":
            for path in impl["basis"].removeprefix("Tracked implementation: ").split(", "):
                assert path in listed, path


def test_no_writes_no_network_no_paths(populated, hub, client, monkeypatch):  # noqa: F811
    def no_network(*a, **k):
        raise AssertionError("research handler attempted a network request")

    monkeypatch.setattr(requests.Session, "get", no_network)
    monkeypatch.setattr(requests.Session, "request", no_network)
    before = _snapshot(hub["root"])
    urls = ["/api/research/experiments?category=weather", "/api/research/experiments?category=sports",
            *[f"/api/research/experiments/{i}" for ids in IDS.values() for i in ids]]
    for u in urls:
        r = client.get(u)
        assert r.status_code == 200, u
        assert str(hub["root"]) not in r.text and str(hub["root"]).replace("\\", "\\\\") not in r.text
        assert ":\\\\" not in r.text
    assert _snapshot(hub["root"]) == before

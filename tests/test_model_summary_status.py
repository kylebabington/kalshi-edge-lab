"""Model summary status reporting: no success without evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research import status_records as SR
from research.weather import service as WS

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
REASON_STATES = {"unknown", "not_checked", "missing", "invalid", "failed", "stale"}


def _records(obj):
    if isinstance(obj, dict):
        if {"kind", "state", "basis"} <= set(obj):
            yield obj
        for v in obj.values():
            yield from _records(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _records(v)


def _research(tmp_path: Path, transfer_art=None, validated=False, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(WS, "CALIBRATION_CSV", tmp_path / "gfs_errors.csv")
        monkeypatch.setattr(WS, "PAIRS_CSV", tmp_path / "pairs.csv")
    return WS._research_status(
        SR,
        transfer_art=transfer_art,
        transfer_validated=validated,
        clinyc_eligible=False,
        hrrr_paths=(tmp_path / "hrrr.csv", tmp_path / "hrrr_report.json"),
        transfer_path=tmp_path / "transfer.json",
    )


def test_phases_without_completion_record_are_unknown_not_complete(tmp_path, monkeypatch):
    status, details = _research(tmp_path, monkeypatch=monkeypatch)
    for key in ("weather_phase_1", "weather_phase_2", "weather_phase_4_evidence"):
        assert status[key] == "UNKNOWN"
        assert details[key]["reason"]
        assert details[key]["implementation"]["state"] == "implemented"
        assert all(e["state"] == "missing" for e in details[key]["evidence"])
    assert "COMPLETE" not in status.values()


def test_present_file_is_presence_only(tmp_path, monkeypatch):
    (tmp_path / "gfs_errors.csv").write_text("target_date\n2026-01-01\n")
    status, details = _research(tmp_path, monkeypatch=monkeypatch)
    ev = details["weather_phase_1"]["evidence"][0]
    assert ev["state"] == "present"
    assert "validity not checked" in ev["basis"]
    assert ev["as_of"]
    assert status["weather_phase_1"] == "UNKNOWN"


def test_transfer_missing_is_unavailable_and_policy_states_kept(tmp_path, monkeypatch):
    status, details = _research(tmp_path, monkeypatch=monkeypatch)
    assert status["weather_phase_3_transfer"] == "UNAVAILABLE"
    assert details["weather_phase_3_transfer"]["evidence"][0]["state"] == "missing"
    assert status["weather_phase_5_shadow"] == "SHADOW_ONLY"
    assert status["live_recommendations"] == "DISABLED"
    assert details["live_recommendations"]["kind"] == "policy"


def test_transfer_present_but_not_validated_is_experimental(tmp_path, monkeypatch):
    art = {"transfer_validated": False, "transfer_status": "experimental"}
    (tmp_path / "transfer.json").write_text("{}")
    status, _ = _research(tmp_path, transfer_art=art, monkeypatch=monkeypatch)
    assert status["weather_phase_3_transfer"] == "EXPERIMENTAL"
    status, _ = _research(tmp_path, transfer_art={**art, "transfer_validated": True}, validated=True, monkeypatch=monkeypatch)
    assert status["weather_phase_3_transfer"] == "VALIDATED"


def _snap(created: datetime, **quality):
    evidence = {
        name: {"quality_status": q, "retrieved_at": created.isoformat()} for name, q in quality.items()
    }
    return {"snapshot_id": "S1", "created_at": created.isoformat(), "evidence": evidence}


def test_sources_are_configured_never_active(tmp_path):
    legacy, details = WS._source_status(
        SR, snapshots=[], hrrr_paths=(tmp_path / "a", tmp_path / "b"), nws_point_cache=tmp_path / "c", now=NOW
    )
    assert set(legacy.values()) == {"CONFIGURED"}
    for d in details.values():
        assert d["configuration"]["state"] == "CONFIGURED"
        assert d["operations"]["state"] == "unknown"
        assert d["operations"]["reason"]


def test_source_operations_follow_latest_saved_fetch(tmp_path):
    (tmp_path / "c").write_text("{}")
    recent = _snap(NOW - timedelta(hours=2), gfs="ok", observations="missing", hrrr="partial")
    old = _snap(NOW - timedelta(days=5), gfs="missing")
    old["created_at"] = (NOW - timedelta(days=5)).isoformat()
    legacy, details = WS._source_status(
        SR, snapshots=[old, recent], hrrr_paths=(tmp_path / "a", tmp_path / "b"), nws_point_cache=tmp_path / "c", now=NOW
    )
    assert details["GFS"]["operations"]["state"] == "recent_success"
    assert details["OBS"]["operations"]["state"] == "failed"
    assert details["HRRR"]["operations"]["state"] == "unknown"
    assert details["GEFS"]["operations"]["state"] == "unknown"
    assert legacy["NWS"] == "PRESENT"

    stale = _snap(NOW - timedelta(hours=48), gfs="ok")
    _, details = WS._source_status(
        SR, snapshots=[stale], hrrr_paths=(tmp_path / "a", tmp_path / "b"), nws_point_cache=tmp_path / "c", now=NOW
    )
    assert details["GFS"]["operations"]["state"] == "stale"


@pytest.fixture
def isolated_summary(tmp_path, monkeypatch):
    import research.weather.phase4 as P4
    import research.weather.snapshots as SN
    import research.weather.transfer as TR

    monkeypatch.setattr(WS, "CALIBRATION_CSV", tmp_path / "gfs_errors.csv")
    monkeypatch.setattr(WS, "PAIRS_CSV", tmp_path / "pairs.csv")
    monkeypatch.setattr(WS, "load_calibration_csv", lambda *a, **k: [])
    monkeypatch.setattr(WS, "ensure_weather_cache_dirs", lambda: None)
    monkeypatch.setattr(TR, "TRANSFER_REPORT_PATH", tmp_path / "transfer.json")
    monkeypatch.setattr(TR, "load_transfer_assessment", lambda: None)
    monkeypatch.setattr(P4, "HRRR_CALIBRATION_CSV", tmp_path / "hrrr.csv")
    monkeypatch.setattr(P4, "HRRR_EVAL_REPORT", tmp_path / "hrrr.json")
    monkeypatch.setattr(SN, "iter_all_snapshots", lambda: [])
    monkeypatch.setattr(
        SN,
        "load_all_scores",
        lambda: [{"event_ticker": "E1"}, {"event_ticker": "E1"}, {"event_ticker": "E2"}],
    )
    monkeypatch.setattr(WS, "_prospective_validation_summary", lambda transfer_status=None: {"status": "SHADOW ONLY"})
    return WS.get_model_summary()


def test_summary_has_no_invented_success(isolated_summary):
    s = isolated_summary
    assert "COMPLETE" not in s["research_status"].values()
    assert "ACTIVE" not in s["multi_source_evidence"].values()
    assert s["research_status"]["weather_phase_3_transfer"] == "UNAVAILABLE"
    assert s["settlement_transfer"]["stale"] is None
    assert s["settlement_transfer"]["available"] is False
    assert s["prospective_journal"]["scored_snapshots"] == 3
    assert s["prospective_journal"]["settled_events_scored"] == 2
    assert s["mode"] == "RESEARCH_ONLY"


def test_every_unknown_record_has_a_reason(isolated_summary):
    records = list(_records(isolated_summary["research_status_details"])) + list(
        _records(isolated_summary["multi_source_evidence_details"])
    )
    assert records
    for r in records:
        if r["state"] in REASON_STATES:
            assert r["reason"], r


def test_prospective_validation_does_not_default_to_zero_or_experimental(monkeypatch, tmp_path):
    import research.weather.checkpoints as CK
    import research.weather.phase5 as P5

    for name in ("PROSPECTIVE_SUMMARY_PATH", "OPERATIONAL_COMPARISON_PATH", "SHADOW_EVAL_PATH", "ERROR_CORRELATION_PATH"):
        monkeypatch.setattr(P5, name, tmp_path / f"{name}.json")
    monkeypatch.setattr(CK, "list_receipts", lambda: [])
    pv = WS._prospective_validation_summary()
    assert pv["status"] == "SHADOW ONLY"
    assert pv["scored_events"] is None
    assert pv["experimental_transfer_status"] == "unknown"
    assert pv["historical_replay_kind"] == "methodology"
    assert WS._prospective_validation_summary(transfer_status="EXPERIMENTAL")["experimental_transfer_status"] == "experimental"

"""Phase 7 historical replay: isolation, inventory, probe, reconstruction, agreement."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.weather import phase7
from research.weather import phase7_historical_replay as replay
from research.weather import phase7_method as method
from research.weather.checkpoints import checkpoint_scheduled_at
from tests.test_phase7_prospective import (
    LABELS,
    REGISTERED_AT,
    TARGET,
    Clock,
    FakeRuns,
    _capture,
    _default_obs,
    _iem_csv,
    _markets,
    _payload,
    _pool_rows,
)


@pytest.fixture
def paths(tmp_path: Path) -> phase7.Phase7Paths:
    p = phase7.Phase7Paths(root=tmp_path / "phase7", protocol_path=tmp_path / "protocol.json")
    gfs, hrrr = _pool_rows()
    phase7.register_protocol(registered_at=REGISTERED_AT, paths=p, gfs_rows=gfs, hrrr_rows=hrrr)
    return p


def _iem_month(_start, _end, **_kw):
    text = _iem_csv(_default_obs())
    return text, {
        "status": "ok",
        "row_count": len(_default_obs()),
        "http_status": 200,
        "retrieved_at": "2026-10-30T00:00:00+00:00",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "from_cache": False,
    }


def _event_ticker_for(target_date: str) -> str:
    return "KXHIGHNY-" + datetime.strptime(target_date, "%Y-%m-%d").strftime("%y%b%d").upper()


def _markets_for_date(target_date: str, *, settled: bool = True) -> list[dict]:
    event = _event_ticker_for(target_date)
    out = []
    for i, label in enumerate(LABELS):
        row = {"ticker": f"{event}-B{i}", "yes_sub_title": label, "event_ticker": event, "status": "active"}
        if settled:
            row.update({
                "status": "finalized",
                "result": "yes" if label == LABELS[2] else "no",
                "expiration_value": "65",
            })
        out.append(row)
    return out


def _kalshi_settled():
    dates = [(datetime(2026, 10, 3) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]
    out: list[dict] = []
    for target_date in dates:
        out.extend(_markets_for_date(target_date))
    return out


def _replay_paths(tmp_path: Path, phase7_paths: phase7.Phase7Paths) -> replay.ReplayPaths:
    return replay.ReplayPaths(root=tmp_path / "phase7_historical_replay", phase7_root=phase7_paths.root)


def _fetchers(start: datetime, *, runs: dict | None = None, step_s: float = 1.0, iem=_iem_month):
    clock = Clock(start, step_s)
    return replay.ReplayFetchers(
        run_hourly=FakeRuns(clock, runs),
        iem_month=iem,
        kalshi_historical=_kalshi_settled,
        kalshi_settled=lambda: [],
        kalshi_open=lambda: [],
        clock=clock,
        sleep=lambda _s: None,
    )


def _write_probe_ok(paths: replay.ReplayPaths) -> None:
    replay.write_json(paths.probe, {
        "dataset_id": replay.DATASET_ID,
        "archives_retrievable": True,
        "blockers": [],
    }, phase7_root=paths.phase7_root)


def _hashes(root: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def test_replay_root_cannot_sit_under_phase7(paths, tmp_path):
    with pytest.raises(replay.ReplayIsolationError):
        replay.ReplayPaths(root=paths.root / "historical_replay", phase7_root=paths.root)
    with pytest.raises(replay.ReplayIsolationError):
        replay.assert_isolated_write(paths.root / "records" / "x.json", phase7_root=paths.root)
    with pytest.raises(replay.ReplayIsolationError):
        replay.assert_isolated_write(phase7.PROTOCOL_PATH)


def test_inventory_matches_missed_receipts_one_to_one(paths, tmp_path):
    _capture(paths, "d0_1200")
    after = checkpoint_scheduled_at(TARGET, "d0_1500") + timedelta(minutes=31)
    phase7.reconcile_missed(now=after, paths=paths)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    missed = [r for r in inv["rows"] if r["receipt_status"] == phase7.RECEIPT_MISSED]
    receipt_missed = []
    for path in sorted(paths.receipts.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == phase7.RECEIPT_MISSED:
            receipt_missed.append((data["target_date"], data["checkpoint_id"]))
    assert {(r["target_date"], r["checkpoint_id"]) for r in missed} == set(receipt_missed)
    captured = [r for r in inv["rows"] if r["receipt_status"] == phase7.RECEIPT_CAPTURED]
    assert any(r["checkpoint_id"] == "d0_1200" and r["stratum"] == replay.STRATUM_AGREEMENT for r in captured)
    assert all(r["stratum"] == replay.STRATUM_MISSED for r in missed)
    d0900 = [r for r in missed if r["checkpoint_id"] == "d0_0900"]
    assert d0900 and all(r["structurally_unavailable"] and r["priority_band"] == "structurally_unavailable" for r in d0900)
    primary = [r for r in missed if r["checkpoint_id"] in replay.PRIORITY_PRIMARY]
    assert primary and all(r["priority_band"] == "primary" for r in primary)
    before = _hashes(paths.root)
    replay.write_inventory(inv, _replay_paths(tmp_path, paths))
    assert _hashes(paths.root) == before


def test_probe_confirms_archives_and_never_writes_phase7(paths, tmp_path):
    _capture(paths, "d0_1200")
    after = checkpoint_scheduled_at(TARGET, "d0_1500") + timedelta(minutes=31)
    phase7.reconcile_missed(now=after, paths=paths)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    rpaths = _replay_paths(tmp_path, paths)
    before = _hashes(paths.root)
    scheduled = checkpoint_scheduled_at(TARGET, "d0_1200")
    probe = replay.run_probe(
        inv, fetchers=_fetchers(scheduled + timedelta(minutes=5)), paths=rpaths, include_captured=True,
    )
    assert probe["archives_retrievable"] is True
    assert probe["blockers"] == []
    assert (rpaths.probe).is_file()
    assert not (paths.root / "probe.json").exists()
    assert _hashes(paths.root) == before
    primary_missed = [c for c in probe["checkpoints"] if c["stratum"] == replay.STRATUM_MISSED and c["priority_band"] == "primary"]
    assert primary_missed
    assert all(c["gfs"]["retrievable"] and c["hrrr"]["retrievable"] and c["kalshi_ok"] for c in primary_missed)


def test_probe_failure_sets_blockers(paths, tmp_path):
    after = checkpoint_scheduled_at(TARGET, "d0_1500") + timedelta(minutes=31)
    phase7.reconcile_missed(now=after, paths=paths)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    rpaths = _replay_paths(tmp_path, paths)

    def boom_iem(*_a, **_k):
        return None, {"status": "error", "error": "down"}

    fetchers = _fetchers(after)
    fetchers.iem_month = boom_iem
    fetchers.kalshi_historical = lambda: []
    fetchers.run_hourly = FakeRuns(Clock(after), {("ncep_gfs_global", "2026-10-05T06Z"): {"kind": "http_error", "status": 404}})
    probe = replay.run_probe(inv, fetchers=fetchers, paths=rpaths, include_captured=False)
    assert probe["archives_retrievable"] is False
    assert "iem_archive_unavailable" in probe["blockers"]
    assert "kalshi_markets_unavailable" in probe["blockers"]


def test_reconstruction_blocked_without_successful_probe(paths, tmp_path):
    rpaths = _replay_paths(tmp_path, paths)
    inv = replay.build_inventory(phase7_paths=paths, now=checkpoint_scheduled_at(TARGET, "d0_1500") + timedelta(hours=1))
    with pytest.raises(replay.ReplayBlockedError, match="archive probe has not been run"):
        replay.run_reconstruction(inv, phase7_paths=paths, paths=rpaths, require_probe=True)
    replay.write_json(rpaths.probe, {"archives_retrievable": False, "blockers": ["iem_archive_unavailable"]},
                      phase7_root=rpaths.phase7_root)
    with pytest.raises(replay.ReplayBlockedError, match="iem_archive_unavailable"):
        replay.run_reconstruction(inv, phase7_paths=paths, paths=rpaths, require_probe=True)


def test_method_fidelity_matches_live_capture_on_same_payloads(paths, tmp_path):
    live, _ = _capture(paths, "d0_1200")
    rpaths = _replay_paths(tmp_path, paths)
    _write_probe_ok(rpaths)
    after = checkpoint_scheduled_at(TARGET, "d0_1200") + timedelta(minutes=5)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    before = _hashes(paths.root)
    result = replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=_fetchers(after),
        require_probe=True, include_captured=True, nws_actual_by_date={},
        respect_live_windows=False,
    )
    assert _hashes(paths.root) == before
    rec = json.loads(rpaths.record_path(TARGET, "d0_1200").read_text(encoding="utf-8"))
    assert rec["schema"] == replay.RECORD_SCHEMA
    assert rec["not_a_phase7_record"] is True
    assert rec["primary_cohort_eligible"] is False
    assert rec["cohort_eligible"] is False
    assert "prediction_as_of" not in rec
    assert rec["reconstructed_at"]
    live_pred = live["record"]["prediction"]
    for name in ("research_gfs", "research_hrrr", "research_shadow"):
        assert rec["prediction"][name]["probabilities"] == live_pred[name]["probabilities"]
    for name in ("research_gfs", "research_hrrr"):
        assert rec["prediction"][name]["selected_run"]["run_init"] == live_pred[name]["selected_run"]["run_init"]
    assert rec["prediction"]["paired_valid"] is True
    agreement = next(a for a in result["agreements"] if a["checkpoint_id"] == "d0_1200")
    assert agreement["match"] is True
    assert rec["as_of_classes"]["gfs_hrrr"] == replay.AS_OF_ARCHIVE_NOW
    assert rec["kalshi_as_of_class"] == replay.AS_OF_POST_SETTLEMENT
    assert rec["evidence"]["observations_fetch"]["as_of_class"] == replay.AS_OF_ARCHIVE_NOW


def test_d0_0900_stays_structurally_unpaired(paths, tmp_path):
    _capture(paths, "d0_0900")
    rpaths = _replay_paths(tmp_path, paths)
    _write_probe_ok(rpaths)
    after = checkpoint_scheduled_at(TARGET, "d0_0900") + timedelta(minutes=5)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=_fetchers(after),
        require_probe=True, include_captured=True, nws_actual_by_date={},
        respect_live_windows=False,
    )
    rec = json.loads(rpaths.record_path(TARGET, "d0_0900").read_text(encoding="utf-8"))
    assert rec["structurally_unavailable"] is True
    pred = rec["prediction"]
    for name in ("research_gfs", "research_hrrr"):
        assert pred[name]["calibration_feasibility"]["status"] == method.CALIBRATION_INSUFFICIENT_DETERMINISTIC
        assert pred[name]["status"] == "UNAVAILABLE"
        assert pred[name]["reason"] == "calibration_INSUFFICIENT_HISTORY"
    assert pred["paired_valid"] is False
    assert pred["research_shadow"]["status"] == "UNAVAILABLE"


def test_incomplete_hrrr_is_not_replaced_by_older_run(paths, tmp_path):
    init = datetime(2026, 10, 5, 13, tzinfo=timezone.utc)
    runs = {("ncep_hrrr_conus", "2026-10-05T13Z"): {"kind": "ok", "payload": _payload(init, hours=12)}}
    _capture(paths, "d0_1200", runs=runs)
    rpaths = _replay_paths(tmp_path, paths)
    _write_probe_ok(rpaths)
    after = checkpoint_scheduled_at(TARGET, "d0_1200") + timedelta(minutes=5)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=_fetchers(after, runs=runs),
        require_probe=True, include_captured=True, nws_actual_by_date={},
        respect_live_windows=False,
    )
    rec = json.loads(rpaths.record_path(TARGET, "d0_1200").read_text(encoding="utf-8"))
    hrrr = rec["prediction"]["research_hrrr"]
    assert hrrr["status"] == "UNAVAILABLE"
    assert "model_window_unavailable" in hrrr["reason"]
    assert hrrr["selected_run"]["run_init"] == init.isoformat()
    assert rec["prediction"]["paired_valid"] is False


def test_agreement_reports_probability_and_run_mismatches(paths, tmp_path):
    live, _ = _capture(paths, "d0_1200")
    rpaths = _replay_paths(tmp_path, paths)
    _write_probe_ok(rpaths)
    after = checkpoint_scheduled_at(TARGET, "d0_1200") + timedelta(minutes=5)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    other_init = datetime(2026, 10, 5, 6, tzinfo=timezone.utc)
    runs = {("ncep_gfs_global", "2026-10-05T06Z"): {"kind": "ok", "payload": _payload(other_init, base=80.0)}}
    result = replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=_fetchers(after, runs=runs),
        require_probe=True, include_captured=True, nws_actual_by_date={},
        respect_live_windows=False,
    )
    agreement = next(a for a in result["agreements"] if a["checkpoint_id"] == "d0_1200")
    assert agreement["match"] is False
    assert "research_gfs:probabilities" in agreement["mismatches"]
    live_gfs = live["record"]["prediction"]["research_gfs"]["probabilities"]
    replay_rec = json.loads(rpaths.record_path(TARGET, "d0_1200").read_text(encoding="utf-8"))
    assert replay_rec["prediction"]["research_gfs"]["probabilities"] != live_gfs


def test_missing_kalshi_buckets_is_unrecoverable(paths, tmp_path):
    after = checkpoint_scheduled_at(TARGET, "d0_1200") + timedelta(minutes=31)
    phase7.reconcile_missed(now=after, paths=paths)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    rpaths = _replay_paths(tmp_path, paths)
    _write_probe_ok(rpaths)
    fetchers = _fetchers(after)
    fetchers.kalshi_historical = lambda: []
    fetchers.kalshi_open = lambda: []
    result = replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=fetchers,
        require_probe=True, include_captured=False, nws_actual_by_date={},
        respect_live_windows=False,
    )
    assert result["reconstructed_n"] == 0
    assert result["unrecoverable_n"] > 0
    assert all(u["reason"] == "kalshi_buckets_unavailable" for u in result["unrecoverable"])
    assert not any(phase7.record_path(paths, r["target_date"], r["checkpoint_id"]).exists()
                   for r in inv["rows"] if r["receipt_status"] == phase7.RECEIPT_MISSED)


def test_report_covers_misses_probe_and_reconstruction(paths, tmp_path):
    _capture(paths, "d0_1200")
    after = checkpoint_scheduled_at(TARGET, "d0_1500") + timedelta(minutes=31)
    phase7.reconcile_missed(now=after, paths=paths)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    rpaths = _replay_paths(tmp_path, paths)
    probe = replay.run_probe(inv, fetchers=_fetchers(after), paths=rpaths, include_captured=True)
    _write_probe_ok(rpaths)
    reconstruction = replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=_fetchers(after),
        require_probe=True, include_captured=True, nws_actual_by_date={},
        respect_live_windows=False,
    )
    text = replay.build_report(inv, probe, reconstruction)
    assert f"**MISSED: {inv['counts']['missed']}**" in text
    assert "Successfully reconstructed" in text
    assert "Paired GFS/HRRR/shadow" in text
    assert "Agreement with previously CAPTURED" in text
    assert "never enters the prospective cohort" in text
    report_path = replay.write_report(inv, probe=probe, reconstruction=reconstruction, paths=rpaths)
    assert report_path == rpaths.report
    assert rpaths.report.is_file()
    assert not (paths.root / "REPORT.md").exists()


def test_recent_settled_kalshi_fills_dates_missing_from_historical(paths, tmp_path):
    _capture(paths, "d0_1200")
    rpaths = _replay_paths(tmp_path, paths)
    _write_probe_ok(rpaths)
    after = checkpoint_scheduled_at(TARGET, "d0_1200") + timedelta(minutes=5)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    fetchers = _fetchers(after)
    fetchers.kalshi_historical = lambda: []
    fetchers.kalshi_settled = lambda: _markets_for_date(TARGET)
    replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=fetchers,
        require_probe=True, include_captured=True, nws_actual_by_date={},
        respect_live_windows=False,
    )
    rec = json.loads(rpaths.record_path(TARGET, "d0_1200").read_text(encoding="utf-8"))
    assert rec["kalshi_as_of_class"] == replay.AS_OF_POST_SETTLEMENT
    assert rec["event_ticker"].startswith("KXHIGHNY-")


def test_current_open_kalshi_is_flagged_unverifiable_as_of(paths, tmp_path):
    _capture(paths, "d0_1200")
    rpaths = _replay_paths(tmp_path, paths)
    _write_probe_ok(rpaths)
    after = checkpoint_scheduled_at(TARGET, "d0_1200") + timedelta(minutes=5)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    fetchers = _fetchers(after)
    fetchers.kalshi_historical = lambda: []
    fetchers.kalshi_open = lambda: _markets()
    replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=fetchers,
        require_probe=True, include_captured=True, nws_actual_by_date={},
        respect_live_windows=False,
    )
    rec = json.loads(rpaths.record_path(TARGET, "d0_1200").read_text(encoding="utf-8"))
    assert rec["kalshi_as_of_class"] == replay.AS_OF_CURRENT_OPEN


def test_integrity_allows_collector_additions_and_flags_unexpected(paths):
    (paths.records / "2026-10-03").mkdir(parents=True)
    original = paths.records / "2026-10-03" / "d0_1200.json"
    original.write_text('{"ok": true}\n', encoding="utf-8")
    paths.fetch_log.write_text('{"line": 1}\n', encoding="utf-8")
    pending = paths.pending / "2026-10-03"
    pending.mkdir(parents=True)
    pending_file = pending / "d0_0600.json"
    pending_file.write_text('{"pending": true}\n', encoding="utf-8")
    before = replay.build_phase7_manifest(phase7_paths=paths)

    (paths.records / "2026-10-03" / "d0_1500.json").write_text('{"new": true}\n', encoding="utf-8")
    paths.fetch_log.write_text('{"line": 1}\n{"line": 2}\n', encoding="utf-8")
    pending_file.unlink()
    (paths.scores / "2026-10-03").mkdir(parents=True, exist_ok=True)
    (paths.scores / "2026-10-03" / "d0_0600.json").write_text('{"final": true}\n', encoding="utf-8")

    diff = replay.compare_phase7_integrity(before, phase7_paths=paths)
    assert diff["ok"] is True
    assert any(p.endswith("records/2026-10-03/d0_1500.json") for p in diff["collector_additions"])
    assert any(p.endswith("scores/2026-10-03/d0_0600.json") for p in diff["collector_additions"])
    assert diff["append_only_extended_n"] == 1
    assert any(t["change"] == "removed" for t in diff["pending_transitions"])
    assert diff["unexpected"] == []

    original.write_text('{"tampered": true}\n', encoding="utf-8")
    diff2 = replay.compare_phase7_integrity(before, phase7_paths=paths)
    assert diff2["ok"] is False
    assert any(u["reason"] == "content_changed" for u in diff2["unexpected"])


def test_integrity_detects_append_only_prefix_corruption(paths):
    paths.fetch_log.write_text('{"line": 1}\n{"line": 2}\n', encoding="utf-8")
    before = replay.build_phase7_manifest(phase7_paths=paths)
    paths.fetch_log.write_text('{"tampered": true}\n{"line": 2}\n{"line": 3}\n', encoding="utf-8")
    diff = replay.compare_phase7_integrity(before, phase7_paths=paths)
    assert diff["ok"] is False
    assert any(u["reason"] == "append_only_prefix_mismatch" for u in diff["unexpected"])


def test_reconstruction_stops_before_live_capture_window(paths, tmp_path):
    after = checkpoint_scheduled_at(TARGET, "d0_1500") + timedelta(minutes=31)
    phase7.reconcile_missed(now=after, paths=paths)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    rpaths = _replay_paths(tmp_path, paths)
    _write_probe_ok(rpaths)
    near_window = checkpoint_scheduled_at(TARGET, "dminus1_1800") - timedelta(seconds=30)
    result = replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=_fetchers(near_window),
        require_probe=True, include_captured=False, nws_actual_by_date={},
        respect_live_windows=True, time_budget=timedelta(seconds=90),
    )
    assert result["status"] == "stopped_for_live_window"
    assert result["reconstructed_n"] == 0
    assert result["remaining_n"] > 0
    assert result["stopped_reason"] == "live_capture_window"


def test_reconstruction_is_resumable_without_duplicating_records(paths, tmp_path):
    _capture(paths, "d0_1200")
    after = checkpoint_scheduled_at(TARGET, "d0_1500") + timedelta(minutes=31)
    phase7.reconcile_missed(now=after, paths=paths)
    inv = replay.build_inventory(phase7_paths=paths, now=after)
    rpaths = _replay_paths(tmp_path, paths)
    _write_probe_ok(rpaths)
    first = replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=_fetchers(after),
        require_probe=True, include_captured=True, nws_actual_by_date={},
        respect_live_windows=False,
    )
    record_path = rpaths.record_path(TARGET, "d0_1200")
    digest = hashlib.sha256(record_path.read_bytes()).hexdigest()
    second = replay.run_reconstruction(
        inv, phase7_paths=paths, paths=rpaths, fetchers=_fetchers(after),
        require_probe=True, include_captured=True, nws_actual_by_date={},
        respect_live_windows=False,
    )
    assert second["resumed_n"] == second["reconstructed_n"]
    assert hashlib.sha256(record_path.read_bytes()).hexdigest() == digest
    assert first["reconstructed_n"] == second["reconstructed_n"]


def test_diagnostic_accuracy_uses_same_checkpoints_for_model_comparison():
    def _score(brier: float, log_loss: float) -> dict:
        return {"brier": brier, "log_loss": log_loss}

    scores = [
        {
            "status": "ok",
            "winning_bucket": "64° to 65°",
            "target_date": "2026-10-03",
            "checkpoint_id": "d0_1200",
            "streams": {
                "research_gfs": {"score": _score(0.20, 0.50)},
                "research_hrrr": {"score": _score(0.10, 0.40)},
                "research_shadow": {"score": _score(0.30, 0.60)},
            },
        },
        {
            "status": "ok",
            "winning_bucket": "64° to 65°",
            "target_date": "2026-10-04",
            "checkpoint_id": "d0_0900",
            "streams": {
                "research_gfs": {"score": _score(0.40, 0.80)},
                "research_hrrr": {"score": None},
                "research_shadow": {"score": _score(0.50, 0.90)},
            },
        },
    ]
    acc = replay.diagnostic_accuracy(scores)
    assert acc["label"] == "HISTORICAL_REPLAY_DIAGNOSTIC"
    assert acc["not_combined_with_phase7_primary_cohort"] is True
    assert acc["gfs"]["n"] == 2
    assert acc["hrrr"]["n"] == 1
    assert acc["shadow"]["n"] == 2
    assert acc["paired_all_three_n"] == 1
    assert acc["paired_shadow_minus_gfs_brier"] == pytest.approx(0.10)
    assert acc["paired_shadow_minus_hrrr_brier"] == pytest.approx(0.20)
    assert acc["gfs"]["multiclass_brier"] == pytest.approx(0.30)

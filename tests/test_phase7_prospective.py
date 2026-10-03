"""Phase 7 prospective validation: protocol, capture timing, selection, scoring."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.weather import phase7
from research.weather import phase7_method as method
from research.weather.checkpoints import NYC_TZ, checkpoint_scheduled_at
from research.weather.models import (
    MODEL_GFS_OPERATIONAL_LATEST as GFS,
    MODEL_HRRR_OPERATIONAL_LATEST as HRRR,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    REPLAY_MODE_FULL_OPERATIONAL,
    REPLAY_MODE_MODEL_ONLY,
)
from research.weather.resolution import season_for_month
from research.weather.sources.single_run_hourly import (
    WINDOW_REASON_HORIZON_NULL_PADDED,
    WINDOW_REASON_HORIZON_SHORT,
    classify_hourly_payload,
    fetch_run_hourly_logged,
)

REGISTERED_AT = datetime(2026, 10, 2, 12, 0, tzinfo=NYC_TZ)
TARGET = "2026-10-05"
EVENT = "KXHIGHNY-26OCT05"
LABELS = ["61° or below", "62° to 63°", "64° to 65°", "66° to 67°", "68° to 69°", "70° to 71°", "72° or above"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pool_rows() -> tuple[list[dict], list[dict]]:
    gfs: list[dict] = []
    hrrr: list[dict] = []
    counts = {"dminus1_1800": 90, "d0_0600": 90, "d0_0900": 61, "d0_1200": 90, "d0_1500": 90}
    for cp, n in counts.items():
        start = date(2026, 4, 1) if cp != "d0_0900" else date(2026, 5, 21)
        for i in range(n):
            d = (start + timedelta(days=i)).isoformat()
            m = int(d[5:7])
            for model, rows, shift in ((GFS, gfs, 0), (HRRR, hrrr, 1)):
                rows.append(
                    {
                        "model": model,
                        "event_ticker": f"KXHIGHNY-{d}",
                        "target_date": d,
                        "checkpoint_id": cp,
                        "target_regime": REGIME_NWS_CLI_KNYC,
                        "replay_mode": REPLAY_MODE_FULL_OPERATIONAL,
                        "month": str(m),
                        "season": season_for_month(m),
                        "forecast_high_f": "70",
                        "actual_high_f": "70",
                        "residual_f": str(((i * 7 + shift) % 9) - 4),
                    }
                )
    # A key where only GFS is FULL must be excluded for BOTH models.
    gfs.append({**gfs[0], "target_date": "2026-03-01", "event_ticker": "X1"})
    hrrr.append({**hrrr[0], "target_date": "2026-03-01", "event_ticker": "X1", "replay_mode": REPLAY_MODE_MODEL_ONLY})
    # CLINYC-regime rows are never part of the KNYC pool.
    gfs.append({**gfs[0], "target_date": "2026-03-02", "event_ticker": "X2", "target_regime": REGIME_WEATHER_COMPANY_CLINYC})
    hrrr.append({**hrrr[0], "target_date": "2026-03-02", "event_ticker": "X2", "target_regime": REGIME_WEATHER_COMPANY_CLINYC})
    return gfs, hrrr


@pytest.fixture
def paths(tmp_path: Path) -> phase7.Phase7Paths:
    p = phase7.Phase7Paths(root=tmp_path / "phase7", protocol_path=tmp_path / "protocol.json")
    gfs, hrrr = _pool_rows()
    phase7.register_protocol(registered_at=REGISTERED_AT, paths=p, gfs_rows=gfs, hrrr_rows=hrrr)
    return p


def _markets(*, settled: bool = False, winner: str | None = None, expiration: str | None = None) -> list[dict]:
    out = []
    for i, label in enumerate(LABELS):
        m = {"ticker": f"{EVENT}-B{i}", "yes_sub_title": label, "event_ticker": EVENT, "status": "active"}
        if settled:
            m.update({"status": "finalized", "result": "yes" if label == winner else "no"})
            if expiration is not None:
                m["expiration_value"] = expiration
        out.append(m)
    return out


def _event(regime: str = REGIME_WEATHER_COMPANY_CLINYC) -> dict:
    return {"event_ticker": EVENT, "target_date": TARGET, "markets": _markets(), "regime": regime}


class Clock:
    def __init__(self, start: datetime, step_s: float = 1.0):
        self.t = start.astimezone(timezone.utc)
        self.step = timedelta(seconds=step_s)

    def __call__(self) -> datetime:
        self.t += self.step
        return self.t


def _payload(init: datetime, *, hours: int = 48, real_hours: int | None = None, base: float = 66.0) -> dict:
    times, temps = [], []
    for h in range(hours + 1):
        t = init + timedelta(hours=h)
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        temps.append(None if real_hours is not None and h > real_hours else base + (h % 5))
    return {"utc_offset_seconds": 0, "timezone": "GMT", "hourly": {"time": times, "temperature_2m": temps}}


class FakeRuns:
    """Single Runs stand-in. Default: every requested run is a complete 48 h payload."""

    def __init__(self, clock: Clock, overrides: dict | None = None):
        self.clock = clock
        self.overrides = overrides or {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, api_model: str, run_init: datetime) -> dict:
        key = (api_model, run_init.strftime("%Y-%m-%dT%HZ"))
        self.calls.append(key)
        spec = self.overrides.get(key, {"kind": "ok"})
        meta = {"fetch_started_at": self.clock().isoformat()}
        if spec["kind"] == "http_error":
            meta.update(http_status=spec.get("status", 500), fetch_completed_at=self.clock().isoformat(), error="boom")
            return {"outcome": "http_error", "payload": None, "raw_text": '{"error":true,"reason":"x"}', "meta": meta}
        payload = spec.get("payload") or _payload(run_init)
        meta.update(http_status=200, fetch_completed_at=self.clock().isoformat())
        reason = classify_hourly_payload(payload)
        outcome = "ok" if reason is None else "unusable_response"
        if reason:
            meta["unusable_reason"] = reason
        return {"outcome": outcome, "payload": payload, "raw_text": json.dumps(payload), "meta": meta}


def _iem_csv(rows: list[tuple[str, float]]) -> str:
    lines = ["station,valid,tmpf,metar"]
    for valid, temp in rows:
        dt = datetime.strptime(valid, "%Y-%m-%d %H:%M")
        lines.append(f"NYC,{valid},{temp},METAR KNYC {dt:%d%H%M}Z AUTO")
    return "\n".join(lines) + "\n"


def _default_obs() -> list[tuple[str, float]]:
    # Climate day 2026-10-05 starts 05:00Z. d0_1200 cutoff is 16:00Z.
    rows = [(f"2026-10-05 {h:02d}:35", 60.0 + h * 0.5) for h in range(5, 16)]
    rows.append(("2026-10-05 15:45", 90.0))  # available 16:05Z > cutoff → excluded
    return rows


class FakeIem:
    def __init__(self, clock: Clock, rows: list[tuple[str, float]] | None = None):
        self.clock = clock
        self.rows = _default_obs() if rows is None else rows
        self.calls = 0

    def __call__(self, target_date: str, *, deadline=None) -> dict:
        self.calls += 1
        started = self.clock()
        text = _iem_csv(self.rows)
        return {
            "text": text,
            "raw_text": text,
            "params": {},
            "attempts": [{"try": 0, "outcome": "ok", "http_status": 200,
                          "fetch_started_at": started.isoformat(), "fetch_completed_at": self.clock().isoformat()}],
        }


def _fetchers(start: datetime, *, runs: dict | None = None, obs=None, step_s: float = 1.0):
    clock = Clock(start, step_s)
    return phase7.Fetchers(run_hourly=FakeRuns(clock, runs), iem=FakeIem(clock, obs), clock=clock, sleep=lambda s: None)


def _capture(paths, cp: str, *, start_offset_min: float = 5, regime=REGIME_WEATHER_COMPANY_CLINYC, **kw):
    scheduled = checkpoint_scheduled_at(TARGET, cp)
    fetchers = _fetchers(scheduled + timedelta(minutes=start_offset_min), **kw)
    out = phase7.capture_checkpoint(
        protocol=phase7.load_protocol(paths), event=_event(regime), target_date=TARGET,
        checkpoint_id=cp, paths=paths, fetchers=fetchers,
    )
    return out, fetchers


def _tree_hashes(root: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def _fetch_log(paths) -> list[dict]:
    return [json.loads(l) for l in paths.fetch_log.read_text(encoding="utf-8").splitlines() if l]


# ---------------------------------------------------------------------------
# Registration boundaries and protocol integrity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "registered_local, expected_start",
    [
        (datetime(2026, 10, 2, 12, 0), "2026-10-03"),
        (datetime(2026, 10, 2, 17, 59), "2026-10-03"),
        (datetime(2026, 10, 2, 18, 0), "2026-10-04"),  # equal to dminus1_1800 is not "after"
        (datetime(2026, 10, 31, 18, 30), "2026-11-02"),  # across the EDT→EST change
        (datetime(2026, 11, 1, 0, 30), "2026-11-02"),
    ],
)
def test_start_date_rule(registered_local, expected_start):
    reg = registered_local.replace(tzinfo=NYC_TZ)
    start = phase7.start_target_date_for(reg)
    assert start.isoformat() == expected_start
    dates = phase7.protocol_target_dates(start)
    assert len(dates) == 60 and dates[-1] == (start + timedelta(days=59)).isoformat()


def test_protocol_is_write_once_and_freezes_window(paths):
    protocol = phase7.load_protocol(paths)
    assert protocol["start_target_date"] == "2026-10-03"
    assert protocol["end_target_date"] == "2026-12-01"
    assert protocol["collection"]["no_early_stopping"] is True
    assert protocol["collection"]["capture_window_minutes"] == 30
    assert protocol["primary_cohort"]["evaluation_target_regime"] == REGIME_WEATHER_COMPANY_CLINYC
    assert protocol["primary_cohort"]["calibration_target_regime"] == REGIME_NWS_CLI_KNYC
    with pytest.raises(phase7.Phase7Error):
        gfs, hrrr = _pool_rows()
        phase7.register_protocol(registered_at=REGISTERED_AT, paths=paths, gfs_rows=gfs, hrrr_rows=hrrr)
    assert phase7.in_protocol_window(protocol, "2026-10-03")
    assert phase7.in_protocol_window(protocol, "2026-12-01")
    assert not phase7.in_protocol_window(protocol, "2026-10-02")
    assert not phase7.in_protocol_window(protocol, "2026-12-02")


def test_frozen_pool_requires_both_models_full_and_knyc(paths):
    rows = phase7.load_frozen_pool(paths, phase7.load_protocol(paths))
    dates = {r["target_date"] for r in rows}
    assert "2026-03-01" not in dates  # only GFS FULL on this key
    assert "2026-03-02" not in dates  # CLINYC regime
    assert {r["target_regime"] for r in rows} == {REGIME_NWS_CLI_KNYC}


def test_preflight_labels_d0_0900_deterministically_insufficient(paths):
    pre = phase7.load_protocol(paths)["calibration_feasibility_preflight"]
    assert pre["checkpoints_unable_to_meet_thresholds"] == ["d0_0900"]
    s = pre["by_checkpoint"]["d0_0900"]["gfs"]
    assert s["status_counts"] == {method.CALIBRATION_INSUFFICIENT_DETERMINISTIC: 60}
    assert pre["by_checkpoint"]["d0_1200"]["hrrr"]["selected_level_counts"] == {"same_run_global": 60}


def test_hrrr_horizon_preflight_flags_d0_0900():
    pre = phase7.hrrr_expected_horizon_preflight(phase7.protocol_target_dates(date(2026, 10, 3)))
    assert pre["checkpoints_expected_hrrr_horizon_short"] == ["d0_0900"]
    assert pre["by_checkpoint"]["dminus1_1800"]["expected_selected_inits"] == {"18Z_synoptic_48h": 60}


def test_method_drift_refuses_primary_capture(paths):
    protocol = json.loads(paths.protocol_path.read_text(encoding="utf-8"))
    protocol["method"]["group_hashes"]["run_selection"] = "0" * 64
    paths.protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    out, _ = _capture(paths, "d0_1200")
    assert out["status"] == phase7.RECEIPT_MISSED
    receipt = json.loads(phase7.receipt_path(paths, TARGET, "d0_1200").read_text(encoding="utf-8"))
    assert receipt["reason"].startswith(phase7.MISSED_METHOD_DRIFT)
    assert "run_selection" in receipt["reason"]
    assert not phase7.record_path(paths, TARGET, "d0_1200").exists()


def test_frozen_pool_tampering_refuses_capture(paths):
    with paths.frozen_pool.open("a", encoding="utf-8") as fh:
        fh.write("tampered\n")
    out, _ = _capture(paths, "d0_1200")
    assert out["status"] == phase7.RECEIPT_MISSED
    assert "frozen_pool_hash_mismatch" in out["record"]["integrity_problems"]
    assert out["record"]["calibration_integrity_ok"] is False
    assert out["record"]["method_integrity_ok"] is True


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git not available")
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "data").mkdir()
    (repo / "pkg" / "m.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "data" / "knyc_clinyc_pairs.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return repo


def test_git_state_separates_data_changes_from_code_changes(git_repo):
    clean = phase7.git_state(git_repo)
    assert clean["working_tree_dirty"] is False and clean["code_dirty"] is False
    assert len(clean["code_git_sha"]) == 40

    with (git_repo / "data" / "knyc_clinyc_pairs.csv").open("a", encoding="utf-8") as fh:
        fh.write("3,4\n")
    data_only = phase7.git_state(git_repo)
    assert data_only["working_tree_dirty"] is True
    assert data_only["working_tree_changed_paths"] == ["data/knyc_clinyc_pairs.csv"]
    assert data_only["code_dirty"] is False and data_only["code_dirty_paths"] == []

    (git_repo / "pkg" / "m.py").write_text("X = 2\n", encoding="utf-8")
    with_code = phase7.git_state(git_repo)
    assert with_code["working_tree_changed_paths"] == ["data/knyc_clinyc_pairs.csv", "pkg/m.py"]
    assert with_code["code_dirty"] is True and with_code["code_dirty_paths"] == ["pkg/m.py"]


def test_data_only_working_tree_change_does_not_block_capture(paths, monkeypatch):
    monkeypatch.setattr(phase7, "git_state", lambda repo_root=None: {
        "code_git_sha": "f" * 40,
        "working_tree_dirty": True,
        "working_tree_changed_paths": ["data/weather/calibration/knyc_clinyc_pairs.csv"],
        "code_dirty": False,
        "code_dirty_paths": [],
    })
    out, _ = _capture(paths, "d0_1200")
    rec = out["record"]
    assert out["status"] == phase7.RECEIPT_CAPTURED
    assert rec["integrity_problems"] == []
    assert rec["method_integrity_ok"] is True and rec["calibration_integrity_ok"] is True
    assert rec["working_tree_dirty"] is True and rec["code_dirty"] is False
    assert rec["working_tree_changed_paths"] == ["data/weather/calibration/knyc_clinyc_pairs.csv"]


def test_code_dirty_alone_does_not_gate_but_method_change_does(paths, monkeypatch):
    monkeypatch.setattr(phase7, "git_state", lambda repo_root=None: {
        "code_git_sha": "f" * 40, "working_tree_dirty": True,
        "working_tree_changed_paths": ["README.md", "scripts/x.ps1"],
        "code_dirty": True, "code_dirty_paths": ["scripts/x.ps1"],
    })
    out, _ = _capture(paths, "d0_1200")
    assert out["status"] == phase7.RECEIPT_CAPTURED

    real = method.method_fingerprint

    def _modified():
        fp = real()
        fp["group_hashes"] = {**fp["group_hashes"], "probability": "1" * 64}
        return fp

    monkeypatch.setattr(method, "method_fingerprint", _modified)
    out, _ = _capture(paths, "d0_1500")
    assert out["status"] == phase7.RECEIPT_MISSED
    assert out["record"]["method_integrity_ok"] is False
    assert out["record"]["integrity_problems"] == ["method_drift:probability"]
    assert not phase7.record_path(paths, TARGET, "d0_1500").exists()


def test_source_csv_tampering_refuses_capture(paths, tmp_path, monkeypatch):
    src = tmp_path / "gfs_src.csv"
    src.write_text("model,residual_f\nx,1\n", encoding="utf-8")
    protocol = json.loads(paths.protocol_path.read_text(encoding="utf-8"))
    protocol["calibration"]["source_csv_sha256"] = {"gfs_v2_1_csv": phase7.sha256_file(src)}
    paths.protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    monkeypatch.setattr(phase7, "_source_csvs", lambda: {"gfs_v2_1_csv": src})
    assert phase7.integrity_status(protocol, paths)["calibration_integrity_ok"] is True

    with src.open("a", encoding="utf-8") as fh:
        fh.write("y,2\n")
    out, _ = _capture(paths, "d0_1200")
    assert out["status"] == phase7.RECEIPT_MISSED
    assert out["record"]["integrity_problems"] == ["source_csv_hash_mismatch:gfs_v2_1_csv"]
    assert out["record"]["calibration_integrity_ok"] is False


# ---------------------------------------------------------------------------
# Capture: timing, observations, selection, horizon
# ---------------------------------------------------------------------------


def test_capture_records_honest_timing_and_paired_prediction(paths):
    out, _ = _capture(paths, "d0_1200")
    rec = out["record"]
    assert out["status"] == phase7.RECEIPT_CAPTURED
    scheduled = checkpoint_scheduled_at(TARGET, "d0_1200")
    assert rec["scheduled_checkpoint_at"] == scheduled.isoformat()
    assert rec["evidence_cutoff_utc"] == scheduled.astimezone(timezone.utc).isoformat()
    assert rec["prediction_as_of"] != rec["scheduled_checkpoint_at_utc"]
    assert rec["latest_evidence_received_at"] <= rec["prediction_as_of"]
    assert rec["capture_started_at"] < rec["prediction_as_of"]
    assert rec["finalized_within_capture_window"] is True
    assert rec["evaluation_target_regime"] == REGIME_WEATHER_COMPANY_CLINYC
    assert rec["calibration_target_regime"] == REGIME_NWS_CLI_KNYC
    assert rec["transfer_status"] == "experimental"
    pred = rec["prediction"]
    assert pred["paired_valid"] is True
    for name in ("research_gfs", "research_hrrr", "research_shadow"):
        probs = pred[name]["probabilities"]
        assert set(probs) == set(LABELS) and abs(sum(probs.values()) - 1) < 1e-9
    g, h = pred["research_gfs"]["probabilities"], pred["research_hrrr"]["probabilities"]
    for label in LABELS:
        assert pred["research_shadow"]["probabilities"][label] == pytest.approx(0.5 * g[label] + 0.5 * h[label])
    assert pred["research_hrrr"]["selected_run"]["run_init"] == "2026-10-05T13:00:00+00:00"
    assert pred["research_gfs"]["selected_run"]["run_id"] == "day_06z"
    assert pred["research_gfs"]["residual_pool"]["pool_level"] == "same_run_global"
    assert all(d < TARGET for d in pred["research_gfs"]["residual_pool"]["prior_target_dates"])


def test_delayed_observation_excluded_and_trust_rules_applied(paths):
    out, _ = _capture(paths, "d0_1200")
    obs = out["record"]["prediction"]["observations"]
    times = [a["observation_time_utc"] for a in obs["accepted"]]
    assert "2026-10-05T15:35:00+00:00" in times  # T-25 min, available T-5 min
    assert "2026-10-05T15:45:00+00:00" not in times  # T-15 min, available T+5 min
    assert obs["summary"]["status"] == "OK"
    assert obs["summary"]["observed_high_so_far_f"] == pytest.approx(60.0 + 15 * 0.5)
    assert obs["summary"]["n_delayed_at_checkpoint"] == 1


def test_stale_observations_make_intraday_components_unavailable(paths):
    stale = [("2026-10-05 05:35", 60.0), ("2026-10-05 08:35", 62.0)]
    out, _ = _capture(paths, "d0_1200", obs=stale)
    pred = out["record"]["prediction"]
    assert pred["observations"]["summary"]["status"] == "STALE"
    assert pred["research_gfs"]["status"] == "UNAVAILABLE"
    assert pred["research_gfs"]["reason"].startswith("obs_STALE")
    assert pred["paired_valid"] is False


def test_dminus1_uses_full_climate_day_without_observations(paths):
    out, fetchers = _capture(paths, "dminus1_1800")
    pred = out["record"]["prediction"]
    assert fetchers.iem.calls == 0
    assert pred["observations"]["summary"]["status"] == method.OBS_STATUS_NOT_APPLICABLE
    gfs = pred["research_gfs"]
    assert gfs["status"] == "OK" and gfs["observed_high_so_far_f"] is None
    day_start, day_end = phase7.climate_day_bounds_utc(TARGET)
    assert gfs["window"]["window_start_utc"] == day_start.isoformat()
    assert gfs["window"]["expected_hours"] == 24
    assert gfs["selected_run"]["run_id"] == "prev_12z"


@pytest.mark.parametrize(
    "spec, reason",
    [
        ({"hours": 12}, WINDOW_REASON_HORIZON_SHORT),
        ({"hours": 48, "real_hours": 12}, WINDOW_REASON_HORIZON_NULL_PADDED),
    ],
)
def test_incomplete_selected_hrrr_is_unavailable_without_substitution(paths, spec, reason):
    init = datetime(2026, 10, 5, 13, tzinfo=timezone.utc)
    runs = {("ncep_hrrr_conus", "2026-10-05T13Z"): {"kind": "ok", "payload": _payload(init, **spec)}}
    out, fetchers = _capture(paths, "d0_1200", runs=runs)
    pred = out["record"]["prediction"]
    assert pred["research_hrrr"]["status"] == "UNAVAILABLE"
    assert pred["research_hrrr"]["reason"] == f"model_window_unavailable:{reason}"
    assert pred["research_hrrr"]["selected_run"]["run_init"] == init.isoformat()
    assert pred["research_shadow"]["status"] == "UNAVAILABLE"
    assert pred["research_gfs"]["status"] == "OK"
    assert [c for c in fetchers.run_hourly.calls if c[0] == "ncep_hrrr_conus"] == [("ncep_hrrr_conus", "2026-10-05T13Z")]


def test_failed_newest_run_follows_phase5_predicate_and_logs_every_attempt(paths):
    runs = {
        ("ncep_hrrr_conus", "2026-10-05T13Z"): {"kind": "http_error", "status": 400},
        ("ncep_hrrr_conus", "2026-10-05T12Z"): {"kind": "http_error", "status": 503},
        ("ncep_hrrr_conus", "2026-10-05T11Z"): {"kind": "ok", "payload": {"utc_offset_seconds": 0, "hourly": {
            "time": ["2026-10-05T11:00"], "temperature_2m": [None]}}},
    }
    out, fetchers = _capture(paths, "d0_1200", runs=runs)
    hrrr = out["record"]["prediction"]["research_hrrr"]
    assert hrrr["selected_run"]["run_init"] == "2026-10-05T10:00:00+00:00"
    examined = [(e["run_init"][11:13], e["fetch_outcome"]) for e in hrrr["candidates_examined"]]
    assert examined == [("13", "http_error"), ("12", "http_error"), ("11", "unusable_response"), ("10", "ok")]
    log = [e for e in _fetch_log(paths) if e.get("model") == HRRR]
    outcomes = [(e["run_init"][11:13], e["outcome"], e.get("http_status")) for e in log]
    assert outcomes == [
        ("13", "http_error", 400),  # not retried
        ("12", "http_error", 503),
        ("12", "http_error", 503),  # retried once
        ("11", "unusable_response", 200),
        ("10", "ok", 200),
    ]
    assert log[3]["unusable_reason"] == "all_null_values"
    run_dir = paths.root / out["record"]["evidence_dir"]
    assert sorted(p.name for p in run_dir.glob("hrrr_*")) == [
        "hrrr_20261005T10Z_try0.json", "hrrr_20261005T11Z_try0.json",
        "hrrr_20261005T12Z_try0.json", "hrrr_20261005T12Z_try1.json", "hrrr_20261005T13Z_try0.json",
    ]


def test_select_run_never_skips_an_unattempted_newer_candidate():
    cutoff = datetime(2026, 10, 5, 16, tzinfo=timezone.utc)
    cands = method.hrrr_candidates(cutoff)
    older = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    attempts = {older.isoformat(): {"outcome": "ok", "payload": _payload(older)}}
    sel = method.select_run(cands, attempts, target_date=TARGET)
    assert sel["status"] == method.REASON_SEARCH_INCOMPLETE
    assert sel["first_unattempted_run_init"] == "2026-10-05T13:00:00+00:00"


def test_d0_0900_calibration_insufficiency_is_explicit(paths):
    out, _ = _capture(paths, "d0_0900")
    pred = out["record"]["prediction"]
    for name in ("research_gfs", "research_hrrr"):
        block = pred[name]
        assert block["calibration_feasibility"]["status"] == method.CALIBRATION_INSUFFICIENT_DETERMINISTIC
        assert block["status"] == "UNAVAILABLE"
        assert block["reason"] == "calibration_INSUFFICIENT_HISTORY"
    assert out["status"] == phase7.RECEIPT_CAPTURED  # kept in coverage


def test_late_finalization_is_missed_with_diagnostics(paths):
    out, _ = _capture(paths, "d0_1200", start_offset_min=29.9, step_s=5)
    assert out["status"] == phase7.RECEIPT_MISSED
    assert out["reason"] == phase7.MISSED_LATE_FINALIZATION
    assert not phase7.record_path(paths, TARGET, "d0_1200").exists()
    assert list((paths.diagnostics / TARGET / "d0_1200").glob("*.json"))
    receipt = json.loads(phase7.receipt_path(paths, TARGET, "d0_1200").read_text(encoding="utf-8"))
    assert receipt["status"] == phase7.RECEIPT_MISSED


# ---------------------------------------------------------------------------
# Immutability, idempotency, reconciliation
# ---------------------------------------------------------------------------


def test_capture_stage_is_idempotent_and_records_immutable(paths):
    now = checkpoint_scheduled_at(TARGET, "d0_1200") + timedelta(minutes=5)
    fetchers = _fetchers(now)
    loader = lambda client: {TARGET: _event()}
    first = phase7.run_capture_stage(now=now, paths=paths, fetchers=fetchers, events_loader=loader)
    assert [r["status"] for r in first["results"]] == [phase7.RECEIPT_CAPTURED]
    before = _tree_hashes(paths.records) | _tree_hashes(paths.receipts)
    second = phase7.run_capture_stage(now=now, paths=paths, fetchers=_fetchers(now), events_loader=loader)
    assert second["due"] == 0
    assert _tree_hashes(paths.records) | _tree_hashes(paths.receipts) == before

    rec_file = phase7.record_path(paths, TARGET, "d0_1200")
    record = json.loads(rec_file.read_text(encoding="utf-8"))
    with pytest.raises(phase7.Phase7ConflictError):
        phase7.write_once_json(rec_file, {**record, "prediction_as_of": "tampered"})
    assert not phase7.write_receipt(paths, {"target_date": TARGET, "checkpoint_id": "d0_1200", "status": "MISSED"})
    assert json.loads(phase7.receipt_path(paths, TARGET, "d0_1200").read_text(encoding="utf-8"))["status"] == "CAPTURED"


def test_out_of_window_dates_are_never_captured(paths):
    now = checkpoint_scheduled_at("2026-10-02", "d0_1200") + timedelta(minutes=5)
    out = phase7.run_capture_stage(now=now, paths=paths, fetchers=_fetchers(now),
                                   events_loader=lambda c: pytest.fail("must not load events"))
    assert out["due"] == 0


def test_missed_checkpoints_are_reconciled_and_never_backfilled(paths):
    after = checkpoint_scheduled_at(TARGET, "d0_1200") + timedelta(minutes=31)
    res = phase7.reconcile_missed(now=after, paths=paths)
    keys = {(m["target_date"], m["checkpoint_id"]) for m in res["missed"]}
    assert (TARGET, "d0_1200") in keys and (TARGET, "d0_1500") not in keys
    assert ("2026-10-03", "dminus1_1800") in keys
    late = phase7.run_capture_stage(now=after, paths=paths, fetchers=_fetchers(after),
                                    events_loader=lambda c: {TARGET: _event()})
    assert late["due"] == 0
    assert not phase7.record_path(paths, TARGET, "d0_1200").exists()
    again = phase7.reconcile_missed(now=after, paths=paths)
    assert again["missed_this_run"] == 0


# ---------------------------------------------------------------------------
# Settlement-gated scoring, ledger, reproduction, report
# ---------------------------------------------------------------------------


def _no_incumbent(event_ticker, checkpoint_id):
    return {"status": "UNAVAILABLE", "reason": "incumbent_receipt_absent"}


def test_scoring_waits_for_confirmed_settlement_and_ledger_is_idempotent(paths):
    _capture(paths, "d0_1200")
    rec_file = phase7.record_path(paths, TARGET, "d0_1200")
    rec_hash = hashlib.sha256(rec_file.read_bytes()).hexdigest()

    s1 = phase7.score_records(settled_markets=[], paths=paths, nws_actual_by_date={}, incumbent_loader=_no_incumbent)
    assert s1["pending"] == 1 and s1["final_written"] == 0
    assert not phase7.score_path(paths, TARGET, "d0_1200").exists()

    no_value = _markets(settled=True, winner="70° to 71°", expiration=None)
    s2 = phase7.score_records(settled_markets=no_value, paths=paths, nws_actual_by_date={TARGET: 70.0},
                              incumbent_loader=_no_incumbent)
    assert s2["pending"] == 1
    pending = json.loads(phase7.pending_path(paths, TARGET, "d0_1200").read_text(encoding="utf-8"))
    assert "refusing KNYC fallback" in pending["reason"] and pending["checks"] == 2

    settled = _markets(settled=True, winner="70° to 71°", expiration="70")
    s3 = phase7.score_records(settled_markets=settled, paths=paths, nws_actual_by_date={}, incumbent_loader=_no_incumbent)
    assert s3["final_written"] == 1 and s3["ledger"] == {"appended": 1}
    assert not phase7.pending_path(paths, TARGET, "d0_1200").exists()
    score = json.loads(phase7.score_path(paths, TARGET, "d0_1200").read_text(encoding="utf-8"))
    assert score["primary_cohort_eligible"] is True
    assert score["evaluation_target_regime"] == REGIME_WEATHER_COMPANY_CLINYC
    assert score["streams"]["legacy_incumbent"]["score"] is None
    assert score["streams"]["research_shadow"]["score"]["brier"] >= 0

    s4 = phase7.score_records(settled_markets=settled, paths=paths, nws_actual_by_date={}, incumbent_loader=_no_incumbent)
    assert s4["already_final"] == 1 and s4["final_written"] == 0
    assert hashlib.sha256(rec_file.read_bytes()).hexdigest() == rec_hash
    assert len(phase7.cache.read_jsonl(paths.outcomes_ledger)) == 1
    assert phase7.append_outcome_once(paths, {**phase7.cache.read_jsonl(paths.outcomes_ledger)[0]}) == "duplicate"
    conflict = {**phase7.cache.read_jsonl(paths.outcomes_ledger)[0], "actual_high_f": 99.0}
    assert phase7.append_outcome_once(paths, conflict) == "conflict"
    assert len(phase7.cache.read_jsonl(paths.outcomes_ledger)) == 1


def test_other_regime_is_a_separate_stratum(paths):
    _capture(paths, "d0_1200", regime=REGIME_NWS_CLI_KNYC)
    settled = _markets(settled=True, winner="70° to 71°")
    phase7.score_records(settled_markets=settled, paths=paths, nws_actual_by_date={TARGET: 70.0},
                         incumbent_loader=_no_incumbent)
    score = json.loads(phase7.score_path(paths, TARGET, "d0_1200").read_text(encoding="utf-8"))
    assert score["evaluation_target_regime"] == REGIME_NWS_CLI_KNYC
    assert score["primary_cohort_eligible"] is False
    report = phase7.build_progress_report(now=datetime(2026, 10, 6, tzinfo=timezone.utc), paths=paths)
    assert report["primary_cohort"]["paired_checkpoints"] == 0
    assert report["strata_by_regime"][REGIME_NWS_CLI_KNYC]["final_scores"] == 1


def test_offline_reproduction_matches_saved_probabilities_and_score(paths):
    _capture(paths, "d0_1200")
    settled = _markets(settled=True, winner="68° to 69°", expiration="69")
    phase7.score_records(settled_markets=settled, paths=paths, nws_actual_by_date={}, incumbent_loader=_no_incumbent)
    result = phase7.reproduce_record(phase7.record_path(paths, TARGET, "d0_1200"), paths=paths)
    assert result["prediction_identical"] is True
    assert result["probabilities_identical"] is True
    assert result["score_identical"] is True


def test_reproduction_detects_altered_evidence(paths):
    out, _ = _capture(paths, "d0_1200")
    run_dir = paths.root / out["record"]["evidence_dir"]
    hrrr_file = sorted(run_dir.glob("hrrr_*"))[0]
    payload = json.loads(hrrr_file.read_text(encoding="utf-8"))
    payload["hourly"]["temperature_2m"] = [t + 5 if t is not None else None for t in payload["hourly"]["temperature_2m"]]
    hrrr_file.write_text(json.dumps(payload), encoding="utf-8")
    result = phase7.reproduce_record(phase7.record_path(paths, TARGET, "d0_1200"), paths=paths)
    assert result["prediction_identical"] is False


# ---------------------------------------------------------------------------
# Saved evidence bytes and raw-byte integrity
# ---------------------------------------------------------------------------


class FakeHttpResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status
        self.ok = 200 <= status < 400


def _live_fetchers(start: datetime) -> tuple[phase7.Fetchers, list[str]]:
    """Real fetch functions behind fake HTTP getters returning multiline UTF-8 text."""
    clock = Clock(start)
    served: list[str] = []

    def iem_get(url, params=None, timeout=None):
        served.append(_iem_csv(_default_obs()))
        return FakeHttpResponse(served[-1])

    def runs_get(url, params=None, timeout=None):
        init = datetime.strptime(params["run"], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        payload = {**_payload(init), "hourly_units": {"time": "iso8601", "temperature_2m": "°F"}}
        served.append(json.dumps(payload, indent=2, ensure_ascii=False))
        return FakeHttpResponse(served[-1])

    fetchers = phase7.Fetchers(
        run_hourly=lambda api_model, run_init: fetch_run_hourly_logged(
            api_model, run_init, session_get=runs_get, clock=clock),
        iem=lambda target_date, deadline=None: phase7.fetch_iem_live(
            target_date, deadline=deadline, getter=iem_get, clock=clock),
        clock=clock,
        sleep=lambda s: None,
    )
    return fetchers, served


def _capture_live(paths, cp: str = "d0_1200") -> tuple[dict, list[str]]:
    scheduled = checkpoint_scheduled_at(TARGET, cp)
    fetchers, served = _live_fetchers(scheduled + timedelta(minutes=5))
    out = phase7.capture_checkpoint(
        protocol=phase7.load_protocol(paths), event=_event(), target_date=TARGET,
        checkpoint_id=cp, paths=paths, fetchers=fetchers,
    )
    assert out["status"] == phase7.RECEIPT_CAPTURED
    return out["record"], served


def _verify(paths, record: dict) -> dict:
    return phase7.verify_record_evidence(record, paths=paths)


def test_saved_multiline_iem_evidence_bytes_equal_hashed_utf8(paths):
    record, served = _capture_live(paths)
    obs_file = paths.root / record["evidence_dir"] / record["evidence"]["observations_fetch"]["raw_response_file"]
    data = obs_file.read_bytes()
    assert b"\n" in data and b"\r\n" not in data
    assert data == served[0].encode("utf-8")
    expected = record["evidence"]["observations_fetch"]["attempts"][-1]["response_sha256"]
    assert hashlib.sha256(data).hexdigest() == expected
    [entry] = [f for f in _verify(paths, record)["files"] if f["kind"] == "observations"]
    assert entry["status"] == phase7.EVIDENCE_RAW_MATCH and entry["raw_bytes_match"] is True


def test_saved_multiline_open_meteo_evidence_bytes_equal_hashed_utf8(paths):
    record, served = _capture_live(paths)
    model_entries = [f for f in _verify(paths, record)["files"] if f["kind"] == "model_run"]
    assert model_entries
    for entry in model_entries:
        data = (paths.root / record["evidence_dir"] / entry["evidence_file"]).read_bytes()
        assert b"\n" in data and b"\r\n" not in data and "°F".encode("utf-8") in data
        assert data in {s.encode("utf-8") for s in served}
        assert hashlib.sha256(data).hexdigest() == entry["expected_sha256"]
        assert entry["status"] == phase7.EVIDENCE_RAW_MATCH
    result = phase7.reproduce_record(phase7.record_path(paths, TARGET, "d0_1200"), paths=paths)
    assert result["evidence_integrity"]["all_raw_bytes_match"] is True
    assert result["prediction_identical"] is True


def test_historical_crlf_evidence_fails_raw_but_matches_normalized(paths):
    record, _ = _capture_live(paths)
    obs_file = paths.root / record["evidence_dir"] / "iem_knyc.csv"
    obs_file.write_bytes(obs_file.read_bytes().replace(b"\n", b"\r\n"))  # what write_text did on Windows
    result = phase7.reproduce_record(phase7.record_path(paths, TARGET, "d0_1200"), paths=paths)
    [entry] = [f for f in result["evidence_integrity"]["files"] if f["kind"] == "observations"]
    assert entry["raw_bytes_match"] is False and entry["crlf_normalized_match"] is True
    assert entry["status"] == phase7.EVIDENCE_CRLF_NORMALIZED_MATCH_ONLY
    assert result["evidence_integrity"]["all_raw_bytes_match"] is False
    assert result["prediction_identical"] is True and result["probabilities_identical"] is True


def test_changed_evidence_content_fails_both_matches(paths):
    record, _ = _capture_live(paths)
    gfs_name = next(f["evidence_file"] for f in _verify(paths, record)["files"] if f["evidence_file"].startswith("gfs_"))
    gfs_file = paths.root / record["evidence_dir"] / gfs_name
    gfs_file.write_bytes(gfs_file.read_bytes() + b" ")  # JSON-equivalent, so the prediction still reproduces
    result = phase7.reproduce_record(phase7.record_path(paths, TARGET, "d0_1200"), paths=paths)
    [entry] = [f for f in result["evidence_integrity"]["files"] if f["evidence_file"] == gfs_name]
    assert entry["raw_bytes_match"] is False and entry["crlf_normalized_match"] is False
    assert entry["status"] == phase7.EVIDENCE_MISMATCH
    assert result["prediction_identical"] is True


def test_missing_evidence_file_and_missing_expected_hash_are_explicit(paths):
    record, _ = _capture_live(paths)
    (paths.root / record["evidence_dir"] / "iem_knyc.csv").unlink()
    [entry] = [f for f in _verify(paths, record)["files"] if f["kind"] == "observations"]
    assert entry["status"] == phase7.EVIDENCE_MISSING_FILE
    assert entry["actual_sha256"] is None and entry["raw_bytes_match"] is None

    out, _ = _capture(paths, "d0_1500")  # FakeRuns/FakeIem record no response_sha256
    statuses = {f["status"] for f in _verify(paths, out["record"])["files"]}
    assert statuses == {phase7.EVIDENCE_MISSING_EXPECTED_HASH}


def test_evidence_check_leaves_prediction_reproduction_and_exit_codes_unchanged(paths, monkeypatch, capsys):
    import weather_model

    monkeypatch.setattr(phase7, "Phase7Paths", lambda: paths)
    record, _ = _capture_live(paths)
    rec_file = phase7.record_path(paths, TARGET, "d0_1200")
    evidence_dir = paths.root / record["evidence_dir"]

    assert weather_model.cmd_phase7_reproduce(str(rec_file)) == 0
    assert "EVIDENCE INTEGRITY" not in capsys.readouterr().err

    obs_file = evidence_dir / "iem_knyc.csv"
    obs_file.write_bytes(obs_file.read_bytes().replace(b"\n", b"\r\n"))
    assert weather_model.cmd_phase7_reproduce(str(rec_file)) == 0
    captured = capsys.readouterr()
    assert "EVIDENCE INTEGRITY" in captured.err and "CRLF_NORMALIZED_MATCH_ONLY" in captured.err
    assert json.loads(captured.out)["prediction_identical"] is True

    hrrr_file = sorted(evidence_dir.glob("hrrr_*"))[0]
    payload = json.loads(hrrr_file.read_text(encoding="utf-8"))
    payload["hourly"]["temperature_2m"] = [t + 5 if t is not None else None for t in payload["hourly"]["temperature_2m"]]
    hrrr_file.write_bytes(json.dumps(payload).encode("utf-8"))
    assert weather_model.cmd_phase7_reproduce(str(rec_file)) == 4
    capsys.readouterr()

    obs_file.unlink()
    with pytest.raises(FileNotFoundError):
        weather_model.cmd_phase7_reproduce(str(rec_file))
    assert phase7.EVIDENCE_MISSING_FILE in capsys.readouterr().out


def test_progress_report_is_read_only_and_labels_insufficient(paths):
    _capture(paths, "d0_1200")
    phase7.score_records(settled_markets=_markets(settled=True, winner="70° to 71°", expiration="70"),
                         paths=paths, nws_actual_by_date={}, incumbent_loader=_no_incumbent)
    before = _tree_hashes(paths.root) | _tree_hashes(paths.protocol_path.parent)
    report = phase7.build_progress_report(now=datetime(2026, 10, 6, 12, tzinfo=timezone.utc), paths=paths)
    assert _tree_hashes(paths.root) | _tree_hashes(paths.protocol_path.parent) == before
    pc = report["primary_cohort"]
    assert pc["label"] == phase7.LABEL_INSUFFICIENT
    assert pc["paired_intraday_checkpoints"] == 1 and pc["unique_paired_intraday_target_dates"] == 1
    assert report["calibration_unable_to_meet_thresholds"] == ["d0_0900"]
    cov = report["coverage_by_checkpoint"]["d0_1200"]
    assert cov["captured"] == 1 and cov["scheduled_so_far"] == 3
    assert cov["awaiting_receipt"] == 2  # earlier dates not yet reconciled in this test


def test_method_fingerprint_is_stable_and_groups_cover_required_methods():
    fp = method.method_fingerprint()
    assert fp == method.method_fingerprint()
    assert set(fp["group_hashes"]) == {
        "forecast_window", "observation", "run_selection", "probability", "calibration", "shadow", "scoring",
    }
    names = {n for group in fp["function_hashes"].values() for n in group}
    for required in (
        "research.weather.sources.single_run_hourly:remaining_day_high",
        "research.weather.asof_observations:observed_high_asof",
        "research.weather.phase5:choose_operational_hrrr_run",
        "research.weather.probability:select_residual_pool",
        "research.weather.phase5:predict_operational_calibrated",
        "research.weather.shadow:combine_equal_weight",
    ):
        assert required in names
    assert method.method_drift(fp) == []

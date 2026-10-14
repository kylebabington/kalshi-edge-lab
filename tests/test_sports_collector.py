"""Automatic sports collector: lock overlap and stale-lock handling, failed requests, repeated cycles,
settlement cadence, unchanged accepted records and the scheduling gate. No network, no real data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from research.sports import core
from research.sports.live import capture as CP
from research.sports.live import config as LC
from research.sports.live import run as LIVE_RUN
from research.sports.ops import collector as CO
from research.sports.ops import gate as G
from research.sports.ops import lock as LK
from research.sports.ops.http import SHARED_HOSTS, CoordinatedSession
from research.sports.scoring import config as SCC
from research.sports.scoring import run as SCORE_RUN

REPO = Path(__file__).resolve().parents[1]
REAL_SCHEDULING_MILESTONES = CO.scheduling_milestones
T0 = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
BASES = [T0, T0 + timedelta(days=3), T0 + timedelta(days=9)]
BASE_IDS = ["t0", "t0+3d", "t0+9d"]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def ops(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    monkeypatch.setattr(LK, "LOCK_PATH", logs / "cycle.lock")
    monkeypatch.setattr(CO, "LOG_DIR", logs)
    monkeypatch.setattr(CO, "STATE_PATH", logs / "collector_state.json")
    monkeypatch.setattr(CO, "STATUS_PATH", logs / "collector_status.json")
    monkeypatch.setattr(CO, "CYCLES_PATH", logs / "collector_cycles.jsonl")
    monkeypatch.setattr(LC, "CAPTURE_DIR", tmp_path / "capture")
    monkeypatch.setattr(SCC, "SCORE_DIR", tmp_path / "scores")
    calls = {"capture": [], "settle": 0, "milestones": []}
    monkeypatch.setattr(CO, "eligible_competitions", lambda: ({"nfl", "epl"}, {"KXNFLGAME": "nfl", "KXEPLGAME": "epl"}))
    monkeypatch.setattr(CO, "scheduling_milestones", lambda now, session: (calls["milestones"],
                                                                           {"complete": True, "pages": 1, "error": None}))

    def fake_capture(comps, log, session):
        calls["capture"].append(comps)
        return {"exit_code": 0, "duration_s": 1.0, "competitions": comps or "all", "run_id": "r",
                "counts": {"WINDOW_CAPTURE": 0}, "problems": [], "discovery_complete": True, "summary": {"events": []}}

    def fake_settle(log, session):
        calls["settle"] += 1
        return {"exit_code": 0, "duration_s": 0.5, "run_id": f"s{calls['settle']}", "counts": {}}

    monkeypatch.setattr(CO, "run_capture", fake_capture)
    monkeypatch.setattr(CO, "run_settlement", fake_settle)
    return {"logs": logs, "calls": calls, "tmp": tmp_path}


def _quiet(msg):
    pass


def _milestone(mid, start: datetime, tickers):
    return {"milestone_id": mid, "start": _iso(start), "title": f"game {mid}", "event_tickers": tickers}


def _cycles(ops):
    p = ops["logs"] / "collector_cycles.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# ---------------------------------------------------------------- lock

def test_lock_blocks_overlap_and_releases_on_failure(tmp_path):
    p = tmp_path / "cycle.lock"
    with LK.sports_lock("first", p):
        with pytest.raises(LK.LockBusy):
            LK.acquire("second", p)
        assert LK.lock_state(p)["held"] is True
    assert not p.exists()
    with pytest.raises(ValueError):
        with LK.sports_lock("fails", p):
            raise ValueError("boom")
    assert not p.exists()


def test_lock_held_by_another_process_then_stale_after_exit(tmp_path):
    p = tmp_path / "cycle.lock"
    code = ("import sys, time; from pathlib import Path; from research.sports.ops.lock import sports_lock\n"
            "with sports_lock('holder', Path(sys.argv[1])):\n    print('held', flush=True); time.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, "-c", code, str(p)], cwd=REPO, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(LK.LockBusy, match="holder"):
            LK.acquire("me", p)
        assert LK.lock_state(p)["state"] == "alive"
    finally:
        proc.kill()
        proc.wait()
    assert p.exists()  # killed without cleanup: the file stays behind
    assert LK.lock_state(p)["state"] == "dead"
    messages = []
    payload = LK.acquire("me", p, log=messages.append)
    assert payload["pid"] == os.getpid() and any("not running" in m for m in messages)
    LK.release(payload, p)
    assert not p.exists()


def test_lock_pid_reuse_and_unreadable_lock(tmp_path):
    p = tmp_path / "cycle.lock"
    p.write_text(json.dumps({"pid": os.getpid(), "process_start_utc": "1999-01-01T00:00:00.000000Z",
                             "purpose": "old"}), encoding="utf-8")
    assert LK.owner_state(LK.read_owner(p)) == "dead"  # same PID, different process start time
    LK.release(LK.acquire("me", p), p)

    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(LK.LockBusy, match="unreadable"):
        LK.acquire("me", p)
    payload = LK.acquire("me", p, unreadable_grace_s=0)
    assert LK.read_owner(p)["purpose"] == "me"
    LK.release(payload, p)


def test_release_never_removes_another_owners_lock(tmp_path):
    p = tmp_path / "cycle.lock"
    mine = LK.acquire("me", p)
    p.write_text(json.dumps({**mine, "acquired_utc": "other"}), encoding="utf-8")
    LK.release(mine, p)
    assert p.exists()


def test_manual_runs_share_the_collector_lock(ops, monkeypatch):
    monkeypatch.setattr(CP, "run", lambda *a, **k: pytest.fail("capture ran while the lock was held"))
    from research.sports.scoring import score as SC
    monkeypatch.setattr(SC, "run", lambda *a, **k: pytest.fail("settlement ran while the lock was held"))
    with LK.sports_lock("scheduled cycle"):
        assert LIVE_RUN.main(["capture", "--mode", "window"]) == 3
        assert SCORE_RUN.main(["settle"]) == 3
        assert CO.run_cycle(log=_quiet) == 0
    assert ops["calls"]["capture"] == [] and ops["calls"]["settle"] == 0
    assert _cycles(ops)[-1]["result"] == "skipped_lock_busy"


def test_cycle_waits_briefly_for_a_backup_holding_the_lock(ops, monkeypatch):
    import threading
    monkeypatch.setattr(CO, "BACKUP_POLL_S", 0.05)
    held, release = threading.Event(), threading.Event()

    def backup():
        with LK.sports_lock("backup:manual"):
            held.set()
            release.wait(5)

    t = threading.Thread(target=backup)
    t.start()
    held.wait(5)
    threading.Timer(0.3, release.set).start()
    assert CO.run_cycle(log=_quiet, now_fn=lambda: T0) == 0
    t.join()
    last = _cycles(ops)[-1]
    assert last["result"] == "ok" and last["waited_for_backup_s"] > 0

    monkeypatch.setattr(CO, "BACKUP_WAIT_S", 0.2)
    with LK.sports_lock("backup:manual"):
        assert CO.run_cycle(log=_quiet, now_fn=lambda: T0) == 0
    assert _cycles(ops)[-1]["result"] == "skipped_lock_busy"


# ---------------------------------------------------------------- cycles

@pytest.mark.parametrize("base", BASES, ids=BASE_IDS)
def test_cycle_capture_only_when_due_and_settlement_hourly(ops, base):
    calls = ops["calls"]
    clock = {"t": base}
    now_fn = lambda: clock["t"]  # noqa: E731
    calls["milestones"] = [_milestone("m1", base + timedelta(hours=6), ["KXNFLGAME-X"])]
    assert CO.run_cycle(log=_quiet, now_fn=now_fn) == 0
    assert calls["capture"] == [] and calls["settle"] == 1

    clock["t"] = base + timedelta(minutes=5)
    assert CO.run_cycle(log=_quiet, now_fn=now_fn) == 0
    assert calls["settle"] == 1  # not due again within the hour

    clock["t"] = base + timedelta(minutes=65)
    calls["milestones"] = [_milestone("m2", clock["t"] + timedelta(minutes=LC.HORIZON_MIN + 10), ["KXEPLGAME-Y"])]
    assert CO.run_cycle(log=_quiet, now_fn=now_fn) == 0
    assert calls["capture"] == [["epl"]]
    assert calls["settle"] == 1  # due, but deferred while a capture window is open

    calls["milestones"] = []
    clock["t"] = base + timedelta(minutes=70)
    assert CO.run_cycle(log=_quiet, now_fn=now_fn) == 0
    assert calls["settle"] == 2
    cycles = _cycles(ops)
    assert len(cycles) == 4 and all(c["result"] == "ok" for c in cycles)
    assert cycles[2]["settlement"]["skipped"].startswith("deferred")
    status = json.loads((ops["logs"] / "collector_status.json").read_text(encoding="utf-8"))
    assert status["last_settlement_check_at"] and status["consecutive_failures"] == 0
    assert status["cycles_24h"] == 4


def test_settlement_deferral_is_bounded(ops):
    calls = ops["calls"]
    clock = {"t": T0}
    CO.run_cycle(log=_quiet, now_fn=lambda: clock["t"])
    assert calls["settle"] == 1
    for minutes in (65, 130, 200, 245):
        clock["t"] = T0 + timedelta(minutes=minutes)
        start = clock["t"] + timedelta(minutes=LC.HORIZON_MIN + 10)
        calls["milestones"] = [_milestone(f"m{minutes}", start, ["KXNFLGAME-" + str(minutes)])]
        CO.run_cycle(log=_quiet, now_fn=lambda: clock["t"])
    assert calls["settle"] == 2  # deferred from 65 min, forced once the 3 h bound passed


def test_failed_scheduling_requests_fall_back_to_full_capture(ops, monkeypatch):
    monkeypatch.setattr(CO, "scheduling_milestones", REAL_SCHEDULING_MILESTONES)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)

    class Failing:
        def __init__(self):
            self.n = 0

        def get(self, url, **kw):
            self.n += 1
            if self.n % 2:
                raise requests.ConnectionError("offline")
            r = requests.Response()
            r.status_code, r._content = 503, b"unavailable"
            return r

    sess = Failing()
    assert CO.run_cycle(log=_quiet, now_fn=lambda: T0, session=sess, settle_mode="skip") == 0
    assert sess.n == 1 + LC.NETWORK["max_retries"]  # finite retries
    assert ops["calls"]["capture"] == [None]
    rec = _cycles(ops)[-1]
    assert rec["scheduling_discovery"]["complete"] is False and "fallback" in rec["plan"]
    assert not (ops["logs"] / "cycle.lock").exists()


@pytest.mark.parametrize("base", BASES, ids=BASE_IDS)
def test_cycle_errors_are_recorded_and_release_the_lock(ops, monkeypatch, base):
    def broken(comps, log, session):
        raise RuntimeError("disk full")

    monkeypatch.setattr(CO, "run_capture", broken)
    ops["calls"]["milestones"] = [_milestone("m", base + timedelta(minutes=LC.HORIZON_MIN + 10), ["KXNFLGAME-Z"])]
    assert CO.run_cycle(log=_quiet, now_fn=lambda: base) == 1
    rec = _cycles(ops)[-1]
    assert rec["result"] == "error" and "disk full" in rec["detail"]
    assert not LK.LOCK_PATH.exists()
    status = json.loads((ops["logs"] / "collector_status.json").read_text(encoding="utf-8"))
    assert status["consecutive_failures"] == 1 and status["failures_24h"] == 1


def test_capture_problem_exit_code(ops, monkeypatch):
    monkeypatch.setattr(CO, "run_capture", lambda c, l, s: {"exit_code": 1, "duration_s": 1.0, "competitions": c,
                                                           "discovery_complete": False, "counts": {}, "summary": None})
    ops["calls"]["milestones"] = [_milestone("m", T0 + timedelta(minutes=LC.HORIZON_MIN + 10), ["KXNFLGAME-Z"])]
    assert CO.run_cycle(log=_quiet, now_fn=lambda: T0, settle_mode="skip") == 2
    assert _cycles(ops)[-1]["result"] == "capture_problem"


def test_dry_run_writes_no_state(ops):
    ops["calls"]["milestones"] = [_milestone("m", T0 + timedelta(minutes=LC.HORIZON_MIN + 10), ["KXNFLGAME-Z"])]
    assert CO.run_cycle(log=_quiet, now_fn=lambda: T0, dry_run=True) == 0
    assert ops["calls"]["capture"] == [] and ops["calls"]["settle"] == 0
    assert not (ops["logs"] / "collector_state.json").exists()
    assert not (ops["logs"] / "collector_cycles.jsonl").exists()
    dry = list((ops["logs"] / "dry_runs").glob("*.json"))
    assert len(dry) == 1 and json.loads(dry[0].read_text(encoding="utf-8"))["plan"]["competitions"] == ["nfl"]


def test_repeated_cycles_never_replace_accepted_records(ops, monkeypatch):
    target = LC.CAPTURE_DIR / "accepted" / "checkpoint" / "nfl" / "espn_1.json"
    writes = []

    def capture_that_retries(comps, log, session):
        rec = CP.seal({"competition": "nfl", "cluster": "espn:1", "attempt": len(writes)})
        writes.append(CP.write_accepted(target, rec))
        return {"exit_code": 0, "duration_s": 1.0, "competitions": comps, "discovery_complete": True,
                "counts": {"WINDOW_CAPTURE": int(writes[-1])}, "summary": {"events": []}}

    monkeypatch.setattr(CO, "run_capture", capture_that_retries)
    for i in range(3):
        t = T0 + timedelta(minutes=5 * i)
        ops["calls"]["milestones"] = [_milestone(f"m{i}", t + timedelta(minutes=LC.HORIZON_MIN + 10),
                                                 [f"KXNFLGAME-N{i}"])]
        assert CO.run_cycle(log=_quiet, now_fn=lambda t=t: t, settle_mode="skip") == 0
        if i == 0:
            first = target.read_bytes()
    assert writes == [True, False, False]
    assert target.read_bytes() == first and json.loads(first)["attempt"] == 0


# ---------------------------------------------------------------- scheduling gate

def _plan(milestones, now, covered=None, state=None):
    return G.plan(milestones, now.timestamp(), {"KXNFLGAME": "nfl", "KXEPLGAME": "epl", "KXNBAGAME": "nba"},
                  {"nfl", "epl"}, covered or {}, state or {}, lambda s: datetime.fromisoformat(
                      s.replace("Z", "+00:00")).timestamp())


def test_gate_window_reconcile_covered_and_ineligible():
    in_window = _milestone("a", T0 + timedelta(minutes=LC.HORIZON_MIN + 10), ["KXNFLGAME-A"])
    covered = _milestone("b", T0 + timedelta(minutes=LC.HORIZON_MIN + 10), ["KXEPLGAME-B"])
    past = _milestone("c", T0 - timedelta(minutes=10), ["KXNFLGAME-C"])
    later = _milestone("d", T0 + timedelta(hours=1, minutes=40), ["KXEPLGAME-D"])
    ineligible = _milestone("e", T0 + timedelta(minutes=LC.HORIZON_MIN + 10), ["KXNBAGAME-E"])
    p = _plan([in_window, covered, past, later, ineligible], T0, covered={"epl": {"KXEPLGAME-B"}})
    assert p.competitions() == ["nfl"]
    assert sorted(x["reason"] for x in p.due["nfl"]) == ["reconcile", "window"]
    assert p.covered == 1 and p.window_open_or_soon is True

    state = G.update_state({}, None, [past], {"KXNFLGAME": "nfl"}, p, T0.timestamp(), capture_ok=True)
    again = _plan([past], T0 + timedelta(minutes=5), state=state)
    assert again.competitions() == []
    failed = G.update_state({}, None, [past], {"KXNFLGAME": "nfl"}, p, T0.timestamp(), capture_ok=False)
    assert _plan([past], T0 + timedelta(minutes=5), state=failed).competitions() == ["nfl"]


def test_gate_throttles_structural_outcomes():
    m = _milestone("a", T0 + timedelta(minutes=LC.HORIZON_MIN + 10), ["KXNFLGAME-A"])
    p = _plan([m], T0)
    summary = {"events": [{"competition": "nfl", "outcome": "no_linked_contracts",
                           "kalshi_event_tickers": ["KXNFLGAME-A"]}]}
    state = G.update_state({}, summary, [m], {"KXNFLGAME": "nfl"}, p, T0.timestamp(), capture_ok=True)
    assert _plan([m], T0 + timedelta(minutes=5), state=state).throttled == ["nfl|a"]
    assert _plan([m], T0 + timedelta(minutes=G.STRUCTURAL_RETRY_MIN), state=state).competitions() == ["nfl"]


# ---------------------------------------------------------------- shared-host coordination

def test_coordinated_session_spaces_shared_host_only_while_weather_runs(tmp_path):
    clock = {"t": 0.0}
    slept = []

    class Base:
        def get(self, url, **kw):
            r = requests.Response()
            r.status_code, r._content = 200, b"{}"
            return r

    weather_lock = tmp_path / "weather.lock"
    s = CoordinatedSession(base=Base(), weather_lock=weather_lock, clock=lambda: clock["t"],
                           sleep=lambda w: slept.append(w))
    host = next(iter(SHARED_HOSTS))
    s.get(f"https://{host}/a")
    s.get(f"https://{host}/b")
    assert slept == []
    weather_lock.write_text(json.dumps({"pid": os.getpid(), "process_start_utc": LK.process_start_time(os.getpid())}),
                            encoding="utf-8")
    s2 = CoordinatedSession(base=Base(), weather_lock=weather_lock, clock=lambda: clock["t"],
                            sleep=lambda w: slept.append(w))
    s2.get(f"https://{host}/a")
    s2.get(f"https://{host}/b")
    s2.get("https://site.api.espn.com/x")
    assert slept == [pytest.approx(1.0)]
    assert s2.stats[host]["requests"] == 2 and s2.stats[host]["coordinated_waits"] == 1


def test_bottleneck_reported_when_capture_exceeds_headroom():
    assert CO.bottleneck({"duration_s": 60, "competitions": ["nfl"]}, {}) is None
    msg = CO.bottleneck({"duration_s": CO.CAPTURE_HEADROOM_S + 1, "competitions": "all"},
                        {"h": {"requests": 400, "seconds": 250.0}})
    assert "400 serial requests" in msg and "not relaxed" in msg


def test_status_reports_settlement_deferral_window(tmp_path):
    t0 = datetime(2026, 10, 11, 14, 37, 11, tzinfo=timezone.utc).timestamp()
    st = CO.build_status(tmp_path / "none.jsonl", {"settlement_deferred_since_ts": t0})
    assert st["settlement_deferred_since"].startswith("2026-10-11T14:37:11")
    assert st["settlement_forced_by"].startswith("2026-10-11T17:37:11")
    assert CO.build_status(tmp_path / "none.jsonl", {})["settlement_deferred_since"] is None


def _cycle_line(started: float, result: str = "ok") -> str:
    return json.dumps({"started_at": _iso(datetime.fromtimestamp(started, timezone.utc)),
                       "finished_at": _iso(datetime.fromtimestamp(started, timezone.utc)),
                       "result": result, "duration_s": 1.0})


@pytest.mark.parametrize("base", BASES, ids=BASE_IDS)
def test_24h_history_boundary_uses_the_given_reference_time(tmp_path, base):
    as_of = base.timestamp()
    day = 86400
    path = tmp_path / "cycles.jsonl"
    path.write_text("\n".join([
        _cycle_line(as_of - day - 1, "error"),  # just outside
        _cycle_line(as_of - day, "error"),      # exactly at the boundary: inside (inclusive)
        _cycle_line(as_of - day + 1),           # just inside
        _cycle_line(as_of - 60),
    ]) + "\n", encoding="utf-8")
    recent = CO.read_cycles(path, as_of=as_of)
    assert [r["result"] for r in recent] == ["error", "ok", "ok"]
    assert len(CO.read_cycles(path, as_of=as_of + 1)) == 2  # one second later the boundary entry drops out
    assert CO.read_cycles(path, as_of=as_of + 2 * day) == []

    st = CO.build_status(path, {}, as_of=as_of)
    assert st["cycles_24h"] == 3 and st["failures_24h"] == 1 and st["consecutive_failures"] == 0
    assert [f["at"] for f in st["recent_failures"]] == [recent[0]["started_at"]]
    assert CO.build_status(path, {}, as_of=as_of + 1)["failures_24h"] == 0


def test_history_filter_defaults_to_the_wall_clock(tmp_path):
    import time
    path = tmp_path / "cycles.jsonl"
    path.write_text(_cycle_line(time.time() - 60) + "\n" + _cycle_line(T0.timestamp() - 30 * 86400) + "\n",
                    encoding="utf-8")
    assert len(CO.read_cycles(path)) == 1
    assert CO.build_status(path, {})["cycles_24h"] == 1

"""Automatic sports collector cycle (RESEARCH_ONLY / NO_BET).

    python -m research.sports.ops.collector cycle               # one cycle (what the scheduled task runs)
    python -m research.sports.ops.collector cycle --dry-run     # lock + discovery + plan only; writes nothing
    python -m research.sports.ops.collector status              # print the last recorded collector status

One cycle, under the shared sports lock:

1. Scheduling discovery: one paginated Kalshi milestone listing from (now - 3 h). Not evidence; nothing saved.
2. ``gate.plan`` picks the competitions with an uncovered milestone whose capture window may be open, or
   whose window has closed and still needs one capture run to record MISSED.
3. The frozen ``sports_phase2_capture_v2`` window capture runs for exactly those competitions, unchanged:
   its own complete discovery, saved evidence, recording rules, exclusive accepted records, no backfill.
   If scheduling discovery fails the capture runs for every competition instead.
4. At most once an hour, after capture, the frozen settlement scoring run. It is deferred while a capture
   window is open or about to open, for at most 3 hours.

Every cycle appends to ``data/logs/sports/collector_cycles.jsonl`` and rewrites
``collector_status.json``; both are read by the passive sports API.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .. import evaluate as EV
from ..adapters import KALSHI_BASE
from ..core import Fetcher
from ..live import capture as CP
from ..live import config as LC
from ..live import discover as D
from ..scoring import config as SCC
from . import gate as G
from .http import CoordinatedSession
from .lock import LOG_DIR, LockBusy, sports_lock

SCHEMA = "sports_collector_cycle_v1"
CADENCE_MIN = 5
SETTLE_INTERVAL_S = 3600
SETTLE_MAX_DEFER_S = 3 * 3600
CAPTURE_HEADROOM_S = (LC.WINDOW_MIN - CADENCE_MIN) * 60
SCHEDULING_LOOKBACK_H = 3
STATE_PATH = LOG_DIR / "collector_state.json"
STATUS_PATH = LOG_DIR / "collector_status.json"
CYCLES_PATH = LOG_DIR / "collector_cycles.jsonl"
TASK_NAME = "KalshiEdgeLab-SportsProspective"
RECENT_FAILURES = 20


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def make_logger(log_dir: Path = LOG_DIR, echo: bool = True):
    def log(msg: str) -> None:
        now = datetime.now(timezone.utc)
        line = f"[{now.strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
        if echo:
            print(line, flush=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / f"collector_{now.strftime('%Y-%m-%d')}.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return log


def eligible_competitions() -> tuple[set[str], dict]:
    """Competitions the frozen capture can record: supported adapter and an unblocked frozen model."""
    by_key, _, series_comp = CP._comp_maps()
    proto1 = EV.load_protocol(LC.PHASE1_PROTOCOL)["competitions"]
    out = set()
    for k, comp in by_key.items():
        entry = proto1.get(k)
        if LC.ADAPTERS[comp.source]["status"] != "supported" or entry is None:
            continue
        if entry.get("blocked") or (entry.get("params") or {}).get("blocked"):
            continue
        out.add(k)
    return out, series_comp


def covered_tickers(capture_dir: Path | None = None) -> dict[str, set]:
    root = (capture_dir or LC.CAPTURE_DIR) / "accepted" / "checkpoint"
    out: dict[str, set] = {}
    for p in root.glob("*/*.json"):
        rec = _read_json(p, None)
        if rec:
            out.setdefault(rec.get("competition"), set()).update(rec.get("kalshi_event_tickers") or [])
    return out


def scheduling_milestones(now: float, session) -> tuple[list[dict], dict]:
    with tempfile.TemporaryDirectory() as tmp:
        f = Fetcher(root=Path(tmp), min_interval=LC.NETWORK["min_interval_s"], max_retries=LC.NETWORK["max_retries"],
                    timeout=LC.NETWORK["timeout_s"], session=session)
        params = {"limit": LC.PAGE_LIMITS["milestones"], "category": "Sports",
                  "minimum_start_date": _iso(now - SCHEDULING_LOOKBACK_H * 3600)[:19] + "Z"}
        items, st = D.paginate(lambda s, u, p: f.get(s, u, p, cache_statuses=()), "kalshi",
                               f"{KALSHI_BASE}/milestones", params, "milestones")
    return [D.milestone_row(m) for m in items], st


def _new_file(directory: Path, before: set[str]) -> Path | None:
    new = sorted(p for p in directory.glob("*.json") if p.name not in before) if directory.exists() else []
    return new[-1] if new else None


def timing_failures(run_id: str) -> list[dict]:
    out = []
    for p in sorted((LC.CAPTURE_DIR / "attempts" / run_id).glob("*FAILED_ATTEMPT*.json")):
        rec = _read_json(p, {})
        reasons = rec.get("failure_reasons") or []
        if any("timing" in r or "after prediction_as_of" in r for r in reasons):
            out.append({"competition": rec.get("competition"), "cluster": rec.get("cluster"), "reasons": reasons[:3]})
    return out


def run_capture(competitions: list[str] | None, log, session) -> dict:
    runs = LC.CAPTURE_DIR / "runs"
    before = {p.name for p in runs.glob("*.json")} if runs.exists() else set()
    t0 = time.monotonic()
    code = CP.run("window", ",".join(competitions) if competitions else None, log, session=session)
    out = {"exit_code": code, "duration_s": round(time.monotonic() - t0, 1),
           "competitions": competitions or "all"}
    p = _new_file(runs, before)
    summary = _read_json(p, None) if p else None
    if summary:
        out.update({"run_id": summary["run_id"], "counts": summary.get("counts", {}),
                    "problems": summary.get("problems", [])[:10],
                    "discovery_complete": (summary.get("discovery") or {}).get("complete")})
        out["timing_failures"] = timing_failures(summary["run_id"])
    out["summary"] = summary
    return out


def run_settlement(log, session) -> dict:
    runs = SCC.SCORE_DIR / "runs"
    before = {p.name for p in runs.glob("*.json")} if runs.exists() else set()
    from ..scoring import score as SC
    t0 = time.monotonic()
    code = SC.run(log, session=session)
    out = {"exit_code": code, "duration_s": round(time.monotonic() - t0, 1)}
    p = _new_file(runs, before)
    s = _read_json(p, None) if p else None
    if s:
        out.update({"run_id": s["run_id"], "counts": s.get("counts", {}), "eligible_records": s.get("eligible_records")})
    return out


def bottleneck(capture: dict | None, http_stats: dict) -> str | None:
    if not capture or capture.get("duration_s", 0) <= CAPTURE_HEADROOM_S:
        if not (capture or {}).get("timing_failures"):
            return None
    reqs = sum(v["requests"] for v in http_stats.values())
    net = sum(v["seconds"] for v in http_stats.values())
    return (f"capture run took {capture['duration_s']:.0f}s for {capture['competitions']} "
            f"(headroom {CAPTURE_HEADROOM_S}s = {LC.WINDOW_MIN}-min window - {CADENCE_MIN}-min cadence); "
            f"{reqs} serial requests, {net:.0f}s in network calls, frozen spacing "
            f"{LC.NETWORK['min_interval_s']}s/host; timing failures: {len(capture.get('timing_failures') or [])}. "
            "Timing rules are not relaxed.")


def _cycle_body(log, now_fn, session, dry_run: bool, settle_mode: str, rec: dict) -> int:
    state = _read_json(STATE_PATH, {})
    now = now_fn().timestamp()
    eligible, series_comp = eligible_competitions()
    t0 = time.monotonic()
    milestones, st = scheduling_milestones(now, session)
    rec["scheduling_discovery"] = {"complete": st["complete"], "pages": st["pages"], "error": st["error"],
                                   "milestones": len(milestones), "duration_s": round(time.monotonic() - t0, 1)}
    if st["complete"]:
        p = G.plan(milestones, now, series_comp, eligible, covered_tickers(), state, _ts)
        comps = p.competitions()
        rec["plan"] = p.to_dict()
    else:
        p, comps = None, None
        rec["plan"] = {"fallback": "scheduling discovery incomplete; capture runs for every competition"}
        log(f"scheduling discovery incomplete ({st['error']}); falling back to a full capture run")
    log(f"plan: {len(comps) if comps is not None else 'all'} competitions due "
        f"{comps if comps else ''}; window open or soon={p.window_open_or_soon if p else 'unknown'}")
    code = 0
    if dry_run:
        rec["capture"] = {"skipped": "dry run"}
        rec["settlement"] = {"skipped": "dry run"}
        return 0
    if comps is None or comps:
        cap = run_capture(comps, log, session)
        summary = cap.pop("summary")
        rec["capture"] = cap
        ok = cap["exit_code"] == 0 and cap.get("discovery_complete") is True
        if p is not None:
            state.update(G.update_state(state, summary, milestones, series_comp, p, now_fn().timestamp(), ok))
        if not ok:
            code = 2
        accepted = (cap.get("counts") or {}).get("WINDOW_CAPTURE", 0)
        if accepted:
            state["last_successful_capture_at"] = _iso(now_fn().timestamp())
        state["last_capture_run_at"] = _iso(now_fn().timestamp())
    else:
        rec["capture"] = {"skipped": "no competition due"}
    last_settle = state.get("last_settlement_check_ts")
    deferred_since = state.get("settlement_deferred_since_ts")
    t = now_fn().timestamp()
    interval_due = last_settle is None or t - last_settle >= SETTLE_INTERVAL_S
    busy = p is None or p.window_open_or_soon
    if settle_mode == "skip":
        rec["settlement"] = {"skipped": "disabled for this cycle"}
    elif settle_mode == "auto" and not interval_due:
        rec["settlement"] = {"skipped": "not due"}
    elif settle_mode == "auto" and busy and (deferred_since is None or t - deferred_since < SETTLE_MAX_DEFER_S):
        state.setdefault("settlement_deferred_since_ts", t)
        rec["settlement"] = {"skipped": "deferred: a capture window is open or about to open"}
    else:
        s = run_settlement(log, session)
        rec["settlement"] = s
        state["last_settlement_check_ts"] = t
        state["last_settlement_check_at"] = _iso(t)
        state["last_settlement_result"] = {k: s.get(k) for k in ("exit_code", "run_id", "counts")}
        state.pop("settlement_deferred_since_ts", None)
        if s["exit_code"] != 0:
            code = code or 3
    _write_json(STATE_PATH, state)
    return code


BACKUP_WAIT_S = 60.0
BACKUP_POLL_S = 2.0


@contextlib.contextmanager
def _acquire_waiting_for_backup(purpose: str, log, rec: dict):
    """Take the sports lock; a backup holds it only for seconds, so wait for it rather than skip."""
    t0 = time.monotonic()
    with contextlib.ExitStack() as stack:
        while True:
            try:
                stack.enter_context(sports_lock(purpose, log=log))
                break
            except LockBusy as exc:
                holder = str((exc.owner or {}).get("purpose") or "")
                if not holder.startswith("backup:") or time.monotonic() - t0 >= BACKUP_WAIT_S:
                    raise
                time.sleep(BACKUP_POLL_S)
                rec["waited_for_backup_s"] = round(time.monotonic() - t0, 1)
        yield


def run_cycle(log=None, now_fn=None, session=None, dry_run: bool = False, settle_mode: str = "auto",
              purpose: str = "collector cycle") -> int:
    log = log or make_logger()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    sess = CoordinatedSession(base=session)
    started = now_fn().timestamp()
    t0 = time.monotonic()
    rec = {"schema": SCHEMA, "started_at": _iso(started), "dry_run": dry_run, "pid": os.getpid(),
           "purpose": purpose, "research_only": True, "no_bet": True}
    code = 0
    try:
        with _acquire_waiting_for_backup(purpose + (" (dry run)" if dry_run else ""), log, rec):
            log(f"sports collector cycle start ({'dry run' if dry_run else 'live'}; pid {os.getpid()})")
            code = _cycle_body(log, now_fn, sess, dry_run, settle_mode, rec)
            rec["result"] = "ok" if code == 0 else {2: "capture_problem", 3: "settlement_problem"}.get(code, "error")
    except LockBusy as exc:
        rec["result"] = "skipped_lock_busy"
        rec["detail"] = str(exc)
        log(f"{exc}; exiting without work")
        code = 0
    except Exception as exc:  # recorded; the lock is released by the context manager
        rec["result"] = "error"
        rec["detail"] = f"{type(exc).__name__}: {exc}"
        log("ERROR " + traceback.format_exc().strip().replace("\n", "\n    "))
        code = 1
    rec["finished_at"] = _iso(now_fn().timestamp())
    rec["duration_s"] = round(time.monotonic() - t0, 1)
    rec["exit_code"] = code
    rec["http"] = sess.stats
    rec["bottleneck"] = bottleneck(rec.get("capture") if isinstance(rec.get("capture"), dict)
                                   and "duration_s" in rec.get("capture", {}) else None, sess.stats)
    if rec["bottleneck"]:
        log("BOTTLENECK " + rec["bottleneck"])
    log(f"sports collector cycle end: {rec['result']} (exit {code}, {rec['duration_s']}s)")
    if dry_run:
        _write_json(LOG_DIR / "dry_runs" / f"{rec['started_at'].replace(':', '')}.json", rec)
    else:
        record_cycle(rec)
    return code


def record_cycle(rec: dict, cycles_path: Path | None = None, status_path: Path | None = None,
                 state_path: Path | None = None) -> dict:
    cycles_path, status_path, state_path = cycles_path or CYCLES_PATH, status_path or STATUS_PATH, state_path or STATE_PATH
    cycles_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cycles_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    status = build_status(cycles_path, _read_json(state_path, {}))
    _write_json(status_path, status)
    return status


def read_cycles(cycles_path: Path | None = None, since_s: float = 86400) -> list[dict]:
    cycles_path = cycles_path or CYCLES_PATH
    if not cycles_path.exists():
        return []
    cutoff = time.time() - since_s
    out = []
    with open(cycles_path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if _ts(r["started_at"]) >= cutoff:
                out.append(r)
    return out


def build_status(cycles_path: Path, state: dict) -> dict:
    recent = read_cycles(cycles_path)
    worked = [r for r in recent if r.get("result") != "skipped_lock_busy"]
    durations = [r["duration_s"] for r in worked if r.get("duration_s") is not None]
    failures = [{"at": r["started_at"], "result": r.get("result"), "detail": r.get("detail"),
                 "capture_problems": ((r.get("capture") or {}).get("problems") or [])[:3]}
                for r in recent if r.get("result") not in ("ok", "skipped_lock_busy")]
    consecutive = 0
    for r in reversed(worked):
        if r.get("result") == "ok":
            break
        consecutive += 1
    last = recent[-1] if recent else None
    last_ok = next((r for r in reversed(worked) if r.get("result") == "ok"), None)
    return {
        "schema": "sports_collector_status_v1", "task_name": TASK_NAME, "cadence_minutes": CADENCE_MIN,
        "settlement_interval_minutes": SETTLE_INTERVAL_S // 60,
        "last_cycle": {k: (last or {}).get(k) for k in ("started_at", "finished_at", "duration_s", "result",
                                                         "exit_code", "detail", "plan", "capture", "settlement",
                                                         "bottleneck")} if last else None,
        "last_successful_cycle_at": (last_ok or {}).get("finished_at"),
        "last_capture_run_at": state.get("last_capture_run_at"),
        "last_successful_capture_at": state.get("last_successful_capture_at"),
        "last_settlement_check_at": state.get("last_settlement_check_at"),
        "last_settlement_result": state.get("last_settlement_result"),
        "settlement_deferred_since": (_iso(state["settlement_deferred_since_ts"])
                                      if state.get("settlement_deferred_since_ts") else None),
        "settlement_forced_by": (_iso(state["settlement_deferred_since_ts"] + SETTLE_MAX_DEFER_S)
                                 if state.get("settlement_deferred_since_ts") else None),
        "consecutive_failures": consecutive,
        "cycles_24h": len(recent), "skipped_lock_busy_24h": len(recent) - len(worked),
        "failures_24h": len(failures), "recent_failures": failures[-RECENT_FAILURES:],
        "duration_s_24h": ({"median": statistics.median(durations), "max": max(durations)} if durations else None),
        "bottlenecks_24h": [r["bottleneck"] for r in recent if r.get("bottleneck")][-5:],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.sports.ops.collector")
    ap.add_argument("stage", choices=["cycle", "status"])
    ap.add_argument("--dry-run", action="store_true", help="lock, scheduling discovery and plan only; no capture, "
                                                           "no settlement, no collector state")
    ap.add_argument("--settle", choices=["auto", "skip", "force"], default="auto")
    ap.add_argument("--purpose", default="collector cycle")
    a = ap.parse_args(argv)
    if a.stage == "status":
        print(json.dumps(_read_json(STATUS_PATH, {"status": "no cycle recorded"}), indent=1))
        return 0
    return run_cycle(dry_run=a.dry_run, settle_mode=a.settle, purpose=a.purpose)


if __name__ == "__main__":
    sys.exit(main())

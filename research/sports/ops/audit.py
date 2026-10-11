"""Read-only health audit of the automatic sports collector from its existing logs and saved records.

Never runs a cycle, never writes research state. Sources: ``collector_cycles.jsonl``, ``task_*.log``
(wrapper start/end lines), capture records (accepted + attempts) and the scheduler's own definition
(``schtasks /Query /XML``, passive). Every automatic WINDOW_CAPTURE is checked against its own event's
cutoff (``window_open <= prediction_as_of <= finalized_at <= scheduled_cutoff``); the task's run-time limit
says nothing about individual deadlines.

    python -m research.sports.ops.audit
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..live import config as LC
from . import collector as CO
from . import provenance as PV
from . import scheduler as SCH

TASK_LINE = re.compile(r"^(\S+) \[pid (\d+)\] (task start|task end \(exit (-?\d+)\))")


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
    if m and m.group(2) and len(m.group(2)) > 7:
        s = m.group(1) + m.group(2)[:7] + m.group(3)
    return datetime.fromisoformat(s).timestamp()


def _iso(t: float | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stats(xs: list[float]) -> dict | None:
    if not xs:
        return None
    xs = sorted(xs)
    return {"n": len(xs), "min": round(xs[0], 1), "median": round(statistics.median(xs), 1),
            "p90": round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 1), "max": round(xs[-1], 1)}


def read_cycles(path: Path | None = None) -> list[dict]:
    path = path or CO.CYCLES_PATH
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def task_runs(log_dir: Path | None = None) -> list[dict]:
    log_dir = log_dir or CO.LOG_DIR
    starts: dict[str, float] = {}
    runs = []
    for p in sorted(log_dir.glob("task_*.log")):
        for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            m = TASK_LINE.match(line.strip())
            if not m:
                continue
            t, pid = _ts(m.group(1)), m.group(2)
            if m.group(3) == "task start":
                starts[pid] = t
            elif pid in starts:
                runs.append({"pid": int(pid), "start": starts.pop(pid), "end": t, "exit": int(m.group(4))})
    for pid, t in starts.items():
        runs.append({"pid": int(pid), "start": t, "end": None, "exit": None})
    return sorted(runs, key=lambda r: r["start"])


def trigger_definition(runner=subprocess.run) -> dict:
    """StartBoundary / repetition interval from the task XML (passive query); ``{}`` if unavailable."""
    try:
        proc = runner(["schtasks", "/Query", "/TN", CO.TASK_NAME, "/XML"], capture_output=True, timeout=15,
                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        xml = proc.stdout.decode("utf-16", errors="replace") if proc.stdout[:2] in (b"\xff\xfe", b"\xfe\xff") \
            else proc.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError, AttributeError):
        return {}
    start = re.search(r"<StartBoundary>([^<]+)</StartBoundary>", xml)
    interval = re.search(r"<Interval>PT(\d+)M</Interval>", xml)
    multi = re.search(r"<MultipleInstancesPolicy>([^<]+)</MultipleInstancesPolicy>", xml)
    if not start or not interval:
        return {}
    return {"start_boundary": start.group(1), "interval_min": int(interval.group(1)),
            "multiple_instances": multi.group(1) if multi else None}


def classify_triggers(trig: dict, runs: list[dict], until: float) -> dict:
    if not trig:
        return {"available": False}
    t = datetime.fromisoformat(trig["start_boundary"]).timestamp()
    step = trig["interval_min"] * 60
    rows = []
    used = set()
    while t <= until:
        started = next((r for r in runs if r["pid"] not in used and t <= r["start"] < t + step), None)
        running = next((r for r in runs if r["start"] < t and (r["end"] is None or r["end"] > t)), None)
        if started and not (running and running is not started):
            used.add(started["pid"])
            rows.append({"trigger": _iso(t), "outcome": "started", "launch_delay_s": round(started["start"] - t, 1)})
        elif running:
            rows.append({"trigger": _iso(t), "outcome": "not_started_instance_running",
                         "running_since": _iso(running["start"])})
        else:
            rows.append({"trigger": _iso(t), "outcome": "not_started_unexplained"})
        t += step
    delays = [r["launch_delay_s"] for r in rows if r["outcome"] == "started"]
    return {"available": True, **trig, "triggers": len(rows), "outcomes": dict(Counter(r["outcome"] for r in rows)),
            "launch_delay_s": _stats(delays), "not_started": [r for r in rows if r["outcome"] != "started"],
            "slow_launches": [r for r in rows if r.get("launch_delay_s", 0) > 60]}


def _records() -> list[dict]:
    root = LC.CAPTURE_DIR
    out = []
    for p in sorted(root.glob("accepted/*/*/*.json")) + sorted(root.glob("attempts/*/*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            out.append({"status": "UNREADABLE", "_path": p.name})
    return out


def audit(now: float | None = None, trig: dict | None = None) -> dict:
    now = now or datetime.now(timezone.utc).timestamp()
    cycles = [c for c in read_cycles() if not c.get("dry_run")]
    auto = [c for c in cycles if c.get("purpose") == "scheduled"]
    runs = task_runs()
    modes = PV.run_modes()
    first = min((_ts(c["started_at"]) for c in auto), default=None)
    last_end = max((_ts(c.get("finished_at")) or 0 for c in auto), default=None)
    out: dict = {"generated_at": _iso(now)}
    if not auto:
        out["note"] = "no automatic cycles recorded"
        return out
    hours = (last_end - first) / 3600
    out["observation"] = {"first_cycle_start": _iso(first), "last_cycle_end": _iso(last_end),
                          "hours": round(hours, 2), "full_24h": hours >= 24,
                          "note": None if hours >= 24 else f"only {hours:.1f} h observed; not a 24-hour record"}

    results = Counter(c.get("result") for c in auto)
    out["cycles"] = {
        "count": len(auto), "results": dict(results), "exit_codes": dict(Counter(str(c.get("exit_code")) for c in auto)),
        "duration_s": _stats([c["duration_s"] for c in auto if c.get("duration_s") is not None]),
        "capture_stage_s": _stats([(c.get("capture") or {}).get("duration_s") for c in auto
                                   if (c.get("capture") or {}).get("duration_s") is not None]),
        "lock_busy_skips": results.get("skipped_lock_busy", 0),
        "failures": [{"at": c["started_at"], "result": c.get("result"), "detail": c.get("detail")}
                     for c in auto if c.get("result") not in ("ok", "skipped_lock_busy")],
        "capture_problems": sum(len((c.get("capture") or {}).get("problems") or []) for c in auto),
        "timing_failures": sum(len((c.get("capture") or {}).get("timing_failures") or []) for c in auto),
        "bottlenecks": [c["bottleneck"] for c in auto if c.get("bottleneck")],
        "http": {src: {k: sum(((c.get("http") or {}).get(src) or {}).get(k) or 0 for c in auto)
                       for k in ("requests", "errors")}
                 for src in sorted({s for c in auto for s in (c.get("http") or {})})},
    }
    out["task_runs"] = {"count": len(runs), "exit_codes": dict(Counter(str(r["exit"]) for r in runs)),
                        "wall_s": _stats([r["end"] - r["start"] for r in runs if r["end"]])}
    out["scheduler_triggers"] = classify_triggers(trig if trig is not None else trigger_definition(), runs, last_end)

    recs = _records()
    auto_recs = [r for r in recs if PV.mode_of(r.get("run_id"), modes) == "automatic"]
    out["records"] = {"automatic": dict(Counter(r.get("status") for r in auto_recs)),
                      "manual_pilot": dict(Counter(r.get("status") for r in recs
                                                   if PV.mode_of(r.get("run_id"), modes) == "manual_pilot"))}

    caps = [r for r in auto_recs if r.get("status") == "WINDOW_CAPTURE"]
    violations, margins, horizons = [], [], []
    for r in caps:
        wo, cut = _ts(r["window_open"]), _ts(r["scheduled_cutoff"])
        asof, fin = r.get("prediction_as_of_ts") or _ts(r.get("prediction_as_of")), _ts(r.get("finalized_at"))
        ok = wo <= asof <= fin <= cut
        margins.append(cut - fin)
        horizons.append(r.get("horizon_minutes"))
        if not ok:
            violations.append({"event": f"{r['competition']}|{r['cluster']}", "window_open": r["window_open"],
                               "prediction_as_of": r.get("prediction_as_of"), "finalized_at": r.get("finalized_at"),
                               "scheduled_cutoff": r["scheduled_cutoff"]})
    out["deadlines"] = {"automatic_window_captures": len(caps), "violations": violations,
                        "finalized_before_cutoff_margin_s": _stats(margins),
                        "horizon_minutes": _stats([h for h in horizons if h is not None])}

    missed = [r for r in auto_recs if r.get("status") == "MISSED"]
    before, during = [], []
    for r in missed:
        cut = _ts(r["scheduled_cutoff"])
        item = {"event": f"{r['competition']}|{r['cluster']}", "window_open": r["window_open"],
                "scheduled_cutoff": r["scheduled_cutoff"], "written_at": r.get("written_at"),
                "prior_failed_attempts": len(r.get("prior_failed_attempts") or []), "reason": r.get("reason")}
        if cut <= first:
            before.append(item)
        else:
            wo = _ts(r["window_open"])
            item["cycles_during_window"] = [c["started_at"] for c in auto
                                            if wo <= _ts(c["started_at"]) <= cut]
            during.append(item)
    out["expired_windows"] = {
        "total_written_by_automatic_runs": len(missed),
        "cutoff_before_automatic_collection_began": len(before),
        "cutoff_during_automatic_operation": during,
        "note": "MISSED records are written once a window has passed without an accepted capture; windows that "
                "closed before the first automatic cycle were reconciled by it, not missed by it.",
    }
    out["failed_attempts"] = [{"event": f"{r['competition']}|{r['cluster']}", "run_id": r.get("run_id"),
                               "reason": r.get("reason") or r.get("error")}
                              for r in auto_recs if r.get("status") == "FAILED_ATTEMPT"]

    decisions = Counter()
    for c in auto:
        s = c.get("settlement") or {}
        decisions["deferred" if str(s.get("skipped", "")).startswith("deferred") else
                  "not_due" if s.get("skipped") else "ran" if s else "none"] += 1
    state = json.loads(CO.STATE_PATH.read_text(encoding="utf-8")) if CO.STATE_PATH.exists() else {}
    since = state.get("settlement_deferred_since_ts")
    out["settlement"] = {
        "decisions": dict(decisions), "last_collector_check": state.get("last_settlement_check_at"),
        "deferred_since": _iso(since), "forced_by": _iso(since + CO.SETTLE_MAX_DEFER_S) if since else None,
        "deferred_for_min": round((now - since) / 60, 1) if since else None,
    }
    out["scheduler_now"] = {k: v for k, v in SCH.query_task(CO.TASK_NAME).items()
                            if k in ("status", "last_run_time", "last_result", "last_result_decoded", "next_run_time")}
    return out


def main(argv=None) -> int:
    argparse.ArgumentParser(prog="research.sports.ops.audit", description="read-only collector health audit "
                                                                           "(JSON on stdout)").parse_args(argv)
    print(json.dumps(audit(), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

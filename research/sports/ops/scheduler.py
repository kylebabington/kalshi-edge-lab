"""Passive Windows Task Scheduler queries (``schtasks /Query`` only; never creates, runs or changes a task).

Any failure (not Windows, task missing, access denied, unexpected output) yields ``"unknown"`` fields
instead of a guess. Results are cached briefly so polling the UI does not spawn a process per request.
"""

from __future__ import annotations

import csv
import io
import subprocess
import sys
import time

CACHE_S = 30.0
_cache: dict[str, tuple[float, dict]] = {}

UNKNOWN = {"observable": False, "configured": "unknown", "enabled": "unknown", "status": "unknown",
           "next_run_time": "unknown", "last_run_time": "unknown", "last_result": "unknown"}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, capture_output=True, text=True, timeout=10, creationflags=flags)


def query_task(name: str, runner=_run, now=time.monotonic) -> dict:
    hit = _cache.get(name)
    if hit and now() - hit[0] < CACHE_S:
        return hit[1]
    out = _query(name, runner)
    _cache[name] = (now(), out)
    return out


def _query(name: str, runner) -> dict:
    if sys.platform != "win32" and runner is _run:
        return {**UNKNOWN, "reason": "not Windows"}
    try:
        proc = runner(["schtasks", "/Query", "/TN", name, "/V", "/FO", "CSV"])
    except (OSError, subprocess.SubprocessError) as exc:
        return {**UNKNOWN, "reason": f"schtasks unavailable: {type(exc).__name__}"}
    if proc.returncode != 0:
        text = (proc.stderr or proc.stdout or "").strip()
        if "cannot find" in text.lower() or "does not exist" in text.lower():
            return {**UNKNOWN, "observable": True, "configured": False, "enabled": False, "reason": "task not registered"}
        return {**UNKNOWN, "reason": "schtasks query failed"}
    rows = list(csv.DictReader(io.StringIO(proc.stdout)))
    need = ("Next Run Time", "Status", "Last Run Time", "Last Result", "Scheduled Task State")
    if not rows or any(k not in rows[0] for k in need):
        return {**UNKNOWN, "reason": "unrecognized schtasks output"}
    r = rows[0]
    state = r["Scheduled Task State"].strip().lower()
    return {"observable": True, "configured": True,
            "enabled": True if state == "enabled" else False if state == "disabled" else "unknown",
            "status": r["Status"].strip() or "unknown", "next_run_time": r["Next Run Time"].strip() or "unknown",
            "last_run_time": r["Last Run Time"].strip() or "unknown", "last_result": r["Last Result"].strip() or "unknown",
            "logon_mode": r.get("Logon Mode", "").strip() or "unknown",
            "repeat_every": r.get("Repeat: Every", "").strip() or "unknown"}

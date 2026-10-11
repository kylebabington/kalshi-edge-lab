"""Collection provenance for sports_phase2_capture_v2 records, derived without rewriting any record.

The frozen capture protocol and ``live/capture.py`` still say "manual invocation only" / "never scheduled";
that wording is pinned and is no longer an accurate description of how records are collected. Operational
amendment 1 (``docs/research/sports_phase2_capture_v2_ops_amendment_1.md``) records that scheduled collection
began on 2026-10-11. A record's collection mode comes from its ``run_id``:

* ``automatic``: the run was started by a collector cycle with purpose ``scheduled`` (the Task Scheduler
  action, ``collector_cycles.jsonl``),
* ``collector_manual``: a collector cycle started by hand (``--purpose`` other than ``scheduled``),
* ``manual_pilot``: no collector cycle started the run (direct ``research.sports.live.run capture``),
  which covers every capture made before scheduling began.

Capture behaviour (windows, cutoffs, write-once records, evidence) is identical in all three modes.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import collector as CO

AMENDMENT = {
    "id": "sports_phase2_capture_v2_ops_amendment_1",
    "date": "2026-10-11",
    "doc": "docs/research/sports_phase2_capture_v2_ops_amendment_1.md",
    "protocol": "sports_phase2_capture_v2",
    "protocol_modified": False,
    "task_name": CO.TASK_NAME,
    "cadence_minutes": CO.CADENCE_MIN,
    "collector_commit": "38f7134",
    "task_registered_at": "2026-10-11T14:27Z",
    "first_scheduled_action_at": "2026-10-11T14:27:48Z",
    "first_trigger_run_at": "2026-10-11T14:41:41Z",
    "superseded_wording": ['protocol "scheduler": "none (manual invocation only)"',
                           'live/capture.py docstring "never scheduled"'],
}

MODES = ("automatic", "collector_manual", "manual_pilot")
MODE_LABELS = {"automatic": "Automatic (scheduled collector)",
               "collector_manual": "Collector cycle started by hand",
               "manual_pilot": "Manual pilot capture"}


def run_modes(cycles_path: Path | None = None) -> dict[str, dict]:
    """``run_id`` -> ``{"mode", "kind", "cycle_started_at"}`` for capture and settlement runs started by a
    collector cycle (all history, not just 24 h)."""
    path = cycles_path or CO.CYCLES_PATH
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("dry_run"):
                continue
            mode = "automatic" if r.get("purpose") == "scheduled" else "collector_manual"
            for kind in ("capture", "settlement"):
                rid = (r.get(kind) or {}).get("run_id")
                if rid:
                    out[rid] = {"mode": mode, "kind": kind, "cycle_started_at": r.get("started_at")}
    return out


def mode_of(run_id: str | None, modes: dict[str, dict]) -> str:
    return (modes.get(run_id) or {}).get("mode", "manual_pilot")

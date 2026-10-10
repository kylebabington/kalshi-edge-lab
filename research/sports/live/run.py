"""Sports prospective capture v2 command line (RESEARCH_ONLY / NO_BET; manual only, never scheduled).

    python -m research.sports.live.run matrix                       # offline availability matrix (all 313 competitions)
    python -m research.sports.live.run freeze                       # freeze sports_phase2_capture_v2 (write-once)
    python -m research.sports.live.run verify                       # pins + dependency hashes (offline)
    python -m research.sports.live.run upcoming [--hours 24]        # when checkpoint windows open (milestones only)
    python -m research.sports.live.run capture --mode window [--competitions k1,k2]
    python -m research.sports.live.run capture --mode early [--lookahead-hours 6] [--competitions k1,k2]
    python -m research.sports.live.run replay --record <path> | --all   # offline, hash-verified reproduction
"""

from __future__ import annotations

import argparse
import sys
import time

from ..competitions import all_competitions
from ..core import utc_now_iso


def log(msg: str) -> None:
    print(f"[{utc_now_iso()}] {msg}", flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="research.sports.live.run")
    p.add_argument("stage", choices=["matrix", "freeze", "verify", "upcoming", "capture", "replay"])
    p.add_argument("--mode", choices=["window", "early"])
    p.add_argument("--competitions", default=None, help="capture: comma-separated competition keys (default: all)")
    p.add_argument("--lookahead-hours", type=float, default=None, help="early mode: milestone lookahead (max 48)")
    p.add_argument("--hours", type=float, default=24.0, help="upcoming: lookahead hours")
    p.add_argument("--record", help="replay: one record path")
    p.add_argument("--all", action="store_true", help="replay: every accepted and attempt record")
    p.add_argument("--no-write", action="store_true", help="matrix: print only")
    a = p.parse_args(argv)
    t0 = time.time()
    if a.stage == "matrix":
        from . import matrix as MX
        MX.stage_matrix(log, write_files=not a.no_write)
        return 0
    if a.stage in ("freeze", "verify"):
        from . import pins as PN
        comps = all_competitions()[0]
        if a.stage == "freeze":
            PN.freeze(comps, log)
            return 0
        probs = PN.check(PN.load_protocol(), comps, fresh=True)
        for x in probs:
            log(f"PIN PROBLEM: {x}")
        log(f"verify sports_phase2_capture_v2: {'OK' if not probs else f'{len(probs)} problems'} "
            f"({len(comps)} competitions, {time.time() - t0:.0f}s)")
        return 0 if not probs else 1
    if a.stage == "upcoming":
        from . import capture as CP
        return CP.upcoming(log, a.hours)
    if a.stage == "capture":
        if not a.mode:
            raise SystemExit("--mode window|early is required")
        if a.mode == "window" and a.lookahead_hours is not None:
            raise SystemExit("--lookahead-hours applies to early mode only")
        from . import capture as CP
        return CP.run(a.mode, a.competitions, log, lookahead_h=a.lookahead_hours)
    from . import replay as RP
    if a.all:
        return RP.replay_all(log)
    if not a.record:
        raise SystemExit("--record <path> or --all is required")
    return 0 if RP.replay_path(a.record, log) else 1


if __name__ == "__main__":
    sys.exit(main())

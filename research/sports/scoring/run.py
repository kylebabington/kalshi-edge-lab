"""Settlement scoring of sports_phase2_capture_v2 window captures (RESEARCH_ONLY / NO_BET; manual only).

    python -m research.sports.scoring.run settle                         # fetch settlements, write final scores once
    python -m research.sports.scoring.run verify-scores                  # re-check score checksums, sources, evidence
    python -m research.sports.scoring.run report [--no-write]            # descriptive pilot report (offline)
    python -m research.sports.scoring.run fitted-check [--competitions ncaaf]   # live fitted-candidate check
"""

from __future__ import annotations

import argparse
import sys

from ..core import utc_now_iso


def log(msg: str) -> None:
    print(f"[{utc_now_iso()}] {msg}", flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="research.sports.scoring.run")
    p.add_argument("stage", choices=["settle", "verify-scores", "report", "fitted-check"])
    p.add_argument("--competitions", default="ncaaf", help="fitted-check: comma-separated competition keys")
    p.add_argument("--no-write", action="store_true", help="report: print only")
    a = p.parse_args(argv)
    if a.stage == "settle":
        from . import score as SC
        return SC.run(log)
    if a.stage == "verify-scores":
        from . import score as SC
        return SC.verify_scores(log)
    if a.stage == "report":
        from . import report as RE
        return RE.stage_report(log, write=not a.no_write)
    from . import fitted_check as FC
    return FC.run(log, a.competitions)


if __name__ == "__main__":
    sys.exit(main())

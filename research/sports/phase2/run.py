"""Sports Phase 2 command line (RESEARCH_ONLY / NO_BET).

    python -m research.sports.phase2.run oof          # walk-forward out-of-fold forecasts (no network)
    python -m research.sports.phase2.run sample       # freeze the historical market sample + request estimate
    python -m research.sports.phase2.run fetch        # budgeted, resumable candle fetch of the frozen sample
    python -m research.sports.phase2.run select       # fit calibration / blends, freeze the Phase 2 protocol
    python -m research.sports.phase2.run evaluate     # retrospective 2026 scoring (journals before labels)
    python -m research.sports.phase2.run report       # docs + report hashes
    python -m research.sports.phase2.run reproduce    # oof, sample, select, evaluate, report from cache only
    python -m research.sports.phase2.run manifest | verify
    python -m research.sports.phase2.run capture --mode early|checkpoint      # manual prospective capture
    python -m research.sports.phase2.run capture-replay --record <path>       # offline reproduction

No scheduler is registered; captures run only when invoked by hand.
"""

from __future__ import annotations

import argparse
import sys
import time

from ..competitions import all_competitions
from ..core import utc_now_iso
from . import config as C


def log(msg: str) -> None:
    print(f"[{utc_now_iso()}] {msg}", flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="research.sports.phase2.run")
    p.add_argument("stage", choices=["oof", "sample", "fetch", "select", "evaluate", "report", "reproduce",
                                     "manifest", "verify", "capture", "capture-replay"])
    p.add_argument("--protocol", default=C.PROTOCOL)
    p.add_argument("--mode", choices=["early", "checkpoint"], help="capture mode")
    p.add_argument("--competition", default=None, help="capture: competition key (default nba,wnba,nfl,mlb,nhl)")
    p.add_argument("--record", help="capture-replay: path to a capture record")
    p.add_argument("--checked-against", help="manifest: run-level hash file the artifacts must match")
    p.add_argument("--note", default="", help="manifest: provenance note")
    args = p.parse_args(argv)
    t0 = time.time()
    if args.stage in ("capture", "capture-replay"):
        from . import capture as CP
        if args.stage == "capture":
            if not args.mode:
                raise SystemExit("--mode early|checkpoint is required")
            return CP.run_capture(args.mode, args.competition, log)
        if not args.record:
            raise SystemExit("--record is required")
        return CP.replay(args.record, log)
    if args.stage in ("manifest", "verify"):
        from .. import manifest as MAN
        from ..run import report_verify
        if args.stage == "manifest":
            MAN.write_manifest(args.protocol, args.checked_against, args.note, log)
            MAN.write_docs_supplement(args.protocol, log, MAN.DOC_SUPPLEMENT_WHY_NEW)
            return 0
        return report_verify(args.protocol, log)
    if args.competition:
        raise SystemExit("Phase 2 research stages always run over the full supported universe")
    from . import pipeline as P
    comps = all_competitions()[0]
    log(f"phase2 stage={args.stage} protocol={args.protocol} competitions={len(comps)}")
    if args.stage == "oof":
        P.stage_oof(args.protocol, comps, log)
    elif args.stage == "sample":
        P.stage_sample(args.protocol, comps, log)
    elif args.stage == "fetch":
        from . import market as MK
        MK.fetch(args.protocol, log)
    elif args.stage == "select":
        P.stage_select(args.protocol, comps, log)
    elif args.stage == "evaluate":
        P.stage_evaluate(args.protocol, comps, log)
    elif args.stage == "report":
        from . import report as R
        R.stage_report(args.protocol, log)
    elif args.stage == "reproduce":
        from . import report as R
        P.stage_oof(args.protocol, comps, log)
        P.stage_sample(args.protocol, comps, log)
        P.stage_select(args.protocol, comps, log)
        P.stage_evaluate(args.protocol, comps, log)
        R.stage_report(args.protocol, log)
    log(f"done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

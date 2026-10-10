"""Sports Phase 1 CLI (RESEARCH_ONLY / NO_BET).

    python -m research.sports.run all --cache-only         # reproduce v2 from the raw cache, no network
    python -m research.sports.run evaluate --sport Soccer --family total --cache-only
        # filtered: writes data/results/sports_phase1_subsets/<slug>/<protocol>/, never the full results
    python -m research.sports.run build --competition nba,nhl
    python -m research.sports.run verify --protocol sports_phase1_v1
    python -m research.sports.run manifest --protocol sports_phase1_v2

Stages: fetch -> build -> select (freeze protocol) -> evaluate (write-once journals) -> benchmark -> report.
``manifest`` records a protocol's artifact hashes; ``verify`` checks them. sports_phase1_v1 is
preserved: only ``verify`` (and ``manifest`` to check its hashes) may target it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

from .competitions import all_competitions
from .core import NORMALIZED, REPO_ROOT, RESULTS, Fetcher, CacheMiss, utc_now_iso, write_json

SUBSETS = REPO_ROOT / "data" / "results" / "sports_phase1_subsets"


def log(msg: str) -> None:
    print(f"[{utc_now_iso()}] {msg}", flush=True)


def select_competitions(args):
    comps, specs, excl = all_competitions()
    want_c = {x.strip().lower() for x in (args.competition or "").split(",") if x.strip()}
    want_s = {x.strip().lower() for x in (args.sport or "").split(",") if x.strip()}
    out = []
    for c in comps:
        if want_c and c.key.lower() not in want_c:
            continue
        if want_s and c.sport.lower() not in want_s:
            continue
        if args.source and c.source not in args.source.split(","):
            continue
        out.append(c)
    return out, specs, excl


def stage_fetch(args, comps, specs, fetcher):
    from .build import fetch_competition
    stats = {}
    for c in comps:
        try:
            stats[c.key] = fetch_competition(c, specs[c.key], fetcher, log)
        except Exception as exc:  # a broken source must not stop the other competitions
            stats[c.key] = {"error": repr(exc)}
            log(f"FETCH FAILED {c.key}: {exc!r}")
    path = NORMALIZED / "_fetch_stats" / f"{int(time.time())}.json"
    write_json(path, stats)


def stage_build(args, comps, specs, fetcher):
    from . import adapters as ad
    from .build import MilestoneIndex, build_competition
    milestones = ad.load_milestones()
    mindex = MilestoneIndex(milestones)
    for c in comps:
        try:
            s = build_competition(c, specs[c.key], fetcher, milestones, mindex)
            log(f"built {c.key}: ok={s['contracts_ok']} events={s['events']} markets={s['markets']}")
        except CacheMiss as exc:
            write_json(NORMALIZED / c.key / "build_summary.json",
                       {"competition": c.key, "sport": c.sport, "source": c.source, "blocked": f"cache miss: {exc}"})
            log(f"BUILD BLOCKED {c.key}: cache miss")
        except Exception as exc:
            write_json(NORMALIZED / c.key / "build_summary.json",
                       {"competition": c.key, "sport": c.sport, "source": c.source,
                        "blocked": f"build error: {exc!r}", "trace": traceback.format_exc()[-2000:]})
            log(f"BUILD FAILED {c.key}: {exc!r}")


def report_verify(protocol: str, log) -> int:
    from . import manifest as man
    d = man.verify_detail(protocol)
    for x in d["research"]:
        log(f"VERIFY FAILED {protocol} research artifact: {x}")
    for x in d["docs"]:
        log(f"VERIFY FAILED {protocol} document: {x}")
    log(f"verify {protocol}: research artifacts (exact bytes) {'OK' if not d['research'] else 'FAILED'}; "
        f"documents [{d['docs_method']}] {'OK' if not d['docs'] else 'FAILED'}")
    return 1 if d["research"] or d["docs"] else 0


FILTERS = ("competition", "family", "source", "sport")


def subset_slug(args) -> str | None:
    """Deterministic directory name for a filtered run (None for the full run)."""
    parts = []
    for f in FILTERS:
        v = getattr(args, f, None)
        if v:
            parts.append(f"{f}={','.join(sorted(x.strip() for x in v.split(',') if x.strip()))}")
    if not parts:
        return None
    text = ";".join(parts)
    safe = "".join(ch if ch.isalnum() or ch in "=,._-" else "_" for ch in text.replace(";", "__"))[:80]
    return f"{safe}__{hashlib.sha256(text.encode()).hexdigest()[:10]}"


def route_outputs(args) -> Path | None:
    """Send filtered evaluations to a subset directory so full-protocol results are never overwritten.

    Journals stay in the protocol's write-once journal directory (identical bytes are allowed).
    Benchmark, report and ``all`` only run on the full set.
    """
    slug = subset_slug(args)
    if slug is None:
        return None
    if args.stage in ("benchmark", "report", "all"):
        raise SystemExit(f"stage {args.stage} runs only on the full competition set; drop the filters "
                         "(use the evaluate stage for filtered runs)")
    from . import evaluate as ev
    ev.RESULTS = SUBSETS / slug
    return ev.RESULTS / args.protocol


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="research.sports.run")
    p.add_argument("stage", choices=["fetch", "build", "select", "evaluate", "benchmark", "report", "all",
                                     "manifest", "verify", "docs-supplement"])
    p.add_argument("--competition", help="comma-separated competition keys (e.g. nba,epl,k_kxatpmatch)")
    p.add_argument("--sport", help="comma-separated sports (e.g. Basketball,Soccer)")
    p.add_argument("--family", help="comma-separated contract families (game_winner,spread,total,...)")
    p.add_argument("--source", help="comma-separated source types (espn_team,espn_fight,espn_golf,jolpica,kalshi_only)")
    p.add_argument("--cache-only", action="store_true", help="never touch the network; fail on cache miss")
    p.add_argument("--min-interval", type=float, default=0.5, help="seconds between requests to one host")
    p.add_argument("--protocol", default="sports_phase1_v2")
    p.add_argument("--checked-against", help="manifest stage: run-level hash file the artifacts must match")
    p.add_argument("--note", default="", help="manifest stage: provenance note")
    args = p.parse_args(argv)
    if args.stage in ("manifest", "verify", "docs-supplement"):
        from . import manifest as man
        if args.stage == "manifest":
            man.write_manifest(args.protocol, args.checked_against, args.note, log)
            return 0
        if args.stage == "docs-supplement":
            man.write_docs_supplement(args.protocol, log)
            return 0
        return report_verify(args.protocol, log)
    from .evaluate import PRESERVED
    if args.protocol in PRESERVED:
        raise SystemExit(f"{args.protocol} is preserved; only verify/manifest may target it "
                         f"(reproduce at commit {PRESERVED[args.protocol]})")
    subset = route_outputs(args)
    if subset is not None:
        log(f"filtered run: outputs go to {subset}")
    comps, specs, excl = select_competitions(args)
    fetcher = Fetcher(cache_only=args.cache_only, min_interval=args.min_interval)
    stages = ["fetch", "build", "select", "evaluate", "benchmark", "report"] if args.stage == "all" else [args.stage]
    if args.cache_only and "fetch" in stages and args.stage == "all":
        stages.remove("fetch")
    log(f"stages={stages} competitions={len(comps)} cache_only={args.cache_only}")
    for st in stages:
        if st == "fetch":
            stage_fetch(args, comps, specs, fetcher)
        elif st == "build":
            stage_build(args, comps, specs, fetcher)
        elif st == "select":
            from .evaluate import stage_select
            stage_select(args, comps, log)
        elif st == "evaluate":
            from .evaluate import stage_evaluate
            stage_evaluate(args, comps, log)
        elif st == "benchmark":
            from .benchmark import stage_benchmark
            stage_benchmark(args, comps, fetcher, log)
        elif st == "report":
            from .report import stage_report
            stage_report(args, log)
    log(f"done; fetcher stats {json.dumps(fetcher.stats)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CLI for storage policy, disk health, and safe cleanup.

RESEARCH_ONLY / NO_BET.

    python -m research.storage.run policy
    python -m research.storage.run health
    python -m research.storage.run cleanup              # dry-run (default)
    python -m research.storage.run cleanup --execute --confirm DELETE
    python -m research.storage.run report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research.sports.core import REPO_ROOT

from .cleanup import run_cleanup
from .config import CONFIRM_TOKEN, config_as_dict, load_config
from .health import assess_disk, class_summary, write_health_status
from .policy import CLASS_LABELS
from .roots import archive_status, load_roots


def _print(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def cmd_policy(root: Path) -> int:
    cfg = load_config(root=root)
    _print({**config_as_dict(cfg), "classes": CLASS_LABELS})
    return 0


def cmd_health(root: Path, *, write: bool, quick: bool) -> int:
    health = assess_disk(root=root, include_dir_sizes=not quick)
    if write:
        write_health_status(health, root=root)
    _print(health.to_dict())
    return 0 if health.level != "critical" else 2


def cmd_cleanup(root: Path, args: argparse.Namespace) -> int:
    dry_run = not args.execute
    plan = run_cleanup(
        root=root,
        dry_run=dry_run,
        confirm=args.confirm,
        include_http_cache=not args.temp_only,
        include_temp=not args.cache_only,
        strict_refs=args.strict_refs,
    )
    _print(plan.to_dict())
    if plan.refused and not dry_run:
        return 1
    return 0


def cmd_roots(root: Path) -> int:
    roots = load_roots(root)
    st = archive_status(roots)
    _print({
        "schema": "kel_storage_roots_v1",
        "repo_root": str(roots.repo_root),
        "data_dir": str(roots.data_dir),
        "cache_dir": str(roots.cache_dir),
        "archive_dir": None if roots.archive_dir is None else str(roots.archive_dir),
        "archive": {
            "configured": st.configured,
            "available": st.available,
            "path": st.path,
            "free_bytes": st.free_bytes,
            "reason": st.reason,
        },
        "env": ["KALSHI_DATA_DIR", "KALSHI_CACHE_DIR", "KALSHI_ARCHIVE_DIR",
                "KALSHI_STORAGE_MAX_CACHE_GB", "KALSHI_STORAGE_CACHE_RETENTION_DAYS"],
    })
    return 0


def cmd_p0_tmp(root: Path, out: Path | None) -> int:
    """Re-run P0 inventory orphan-.tmp audit (never deletes)."""
    from research.storage._p0_tmp_audit import main as p0_main
    # Script writes under repo data/logs/storage; honor --root via env not needed —
    # audit module uses REPO_ROOT. Prefer importing helpers if root differs.
    if root.resolve() != REPO_ROOT.resolve():
        _print({"error": "p0-tmp currently audits the live REPO_ROOT inventory only",
                "root": str(root), "repo_root": str(REPO_ROOT)})
        return 2
    code = p0_main()
    if out:
        src = REPO_ROOT / "data" / "logs" / "storage" / "p0_inventory_tmp_candidates.json"
        if src.is_file() and out.resolve() != src.resolve():
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"copied {out}")
    return code


def cmd_report(root: Path, out: Path | None) -> int:
    """Emit a machine-readable storage report (also suitable for docs refresh)."""
    cfg = load_config(root=root)
    health = assess_disk(root=root, include_dir_sizes=False, record_sample=True)
    write_health_status(health, root=root)
    plan = run_cleanup(root=root, dry_run=True, include_http_cache=False, include_temp=True)
    report = {
        "schema": "kel_storage_report_v1",
        "research_only": True,
        "no_bet": True,
        "classes": class_summary(),
        "policy": config_as_dict(cfg),
        "health": health.to_dict(),
        "cleanup_dry_run": {
            "candidates": len(plan.candidates),
            "bytes_eligible": plan.bytes_eligible,
            "skipped_protected": plan.skipped_protected,
            "skipped_referenced": plan.skipped_referenced,
            "sample": plan.candidates[:25],
        },
        "recovery": {
            "sports_capture_evidence": "data/cache/sports/raw_capture_v2 (immutable; restore from ~/.kel/sports-state-backup-v2)",
            "sports_settlement_evidence": "data/cache/sports/raw_settlement_v1 (immutable)",
            "never_fabricate": True,
            "cleanup_default": "dry-run; destructive requires --execute --confirm DELETE",
        },
    }
    text = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text, end="")
    return 0


def cmd_migrate_evidence(root: Path, args: argparse.Namespace) -> int:
    from .inventory import freeze_baseline_runs, load_baseline_runs
    from .migrate_evidence import migrate_batch, migrate_until_done, retirement_report

    if args.retirement_report:
        rep = retirement_report(repo_root=root, archive_dir=args.archive_dir)
        _print(rep)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
        return 0
    if args.until_done:
        summary = migrate_until_done(
            repo_root=root,
            archive_dir=args.archive_dir,
            batch_size=args.batch_size,
            dry_run=not args.execute,
            checkpoint_path=args.checkpoint,
            update_meta=not args.skip_meta_update,
            max_batches=args.max_batches,
        )
        _print(summary)
        return 0 if summary.get("stopped_reason") in ("complete", "dry_run_single_batch") else 1
    freeze_baseline_runs(repo_root=root)
    baseline = load_baseline_runs(root)
    result = migrate_batch(
        repo_root=root,
        archive_dir=args.archive_dir,
        batch_size=args.batch_size,
        dry_run=not args.execute,
        checkpoint_path=args.checkpoint,
        update_meta=not args.skip_meta_update,
        baseline_run_ids=baseline,
    )
    _print(result.to_dict())
    return 1 if result.refused and args.execute else 0


def cmd_build_inventory(root: Path, args: argparse.Namespace) -> int:
    from .inventory import build_inventory, freeze_baseline_runs

    baseline = freeze_baseline_runs(repo_root=root)
    meta = build_inventory(
        repo_root=root,
        archive_dir=args.archive_dir,
        run_ids=list(baseline.get("run_ids") or []),
    )
    _print({**meta, "baseline_runs": baseline.get("count")})
    return 0 if meta.get("missing_sha256_rows", 0) == 0 else 1


def cmd_verify_archive(root: Path, args: argparse.Namespace) -> int:
    from .verify_archive import verify_archive, write_integrity_report

    report = verify_archive(
        repo_root=root,
        archive_dir=args.archive_dir,
        mode=args.mode,
        sample_limit=args.sample_limit,
    )
    path = write_integrity_report(report, repo_root=root)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _print(report)
    print(f"wrote {path}", file=sys.stderr)
    return 0 if report.get("retirement_eligible") else 1


def cmd_configure_archive(root: Path, args: argparse.Namespace) -> int:
    from .roots import write_persistent_archive_dir, load_roots, archive_status

    path = write_persistent_archive_dir(args.path, repo_root=root)
    roots = load_roots(root)
    st = archive_status(roots)
    _print({
        "wrote": str(path),
        "archive_dir": str(roots.archive_dir) if roots.archive_dir else None,
        "available": st.available,
        "reason": st.reason,
        "free_bytes": st.free_bytes,
    })
    return 0 if st.available else 1


def cmd_retirement_proposal(root: Path, args: argparse.Namespace) -> int:
    from .retirement import build_retirement_proposal, write_reliability_assessment

    write_reliability_assessment(repo_root=root)
    prop = build_retirement_proposal(repo_root=root, archive_dir=args.archive_dir)
    _print(prop)
    return 0


def cmd_cas_stats(root: Path, args: argparse.Namespace) -> int:
    from .cas_stats import measure_cas_effectiveness, write_cas_stats

    report = measure_cas_effectiveness(repo_root=root, sample_runs=args.sample_runs)
    path = write_cas_stats(report, repo_root=root)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        _print(report)
    print(f"wrote {path}", file=sys.stderr)
    return 0


def cmd_savings(root: Path, out: Path | None) -> int:
    """P5 combined savings report (actual vs estimated clearly labeled)."""
    import shutil
    from .cas_stats import measure_cas_effectiveness
    from .migrate_evidence import retirement_report

    p0_path = root / "data" / "logs" / "storage" / "p0_tmp_delete_result.json"
    p0 = json.loads(p0_path.read_text(encoding="utf-8")) if p0_path.is_file() else None
    cas = measure_cas_effectiveness(repo_root=root, sample_runs=20)
    retire = retirement_report(repo_root=root)
    c_free = shutil.disk_usage(str(root)).free
    d_free = None
    try:
        d_free = shutil.disk_usage("D:/").free
    except OSError:
        pass
    arch = archive_status(load_roots(root))
    report = {
        "schema": "kel_storage_savings_v1",
        "research_only": True,
        "no_bet": True,
        "c_free_gb_actual": round(c_free / 1024**3, 3),
        "d_free_gb_actual": None if d_free is None else round(d_free / 1024**3, 3),
        "p0_cleanup_actual": p0,
        "cas_effectiveness": cas,
        "migration_retirement_report": retire,
        "archive": {
            "configured": arch.configured,
            "available": arch.available,
            "path": arch.path,
            "reason": arch.reason,
        },
        "next_approval_required": (
            "Historical source retirement of migrated raw_capture_v2 .body files "
            "(NOT executed; see migration_retirement_report)"
        ),
    }
    text = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    dest = out or (root / "data" / "logs" / "storage" / "savings_report.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"wrote {dest}")
    _print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research.storage.run", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root (tests may override)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("policy", help="print storage classification and retention defaults")
    p_h = sub.add_parser("health", help="assess free space, largest caches, collector locks")
    p_h.add_argument("--write", action="store_true", help="persist data/logs/storage/disk_health.json")
    p_h.add_argument("--quick", action="store_true", help="free space + locks only (skip directory size walks)")

    p_c = sub.add_parser("cleanup", help="list or delete eligible disposable files (dry-run default)")
    p_c.add_argument("--execute", action="store_true", help="actually delete (requires --confirm DELETE)")
    p_c.add_argument("--confirm", default=None, help=f"must be {CONFIRM_TOKEN} with --execute")
    p_c.add_argument("--temp-only", action="store_true", help="only temporary files")
    p_c.add_argument("--cache-only", action="store_true", help="only reconstructable HTTP caches")
    p_c.add_argument("--strict-refs", action="store_true",
                     help="also scan capture/score records for pinned evidence paths (slower)")

    p_r = sub.add_parser("report", help="JSON storage report (health + dry-run cleanup summary)")
    p_r.add_argument("--out", type=Path, default=None, help="optional output path")

    sub.add_parser("roots", help="print configured data/cache/archive roots and archive availability")

    p0 = sub.add_parser("p0-tmp", help="audit orphan inventory *.tmp candidates (never deletes)")
    p0.add_argument("--out", type=Path, default=None, help="optional copy of the candidate JSON")

    p_m = sub.add_parser(
        "migrate-evidence",
        help="batch-migrate raw_capture_v2 bodies to archive CAS (never deletes sources)",
    )
    p_m.add_argument("--execute", action="store_true", help="perform migration (default is dry-run)")
    p_m.add_argument("--until-done", action="store_true",
                     help="loop batches until baseline complete (or hard stop)")
    p_m.add_argument("--batch-size", type=int, default=8, help="run_ids per batch (default 8)")
    p_m.add_argument("--max-batches", type=int, default=None, help="optional cap for until-done")
    p_m.add_argument("--archive-dir", type=Path, default=None, help="override archive dir")
    p_m.add_argument("--checkpoint", type=Path, default=None, help="checkpoint JSON path")
    p_m.add_argument("--skip-meta-update", action="store_true", help="do not rewrite local meta sidecars")
    p_m.add_argument("--retirement-report", action="store_true",
                     help="emit source-retirement size report only (no deletes)")
    p_m.add_argument("--out", type=Path, default=None, help="optional report output path")

    p_inv = sub.add_parser("build-inventory", help="permanent historical evidence inventory + checksum")
    p_inv.add_argument("--archive-dir", type=Path, default=None)

    p_va = sub.add_parser("verify-archive", help="Mode A/B archive integrity verification")
    p_va.add_argument("--mode", default="auto", choices=["auto", "a", "b", "source-to-archive", "archive-only"])
    p_va.add_argument("--archive-dir", type=Path, default=None)
    p_va.add_argument("--sample-limit", type=int, default=None, help="optional cap for quicker smoke checks")
    p_va.add_argument("--out", type=Path, default=None)

    p_cfg = sub.add_parser("configure-archive", help="persist archive_dir in storage_policy.local.json")
    p_cfg.add_argument("path", type=Path, help="archive root e.g. D:\\kalshi-archive")

    p_ret = sub.add_parser("retirement-proposal", help="non-executing source retirement proposal")
    p_ret.add_argument("--archive-dir", type=Path, default=None)

    p_cs = sub.add_parser("cas-stats", help="measure CAS vs legacy evidence storage (actual)")
    p_cs.add_argument("--sample-runs", type=int, default=30)
    p_cs.add_argument("--out", type=Path, default=None)

    p_s = sub.add_parser("savings", help="P5 combined savings report")
    p_s.add_argument("--out", type=Path, default=None)

    args = ap.parse_args(argv)
    root = args.root
    if args.cmd == "policy":
        return cmd_policy(root)
    if args.cmd == "health":
        return cmd_health(root, write=args.write, quick=args.quick)
    if args.cmd == "cleanup":
        return cmd_cleanup(root, args)
    if args.cmd == "report":
        return cmd_report(root, args.out)
    if args.cmd == "roots":
        return cmd_roots(root)
    if args.cmd == "p0-tmp":
        return cmd_p0_tmp(root, args.out)
    if args.cmd == "migrate-evidence":
        return cmd_migrate_evidence(root, args)
    if args.cmd == "build-inventory":
        return cmd_build_inventory(root, args)
    if args.cmd == "verify-archive":
        return cmd_verify_archive(root, args)
    if args.cmd == "configure-archive":
        return cmd_configure_archive(root, args)
    if args.cmd == "retirement-proposal":
        return cmd_retirement_proposal(root, args)
    if args.cmd == "cas-stats":
        return cmd_cas_stats(root, args)
    if args.cmd == "savings":
        return cmd_savings(root, args.out)
    raise SystemExit(f"unknown command {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())

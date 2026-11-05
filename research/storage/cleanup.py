"""Safe cleanup of reconstructable caches and temporary files.

Dry-run by default. Destructive mode requires ``--confirm DELETE``. Never deletes
immutable evidence, append-only logs, unknown paths, or any file referenced as
required evidence. Refuses to run destructively while a collector lock is held.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from research.sports.core import REPO_ROOT

from .config import CONFIRM_TOKEN, StorageConfig, load_config
from .health import collector_lock_snapshot
from .policy import CLASS_HTTP_CACHE, CLASS_TEMP, classify, rel_posix
from .refs import collect_required_evidence_paths


def _disk_free_snapshot(root: Path) -> dict:
    """Free-space only — cleanup must not walk multi-GB cache trees twice."""
    usage = shutil.disk_usage(str(root))
    return {
        "assessed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_gb": round(usage.free / (1024 ** 3), 3),
    }


@dataclass
class CleanupCandidate:
    path: str
    bytes: int
    classification: str
    reason: str
    mtime: float | None = None


@dataclass
class CleanupPlan:
    schema: str = "kel_storage_cleanup_plan_v1"
    research_only: bool = True
    no_bet: bool = True
    dry_run: bool = True
    confirmed: bool = False
    refused: list[str] = field(default_factory=list)
    disk_before: dict = field(default_factory=dict)
    disk_after: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    deleted: list[dict] = field(default_factory=list)
    skipped_protected: int = 0
    skipped_referenced: int = 0
    bytes_eligible: int = 0
    bytes_deleted: int = 0
    finished_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _limit_for(rel: str, cfg: StorageConfig):
    for lim in cfg.cache_limits:
        prefix = lim.prefix if lim.prefix.endswith("/") else lim.prefix + "/"
        if rel == lim.prefix.rstrip("/") or rel.startswith(prefix):
            return lim
    return None


def iter_temp_candidates(root: Path, *, budget_s: float = 20.0) -> list[CleanupCandidate]:
    """Find ``*.tmp`` / staging files under reconstructable caches and logs only."""
    out: list[CleanupCandidate] = []
    t0 = datetime.now(timezone.utc).timestamp()
    search_roots = [
        root / "data" / "cache" / "sports" / "raw",
        root / "data" / "cache" / "sports" / "raw_capture_v2_dev",
        root / "data" / "cache" / "sports" / "kalshi_discovery",
        root / "data" / "cache" / "weather",
        root / "data" / "cache" / "inventory",
        root / "data" / "cache" / "logs",
        root / "data" / "logs",
    ]
    skip_dir_names = {
        "raw_capture_v2", "raw_settlement_v1", "raw_challenger_itf_v1", "evidence_blobs_v1",
        "capture_v2", "capture_v2_scores", "phase7", "phase7_historical_replay",
    }
    for base in search_roots:
        if not base.exists():
            continue
        if datetime.now(timezone.utc).timestamp() - t0 > budget_s:
            break
        for dirpath, dirnames, filenames in os.walk(base):
            # Keep walking into inventory even when names start with '.' is for dirs only;
            # orphan atomic temps are files named .usable_*.jsonl.tmp at the inventory root.
            dirnames[:] = [d for d in dirnames if d not in skip_dir_names and not d.startswith(".")]
            for name in filenames:
                if not (name.endswith(".tmp") or name.endswith(".pyc") or name.endswith(".swp")):
                    continue
                p = Path(dirpath) / name
                if classify(p, root=root) != CLASS_TEMP:
                    continue
                out.append(CleanupCandidate(rel_posix(p, root), _file_size(p), CLASS_TEMP,
                                            "temporary file", _mtime(p)))
            if datetime.now(timezone.utc).timestamp() - t0 > budget_s:
                dirnames[:] = []
                break
    staging = root / "data" / "logs" / "sports" / ".backup_staging"
    if staging.exists():
        for p in staging.rglob("*"):
            if p.is_file():
                out.append(CleanupCandidate(rel_posix(p, root), _file_size(p), CLASS_TEMP,
                                            "backup staging", _mtime(p)))
    return out


def iter_http_cache_candidates(root: Path, cfg: StorageConfig, *, now_ts: float | None = None,
                               budget_s: float = 60.0) -> list[CleanupCandidate]:
    now_ts = datetime.now(timezone.utc).timestamp() if now_ts is None else now_ts
    out: list[CleanupCandidate] = []
    t0 = now_ts
    # Age-based
    for lim in cfg.cache_limits:
        if datetime.now(timezone.utc).timestamp() - t0 > budget_s:
            break
        base = root / lim.prefix.rstrip("/")
        if not base.exists():
            continue
        max_age_s = lim.max_age_days * 86400.0
        for dirpath, dirnames, filenames in os.walk(base):
            for name in filenames:
                p = Path(dirpath) / name
                if classify(p, root=root) != CLASS_HTTP_CACHE:
                    continue
                mt = _mtime(p)
                if mt is None:
                    continue
                age = now_ts - mt
                if age < max_age_s:
                    continue
                out.append(CleanupCandidate(
                    rel_posix(p, root), _file_size(p), CLASS_HTTP_CACHE,
                    f"older than {lim.max_age_days}d under {lim.prefix}", mt))
            if datetime.now(timezone.utc).timestamp() - t0 > budget_s:
                dirnames[:] = []
                break
    # Size-based: if a root exceeds max_bytes, mark oldest files beyond the cap.
    for lim in cfg.cache_limits:
        if datetime.now(timezone.utc).timestamp() - t0 > budget_s:
            break
        base = root / lim.prefix.rstrip("/")
        if not base.exists():
            continue
        files: list[tuple[float, Path, int]] = []
        total = 0
        for dirpath, dirnames, filenames in os.walk(base):
            for name in filenames:
                p = Path(dirpath) / name
                if classify(p, root=root) != CLASS_HTTP_CACHE:
                    continue
                sz = _file_size(p)
                mt = _mtime(p) or 0.0
                files.append((mt, p, sz))
                total += sz
            if datetime.now(timezone.utc).timestamp() - t0 > budget_s:
                dirnames[:] = []
                break
        if total <= lim.max_bytes:
            continue
        files.sort()  # oldest first
        keep_budget = lim.max_bytes
        already = {c.path for c in out}
        running = total
        for mt, p, sz in files:
            if running <= keep_budget:
                break
            rel = rel_posix(p, root)
            if rel in already:
                running -= sz
                continue
            out.append(CleanupCandidate(rel, sz, CLASS_HTTP_CACHE,
                                        f"over size cap {lim.max_bytes} for {lim.prefix}", mt))
            already.add(rel)
            running -= sz
    # Global regenerable budget (immutable evidence never walked here).
    out.extend(_global_cache_overage(root, cfg, out, budget_s=max(5.0, budget_s / 3)))
    return out


def _global_cache_overage(
    root: Path,
    cfg: StorageConfig,
    already: list[CleanupCandidate],
    *,
    budget_s: float,
) -> list[CleanupCandidate]:
    """Mark oldest regenerable cache files beyond ``max_cache_bytes``."""
    t0 = datetime.now(timezone.utc).timestamp()
    already_paths = {c.path for c in already}
    files: list[tuple[float, Path, int]] = []
    total = 0
    for lim in cfg.cache_limits:
        if datetime.now(timezone.utc).timestamp() - t0 > budget_s:
            break
        base = root / lim.prefix.rstrip("/")
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            for name in filenames:
                p = Path(dirpath) / name
                if classify(p, root=root) != CLASS_HTTP_CACHE:
                    continue
                sz = _file_size(p)
                mt = _mtime(p) or 0.0
                files.append((mt, p, sz))
                total += sz
            if datetime.now(timezone.utc).timestamp() - t0 > budget_s:
                dirnames[:] = []
                break
    if total <= cfg.max_cache_bytes:
        return []
    files.sort()
    running = total
    extra: list[CleanupCandidate] = []
    for mt, p, sz in files:
        if running <= cfg.max_cache_bytes:
            break
        rel = rel_posix(p, root)
        if rel in already_paths:
            running -= sz
            continue
        extra.append(CleanupCandidate(
            rel, sz, CLASS_HTTP_CACHE,
            f"over global regenerable cap {cfg.max_cache_bytes}", mt))
        already_paths.add(rel)
        running -= sz
    return extra


_REF_SCAN_MARKERS = (
    "raw_capture_v2/",
    "raw_settlement_v1/",
    "raw_challenger_itf_v1/",
    "evidence_blobs_v1/",
    "phase7/evidence/",
    "phase7_historical_replay/evidence/",
)


def plan_cleanup(
    *,
    root: Path = REPO_ROOT,
    cfg: StorageConfig | None = None,
    include_http_cache: bool = True,
    include_temp: bool = True,
    required_refs: set[Path] | None = None,
    strict_refs: bool = False,
    now_ts: float | None = None,
) -> tuple[list[CleanupCandidate], int, int]:
    """Return (candidates, skipped_protected, skipped_referenced).

    Path classification is the primary protection (immutable prefixes are never
    candidates). Record-level evidence refs are loaded when ``strict_refs`` is
    set, when the caller passes ``required_refs``, or when a candidate path
    unexpectedly sits under an evidence marker (defense in depth).
    """
    cfg = cfg or load_config(root=root)
    raw: list[CleanupCandidate] = []
    if include_temp:
        raw.extend(iter_temp_candidates(root))
    if include_http_cache:
        raw.extend(iter_http_cache_candidates(root, cfg, now_ts=now_ts))

    need_refs = required_refs is not None or strict_refs or any(
        any(m in c.path.replace("\\", "/") for m in _REF_SCAN_MARKERS) for c in raw
    )
    if required_refs is not None:
        refs_resolved = {p.resolve() for p in required_refs}
    elif need_refs:
        refs_resolved = {p.resolve() for p in collect_required_evidence_paths(root=root)}
    else:
        refs_resolved = set()

    skipped_protected = 0
    skipped_referenced = 0
    eligible: list[CleanupCandidate] = []
    seen: set[str] = set()
    for c in raw:
        if c.path in seen:
            continue
        seen.add(c.path)
        abs_path = (root / c.path).resolve()
        cls = classify(abs_path, root=root)
        if cls not in (CLASS_HTTP_CACHE, CLASS_TEMP):
            skipped_protected += 1
            continue
        if refs_resolved and abs_path in refs_resolved:
            skipped_referenced += 1
            continue
        if refs_resolved and any(abs_path == r or r in abs_path.parents
                                 for r in refs_resolved if r.is_dir()):
            skipped_referenced += 1
            continue
        eligible.append(c)
    return eligible, skipped_protected, skipped_referenced


def run_cleanup(
    *,
    root: Path = REPO_ROOT,
    cfg: StorageConfig | None = None,
    dry_run: bool = True,
    confirm: str | None = None,
    include_http_cache: bool = True,
    include_temp: bool = True,
    required_refs: set[Path] | None = None,
    strict_refs: bool = False,
) -> CleanupPlan:
    cfg = cfg or load_config(root=root)
    before = _disk_free_snapshot(root)
    plan = CleanupPlan(dry_run=dry_run, confirmed=confirm == CONFIRM_TOKEN,
                       disk_before=before)

    if not dry_run:
        if confirm != CONFIRM_TOKEN:
            plan.refused.append(
                f"destructive cleanup requires --confirm {CONFIRM_TOKEN}; refusing"
            )
            plan.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            return plan
        if cfg.refuse_cleanup_when_collectors_busy:
            locks = collector_lock_snapshot(root)
            if locks["any_busy"]:
                plan.refused.append(
                    "collector lock held; refusing destructive cleanup so active collection is not interrupted "
                    f"(sports.held={locks['sports'].get('held')}, weather.held={locks['weather'].get('held')})"
                )
                plan.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                return plan

    candidates, skipped_prot, skipped_ref = plan_cleanup(
        root=root, cfg=cfg, include_http_cache=include_http_cache, include_temp=include_temp,
        required_refs=required_refs, strict_refs=strict_refs)
    plan.candidates = [asdict(c) for c in candidates]
    plan.skipped_protected = skipped_prot
    plan.skipped_referenced = skipped_ref
    plan.bytes_eligible = sum(c.bytes for c in candidates)

    if dry_run or plan.refused:
        plan.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return plan

    deleted = []
    bytes_deleted = 0
    for c in candidates:
        path = root / c.path
        try:
            if path.is_file():
                sz = _file_size(path)
                path.unlink()
                deleted.append({"path": c.path, "bytes": sz})
                bytes_deleted += sz
        except OSError as exc:
            plan.refused.append(f"failed to delete {c.path}: {exc}")
    plan.deleted = deleted
    plan.bytes_deleted = bytes_deleted
    plan.disk_after = _disk_free_snapshot(root)
    plan.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return plan

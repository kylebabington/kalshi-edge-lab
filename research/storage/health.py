"""Disk-health assessment and collector-facing status payload."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from research.sports.core import REPO_ROOT
from research.sports.ops import lock as LK

from .config import StorageConfig, load_config
from .policy import CLASS_LABELS, classify, http_cache_roots, immutable_roots, rel_posix


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class DirSize:
    path: str
    bytes: int
    files: int
    classification: str
    timed_out: bool = False


@dataclass
class DiskHealth:
    schema: str = "kel_disk_health_v1"
    research_only: bool = True
    no_bet: bool = True
    assessed_at: str = ""
    path: str = ""
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    free_gb: float = 0.0
    level: str = "ok"  # ok | warning | critical
    warning_bytes: int = 0
    critical_bytes: int = 0
    estimated_growth_bytes_per_day: float | None = None
    days_to_warning: float | None = None
    largest_cache_dirs: list[dict] = field(default_factory=list)
    immutable_dirs: list[dict] = field(default_factory=list)
    collectors: dict = field(default_factory=dict)
    archive: dict = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def dir_size(path: Path, *, budget_s: float = 8.0) -> DirSize:
    cls = classify(path)
    if not path.exists():
        return DirSize(rel_posix(path), 0, 0, cls)
    t0 = datetime.now().timestamp()
    n = b = 0
    timed_out = False
    for root, dirs, files in os.walk(path):
        # Skip nested immutable evidence when walking a parent cache tree? No — size is factual.
        for name in files:
            try:
                b += os.path.getsize(os.path.join(root, name))
                n += 1
            except OSError:
                pass
        if datetime.now().timestamp() - t0 > budget_s:
            timed_out = True
            break
    return DirSize(rel_posix(path), b, n, cls, timed_out)


def _growth_rate(history_path: Path, lookback_hours: float) -> float | None:
    if not history_path.is_file():
        return None
    rows = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    if len(rows) < 2:
        return None
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - lookback_hours * 3600
    recent = [r for r in rows if float(r.get("ts", 0)) >= cutoff]
    if len(recent) < 2:
        recent = rows[-8:]
    if len(recent) < 2:
        return None
    a, b = recent[0], recent[-1]
    dt = float(b["ts"]) - float(a["ts"])
    if dt <= 0:
        return None
    du = int(b["used_bytes"]) - int(a["used_bytes"])
    return du * 86400.0 / dt


def append_usage_sample(root: Path = REPO_ROOT, path: Path | None = None) -> None:
    usage = shutil.disk_usage(str(root))
    path = path or (root / "data" / "logs" / "storage" / "disk_usage_history.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).timestamp(), "used_bytes": usage.used,
           "free_bytes": usage.free, "total_bytes": usage.total}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def collector_lock_snapshot(root: Path = REPO_ROOT) -> dict:
    sports = LK.lock_state(root / "data" / "logs" / "sports" / "cycle.lock")
    weather = LK.lock_state(root / "data" / "logs" / "prospective" / "cycle.lock")
    return {
        "sports": sports,
        "weather": weather,
        "any_busy": bool(sports.get("held") or weather.get("held")),
    }


def assess_disk(
    *,
    root: Path = REPO_ROOT,
    cfg: StorageConfig | None = None,
    budget_s_per_dir: float = 5.0,
    record_sample: bool = True,
    include_dir_sizes: bool = True,
) -> DiskHealth:
    cfg = cfg or load_config(root=root)
    if record_sample:
        try:
            append_usage_sample(root)
        except OSError:
            pass
    usage = shutil.disk_usage(str(root))
    level = "ok"
    if usage.free <= cfg.free_critical_bytes:
        level = "critical"
    elif usage.free <= cfg.free_warning_bytes:
        level = "warning"

    cache_sizes: list[DirSize] = []
    imm: list[DirSize] = []
    if include_dir_sizes:
        for p in http_cache_roots(root):
            if p.exists():
                cache_sizes.append(dir_size(p, budget_s=budget_s_per_dir))
        cache_sizes.sort(key=lambda d: d.bytes, reverse=True)
        for p in immutable_roots(root):
            if p.exists():
                imm.append(dir_size(p, budget_s=min(3.0, budget_s_per_dir)))

    growth = _growth_rate(root / "data" / "logs" / "storage" / "disk_usage_history.jsonl",
                          cfg.growth_lookback_hours)
    collectors = collector_lock_snapshot(root)
    from .roots import archive_status, load_roots
    roots = load_roots(root)
    arch = archive_status(roots)
    archive_info = {
        "configured": arch.configured,
        "available": arch.available,
        "path": arch.path,
        "free_bytes": arch.free_bytes,
        "reason": arch.reason,
    }
    days_to_warning = None
    if growth is not None and growth > 0:
        headroom = max(0, usage.free - cfg.free_warning_bytes)
        days_to_warning = round(headroom / growth, 2)
    recs: list[str] = []
    if level == "critical":
        recs.append("Free space is at or below the critical threshold; pause non-essential work and run "
                    "`python -m research.storage.run cleanup` (dry-run) then confirm DELETE only for "
                    "reconstructable caches / temp files.")
    elif level == "warning":
        recs.append("Free space is below the warning threshold; plan a dry-run cleanup of HTTP caches.")
    if days_to_warning is not None and days_to_warning < 7:
        recs.append(
            f"Estimated {days_to_warning} day(s) until warning threshold at current growth; "
            "prefer CAS capture + archive migration over emergency deletes."
        )
    if collectors["any_busy"]:
        recs.append("A collector lock is held; destructive cleanup is refused until cycles finish.")
    if arch.configured and not arch.available:
        recs.append(f"Archive configured but unavailable: {arch.reason}")
    recs.append("Never delete data/cache/sports/raw_capture_v2 or raw_settlement_v1 without separate "
                "retirement approval after verified archive migration.")

    health = DiskHealth(
        assessed_at=_iso_now(),
        path=str(Path(root).resolve()),
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        free_gb=round(usage.free / (1024 ** 3), 3),
        level=level,
        warning_bytes=cfg.free_warning_bytes,
        critical_bytes=cfg.free_critical_bytes,
        estimated_growth_bytes_per_day=None if growth is None else round(growth, 1),
        days_to_warning=days_to_warning,
        largest_cache_dirs=[asdict(d) for d in cache_sizes[:12]],
        immutable_dirs=[asdict(d) for d in imm[:16]],
        collectors=collectors,
        archive=archive_info,
        recommendations=recs,
    )
    return health


def write_health_status(health: DiskHealth, path: Path | None = None, root: Path = REPO_ROOT) -> Path:
    path = path or (root / "data" / "logs" / "storage" / "disk_health.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(health.to_dict(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def class_summary() -> dict[str, str]:
    return dict(CLASS_LABELS)

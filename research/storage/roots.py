"""Configurable data / cache / archive roots (Windows-safe).

Precedence for archive_dir:
  1. Explicit caller override
  2. KALSHI_ARCHIVE_DIR environment variable
  3. Persistent ``archive_dir`` in storage_policy.local.json
  4. Unset → clear error when archive is required

Never silently redirects immutable evidence to an unexpected volume.
Active prospective captures must not depend on the archive volume.
RESEARCH_ONLY / NO_BET.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from research.sports.core import REPO_ROOT

POLICY_LOCAL_REL = "data/logs/storage/storage_policy.local.json"


@dataclass(frozen=True)
class StorageRoots:
    repo_root: Path
    data_dir: Path
    cache_dir: Path
    archive_dir: Path | None


@dataclass(frozen=True)
class ArchiveStatus:
    configured: bool
    available: bool
    path: str | None
    free_bytes: int | None
    reason: str


def _resolve(raw: str | None, default: Path) -> Path:
    if not raw:
        return default.resolve()
    return Path(raw).expanduser().resolve()


def _policy_local_path(repo_root: Path) -> Path:
    return repo_root / POLICY_LOCAL_REL


def read_persistent_archive_dir(repo_root: Path = REPO_ROOT) -> str | None:
    path = _policy_local_path(repo_root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    val = raw.get("archive_dir")
    if val is None or str(val).strip() == "":
        return None
    return str(val)


def write_persistent_archive_dir(archive_dir: str | Path, *, repo_root: Path = REPO_ROOT) -> Path:
    """Merge ``archive_dir`` into storage_policy.local.json (create if missing)."""
    path = _policy_local_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    data["archive_dir"] = str(Path(archive_dir))
    data.setdefault("schema", "kel_storage_policy_v1")
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_roots(
    repo_root: Path = REPO_ROOT,
    *,
    archive_dir_override: Path | str | None = None,
) -> StorageRoots:
    repo_root = Path(repo_root).resolve()
    data = _resolve(os.environ.get("KALSHI_DATA_DIR"), repo_root / "data")
    cache = _resolve(os.environ.get("KALSHI_CACHE_DIR"), data / "cache")
    if archive_dir_override is not None:
        archive: Path | None = Path(archive_dir_override).expanduser().resolve()
    elif os.environ.get("KALSHI_ARCHIVE_DIR"):
        archive = Path(os.environ["KALSHI_ARCHIVE_DIR"]).expanduser().resolve()
    else:
        persisted = read_persistent_archive_dir(repo_root)
        archive = Path(persisted).expanduser().resolve() if persisted else None
    return StorageRoots(repo_root=repo_root, data_dir=data, cache_dir=cache, archive_dir=archive)


def archive_status(roots: StorageRoots | None = None) -> ArchiveStatus:
    roots = roots or load_roots()
    if roots.archive_dir is None:
        return ArchiveStatus(
            False, False, None, None,
            "archive not configured (set CLI --archive-dir, KALSHI_ARCHIVE_DIR, or "
            "archive_dir in data/logs/storage/storage_policy.local.json)",
        )
    path = roots.archive_dir
    try:
        if not path.exists():
            drive = path.anchor or path.drive
            probe = Path(drive) if drive else path.parent
            if not probe.exists():
                return ArchiveStatus(True, False, str(path), None, f"archive volume missing: {probe}")
            usage = shutil.disk_usage(str(probe))
            return ArchiveStatus(
                True, True, str(path), usage.free,
                "archive path missing but volume reachable",
            )
        usage = shutil.disk_usage(str(path))
        return ArchiveStatus(True, True, str(path), usage.free, "ok")
    except OSError as exc:
        return ArchiveStatus(True, False, str(path), None, f"archive unavailable: {exc}")

"""Incremental historical sports evidence migration to archive CAS.

Reads legacy ``{key}.body`` under ``raw_capture_v2``, stores unique payloads on
the archive volume (``KALSHI_ARCHIVE_DIR``), updates per-run meta with archive
CAS descriptors, and **never deletes** source bodies.

RESEARCH_ONLY / NO_BET.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from research.sports.core import REPO_ROOT

from .cas import (
    LOCATION_ARCHIVE,
    archive_blobs_dir,
    read_blob,
    sha256_bytes,
    store_blob,
)
from .roots import archive_status, load_roots
from .write import atomic_write_text

CAPTURE_REL = "data/cache/sports/raw_capture_v2"
CHECKPOINT_REL = "data/logs/storage/migrate_evidence_checkpoint.json"
MANIFEST_NAME = "manifests/sports_evidence_cas_v1.jsonl"
PROGRESS_REL = "data/logs/storage/migrate_progress.jsonl"
LOCK_REL = "data/logs/storage/migrate_evidence.lock"
DEFAULT_BATCH_RUNS = 8
# Stop if C: free falls below this (keep headroom for collectors / OS).
# Migration writes blobs to D:; C: cost is meta/checkpoint. Keep enough for
# an active collector cycle, but allow finishing historical migrate when C: is
# chronically tight after HTTP-cache reclaim.
MIN_C_FREE_BYTES = 1 * 1024 * 1024 * 1024
MAX_CONSECUTIVE_NO_PROGRESS = 3
MAX_CONSECUTIVE_BATCH_FAILURES = 5
# Persist often enough for safe resume without rewriting a multi‑MB JSON every payload.
CHECKPOINT_EVERY_N_FILES = 50


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class MigrateResult:
    schema: str = "kel_migrate_evidence_batch_v1"
    research_only: bool = True
    no_bet: bool = True
    dry_run: bool = False
    started_at: str = ""
    finished_at: str = ""
    archive_dir: str | None = None
    source_root: str = ""
    batch_run_ids: list[str] = field(default_factory=list)
    files_seen: int = 0
    files_migrated: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    bytes_source: int = 0
    bytes_archived_new: int = 0
    bytes_deduped: int = 0
    verified: int = 0
    refused: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    checkpoint_path: str = ""
    manifest_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def default_checkpoint_path(repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / CHECKPOINT_REL


def load_checkpoint(path: Path) -> dict:
    if not path.is_file():
        return {
            "schema": "kel_migrate_evidence_checkpoint_v1",
            "completed_keys": {},  # run_id -> list of "source/key"
            "completed_runs": [],
            "stats": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def list_run_ids(source_root: Path) -> list[str]:
    if not source_root.is_dir():
        return []
    return sorted(
        p.name for p in source_root.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


def iter_body_files(run_dir: Path) -> Iterator[Path]:
    for dirpath, _dirnames, filenames in os.walk(run_dir):
        for name in filenames:
            if name.endswith(".body"):
                yield Path(dirpath) / name


def _key_token(body_path: Path, run_dir: Path) -> str:
    rel = body_path.relative_to(run_dir).as_posix()
    if rel.endswith(".body"):
        rel = rel[: -len(".body")]
    return rel


def _preflight(archive_dir: Path | None, *, need_bytes: int, repo_root: Path = REPO_ROOT) -> list[str]:
    refused: list[str] = []
    if archive_dir is None:
        refused.append("archive not configured; refuse migration")
        return refused
    st = archive_status(load_roots(repo_root, archive_dir_override=archive_dir))
    if not st.available:
        refused.append(f"archive unavailable: {st.reason}")
        return refused
    free = st.free_bytes or 0
    if free < int(need_bytes * 1.2) + (512 * 1024 * 1024):
        refused.append(
            f"insufficient archive free space: free={free} need~={int(need_bytes * 1.2)}"
        )
    try:
        c_free = shutil.disk_usage(str(repo_root)).free
        if c_free < MIN_C_FREE_BYTES:
            refused.append(
                f"C: free space unsafe for ops ({c_free} < {MIN_C_FREE_BYTES}); "
                "pause migration to protect collectors"
            )
    except OSError as exc:
        refused.append(f"cannot assess C: free space: {exc}")
    return refused


class MigrateLockError(RuntimeError):
    pass


class _MigrateLock:
    """Exclusive migrate lock (refuse competing processes)."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        self._fd = os.open(str(self.path), flags)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            raise MigrateLockError(
                f"another migrate-evidence process holds {self.path}"
            ) from exc
        payload = json.dumps({"pid": os.getpid(), "acquired_at": _iso_now()}) + "\n"
        os.ftruncate(self._fd, 0)
        os.write(self._fd, payload.encode("utf-8"))

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def _append_progress(repo_root: Path, row: dict) -> None:
    path = repo_root / PROGRESS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _disk_snapshot(repo_root: Path, archive_dir: Path | None) -> dict:
    out: dict[str, Any] = {}
    try:
        u = shutil.disk_usage(str(repo_root))
        out["c_free_bytes"] = u.free
        out["c_free_gb"] = round(u.free / 1024**3, 3)
    except OSError:
        out["c_free_bytes"] = None
    if archive_dir is not None:
        try:
            probe = archive_dir if archive_dir.exists() else Path(archive_dir.anchor or archive_dir.drive)
            u = shutil.disk_usage(str(probe))
            out["d_free_bytes"] = u.free
            out["d_free_gb"] = round(u.free / 1024**3, 3)
        except OSError:
            out["d_free_bytes"] = None
    return out


def _estimate_batch_bytes(source_root: Path, run_ids: list[str]) -> int:
    total = 0
    for run_id in run_ids:
        run_dir = source_root / run_id
        if not run_dir.is_dir():
            continue
        for body in iter_body_files(run_dir):
            try:
                total += body.stat().st_size
            except OSError:
                continue
    return total


def migrate_batch(
    *,
    repo_root: Path = REPO_ROOT,
    archive_dir: Path | None = None,
    source_rel: str = CAPTURE_REL,
    batch_size: int = DEFAULT_BATCH_RUNS,
    dry_run: bool = False,
    checkpoint_path: Path | None = None,
    update_meta: bool = True,
    baseline_run_ids: list[str] | None = None,
) -> MigrateResult:
    """Migrate up to ``batch_size`` not-yet-complete run_ids to archive CAS."""
    roots = load_roots(repo_root, archive_dir_override=archive_dir)
    archive_dir = archive_dir or roots.archive_dir
    source_root = repo_root / source_rel
    checkpoint_path = checkpoint_path or default_checkpoint_path(repo_root)
    result = MigrateResult(
        dry_run=dry_run,
        started_at=_iso_now(),
        archive_dir=None if archive_dir is None else str(archive_dir),
        source_root=str(source_root),
        checkpoint_path=str(checkpoint_path),
    )

    ckpt = load_checkpoint(checkpoint_path)
    completed_runs = set(ckpt.get("completed_runs") or [])
    completed_keys: dict[str, set[str]] = {
        k: set(v) for k, v in (ckpt.get("completed_keys") or {}).items()
    }

    universe = baseline_run_ids if baseline_run_ids is not None else list_run_ids(source_root)
    pending = [r for r in universe if r not in completed_runs]
    batch = pending[: max(1, batch_size)]
    result.batch_run_ids = batch
    if not batch:
        result.finished_at = _iso_now()
        result.refused.append("no pending run_ids to migrate")
        return result

    est = _estimate_batch_bytes(source_root, batch)
    refused = _preflight(archive_dir, need_bytes=est if not dry_run else 0, repo_root=repo_root)
    if refused:
        result.refused.extend(refused)
        result.finished_at = _iso_now()
        return result

    assert archive_dir is not None
    abs_blobs = archive_blobs_dir(archive_dir)
    manifest_path = Path(archive_dir) / MANIFEST_NAME
    result.manifest_path = str(manifest_path)

    if not dry_run:
        abs_blobs.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

    def _persist_checkpoint(*, mark_runs: bool = False) -> None:
        if mark_runs:
            ckpt["completed_runs"] = sorted(completed_runs)
        ckpt["completed_keys"] = {k: sorted(v) for k, v in completed_keys.items()}
        ckpt["updated_at"] = _iso_now()
        save_checkpoint(checkpoint_path, ckpt)

    since_ckpt = 0
    for run_id in batch:
        run_dir = source_root / run_id
        done_keys = completed_keys.setdefault(run_id, set())
        run_ok = True
        for body_path in iter_body_files(run_dir):
            result.files_seen += 1
            token = _key_token(body_path, run_dir)
            if token in done_keys:
                result.files_skipped += 1
                continue
            meta_path = body_path.parent / (body_path.name[: -len(".body")] + ".meta.json")
            try:
                body = body_path.read_bytes()
            except OSError as exc:
                result.files_failed += 1
                run_ok = False
                result.errors.append({"path": str(body_path), "error": str(exc)})
                continue
            result.bytes_source += len(body)
            digest = sha256_bytes(body)
            if dry_run:
                result.files_migrated += 1
                continue
            try:
                storage = store_blob(
                    body,
                    absolute_blob_root=abs_blobs,
                    location=LOCATION_ARCHIVE,
                )
                # Verify read-back
                got = read_blob(
                    digest,
                    encoding=storage.get("encoding"),
                    absolute_blob_root=abs_blobs,
                )
                if got != body or sha256_bytes(got) != digest:
                    raise RuntimeError("post-write verification failed")
                result.verified += 1
                if storage.get("deduped"):
                    result.bytes_deduped += len(body)
                else:
                    result.bytes_archived_new += int(storage.get("stored_bytes") or 0)

                if update_meta:
                    meta: dict[str, Any] = {}
                    if meta_path.is_file():
                        try:
                            meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            meta = {}
                    meta = {
                        **meta,
                        "sha256": meta.get("sha256") or digest,
                        "bytes": meta.get("bytes") or len(body),
                        "storage": storage,
                        "migration": {
                            "schema": "kel_evidence_migrate_v1",
                            "migrated_at": _iso_now(),
                            "source_body_kept": True,
                            "archive_dir": str(archive_dir),
                        },
                    }
                    atomic_write_text(
                        meta_path, json.dumps(meta, indent=1, sort_keys=True) + "\n"
                    )

                try:
                    source_rel_path = body_path.resolve().relative_to(repo_root.resolve()).as_posix()
                except ValueError:
                    source_rel_path = str(body_path)
                row = {
                    "ts": _iso_now(),
                    "run_id": run_id,
                    "key_path": token,
                    "source_body": source_rel_path,
                    "sha256": digest,
                    "logical_bytes": len(body),
                    "stored_bytes": storage.get("stored_bytes"),
                    "encoding": storage.get("encoding"),
                    "deduped": storage.get("deduped"),
                    "verified": True,
                }
                with open(manifest_path, "a", encoding="utf-8") as mf:
                    mf.write(json.dumps(row, sort_keys=True) + "\n")

                done_keys.add(token)
                result.files_migrated += 1
                since_ckpt += 1
                if since_ckpt >= CHECKPOINT_EVERY_N_FILES:
                    _persist_checkpoint()
                    since_ckpt = 0
            except Exception as exc:
                result.files_failed += 1
                run_ok = False
                result.errors.append({"path": str(body_path), "error": str(exc)})
                # Always persist after a failure so resume does not redo verified work.
                _persist_checkpoint()
                since_ckpt = 0

        if run_ok and not dry_run:
            # Mark run complete only if every body file is in done_keys.
            all_tokens = {_key_token(p, run_dir) for p in iter_body_files(run_dir)}
            if all_tokens <= done_keys:
                completed_runs.add(run_id)
                _persist_checkpoint(mark_runs=True)
                since_ckpt = 0
        elif not dry_run and since_ckpt:
            _persist_checkpoint()
            since_ckpt = 0

    if not dry_run and since_ckpt:
        _persist_checkpoint()

    result.finished_at = _iso_now()
    disk = _disk_snapshot(repo_root, archive_dir)
    _append_progress(repo_root, {
        "ts": result.finished_at,
        "event": "batch",
        "dry_run": dry_run,
        "batch_run_ids": result.batch_run_ids,
        "files_migrated": result.files_migrated,
        "files_failed": result.files_failed,
        "files_skipped": result.files_skipped,
        "bytes_source": result.bytes_source,
        "bytes_archived_new": result.bytes_archived_new,
        "bytes_deduped": result.bytes_deduped,
        "verified": result.verified,
        "refused": result.refused,
        **disk,
    })
    return result


def migrate_until_done(
    *,
    repo_root: Path = REPO_ROOT,
    archive_dir: Path | None = None,
    source_rel: str = CAPTURE_REL,
    batch_size: int = DEFAULT_BATCH_RUNS,
    dry_run: bool = False,
    checkpoint_path: Path | None = None,
    update_meta: bool = True,
    max_batches: int | None = None,
) -> dict[str, Any]:
    """Loop migrate_batch until baseline complete or hard stop."""
    from .inventory import freeze_baseline_runs, load_baseline_runs

    lock_path = repo_root / LOCK_REL
    summary: dict[str, Any] = {
        "schema": "kel_migrate_evidence_until_done_v1",
        "research_only": True,
        "no_bet": True,
        "started_at": _iso_now(),
        "batches": [],
        "stopped_reason": None,
    }
    freeze_baseline_runs(repo_root=repo_root, source_rel=source_rel)
    baseline = load_baseline_runs(repo_root) or []
    summary["baseline_runs"] = len(baseline)
    no_progress = 0
    failures = 0
    try:
        with _MigrateLock(lock_path):
            batch_i = 0
            while True:
                if max_batches is not None and batch_i >= max_batches:
                    summary["stopped_reason"] = "max_batches"
                    break
                batch_i += 1
                result = migrate_batch(
                    repo_root=repo_root,
                    archive_dir=archive_dir,
                    source_rel=source_rel,
                    batch_size=batch_size,
                    dry_run=dry_run,
                    checkpoint_path=checkpoint_path,
                    update_meta=update_meta,
                    baseline_run_ids=baseline,
                )
                summary["batches"].append(result.to_dict())
                if result.refused and "no pending run_ids" in (result.refused[0] if result.refused else ""):
                    summary["stopped_reason"] = "complete"
                    break
                if result.refused:
                    failures += 1
                    summary["stopped_reason"] = f"refused: {result.refused}"
                    if failures >= MAX_CONSECUTIVE_BATCH_FAILURES:
                        break
                    continue
                if result.files_migrated == 0 and result.files_failed == 0 and not result.refused:
                    no_progress += 1
                elif result.files_migrated == 0 and result.files_failed > 0:
                    failures += 1
                    no_progress += 1
                else:
                    no_progress = 0
                    failures = 0
                if no_progress >= MAX_CONSECUTIVE_NO_PROGRESS:
                    summary["stopped_reason"] = "no_progress"
                    break
                if failures >= MAX_CONSECUTIVE_BATCH_FAILURES:
                    summary["stopped_reason"] = "repeated_failures"
                    break
                if dry_run:
                    # One dry-run batch is enough for until-done dry mode.
                    summary["stopped_reason"] = "dry_run_single_batch"
                    break
    except MigrateLockError as exc:
        summary["stopped_reason"] = str(exc)
        summary["refused"] = [str(exc)]
    summary["finished_at"] = _iso_now()
    ckpt = load_checkpoint(checkpoint_path or default_checkpoint_path(repo_root))
    summary["completed_runs"] = len(ckpt.get("completed_runs") or [])
    summary["completed_keys"] = sum(len(v) for v in (ckpt.get("completed_keys") or {}).values())
    _append_progress(repo_root, {"ts": summary["finished_at"], "event": "until_done", **{
        k: summary[k] for k in ("stopped_reason", "completed_runs", "completed_keys", "baseline_runs")
    }})
    return summary


def retirement_report(
    *,
    repo_root: Path = REPO_ROOT,
    archive_dir: Path | None = None,
    source_rel: str = CAPTURE_REL,
    checkpoint_path: Path | None = None,
) -> dict:
    """Summarize verified migration progress; does **not** delete originals."""
    roots = load_roots(repo_root)
    archive_dir = archive_dir or roots.archive_dir
    source_root = repo_root / source_rel
    checkpoint_path = checkpoint_path or default_checkpoint_path(repo_root)
    ckpt = load_checkpoint(checkpoint_path)
    completed_runs = list(ckpt.get("completed_runs") or [])
    completed_keys = ckpt.get("completed_keys") or {}

    original_bytes = 0
    original_files = 0
    for run_id in completed_runs:
        run_dir = source_root / run_id
        for body in iter_body_files(run_dir):
            original_files += 1
            try:
                original_bytes += body.stat().st_size
            except OSError:
                pass

    archive_blob_bytes = 0
    archive_blob_files = 0
    if archive_dir is not None:
        blobs = archive_blobs_dir(archive_dir)
        if blobs.is_dir():
            for dirpath, _dn, filenames in os.walk(blobs):
                for name in filenames:
                    if name.endswith(".tmp"):
                        continue
                    p = Path(dirpath) / name
                    try:
                        archive_blob_bytes += p.stat().st_size
                        archive_blob_files += 1
                    except OSError:
                        continue

    keys_done = sum(len(v) for v in completed_keys.values())
    return {
        "schema": "kel_evidence_retirement_report_v1",
        "research_only": True,
        "no_bet": True,
        "assessed_at": _iso_now(),
        "deletion_status": "NOT_EXECUTED — awaiting separate approval",
        "source_root": str(source_root),
        "archive_dir": None if archive_dir is None else str(archive_dir),
        "archive_blobs": None if archive_dir is None else str(archive_blobs_dir(archive_dir)),
        "completed_runs": len(completed_runs),
        "completed_run_ids_sample": completed_runs[:20],
        "completed_keys": keys_done,
        "original_body_files": original_files,
        "original_bytes": original_bytes,
        "original_gb": round(original_bytes / 1024**3, 4),
        "archive_blob_files": archive_blob_files,
        "archive_blob_bytes": archive_blob_bytes,
        "archive_blob_gb": round(archive_blob_bytes / 1024**3, 4),
        "estimated_dedup_and_compression_savings_bytes": max(0, original_bytes - archive_blob_bytes),
        "estimated_c_reclaim_if_bodies_retired_gb": round(original_bytes / 1024**3, 4),
        "note": "Local .body files are still present. Retiring them requires a separate explicit approval.",
        "checkpoint_path": str(checkpoint_path),
    }

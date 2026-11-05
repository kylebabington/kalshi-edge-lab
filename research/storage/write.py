"""Atomic cache/evidence body writes that fail closed on disk errors.

A failed write must never leave a partial body that could be mistaken for
valid evidence, and must never overwrite an existing immutable body with
different bytes. Used by the sports Fetcher for reconstructable caches and
for per-run evidence roots (write-once semantics when ``write_once`` is set).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


class DiskFullError(OSError):
    """Raised when a write cannot complete because the volume is full (or equivalent)."""


class EvidenceWriteError(RuntimeError):
    """Raised when a write would corrupt or replace pinned evidence bytes."""


def _is_enospc(exc: BaseException) -> bool:
    if isinstance(exc, DiskFullError):
        return True
    if isinstance(exc, OSError):
        # Windows: ERROR_DISK_FULL=112, ERROR_HANDLE_DISK_FULL=39; POSIX: ENOSPC=28
        if getattr(exc, "errno", None) in (28, 39, 112):
            return True
        if getattr(exc, "winerror", None) in (39, 112):
            return True
        msg = str(exc).lower()
        if "no space" in msg or "disk full" in msg or "not enough space" in msg:
            return True
    return False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes, *, fsync: bool = True) -> None:
    """Write ``data`` via a sibling temp file and ``os.replace``. Cleans up on failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        last_error: Exception | None = None
        for attempt in range(8):
            try:
                os.replace(tmp_name, path)
                last_error = None
                break
            except PermissionError as error:
                last_error = error
                time.sleep(0.05 * (2 ** attempt))
        if last_error is not None:
            raise last_error
    except Exception as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        if _is_enospc(exc):
            raise DiskFullError(f"disk full writing {path}") from exc
        raise


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def write_cache_pair(
    body_path: Path,
    meta_path: Path,
    body: bytes,
    meta: dict,
    *,
    write_once: bool = False,
) -> None:
    """Atomically persist a body + metadata sidecar.

    Order: write body to a temp name, write meta to a temp name, then replace both.
    If either step fails, temps are removed and any pre-existing pair is left intact.
    When ``write_once`` is True, an existing body with a different sha256 is refused;
    identical bytes are a no-op.
    """
    body_path = Path(body_path)
    meta_path = Path(meta_path)
    digest = sha256_bytes(body)
    meta = {**meta, "sha256": meta.get("sha256") or digest, "bytes": len(body)}
    if meta["sha256"] != digest:
        raise EvidenceWriteError(f"meta sha256 {meta['sha256']} != body sha256 {digest}")

    if body_path.exists() or meta_path.exists():
        if write_once:
            if body_path.exists():
                existing = sha256_bytes(body_path.read_bytes())
                if existing != digest:
                    raise EvidenceWriteError(
                        f"refusing to overwrite evidence {body_path}: "
                        f"existing {existing[:12]} != new {digest[:12]}"
                    )
                # Identical body: ensure meta matches, then done.
                if meta_path.exists():
                    try:
                        old = json.loads(meta_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as exc:
                        raise EvidenceWriteError(f"unreadable evidence meta {meta_path}: {exc}") from exc
                    if old.get("sha256") == digest:
                        return
                atomic_write_text(meta_path, json.dumps(meta, sort_keys=True, indent=1) + "\n")
                return
            raise EvidenceWriteError(f"meta exists without body for evidence pair {meta_path}")
        # Reconstructable cache: replace atomically below.

    body_path.parent.mkdir(parents=True, exist_ok=True)
    fd_b, tmp_body = tempfile.mkstemp(dir=str(body_path.parent), prefix=f".{body_path.name}.", suffix=".tmp")
    fd_m, tmp_meta = tempfile.mkstemp(dir=str(meta_path.parent), prefix=f".{meta_path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd_b, "wb") as hb:
            hb.write(body)
            hb.flush()
            os.fsync(hb.fileno())
        meta_bytes = (json.dumps(meta, sort_keys=True, indent=1) + "\n").encode("utf-8")
        with os.fdopen(fd_m, "wb") as hm:
            hm.write(meta_bytes)
            hm.flush()
            os.fsync(hm.fileno())
        os.replace(tmp_body, body_path)
        try:
            os.replace(tmp_meta, meta_path)
        except Exception:
            # Body is already visible; remove it so cached() cannot serve a body without meta.
            try:
                body_path.unlink()
            except OSError:
                pass
            raise
    except Exception as exc:
        for tmp in (tmp_body, tmp_meta):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if _is_enospc(exc):
            raise DiskFullError(f"disk full writing cache pair {body_path}") from exc
        raise

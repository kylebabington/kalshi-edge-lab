"""Content-addressed evidence payloads (sports capture / settlement).

Deduplicates identical HTTP response **bodies** across runs while preserving
per-capture receipts (meta sidecars with timestamps, url, params, run layout).

Rules (research integrity):
- Dedup happens only **after** bytes are received from the network (or an
  explicit local write of those bytes). Callers must never skip a live fetch
  merely because a blob with some other request's hash already exists.
- Logical payload sha256 is unchanged (hash over uncompressed bytes).
- Legacy ``{key}.body`` files remain readable forever.
- Blobs are write-once; different bytes under the same digest are refused.

RESEARCH_ONLY / NO_BET. Does not change Phase 7 weather capture behavior.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from research.sports.core import REPO_ROOT

from .write import DiskFullError, EvidenceWriteError, _is_enospc, atomic_write_bytes, sha256_bytes

CAS_SCHEMA = "kel_evidence_cas_v1"
BLOBS_REL = "data/cache/sports/evidence_blobs_v1"
ARCHIVE_BLOBS_REL = "sports/evidence_blobs_v1"
DEFAULT_BLOB_ROOT = REPO_ROOT / BLOBS_REL
LOCATION_LOCAL = "local"
LOCATION_ARCHIVE = "archive"


class EvidenceMissing(FileNotFoundError):
    """Raised when neither a legacy body nor a CAS blob can be resolved."""


def archive_blobs_dir(archive_dir: Path) -> Path:
    """Absolute CAS blob root under an archive volume (not under repo data/)."""
    return Path(archive_dir) / ARCHIVE_BLOBS_REL


def blob_root(
    root: Path | None = None,
    *,
    absolute_blob_root: Path | None = None,
) -> Path:
    if absolute_blob_root is not None:
        return Path(absolute_blob_root)
    if root is None:
        return DEFAULT_BLOB_ROOT
    return Path(root) / BLOBS_REL


def blob_paths(
    digest: str,
    *,
    root: Path | None = None,
    absolute_blob_root: Path | None = None,
) -> dict[str, Path]:
    """Candidate on-disk paths for a logical sha256 (gzip preferred, then identity)."""
    base = blob_root(root, absolute_blob_root=absolute_blob_root) / "sha256" / digest[:2] / digest
    return {"gzip": Path(str(base) + ".gz"), "identity": base}


def _write_blob_file(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        if _is_enospc(exc):
            raise DiskFullError(f"disk full writing evidence blob {path}") from exc
        raise


def store_blob(
    body: bytes,
    *,
    root: Path | None = None,
    absolute_blob_root: Path | None = None,
    prefer_gzip: bool = True,
    location: str = LOCATION_LOCAL,
) -> dict[str, Any]:
    """Persist ``body`` once under its sha256. Returns storage descriptor."""
    digest = sha256_bytes(body)
    paths = blob_paths(digest, root=root, absolute_blob_root=absolute_blob_root)
    # Already present?
    for encoding, path in (("gzip", paths["gzip"]), ("identity", paths["identity"])):
        if path.is_file():
            existing = read_blob(
                digest, encoding=encoding, root=root, absolute_blob_root=absolute_blob_root
            )
            if existing != body:
                raise EvidenceWriteError(
                    f"CAS collision at {path}: existing logical bytes differ for {digest[:16]}"
                )
            return {
                "schema": CAS_SCHEMA,
                "sha256": digest,
                "encoding": encoding,
                "location": location,
                "blob_relpath": _rel_under_blobs(
                    path, root=root, absolute_blob_root=absolute_blob_root
                ),
                "logical_bytes": len(body),
                "stored_bytes": path.stat().st_size,
                "deduped": True,
            }

    encoding = "identity"
    stored = body
    dest = paths["identity"]
    if prefer_gzip:
        compressed = gzip.compress(body, compresslevel=6, mtime=0)
        if len(compressed) < len(body):
            encoding = "gzip"
            stored = compressed
            dest = paths["gzip"]

    _write_blob_file(dest, stored)
    return {
        "schema": CAS_SCHEMA,
        "sha256": digest,
        "encoding": encoding,
        "location": location,
        "blob_relpath": _rel_under_blobs(dest, root=root, absolute_blob_root=absolute_blob_root),
        "logical_bytes": len(body),
        "stored_bytes": len(stored),
        "deduped": False,
    }


def _rel_under_blobs(
    path: Path,
    *,
    root: Path | None,
    absolute_blob_root: Path | None = None,
) -> str:
    base = blob_root(root, absolute_blob_root=absolute_blob_root).resolve()
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def read_blob(
    digest: str,
    *,
    encoding: str | None = None,
    root: Path | None = None,
    absolute_blob_root: Path | None = None,
) -> bytes:
    paths = blob_paths(digest, root=root, absolute_blob_root=absolute_blob_root)
    order = [encoding] if encoding in ("gzip", "identity") else ["gzip", "identity"]
    last_err: Exception | None = None
    for enc in order:
        if enc is None:
            continue
        path = paths[enc]
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
            return gzip.decompress(raw) if enc == "gzip" else raw
        except OSError as exc:
            last_err = exc
            continue
    if last_err:
        raise EvidenceMissing(f"evidence blob unreadable for {digest[:16]}: {last_err}") from last_err
    raise EvidenceMissing(f"evidence blob missing for {digest[:16]}")


def _try_read_blob_locations(
    digest: str,
    *,
    encoding: str | None,
    prefer_archive: bool,
) -> bytes:
    """Resolve blob from local and/or archive roots. Clear error if archive-only and missing."""
    from .roots import archive_status, load_roots

    roots = load_roots()
    arch = archive_status(roots)
    attempts: list[tuple[str, Path | None]] = []
    if prefer_archive and arch.available and roots.archive_dir is not None:
        attempts.append((LOCATION_ARCHIVE, archive_blobs_dir(roots.archive_dir)))
    attempts.append((LOCATION_LOCAL, DEFAULT_BLOB_ROOT))
    if not prefer_archive and arch.available and roots.archive_dir is not None:
        attempts.append((LOCATION_ARCHIVE, archive_blobs_dir(roots.archive_dir)))

    last_missing: Exception | None = None
    for loc, abs_root in attempts:
        if abs_root is None:
            continue
        try:
            return read_blob(digest, encoding=encoding, absolute_blob_root=abs_root)
        except EvidenceMissing as exc:
            last_missing = exc
            continue
    if prefer_archive and not arch.available:
        raise EvidenceMissing(
            f"evidence blob {digest[:16]} is archived but archive unavailable "
            f"({arch.reason}); set/connect KALSHI_ARCHIVE_DIR"
        )
    if last_missing:
        raise last_missing
    raise EvidenceMissing(f"evidence blob missing for {digest[:16]}")


def write_evidence_payload(
    body_path: Path,
    meta_path: Path,
    body: bytes,
    meta: dict,
    *,
    write_once: bool = True,
    blob_repo_root: Path | None = None,
) -> dict[str, Any]:
    """Write CAS blob + meta receipt (no per-run full body copy).

    ``body_path`` is the legacy ``{key}.body`` location (used as the receipt
    directory anchor). A full ``.body`` is **not** written for new CAS entries.
    """
    body_path = Path(body_path)
    meta_path = Path(meta_path)
    digest = sha256_bytes(body)
    meta = {**meta, "sha256": meta.get("sha256") or digest, "bytes": len(body)}
    if meta["sha256"] != digest:
        raise EvidenceWriteError(f"meta sha256 {meta['sha256']} != body sha256 {digest}")

    # Legacy body already present: preserve write-once semantics, do not CAS-convert here.
    if body_path.exists():
        existing = sha256_bytes(body_path.read_bytes())
        if write_once and existing != digest:
            raise EvidenceWriteError(
                f"refusing to overwrite evidence {body_path}: "
                f"existing {existing[:12]} != new {digest[:12]}"
            )
        if existing == digest:
            if meta_path.exists():
                try:
                    old = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    old = {}
                if old.get("sha256") == digest:
                    return {"storage": old.get("storage") or {"schema": "legacy_body"}, "deduped": True}
            from .write import atomic_write_text
            atomic_write_text(meta_path, json.dumps(meta, sort_keys=True, indent=1) + "\n")
            return {"storage": {"schema": "legacy_body"}, "deduped": True}

    storage = store_blob(body, root=blob_repo_root, location=LOCATION_LOCAL)
    out_meta = {**meta, "storage": storage}
    # If meta already exists with same sha and CAS pointer, no-op.
    if meta_path.exists() and write_once:
        try:
            old = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EvidenceWriteError(f"unreadable evidence meta {meta_path}: {exc}") from exc
        if old.get("sha256") == digest and (old.get("storage") or {}).get("sha256") == digest:
            return storage
        if old.get("sha256") and old.get("sha256") != digest:
            raise EvidenceWriteError(
                f"refusing to overwrite evidence meta {meta_path}: "
                f"existing {old.get('sha256', '')[:12]} != new {digest[:12]}"
            )

    meta_path.parent.mkdir(parents=True, exist_ok=True)
    from .write import atomic_write_text
    try:
        atomic_write_text(meta_path, json.dumps(out_meta, sort_keys=True, indent=1) + "\n")
    except Exception as exc:
        if _is_enospc(exc):
            raise DiskFullError(f"disk full writing evidence meta {meta_path}") from exc
        raise
    return storage


def read_evidence_payload(
    body_path: Path,
    meta_path: Path | None = None,
    *,
    meta: dict | None = None,
    expected_sha256: str | None = None,
    blob_repo_root: Path | None = None,
) -> bytes:
    """Load evidence bytes from legacy ``.body`` or CAS blob referenced by meta."""
    body_path = Path(body_path)
    if meta_path is None and body_path.name.endswith(".body"):
        meta_path = body_path.parent / (body_path.name[:-5] + ".meta.json")

    if body_path.is_file():
        data = body_path.read_bytes()
        if expected_sha256 and sha256_bytes(data) != expected_sha256:
            raise EvidenceWriteError(f"legacy body hash mismatch: {body_path}")
        return data

    info = meta
    if info is None and meta_path is not None and Path(meta_path).is_file():
        info = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    if not info:
        raise EvidenceMissing(f"evidence bytes missing: {body_path}")

    storage = info.get("storage") or {}
    digest = storage.get("sha256") or info.get("sha256") or expected_sha256
    if not digest:
        raise EvidenceMissing(f"evidence meta lacks sha256: {meta_path}")
    if blob_repo_root is not None:
        data = read_blob(digest, encoding=storage.get("encoding"), root=blob_repo_root)
    else:
        prefer_archive = storage.get("location") == LOCATION_ARCHIVE
        data = _try_read_blob_locations(
            digest, encoding=storage.get("encoding"), prefer_archive=prefer_archive
        )
    if expected_sha256 and sha256_bytes(data) != expected_sha256:
        raise EvidenceWriteError(f"CAS blob hash mismatch for {digest[:16]}")
    if info.get("sha256") and sha256_bytes(data) != info["sha256"]:
        raise EvidenceWriteError(f"CAS blob does not match meta sha256 for {meta_path}")
    return data


def evidence_payload_exists(body_path: Path, meta_path: Path | None = None) -> bool:
    body_path = Path(body_path)
    if body_path.is_file():
        return True
    if meta_path is None and body_path.name.endswith(".body"):
        meta_path = body_path.parent / (body_path.name[:-5] + ".meta.json")
    if meta_path is None or not Path(meta_path).is_file():
        return False
    try:
        info = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    storage = info.get("storage") or {}
    digest = storage.get("sha256") or info.get("sha256")
    if not digest:
        return False
    try:
        prefer_archive = storage.get("location") == LOCATION_ARCHIVE
        _try_read_blob_locations(
            digest, encoding=storage.get("encoding"), prefer_archive=prefer_archive
        )
        return True
    except EvidenceMissing:
        return False

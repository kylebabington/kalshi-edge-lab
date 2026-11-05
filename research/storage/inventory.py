"""Permanent historical evidence inventory (pre-retirement).

Builds a verifiable catalog of every capture run / payload under
``raw_capture_v2`` with logical sha256, sizes, timestamps, and expected
archive blob locations. Checksummed copies live on the archive volume and
locally under ``data/logs/storage/``.

RESEARCH_ONLY / NO_BET. Never deletes evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from research.sports.core import REPO_ROOT

from .cas import ARCHIVE_BLOBS_REL, LOCATION_ARCHIVE, archive_blobs_dir, sha256_bytes
from .migrate_evidence import CAPTURE_REL, iter_body_files, list_run_ids
from .roots import load_roots
from .write import atomic_write_text

INVENTORY_SCHEMA = "kel_historical_evidence_inventory_v1"
LOCAL_INV_REL = "data/logs/storage/historical_evidence_inventory_v1.jsonl"
LOCAL_META_REL = "data/logs/storage/historical_evidence_inventory_v1.meta.json"
ARCHIVE_INV_NAME = "manifests/historical_evidence_inventory_v1.jsonl"
ARCHIVE_META_NAME = "manifests/historical_evidence_inventory_v1.meta.json"
BASELINE_REL = "data/logs/storage/migrate_baseline_runs.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso_from_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def expected_blob_relpath(digest: str, encoding: str = "gzip") -> str:
    base = f"sha256/{digest[:2]}/{digest}"
    return base + (".gz" if encoding == "gzip" else "")


def iter_inventory_rows(
    *,
    repo_root: Path = REPO_ROOT,
    source_rel: str = CAPTURE_REL,
    run_ids: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    source_root = repo_root / source_rel
    runs = run_ids if run_ids is not None else list_run_ids(source_root)
    for run_id in runs:
        run_dir = source_root / run_id
        if not run_dir.is_dir():
            continue
        for body_path in iter_body_files(run_dir):
            token = body_path.relative_to(run_dir).as_posix()
            if token.endswith(".body"):
                token = token[: -len(".body")]
            meta_path = body_path.parent / (body_path.name[: -len(".body")] + ".meta.json")
            meta: dict[str, Any] = {}
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
            try:
                st = body_path.stat()
                size = st.st_size
                mtime = _iso_from_mtime(st.st_mtime)
            except OSError:
                size = None
                mtime = None
            digest = meta.get("sha256")
            if not digest and body_path.is_file():
                try:
                    digest = sha256_bytes(body_path.read_bytes())
                except OSError:
                    digest = None
            storage = meta.get("storage") or {}
            encoding = storage.get("encoding") or "gzip"
            yield {
                "run_id": run_id,
                "evidence_key_path": token,
                "source": token.split("/", 1)[0] if "/" in token else token,
                "source_body_rel": body_path.relative_to(repo_root).as_posix()
                if _under(body_path, repo_root)
                else str(body_path),
                "meta_rel": meta_path.relative_to(repo_root).as_posix()
                if meta_path.is_file() and _under(meta_path, repo_root)
                else (str(meta_path) if meta_path.is_file() else None),
                "logical_sha256": digest,
                "logical_bytes": meta.get("bytes") or size,
                "body_mtime_utc": mtime,
                "retrieved_at": meta.get("retrieved_at") or meta.get("requested_at"),
                "url": meta.get("url"),
                "status": meta.get("status"),
                "expected_archive_blob_rel": (
                    f"{ARCHIVE_BLOBS_REL}/{expected_blob_relpath(digest, encoding)}"
                    if digest
                    else None
                ),
                "storage_location": storage.get("location") or LOCATION_ARCHIVE,
            }


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def build_inventory(
    *,
    repo_root: Path = REPO_ROOT,
    archive_dir: Path | None = None,
    source_rel: str = CAPTURE_REL,
    run_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Write inventory JSONL locally + on archive; return meta with checksum."""
    roots = load_roots(repo_root, archive_dir_override=archive_dir)
    archive_dir = roots.archive_dir
    local_jsonl = repo_root / LOCAL_INV_REL
    local_meta = repo_root / LOCAL_META_REL
    local_jsonl.parent.mkdir(parents=True, exist_ok=True)

    h = hashlib.sha256()
    n = 0
    runs_seen: set[str] = set()
    bytes_total = 0
    missing_hash = 0
    with open(local_jsonl, "w", encoding="utf-8", newline="\n") as out:
        for row in iter_inventory_rows(repo_root=repo_root, source_rel=source_rel, run_ids=run_ids):
            line = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            out.write(line)
            h.update(line.encode("utf-8"))
            n += 1
            runs_seen.add(row["run_id"])
            if row.get("logical_bytes"):
                bytes_total += int(row["logical_bytes"])
            if not row.get("logical_sha256"):
                missing_hash += 1

    digest = h.hexdigest()
    meta = {
        "schema": INVENTORY_SCHEMA,
        "research_only": True,
        "no_bet": True,
        "created_at": _iso_now(),
        "source_rel": source_rel,
        "rows": n,
        "runs": len(runs_seen),
        "logical_bytes_sum": bytes_total,
        "missing_sha256_rows": missing_hash,
        "inventory_sha256": digest,
        "local_inventory": LOCAL_INV_REL,
        "baseline_frozen": run_ids is not None,
    }
    atomic_write_text(local_meta, json.dumps(meta, indent=2, sort_keys=True) + "\n")

    if archive_dir is not None:
        arch_jsonl = Path(archive_dir) / ARCHIVE_INV_NAME
        arch_meta = Path(archive_dir) / ARCHIVE_META_NAME
        arch_jsonl.parent.mkdir(parents=True, exist_ok=True)
        # Copy bytes (streaming) to archive
        with open(local_jsonl, "rb") as src, open(arch_jsonl, "wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        meta_arch = {**meta, "archive_inventory": ARCHIVE_INV_NAME}
        atomic_write_text(arch_meta, json.dumps(meta_arch, indent=2, sort_keys=True) + "\n")
        meta["archive_inventory"] = str(arch_jsonl)
        meta["archive_meta"] = str(arch_meta)

    return meta


def freeze_baseline_runs(*, repo_root: Path = REPO_ROOT, source_rel: str = CAPTURE_REL) -> dict:
    """Snapshot run_ids at migration start so new captures do not shift the target."""
    path = repo_root / BASELINE_REL
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    runs = list_run_ids(repo_root / source_rel)
    data = {
        "schema": "kel_migrate_baseline_runs_v1",
        "created_at": _iso_now(),
        "source_rel": source_rel,
        "run_ids": runs,
        "count": len(runs),
        "note": "Fixed historical migration target; new prospective runs are excluded.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


def load_baseline_runs(repo_root: Path = REPO_ROOT) -> list[str] | None:
    path = repo_root / BASELINE_REL
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("run_ids") or [])


def load_inventory_rows(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)

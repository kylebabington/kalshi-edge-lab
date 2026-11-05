"""Measure CAS effectiveness vs legacy per-run evidence storage.

RESEARCH_ONLY / NO_BET. Actual measurements only; projections labeled estimated.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.sports.core import REPO_ROOT

from .cas import BLOBS_REL, DEFAULT_BLOB_ROOT, sha256_bytes
from .migrate_evidence import CAPTURE_REL, iter_body_files, list_run_ids


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _dir_size(path: Path) -> tuple[int, int]:
    n = b = 0
    if not path.exists():
        return 0, 0
    for dirpath, _dn, filenames in os.walk(path):
        for name in filenames:
            if name.endswith(".tmp"):
                continue
            try:
                b += (Path(dirpath) / name).stat().st_size
                n += 1
            except OSError:
                continue
    return b, n


def measure_cas_effectiveness(
    *,
    repo_root: Path = REPO_ROOT,
    sample_runs: int = 30,
    source_rel: str = CAPTURE_REL,
) -> dict[str, Any]:
    source_root = repo_root / source_rel
    run_ids = list_run_ids(source_root)
    # Prefer newest runs for "recent" sample
    recent = sorted(run_ids, reverse=True)[:sample_runs]

    sha_counts: Counter[str] = Counter()
    sha_bytes: dict[str, int] = {}
    bodies = 0
    body_bytes = 0
    cas_meta_runs = 0
    legacy_only_runs = 0
    cas_logical = 0
    cas_stored_reported = 0
    cas_deduped_metas = 0

    per_run: list[dict] = []
    for run_id in recent:
        run_dir = source_root / run_id
        run_bodies = 0
        run_bytes = 0
        run_shas: set[str] = set()
        has_cas_meta = False
        for body_path in iter_body_files(run_dir):
            try:
                st = body_path.stat()
            except OSError:
                continue
            bodies += 1
            run_bodies += 1
            body_bytes += st.st_size
            run_bytes += st.st_size
            meta_path = body_path.parent / (body_path.name[: -len(".body")] + ".meta.json")
            digest = None
            stored_rep = None
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
                digest = meta.get("sha256")
                storage = meta.get("storage") or {}
                if storage.get("schema"):
                    has_cas_meta = True
                    cas_logical += int(storage.get("logical_bytes") or meta.get("bytes") or st.st_size)
                    if storage.get("stored_bytes") is not None:
                        stored_rep = int(storage["stored_bytes"])
                        cas_stored_reported += stored_rep
                    if storage.get("deduped"):
                        cas_deduped_metas += 1
            if not digest:
                # Hash first 0 bytes path — for large files avoid re-hashing whole tree when meta has sha
                try:
                    digest = sha256_bytes(body_path.read_bytes())
                except OSError:
                    continue
            sha_counts[digest] += 1
            sha_bytes[digest] = st.st_size
            run_shas.add(digest)
        if has_cas_meta:
            cas_meta_runs += 1
        else:
            legacy_only_runs += 1
        per_run.append({
            "run_id": run_id,
            "bodies": run_bodies,
            "bytes": run_bytes,
            "unique_sha": len(run_shas),
        })

    unique = len(sha_counts)
    unique_bytes = sum(sha_bytes.values())
    dup_extra = sum(c - 1 for c in sha_counts.values())
    local_blob_bytes, local_blob_files = _dir_size(repo_root / BLOBS_REL)

    avg_run_bytes = (body_bytes / len(recent)) if recent else 0.0
    # Collector cadence historically ~5 min → 288 runs/day if continuous.
    estimated_daily_legacy_gb = (avg_run_bytes * 288) / 1024**3 if recent else None
    # CAS projection: unique growth is hard; use unique/total ratio on sample.
    ratio = (unique_bytes / body_bytes) if body_bytes else 1.0
    estimated_daily_cas_gb = (
        None if estimated_daily_legacy_gb is None else estimated_daily_legacy_gb * ratio
    )

    return {
        "schema": "kel_cas_effectiveness_v1",
        "research_only": True,
        "no_bet": True,
        "assessed_at": _iso_now(),
        "source_root": str(source_root),
        "measurement": "actual",
        "sample_runs": len(recent),
        "total_run_dirs": len(run_ids),
        "legacy_sample": {
            "body_files": bodies,
            "body_bytes": body_bytes,
            "body_gb": round(body_bytes / 1024**3, 4),
            "unique_sha256": unique,
            "unique_payload_bytes": unique_bytes,
            "unique_payload_gb": round(unique_bytes / 1024**3, 4),
            "duplicate_extra_copies": dup_extra,
            "dedup_ratio_actual": round(body_bytes / unique_bytes, 3) if unique_bytes else None,
            "avg_bytes_per_run_actual": round(avg_run_bytes, 1),
            "legacy_only_runs_in_sample": legacy_only_runs,
            "runs_with_cas_meta_in_sample": cas_meta_runs,
        },
        "local_cas_blob_store": {
            "path": str(DEFAULT_BLOB_ROOT),
            "files_actual": local_blob_files,
            "bytes_actual": local_blob_bytes,
            "gb_actual": round(local_blob_bytes / 1024**3, 4),
        },
        "cas_meta_stats_in_sample": {
            "logical_bytes_sum": cas_logical,
            "stored_bytes_sum_reported": cas_stored_reported,
            "deduped_meta_count": cas_deduped_metas,
        },
        "projections_estimated": {
            "label": "estimated",
            "assumed_runs_per_day": 288,
            "daily_growth_legacy_gb": None
            if estimated_daily_legacy_gb is None
            else round(estimated_daily_legacy_gb, 3),
            "daily_growth_if_unique_ratio_gb": None
            if estimated_daily_cas_gb is None
            else round(estimated_daily_cas_gb, 3),
            "unique_to_stored_ratio_from_sample": round(ratio, 3),
            "note": "Daily projection assumes continuous ~5-minute capture cadence; actual cadence varies.",
        },
        "per_run_sample": per_run[:15],
    }


def write_cas_stats(report: dict, *, repo_root: Path = REPO_ROOT) -> Path:
    out = repo_root / "data" / "logs" / "storage" / "cas_effectiveness.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out

"""Independent archive integrity verification (Mode A source+archive, Mode B archive-only).

Never treats an empty source directory as success. Mode B uses the permanent
evidence inventory and does not require original ``.body`` files.

RESEARCH_ONLY / NO_BET.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.sports.core import REPO_ROOT

from .cas import archive_blobs_dir, read_blob, sha256_bytes
from .inventory import (
    ARCHIVE_INV_NAME,
    LOCAL_INV_REL,
    LOCAL_META_REL,
    load_baseline_runs,
    load_inventory_rows,
)
from .migrate_evidence import CAPTURE_REL, iter_body_files, list_run_ids
from .roots import load_roots
from .write import atomic_write_text

REPORT_REL = "data/logs/storage/archive_integrity_report.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _blob_exists(abs_blobs: Path, digest: str, encoding: str | None) -> bool:
    base = abs_blobs / "sha256" / digest[:2] / digest
    if encoding == "identity":
        return base.is_file()
    if encoding == "gzip":
        return Path(str(base) + ".gz").is_file()
    return Path(str(base) + ".gz").is_file() or base.is_file()


def verify_archive(
    *,
    repo_root: Path = REPO_ROOT,
    archive_dir: Path | None = None,
    mode: str = "auto",
    source_rel: str = CAPTURE_REL,
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Run Mode A (bodies present) and/or Mode B (inventory-driven archive-only)."""
    roots = load_roots(repo_root, archive_dir_override=archive_dir)
    archive_dir = archive_dir or roots.archive_dir
    source_root = repo_root / source_rel
    local_inv = repo_root / LOCAL_INV_REL
    local_meta = repo_root / LOCAL_META_REL

    report: dict[str, Any] = {
        "schema": "kel_archive_integrity_report_v1",
        "research_only": True,
        "no_bet": True,
        "assessed_at": _iso_now(),
        "archive_dir": None if archive_dir is None else str(archive_dir),
        "mode_requested": mode,
        "mode_a": None,
        "mode_b": None,
        "retirement_eligible": False,
        "blocking_issues": [],
    }

    if archive_dir is None:
        report["blocking_issues"].append("archive not configured")
        return report

    abs_blobs = archive_blobs_dir(archive_dir)
    bodies_present = source_root.is_dir() and any(source_root.iterdir())
    # Never interpret empty source as success for Mode A.
    run_mode_a = mode in ("auto", "a", "source", "source-to-archive") and bodies_present
    run_mode_b = mode in ("auto", "b", "archive-only", "archive")

    if mode in ("a", "source", "source-to-archive") and not bodies_present:
        report["blocking_issues"].append(
            "Mode A requested but source evidence directory is empty/missing — "
            "refusing to treat as success; use Mode B with inventory"
        )

    if run_mode_a:
        report["mode_a"] = _mode_a(
            repo_root=repo_root,
            source_root=source_root,
            abs_blobs=abs_blobs,
            sample_limit=sample_limit,
        )
        if report["mode_a"]["original_payload_files"] == 0:
            report["blocking_issues"].append(
                "Mode A found zero source .body payloads — refusing empty-source success; use Mode B"
            )
        if report["mode_a"]["missing_refs"] or report["mode_a"]["corrupt_payloads"]:
            report["blocking_issues"].append("Mode A found missing or corrupt evidence")

    if run_mode_b:
        inv_path = local_inv
        # Prefer archive inventory when local file is missing or is a compact pointer.
        if archive_dir is not None:
            cand = Path(archive_dir) / ARCHIVE_INV_NAME
            use_archive = False
            if not inv_path.is_file():
                use_archive = cand.is_file()
            elif inv_path.is_file():
                try:
                    head = inv_path.read_text(encoding="utf-8")[:200].lstrip()
                    if head.startswith("{") and "inventory_pointer" in head:
                        use_archive = cand.is_file()
                except OSError:
                    pass
            if use_archive:
                inv_path = cand
        report["mode_b"] = _mode_b(
            inventory_path=inv_path,
            inventory_meta_path=local_meta,
            abs_blobs=abs_blobs,
            sample_limit=sample_limit,
        )
        if report["mode_b"].get("error"):
            report["blocking_issues"].append(report["mode_b"]["error"])
        elif report["mode_b"]["missing_refs"] or report["mode_b"]["corrupt_payloads"]:
            report["blocking_issues"].append("Mode B found missing or corrupt evidence")

    a_ok = report["mode_a"] is None or (
        report["mode_a"]["missing_refs"] == 0 and report["mode_a"]["corrupt_payloads"] == 0
        and report["mode_a"]["failed_runs"] == 0
        and report["mode_a"].get("original_payload_files", 0) > 0
    )
    b_ok = report["mode_b"] is None or (
        not report["mode_b"].get("error")
        and report["mode_b"]["missing_refs"] == 0
        and report["mode_b"]["corrupt_payloads"] == 0
        and report["mode_b"].get("original_payload_files", 0) > 0
    )
    # Single-mode runs are eligible for that mode; auto requires both.
    if mode in ("auto",):
        report["retirement_eligible"] = bool(
            report["mode_a"] is not None and report["mode_b"] is not None
            and a_ok and b_ok and not report["blocking_issues"]
        )
    else:
        report["retirement_eligible"] = bool(a_ok and b_ok and not report["blocking_issues"])
    # Still require independent backup flag elsewhere before actual retirement.
    report["note"] = (
        "retirement_eligible refers to integrity only; independent backup status is separate"
    )
    return report


def _mode_a(
    *,
    repo_root: Path,
    source_root: Path,
    abs_blobs: Path,
    sample_limit: int | None,
) -> dict[str, Any]:
    baseline = load_baseline_runs(repo_root)
    runs = baseline if baseline is not None else list_run_ids(source_root)
    out = {
        "mode": "source-to-archive",
        "original_runs_expected": len(runs),
        "original_payload_files": 0,
        "unique_payload_hashes": 0,
        "verified_refs": 0,
        "missing_refs": 0,
        "corrupt_payloads": 0,
        "failed_runs": 0,
        "missing_samples": [],
        "corrupt_samples": [],
    }
    unique: set[str] = set()
    failed_runs: set[str] = set()
    checked = 0
    for run_id in runs:
        run_dir = source_root / run_id
        if not run_dir.is_dir():
            out["failed_runs"] += 1
            failed_runs.add(run_id)
            out["missing_samples"].append({"run_id": run_id, "error": "run_dir_missing"})
            continue
        for body_path in iter_body_files(run_dir):
            if sample_limit is not None and checked >= sample_limit:
                out["sampled"] = True
                out["unique_payload_hashes"] = len(unique)
                return out
            checked += 1
            out["original_payload_files"] += 1
            meta_path = body_path.parent / (body_path.name[: -len(".body")] + ".meta.json")
            try:
                body = body_path.read_bytes()
            except OSError as exc:
                out["corrupt_payloads"] += 1
                out["corrupt_samples"].append({"path": str(body_path), "error": str(exc)})
                failed_runs.add(run_id)
                continue
            digest = sha256_bytes(body)
            unique.add(digest)
            encoding = None
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    encoding = (meta.get("storage") or {}).get("encoding")
                    if meta.get("sha256") and meta["sha256"] != digest:
                        out["corrupt_payloads"] += 1
                        out["corrupt_samples"].append({
                            "path": str(body_path),
                            "error": "meta_sha_mismatch",
                        })
                        failed_runs.add(run_id)
                        continue
                except (OSError, ValueError):
                    pass
            try:
                got = read_blob(digest, encoding=encoding, absolute_blob_root=abs_blobs)
                if got != body:
                    out["corrupt_payloads"] += 1
                    out["corrupt_samples"].append({
                        "path": str(body_path),
                        "error": "archive_bytes_differ",
                    })
                    failed_runs.add(run_id)
                else:
                    out["verified_refs"] += 1
            except Exception as exc:
                out["missing_refs"] += 1
                out["missing_samples"].append({
                    "path": str(body_path),
                    "sha256": digest,
                    "error": str(exc),
                })
                failed_runs.add(run_id)
    out["unique_payload_hashes"] = len(unique)
    out["failed_runs"] = len(failed_runs)
    out["missing_samples"] = out["missing_samples"][:25]
    out["corrupt_samples"] = out["corrupt_samples"][:25]
    return out


def _mode_b(
    *,
    inventory_path: Path,
    inventory_meta_path: Path,
    abs_blobs: Path,
    sample_limit: int | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "mode": "archive-only",
        "inventory_path": str(inventory_path),
        "original_runs_expected": 0,
        "original_payload_files": 0,
        "unique_payload_hashes": 0,
        "archived_payloads_checked": 0,
        "verified_refs": 0,
        "missing_refs": 0,
        "corrupt_payloads": 0,
        "failed_runs": 0,
        "missing_samples": [],
        "corrupt_samples": [],
    }
    if not inventory_path.is_file():
        out["error"] = (
            "permanent evidence inventory missing — cannot run Mode B; "
            "refusing empty-source success"
        )
        return out
    meta = {}
    if inventory_meta_path.is_file():
        try:
            meta = json.loads(inventory_meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    # Re-hash inventory to detect tampering
    import hashlib
    h = hashlib.sha256()
    with open(inventory_path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    digest = h.hexdigest()
    if meta.get("inventory_sha256") and meta["inventory_sha256"] != digest:
        out["error"] = "inventory checksum mismatch"
        return out
    out["inventory_sha256_ok"] = True
    out["inventory_sha256"] = digest

    runs: set[str] = set()
    unique: set[str] = set()
    failed_runs: set[str] = set()
    checked = 0
    for row in load_inventory_rows(inventory_path):
        if sample_limit is not None and checked >= sample_limit:
            out["sampled"] = True
            break
        checked += 1
        out["original_payload_files"] += 1
        run_id = row.get("run_id") or ""
        runs.add(run_id)
        sha = row.get("logical_sha256")
        if not sha:
            out["missing_refs"] += 1
            out["missing_samples"].append({"row": checked, "error": "inventory_missing_sha"})
            failed_runs.add(run_id)
            continue
        unique.add(sha)
        out["archived_payloads_checked"] += 1
        encoding = None
        # Prefer gzip then identity via read_blob
        try:
            got = read_blob(sha, encoding=encoding, absolute_blob_root=abs_blobs)
            if sha256_bytes(got) != sha:
                out["corrupt_payloads"] += 1
                out["corrupt_samples"].append({"sha256": sha, "error": "hash_mismatch"})
                failed_runs.add(run_id)
            elif row.get("logical_bytes") is not None and len(got) != int(row["logical_bytes"]):
                out["corrupt_payloads"] += 1
                out["corrupt_samples"].append({"sha256": sha, "error": "size_mismatch"})
                failed_runs.add(run_id)
            else:
                out["verified_refs"] += 1
        except Exception as exc:
            out["missing_refs"] += 1
            out["missing_samples"].append({"sha256": sha, "error": str(exc)})
            failed_runs.add(run_id)

    out["original_runs_expected"] = len(runs)
    out["unique_payload_hashes"] = len(unique)
    out["failed_runs"] = len(failed_runs)
    out["missing_samples"] = out["missing_samples"][:25]
    out["corrupt_samples"] = out["corrupt_samples"][:25]
    if out["original_payload_files"] == 0:
        out["error"] = "inventory has zero rows — refusing success"
    return out


def write_integrity_report(report: dict, *, repo_root: Path = REPO_ROOT) -> Path:
    path = repo_root / REPORT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return path

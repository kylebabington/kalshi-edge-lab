"""Source-retirement **proposal** only — never deletes ``.body`` files.

RESEARCH_ONLY / NO_BET.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.sports.core import REPO_ROOT

from .migrate_evidence import retirement_report
from .roots import load_roots
from .verify_archive import verify_archive
from .write import atomic_write_text

PROPOSAL_REL = "data/logs/storage/source_retirement_proposal.json"
RELIABILITY_REL = "data/logs/storage/archive_reliability_assessment.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def assess_independent_backup(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Check whether sports-state backups cover archive CAS on D:."""
    home = Path.home() / ".kel"
    v2 = home / "sports-state-backup-v2"
    legacy = home / "sports-state-backup"
    has_v2 = v2.exists()
    # Archive CAS lives under D:/kalshi-archive/sports/evidence_blobs_v1 — not in ~/.kel by design.
    covers_archive_cas = False
    if has_v2:
        # Look for evidence_blobs_v1 under backup state tree (unlikely).
        for p in v2.rglob("evidence_blobs_v1"):
            covers_archive_cas = True
            break
    return {
        "schema": "kel_archive_backup_assessment_v1",
        "assessed_at": _iso_now(),
        "sports_state_backup_v2_present": has_v2,
        "sports_state_backup_legacy_present": legacy.exists(),
        "independent_backup_of_archive_cas": covers_archive_cas,
        "exfat_archive_volume": True,
        "single_point_of_failure": not covers_archive_cas,
        "recommendation": (
            "Do not retire local .body originals until an independent backup of "
            "D:\\kalshi-archive\\sports\\evidence_blobs_v1 (+ manifests/inventory) exists "
            "(e.g. second external drive, cloud archive, or verified offsite copy). "
            "Do not auto-create another enormous C: mirror."
        ),
        "exfat_notes": [
            "exFAT lacks journaling; use verify-after-write and Mode A/B audits.",
            "Atomic replace within the volume mitigates torn finals; sudden disconnect can still lose in-flight temps.",
            "Interrupted migrate resumes from checkpoint; completed blobs are content-addressed.",
        ],
    }


def write_reliability_assessment(*, repo_root: Path = REPO_ROOT) -> Path:
    data = assess_independent_backup(repo_root=repo_root)
    path = repo_root / RELIABILITY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


def build_retirement_proposal(
    *,
    repo_root: Path = REPO_ROOT,
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    integrity = verify_archive(repo_root=repo_root, archive_dir=archive_dir, mode="auto")
    retire = retirement_report(repo_root=repo_root, archive_dir=archive_dir)
    backup = assess_independent_backup(repo_root=repo_root)
    roots = load_roots(repo_root, archive_dir_override=archive_dir)

    proposal = {
        "schema": "kel_source_retirement_proposal_v1",
        "research_only": True,
        "no_bet": True,
        "created_at": _iso_now(),
        "status": "NOT_EXECUTED — awaiting separate approval",
        "archive_dir": None if roots.archive_dir is None else str(roots.archive_dir),
        "integrity": {
            "retirement_eligible_integrity": integrity.get("retirement_eligible"),
            "blocking_issues": integrity.get("blocking_issues"),
            "mode_a_missing": (integrity.get("mode_a") or {}).get("missing_refs"),
            "mode_a_corrupt": (integrity.get("mode_a") or {}).get("corrupt_payloads"),
            "mode_b_missing": (integrity.get("mode_b") or {}).get("missing_refs"),
            "mode_b_corrupt": (integrity.get("mode_b") or {}).get("corrupt_payloads"),
        },
        "backup": backup,
        "estimated_reclaim_gb": retire.get("estimated_c_reclaim_if_bodies_retired_gb"),
        "completed_runs": retire.get("completed_runs"),
        "original_bytes": retire.get("original_bytes"),
        "archive_blob_bytes": retire.get("archive_blob_bytes"),
        "procedure": [
            "1. Confirm Mode A and Mode B integrity reports show zero missing/corrupt.",
            "2. Confirm independent_backup_of_archive_cas is true (or explicit risk acceptance).",
            "3. Confirm KALSHI_ARCHIVE_DIR / persistent archive_dir available.",
            "4. Process completed_runs in small batches (e.g. 5–10 runs).",
            "5. Per body: verify archive blob hash matches source; only then unlink .body.",
            "6. Retain all .meta.json, capture records, scores, predictions, manifests.",
            "7. Re-run Mode B + a sample SavedEvidence/replay check after each batch.",
            "8. Stop immediately on any hash mismatch; never continue.",
            "9. Record every retired path in a retirement manifest on C: and D:.",
        ],
        "authorization_gate": (
            "Backup unresolved OR integrity incomplete → DO NOT AUTHORIZE retirement. "
            "User must approve a separate DELETE command."
        ),
        "authorized_to_delete_bodies_now": False,
    }
    if not backup.get("independent_backup_of_archive_cas"):
        proposal["authorization_gate"] = (
            "UNRESOLVED: archive CAS on D: has no independent backup. "
            "Complete migration/verification, but do not retire the only remaining originals."
        )
    path = repo_root / PROPOSAL_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(proposal, indent=2, sort_keys=True) + "\n")
    proposal["wrote"] = str(path)
    return proposal

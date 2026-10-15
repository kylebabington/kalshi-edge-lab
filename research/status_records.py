"""Status records for research presentation.

A status record says what kind of claim is being made and what supports it:

- ``implementation``: ``implemented`` only when a tracked module or completion
  record is declared; otherwise ``unknown``.
- ``evidence``: ``present`` / ``missing`` / ``invalid`` / ``not_checked``.
  File presence proves presence only, never validity or completed research.
- ``operations``: ``recent_success`` / ``stale`` / ``failed`` / ``unknown``.
- ``integrity``: ``verified`` / ``failed`` only when a verification actually ran
  in the current request; otherwise ``not_checked``.
- ``policy``: intentional states such as RESEARCH_ONLY, NO_BET, SHADOW_ONLY.
- ``configuration``: e.g. a source being configured. Never evidence of a fetch.

Helpers here only stat files or inspect already-parsed data. No hashing, no
directory scans, no writes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

NEEDS_REASON = {"unknown", "not_checked", "missing", "invalid", "failed", "stale", "not_applicable"}


def record(
    kind: str,
    state: str,
    label: str,
    basis: str,
    *,
    source: str | None = None,
    as_of: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if state in NEEDS_REASON and not reason:
        reason = basis
    return {
        "kind": kind,
        "state": state,
        "label": label,
        "basis": basis,
        "source": source,
        "as_of": as_of,
        "reason": reason,
    }


def rel(path: Path | str) -> str:
    """Repo-relative path. Paths outside the repo are reduced to their name (no machine paths)."""
    p = Path(path)
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return f"(outside repository)/{p.name}"


def mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def policy(label: str, value: str, basis: str, *, source: str | None = None) -> dict[str, Any]:
    return record("policy", value, label, basis, source=source)


def configuration(label: str, value: str, basis: str, *, source: str | None = None) -> dict[str, Any]:
    return record("configuration", value, label, basis, source=source)


def implementation(label: str, tracked_paths: Iterable[str]) -> dict[str, Any]:
    """``tracked_paths`` are repo-relative files declared as tracked (verified by tests)."""
    paths = [p for p in tracked_paths if (REPO_ROOT / p).is_file()]
    if paths:
        return record(
            "implementation",
            "implemented",
            label,
            "Tracked implementation: " + ", ".join(paths),
            source=paths[0],
        )
    return record(
        "implementation",
        "unknown",
        label,
        "No tracked implementation record found",
        reason="No tracked implementation or completion record is declared for this item",
    )


def file_evidence(label: str, path: Path, *, what: str = "file") -> dict[str, Any]:
    """Presence only. Validity is not checked."""
    if path.is_file():
        return record(
            "evidence",
            "present",
            label,
            f"{what} present; validity not checked",
            source=rel(path),
            as_of=mtime_iso(path),
        )
    return record("evidence", "missing", label, f"{what} not found on this machine", source=rel(path))


def json_evidence(
    label: str,
    path: Path,
    *,
    required_keys: Iterable[str] = (),
    parsed: Any = None,
) -> dict[str, Any]:
    """Present / invalid / missing for a small JSON artifact.

    Pass ``parsed`` when the caller has already read the file, to avoid a second read.
    """
    if not path.is_file():
        return record("evidence", "missing", label, "artifact not found on this machine", source=rel(path))
    data = parsed
    if data is None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
    if not isinstance(data, dict):
        return record(
            "evidence",
            "invalid",
            label,
            "artifact present but could not be parsed as a JSON object",
            source=rel(path),
            as_of=mtime_iso(path),
        )
    missing = [k for k in required_keys if k not in data]
    if missing:
        return record(
            "evidence",
            "invalid",
            label,
            "artifact present but missing required fields: " + ", ".join(missing),
            source=rel(path),
            as_of=mtime_iso(path),
        )
    as_of = data.get("generated_at") if isinstance(data.get("generated_at"), str) else mtime_iso(path)
    return record(
        "evidence",
        "present",
        label,
        "artifact present and parseable; research conclusions not re-verified",
        source=rel(path),
        as_of=as_of,
    )


def not_checked(kind: str, label: str, reason: str) -> dict[str, Any]:
    return record(kind, "not_checked", label, reason, reason=reason)


def unknown(kind: str, label: str, reason: str, *, source: str | None = None) -> dict[str, Any]:
    return record(kind, "unknown", label, reason, source=source, reason=reason)


def age_hours(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 3600.0

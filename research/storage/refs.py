"""Collect absolute paths of bytes that records pin as required evidence.

Cleanup refuses to delete any of these paths even if they appear under a
reconstructable cache prefix (defense in depth — live evidence roots are
already classified immutable).
"""

from __future__ import annotations

import json
from pathlib import Path

from research.sports.core import REPO_ROOT


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def evidence_body_path(root: Path, source: str, key: str) -> Path:
    return root / source / key[:2] / f"{key}.body"


def evidence_meta_path(root: Path, source: str, key: str) -> Path:
    return root / source / key[:2] / f"{key}.meta.json"


def collect_sports_capture_refs(
    *,
    capture_dir: Path | None = None,
    evidence_root: Path | None = None,
    root: Path = REPO_ROOT,
) -> set[Path]:
    """Paths of capture evidence bodies/metas referenced by accepted + attempt records."""
    # Prefer paths under ``root`` so tests and alternate checkouts never walk the live tree.
    capture_dir = capture_dir or (root / "data" / "sports" / "phase2" / "capture_v2")
    evidence_root = evidence_root or (root / "data" / "cache" / "sports" / "raw_capture_v2")
    out: set[Path] = set()
    for folder in (capture_dir / "accepted", capture_dir / "attempts"):
        if not folder.exists():
            continue
        for path in folder.rglob("*.json"):
            if path.stat().st_size == 0:
                continue
            rec = _load_json(path)
            if not rec:
                continue
            run_id = rec.get("run_id")
            if not run_id:
                continue
            run_root = evidence_root / run_id
            for e in rec.get("evidence") or []:
                key = e.get("key") or ""
                source = e.get("source") or "kalshi"
                if not key:
                    continue
                out.add(evidence_body_path(run_root, source, key).resolve())
                out.add(evidence_meta_path(run_root, source, key).resolve())
    return out


def collect_sports_settlement_refs(
    *,
    score_dir: Path | None = None,
    evidence_root: Path | None = None,
    root: Path = REPO_ROOT,
) -> set[Path]:
    score_dir = score_dir or (root / "data" / "sports" / "phase2" / "capture_v2_scores")
    evidence_root = evidence_root or (root / "data" / "cache" / "sports" / "raw_settlement_v1")
    out: set[Path] = set()
    finalized = score_dir / "finalized"
    if not finalized.exists():
        return out
    for path in finalized.rglob("*.json"):
        if path.stat().st_size == 0:
            continue
        sc = _load_json(path)
        if not sc:
            continue
        run_id = sc.get("settlement_run_id")
        if not run_id:
            continue
        run_root = evidence_root / run_id
        for e in sc.get("settlement_evidence") or []:
            key = e.get("key") or ""
            source = e.get("source") or "kalshi"
            if not key:
                continue
            out.add(evidence_body_path(run_root, source, key).resolve())
            out.add(evidence_meta_path(run_root, source, key).resolve())
    return out


def collect_weather_phase7_refs(*, root: Path = REPO_ROOT) -> set[Path]:
    """Evidence files under phase7/evidence referenced by records or receipt sidecars."""
    out: set[Path] = set()
    evidence = root / "data" / "weather" / "phase7" / "evidence"
    if evidence.exists():
        for p in evidence.rglob("*"):
            if p.is_file():
                out.add(p.resolve())
    replay_ev = root / "data" / "weather" / "phase7_historical_replay" / "evidence"
    if replay_ev.exists():
        for p in replay_ev.rglob("*"):
            if p.is_file():
                out.add(p.resolve())
    return out


def collect_required_evidence_paths(
    *,
    root: Path = REPO_ROOT,
    include_weather: bool = True,
    capture_dir: Path | None = None,
    score_dir: Path | None = None,
) -> set[Path]:
    refs = set()
    refs |= collect_sports_capture_refs(capture_dir=capture_dir, root=root)
    refs |= collect_sports_settlement_refs(score_dir=score_dir, root=root)
    if include_weather:
        refs |= collect_weather_phase7_refs(root=root)
    return refs

"""Filesystem layout for Explorer activity deltas (refresh-only writes)."""

from __future__ import annotations

from pathlib import Path

from kalshi.cache import CACHE_ROOT

ACTIVITY_ROOT = CACHE_ROOT / "activity"
EXPLORE_DIR = ACTIVITY_ROOT / "explore"
PENDING_DIR = ACTIVITY_ROOT / "explore_pending"


def explore_run_path(run_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in run_id)
    return EXPLORE_DIR / f"{safe}.jsonl"


def pending_item_path(item_id: str) -> Path:
    """One atomic file per activity id so retries cannot silently lose or double-apply."""
    digest = "".join(c if c.isalnum() or c in "-_." else "_" for c in item_id)
    if len(digest) > 180:
        digest = digest[:160] + "_" + str(abs(hash(item_id)) % 10_000_000)
    prefix = (digest[:2] or "zz").lower()
    return PENDING_DIR / prefix / f"{digest}.json"


def ensure_activity_dirs() -> None:
    for path in (ACTIVITY_ROOT, EXPLORE_DIR, PENDING_DIR):
        path.mkdir(parents=True, exist_ok=True)

"""Filesystem layout for the Market Explorer cache."""

from __future__ import annotations

from pathlib import Path

from kalshi.cache import CACHE_ROOT, REPO_ROOT

EXPLORE_ROOT = CACHE_ROOT / "explore"
RUNS_DIR = EXPLORE_ROOT / "runs"
EVENTS_DIR = EXPLORE_ROOT / "events"
SERIES_PATH = EXPLORE_ROOT / "series.json"
TAGS_PATH = EXPLORE_ROOT / "tags_by_categories.json"
EVENTS_INDEX_PATH = EXPLORE_ROOT / "events_index.json"
REFRESH_STATUS_PATH = EXPLORE_ROOT / "refresh_status.json"
LOOKUP_ROTATION_PATH = EXPLORE_ROOT / "lookup_rotation.json"
LATEST_MANIFEST_PATH = EXPLORE_ROOT / "latest_manifest.json"
LOG_DIR = REPO_ROOT / "data" / "logs" / "explore"
LOCK_PATH = LOG_DIR / "refresh.lock"

# Retrieval freshness thresholds (seconds since newest successful retrieval).
FRESH_S = 2 * 3600
AGING_S = 12 * 3600

LOOKUP_CAP = 1500
LOOKUP_BATCH = 50
OPEN_PAGE_LIMIT = 200
OPEN_PAGE_CAP = 2000
MARKETS_PAGE_LIMIT = 1000
MAX_WATCHED = 500
INDEX_REBUILD_EVERY_PAGES = 25

# Truncate error strings in API responses.
ERROR_MAX_CHARS = 500


def event_shard_path(event_ticker: str) -> Path:
    """Stable shard path; prefix keeps directories from getting too large."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in event_ticker)
    prefix = (safe[:2] or "zz").lower()
    return EVENTS_DIR / prefix / f"{safe}.json"


def ensure_explore_dirs() -> None:
    for path in (EXPLORE_ROOT, RUNS_DIR, EVENTS_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)

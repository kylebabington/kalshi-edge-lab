"""In-memory Activity Center index.

GET paths call ``ensure_index()`` which may rebuild from saved artifacts into
process memory only. It never writes derived.jsonl, index.json, or sources_state.json.
Explorer deltas persist only during explicit refresh (see explore_emit).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from research.activity import derive
from research.activity import explore_emit as AE
from research.activity.types import ACTIVITY_TYPES, ActivityItem

_lock = threading.RLock()
_items_by_id: dict[str, ActivityItem] = {}
_ordered: list[ActivityItem] = []
_source_sigs: list[tuple[str, int | None, int | None]] | None = None
_built = False
_built_at: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sort_key(it: ActivityItem) -> tuple:
    # Missing observed_at sorts last (unknown), never substituted with index time.
    obs = it.get("observed_at") or ""
    return (1 if obs else 0, obs, it.get("id") or "")


def _rebuild(*, path_overrides: dict[str, Any] | None = None) -> None:
    global _items_by_id, _ordered, _source_sigs, _built, _built_at
    path_overrides = path_overrides or {}
    by_id: dict[str, ActivityItem] = {}

    for it in AE.load_explore_activity_files():
        iid = it.get("id")
        if isinstance(iid, str) and iid:
            by_id[iid] = it

    for it in derive.derive_all(**path_overrides):
        iid = it.get("id")
        if isinstance(iid, str) and iid:
            by_id[iid] = it

    ordered = sorted(by_id.values(), key=_sort_key, reverse=True)
    _items_by_id = by_id
    _ordered = ordered
    _source_sigs = AE.source_signatures() + derive.source_signatures(
        snapshot_root=path_overrides.get("snapshot_root"),
        score_root=path_overrides.get("score_root"),
        receipt_root=path_overrides.get("receipt_root"),
        weather_log_dir=path_overrides.get("weather_log_dir"),
        sports_capture_dir=path_overrides.get("sports_capture_dir"),
        sports_score_dir=path_overrides.get("sports_score_dir"),
        sports_cycles=path_overrides.get("sports_cycles"),
        sports_backup=path_overrides.get("sports_backup"),
    )
    _built = True
    _built_at = _now_iso()


def reset_memory() -> None:
    """Test helper: clear the in-memory index."""
    global _items_by_id, _ordered, _source_sigs, _built, _built_at
    with _lock:
        _items_by_id = {}
        _ordered = []
        _source_sigs = None
        _built = False
        _built_at = None


def ensure_index(*, force: bool = False, path_overrides: dict[str, Any] | None = None) -> None:
    """Incrementally refresh the shared in-memory index. Never writes activity cache files."""
    path_overrides = path_overrides or {}
    with _lock:
        if not force and _built and _source_sigs is not None:
            current = AE.source_signatures() + derive.source_signatures(
                snapshot_root=path_overrides.get("snapshot_root"),
                score_root=path_overrides.get("score_root"),
                receipt_root=path_overrides.get("receipt_root"),
                weather_log_dir=path_overrides.get("weather_log_dir"),
                sports_capture_dir=path_overrides.get("sports_capture_dir"),
                sports_score_dir=path_overrides.get("sports_score_dir"),
                sports_cycles=path_overrides.get("sports_cycles"),
                sports_backup=path_overrides.get("sports_backup"),
            )
            if current == _source_sigs:
                return
        _rebuild(path_overrides=path_overrides)


def _subject_matches(it: ActivityItem, watched: set[str]) -> bool:
    if not watched:
        return True
    sub = it.get("subject") or {}
    keys: list[str] = []
    kind = sub.get("kind")
    key = sub.get("key")
    if isinstance(kind, str) and isinstance(key, str):
        if kind == "event_ticker":
            keys.extend([f"kalshi:{key}", f"weather:{key}"])
        elif kind == "sports_event_id":
            keys.append(f"sports:{key}")
        else:
            keys.append(f"{kind}:{key}")
    for alias in sub.get("aliases") or []:
        if isinstance(alias, str):
            keys.append(alias)
    return any(k in watched for k in keys)


def list_activity(
    *,
    category: str | None = None,
    type: str | None = None,
    q: str | None = None,
    watched: list[str] | None = None,
    page: int = 1,
    page_size: int = 40,
    path_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_index(path_overrides=path_overrides)
    watched_set = {w.strip() for w in (watched or []) if w and w.strip()}
    qn = (q or "").strip().lower()
    with _lock:
        items = list(_ordered)
        built_at = _built_at

    filtered: list[ActivityItem] = []
    for it in items:
        if category and it.get("category") != category:
            continue
        if type and it.get("type") != type:
            continue
        if watched_set and not _subject_matches(it, watched_set):
            continue
        if qn:
            blob = " ".join(
                str(x)
                for x in (
                    it.get("title"),
                    it.get("summary"),
                    it.get("id"),
                    (it.get("subject") or {}).get("key"),
                )
                if x
            ).lower()
            if qn not in blob:
                continue
        filtered.append(it)

    total = len(filtered)
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    if page > pages:
        page = pages
    start = (page - 1) * page_size
    slice_ = filtered[start : start + page_size]
    return {
        "generated_at": _now_iso(),
        "index_built_at": built_at,
        "mode": "saved",
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "items": slice_,
        "notes": [
            "observed_at is when this app recorded the observation; source_at is the event/source time.",
            "Missing timestamps stay unknown — indexing time is never substituted.",
            "GET is network-free and write-free; Explorer deltas persist only during explicit refresh.",
        ],
    }


def summary(*, path_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_index(path_overrides=path_overrides)
    with _lock:
        items = list(_ordered)
        built_at = _built_at
    by_type: dict[str, int] = {t: 0 for t in ACTIVITY_TYPES}
    by_category: dict[str, int] = {}
    newest: str | None = None
    for it in items:
        t = it.get("type")
        if isinstance(t, str) and t in by_type:
            by_type[t] += 1
        elif isinstance(t, str):
            by_type[t] = by_type.get(t, 0) + 1
        cat = it.get("category") or "unknown"
        by_category[cat] = by_category.get(cat, 0) + 1
        obs = it.get("observed_at")
        if isinstance(obs, str) and obs and (newest is None or obs > newest):
            newest = obs
    return {
        "generated_at": _now_iso(),
        "index_built_at": built_at,
        "mode": "saved",
        "total": len(items),
        "by_type": by_type,
        "by_category": by_category,
        "newest_observed_at": newest,
        "notes": [
            "Unread state is browser-local; this summary does not track read/unread.",
        ],
    }


def all_ids(
    *,
    category: str | None = None,
    type: str | None = None,
    q: str | None = None,
    watched: list[str] | None = None,
    path_overrides: dict[str, Any] | None = None,
) -> list[str]:
    """Stable ids for the current filtered feed snapshot (mark-all-read)."""
    result = list_activity(
        category=category,
        type=type,
        q=q,
        watched=watched,
        page=1,
        page_size=200,
        path_overrides=path_overrides,
    )
    ids = [it["id"] for it in result["items"] if it.get("id")]
    # Paginate remaining pages.
    for page in range(2, (result["pages"] or 1) + 1):
        more = list_activity(
            category=category,
            type=type,
            q=q,
            watched=watched,
            page=page,
            page_size=200,
            path_overrides=path_overrides,
        )
        ids.extend(it["id"] for it in more["items"] if it.get("id"))
    return ids

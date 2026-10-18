"""Explorer activity deltas written only during explicit refresh upserts.

Crash-safe sequence per upsert:
1. Diff prior shard vs incoming (no discovery-as-quote/status).
2. Persist each item as an atomic pending file keyed by deterministic id (create-exclusive).
3. Caller writes the event shard.
4. Fold pending files for this run into the append-only run jsonl.

Retrying the same observation reuses the same ids, so pending create-exclusive and
index-side dedupe prevent duplicates. Writing pending before the shard prevents
silent loss if the process dies between shard update and activity append.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from research.activity import paths as AP
from research.activity.types import (
    CATEGORY_KALSHI,
    QUOTE_FIELDS,
    TYPE_EXPLORE_DISCOVERED,
    TYPE_EXPLORE_QUOTE_CHANGE,
    TYPE_EXPLORE_STATUS_CHANGE,
    ActivityItem,
    item,
)

_lock = threading.RLock()
_TITLE_MAX = 120


def _bound(text: str | None, n: int = _TITLE_MAX) -> str:
    if not text:
        return ""
    text = str(text).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _market_map(markets: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for m in markets or []:
        if not isinstance(m, dict):
            continue
        t = m.get("ticker")
        if isinstance(t, str) and t:
            out[t] = m
    return out


def _quote_changes(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field in QUOTE_FIELDS:
        ov, nv = old.get(field), new.get(field)
        if ov != nv:
            changes.append({"field": field, "from": ov, "to": nv})
    return changes


def compute_explore_deltas(
    *,
    event_ticker: str,
    prev: dict[str, Any] | None,
    entry: dict[str, Any],
    retrieved_at: str,
    run_id: str,
    is_new: bool,
) -> list[ActivityItem]:
    """Build activity items for one upsert. Never infers closure from crawl absence."""
    title = _bound(entry.get("title") or event_ticker) or event_ticker
    subject = {
        "kind": "event_ticker",
        "key": event_ticker,
        "aliases": [f"kalshi:{event_ticker}"],
    }
    href = f"/explore/events/{event_ticker}"
    out: list[ActivityItem] = []

    if is_new:
        first_seen = entry.get("first_seen_at") or retrieved_at
        out.append(
            item(
                id=f"explore.discovered:{event_ticker}:{first_seen}",
                type=TYPE_EXPLORE_DISCOVERED,
                category=CATEGORY_KALSHI,
                observed_at=first_seen,
                source_at=first_seen,
                title=f"Discovered {title}",
                summary=f"First seen by this app during explore refresh {run_id}.",
                href=href,
                subject=subject,
                payload={"run_id": run_id, "event_ticker": event_ticker},
            )
        )
        return out

    if prev is None:
        return out

    prev_markets = _market_map(prev.get("markets"))
    new_markets = _market_map(entry.get("markets"))

    # Event-level lifecycle only when markets_complete allows claiming finality.
    prev_life = (prev.get("lifecycle") or {}).get("state")
    new_life = (entry.get("lifecycle") or {}).get("state")
    if (
        entry.get("markets_complete")
        and prev_life
        and new_life
        and prev_life != new_life
    ):
        out.append(
            item(
                id=f"explore.status:{event_ticker}:event:{retrieved_at}:{prev_life}->{new_life}",
                type=TYPE_EXPLORE_STATUS_CHANGE,
                category=CATEGORY_KALSHI,
                observed_at=retrieved_at,
                source_at=retrieved_at,
                title=f"Status changed: {title}",
                summary=f"Event lifecycle {prev_life} → {new_life}.",
                href=href,
                subject=subject,
                payload={
                    "run_id": run_id,
                    "scope": "event",
                    "from": prev_life,
                    "to": new_life,
                    "event_ticker": event_ticker,
                },
            )
        )

    for ticker, new_m in new_markets.items():
        old_m = prev_markets.get(ticker)
        if old_m is None:
            # Newly listed market under a known event — not initial event discovery.
            # Status/quote comparisons need a prior observation; skip until next refresh.
            continue

        old_status = old_m.get("status")
        new_status = new_m.get("status")
        if old_status != new_status and new_status is not None:
            out.append(
                item(
                    id=(
                        f"explore.status:{event_ticker}:{ticker}:{retrieved_at}"
                        f":{old_status}->{new_status}"
                    ),
                    type=TYPE_EXPLORE_STATUS_CHANGE,
                    category=CATEGORY_KALSHI,
                    observed_at=retrieved_at,
                    source_at=retrieved_at,
                    title=f"Market status: {_bound(new_m.get('title') or ticker)}",
                    summary=f"{ticker}: {old_status} → {new_status}.",
                    href=href,
                    subject=subject,
                    payload={
                        "run_id": run_id,
                        "scope": "market",
                        "event_ticker": event_ticker,
                        "market_ticker": ticker,
                        "from": old_status,
                        "to": new_status,
                    },
                )
            )

        changes = _quote_changes(old_m, new_m)
        if not changes:
            continue
        old_retrieved = old_m.get("retrieved_at")
        new_retrieved = new_m.get("retrieved_at") or retrieved_at
        out.append(
            item(
                id=f"explore.quote:{event_ticker}:{ticker}:{new_retrieved}",
                type=TYPE_EXPLORE_QUOTE_CHANGE,
                category=CATEGORY_KALSHI,
                observed_at=new_retrieved,
                source_at=new_retrieved,
                title=f"Quote changed: {_bound(new_m.get('title') or ticker)}",
                summary=f"{ticker}: {len(changes)} field(s) changed.",
                href=href,
                subject=subject,
                payload={
                    "run_id": run_id,
                    "event_ticker": event_ticker,
                    "market_ticker": ticker,
                    "old_retrieved_at": old_retrieved,
                    "new_retrieved_at": new_retrieved,
                    "changes": changes,
                },
            )
        )

    return out


def _write_pending(it: ActivityItem) -> Path | None:
    """Create-exclusive pending file. Returns path if created, None if already present."""
    AP.ensure_activity_dirs()
    path = AP.pending_item_path(it["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(it, sort_keys=True, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(str(path), flags, 0o644)
    except FileExistsError:
        return None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def persist_pending(items: list[ActivityItem]) -> list[Path]:
    """Write deterministic pending files before the shard update. Idempotent on retry."""
    written: list[Path] = []
    with _lock:
        for it in items:
            path = _write_pending(it)
            if path is not None:
                written.append(path)
    return written


def fold_pending_into_run(run_id: str, item_ids: list[str] | None = None) -> int:
    """Append pending items into the run jsonl and remove pending files."""
    AP.ensure_activity_dirs()
    run_path = AP.explore_run_path(run_id)
    run_path.parent.mkdir(parents=True, exist_ok=True)
    folded = 0
    with _lock:
        if item_ids is not None:
            paths = [AP.pending_item_path(i) for i in item_ids]
        else:
            paths = sorted(AP.PENDING_DIR.glob("*/*.json")) if AP.PENDING_DIR.is_dir() else []
        lines: list[str] = []
        for path in paths:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                data = json.loads(text)
                if not isinstance(data, dict) or not data.get("id"):
                    continue
                lines.append(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")
            except (OSError, ValueError):
                continue
        if lines:
            with open(run_path, "a", encoding="utf-8") as f:
                f.writelines(lines)
                f.flush()
                os.fsync(f.fileno())
            folded = len(lines)
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return folded


def emit_and_stage(
    *,
    event_ticker: str,
    prev: dict[str, Any] | None,
    entry: dict[str, Any],
    retrieved_at: str,
    run_id: str,
    is_new: bool,
) -> list[ActivityItem]:
    """Compute deltas, stage pending files, return items (caller writes shard then folds)."""
    items = compute_explore_deltas(
        event_ticker=event_ticker,
        prev=prev,
        entry=entry,
        retrieved_at=retrieved_at,
        run_id=run_id,
        is_new=is_new,
    )
    if items:
        persist_pending(items)
    return items


def load_explore_activity_files() -> list[ActivityItem]:
    """Read persisted explore activity (run jsonl + any leftover pending). Read-only."""
    out: list[ActivityItem] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        iid = raw.get("id")
        if not isinstance(iid, str) or not iid or iid in seen:
            return
        seen.add(iid)
        out.append(raw)  # type: ignore[arg-type]

    if AP.EXPLORE_DIR.is_dir():
        for path in sorted(AP.EXPLORE_DIR.glob("*.jsonl")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    _add(json.loads(line))
                except ValueError:
                    continue

    if AP.PENDING_DIR.is_dir():
        for path in sorted(AP.PENDING_DIR.glob("*/*.json")):
            try:
                _add(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue

    return out


def source_signatures() -> list[tuple[str, int | None, int | None]]:
    """(path, mtime_ns, size) for explore activity files — used by in-memory index."""
    sigs: list[tuple[str, int | None, int | None]] = []
    for root in (AP.EXPLORE_DIR, AP.PENDING_DIR):
        if not root.is_dir():
            continue
        pattern = "*.jsonl" if root == AP.EXPLORE_DIR else "*/*.json"
        for path in sorted(root.glob(pattern)):
            try:
                st = path.stat()
                sigs.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                sigs.append((str(path), None, None))
    return sigs

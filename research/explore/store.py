"""Shard-backed event store. Never deletes entries; upserts only touched shards."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from typing import Any

from kalshi.cache import read_json, write_json
from research.explore import paths as P
from research.explore.freshness import (
    derive_lifecycle,
    parse_count,
    parse_price,
    quote_summary,
    contract_status_counts,
)

_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_market(raw: dict[str, Any], *, retrieved_at: str, source: str, run_id: str) -> dict[str, Any]:
    yes_bid_size = raw.get("yes_bid_size_fp")
    yes_ask_size = raw.get("yes_ask_size_fp")
    return {
        "ticker": raw.get("ticker"),
        "event_ticker": raw.get("event_ticker"),
        "title": raw.get("title") or raw.get("yes_sub_title"),
        "yes_sub_title": raw.get("yes_sub_title"),
        "no_sub_title": raw.get("no_sub_title"),
        "status": raw.get("status"),
        "yes_bid": parse_price(raw.get("yes_bid_dollars"), size=yes_bid_size),
        "yes_ask": parse_price(raw.get("yes_ask_dollars"), size=yes_ask_size),
        "no_bid": parse_price(raw.get("no_bid_dollars")),
        "no_ask": parse_price(raw.get("no_ask_dollars")),
        "yes_bid_size": parse_count(yes_bid_size),
        "yes_ask_size": parse_count(yes_ask_size),
        "last_price": parse_price(raw.get("last_price_dollars")),
        "volume": parse_count(raw.get("volume_fp")),
        "volume_24h": parse_count(raw.get("volume_24h_fp")),
        "open_interest": parse_count(raw.get("open_interest_fp")),
        "open_time": raw.get("open_time"),
        "close_time": raw.get("close_time"),
        "expiration_time": raw.get("expiration_time"),
        "updated_time": raw.get("updated_time"),
        "rules_primary": raw.get("rules_primary"),
        "rules_secondary": raw.get("rules_secondary"),
        "result": raw.get("result") or None,
        "retrieved_at": retrieved_at,
        "source": source,
        "run_id": run_id,
        "not_in_latest_response": False,
    }


def _merge_markets(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    retrieved_at: str,
    source: str,
    run_id: str,
    mark_missing: bool,
) -> list[dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    for m in existing:
        t = m.get("ticker")
        if isinstance(t, str) and t:
            by_ticker[t] = dict(m)
    seen: set[str] = set()
    for raw in incoming:
        if not isinstance(raw, dict):
            continue
        t = raw.get("ticker")
        if not isinstance(t, str) or not t:
            continue
        seen.add(t)
        by_ticker[t] = _normalize_market(raw, retrieved_at=retrieved_at, source=source, run_id=run_id)
    if mark_missing:
        for t, m in by_ticker.items():
            if t not in seen:
                m = dict(m)
                m["not_in_latest_response"] = True
                by_ticker[t] = m
    return sorted(by_ticker.values(), key=lambda m: m.get("ticker") or "")


def load_event(event_ticker: str) -> dict[str, Any] | None:
    path = P.event_shard_path(event_ticker)
    data = read_json(path, default=None)
    return data if isinstance(data, dict) else None


def list_event_tickers() -> list[str]:
    if not P.EVENTS_DIR.is_dir():
        return []
    out: list[str] = []
    for path in P.EVENTS_DIR.glob("*/*.json"):
        data = read_json(path, default=None)
        if isinstance(data, dict) and isinstance(data.get("event_ticker"), str):
            out.append(data["event_ticker"])
    return sorted(set(out))


def upsert_event(
    raw_event: dict[str, Any],
    *,
    retrieved_at: str,
    source: str,
    run_id: str,
    listed_open: bool = False,
    markets_complete: bool | None = None,
    markets_complete_basis: str | None = None,
    extra_markets: list[dict[str, Any]] | None = None,
    mark_missing_markets: bool = True,
    last_lookup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge one event into its shard. Never deletes prior markets or fields.

    Activity Center deltas are staged (pending files) before the shard write and
    folded into the run jsonl after, so a crash cannot silently drop changes.
    """
    event_ticker = raw_event.get("event_ticker") or raw_event.get("ticker")
    if not isinstance(event_ticker, str) or not event_ticker:
        raise ValueError("event missing event_ticker")

    with _lock:
        loaded = load_event(event_ticker)
        is_new = loaded is None
        prev = loaded or {
            "event_ticker": event_ticker,
            "markets": [],
            "first_seen_at": retrieved_at,
        }
        nested = raw_event.get("markets")
        if not isinstance(nested, list):
            nested = []
        if extra_markets:
            # Prefer later pages; merge by ticker after.
            nested = list(nested) + list(extra_markets)

        markets = _merge_markets(
            prev.get("markets") or [],
            nested,
            retrieved_at=retrieved_at,
            source=source,
            run_id=run_id,
            mark_missing=mark_missing_markets and bool(nested or extra_markets),
        )

        complete = markets_complete if markets_complete is not None else bool(prev.get("markets_complete"))
        complete_basis = markets_complete_basis or prev.get("markets_complete_basis")

        entry = {
            **prev,
            "event_ticker": event_ticker,
            "series_ticker": raw_event.get("series_ticker") or prev.get("series_ticker"),
            "title": raw_event.get("title") or prev.get("title"),
            "sub_title": raw_event.get("sub_title") or prev.get("sub_title"),
            "event_category_deprecated": raw_event.get("category"),
            "mutually_exclusive": raw_event.get("mutually_exclusive"),
            "settlement_sources": raw_event.get("settlement_sources") or prev.get("settlement_sources"),
            "last_updated_ts": raw_event.get("last_updated_ts"),
            "markets": markets,
            "markets_complete": complete,
            "markets_complete_basis": complete_basis,
            "retrieved_at": retrieved_at,
            "source": source,
            "run_id": run_id,
            "first_seen_at": prev.get("first_seen_at") or retrieved_at,
        }
        if listed_open:
            entry["listed_open_at"] = retrieved_at
            entry["listed_open_run_id"] = run_id
        if last_lookup is not None:
            entry["last_lookup"] = last_lookup

        lifecycle = derive_lifecycle(markets, markets_complete=bool(complete))
        entry["lifecycle"] = lifecycle

        # Stage activity before the shard write so a crash cannot lose the observation.
        from research.activity import explore_emit as AE

        staged = AE.emit_and_stage(
            event_ticker=event_ticker,
            prev=None if is_new else prev,
            entry=entry,
            retrieved_at=retrieved_at,
            run_id=run_id,
            is_new=is_new,
        )
        write_json(P.event_shard_path(event_ticker), entry, compact=False)
        if staged:
            AE.fold_pending_into_run(run_id, [it["id"] for it in staged])
        return entry


def record_lookup_failure(
    event_ticker: str,
    *,
    outcome: str,
    reason: str,
    at: str | None = None,
) -> dict[str, Any] | None:
    """Attach last_lookup without changing cached markets/status."""
    with _lock:
        prev = load_event(event_ticker)
        if prev is None:
            # Never-cached ticker: create a stub so watched events stay visible.
            prev = {
                "event_ticker": event_ticker,
                "markets": [],
                "markets_complete": False,
                "markets_complete_basis": "Never successfully retrieved",
                "first_seen_at": at or _now_iso(),
                "lifecycle": {
                    "state": "unknown",
                    "basis": "Never successfully retrieved",
                    "reason": reason,
                    "markets_complete": False,
                },
            }
        prev["last_lookup"] = {
            "at": at or _now_iso(),
            "outcome": outcome,
            "reason": reason[: P.ERROR_MAX_CHARS],
        }
        write_json(P.event_shard_path(event_ticker), prev, compact=False)
        return prev


def index_row(entry: dict[str, Any], series_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    markets = entry.get("markets") or []
    series = series_meta or {}
    category = series.get("category") or entry.get("event_category_deprecated")
    categories = list(series.get("categories") or [])
    if category and category not in categories:
        categories = [category, *categories]
    close_times = [m.get("close_time") for m in markets if m.get("close_time")]
    volumes = [m.get("volume_24h") for m in markets if m.get("volume_24h") is not None]
    return {
        "event_ticker": entry.get("event_ticker"),
        "series_ticker": entry.get("series_ticker"),
        "title": entry.get("title"),
        "sub_title": entry.get("sub_title"),
        "category": category,
        "categories": categories,
        "lifecycle": (entry.get("lifecycle") or {}).get("state"),
        "lifecycle_detail": entry.get("lifecycle"),
        "markets_complete": bool(entry.get("markets_complete")),
        "listed_open_at": entry.get("listed_open_at"),
        "retrieved_at": entry.get("retrieved_at"),
        "last_lookup": entry.get("last_lookup"),
        "contract_status_counts": contract_status_counts(markets),
        "open_contracts": sum(1 for m in markets if (m.get("status") or "").lower() == "active"),
        "market_count": len(markets),
        "close_time_min": min(close_times) if close_times else None,
        "close_time_max": max(close_times) if close_times else None,
        "volume_24h_total": sum(volumes) if volumes else None,
        "quote": quote_summary(markets),
    }


def rebuild_events_index(series_by_ticker: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Scan shards into a compact listing index. Call at step boundaries, not every page."""
    series_by_ticker = series_by_ticker or {}
    rows: list[dict[str, Any]] = []
    if P.EVENTS_DIR.is_dir():
        for path in sorted(P.EVENTS_DIR.glob("*/*.json")):
            entry = read_json(path, default=None)
            if not isinstance(entry, dict):
                continue
            st = entry.get("series_ticker")
            rows.append(index_row(entry, series_by_ticker.get(st) if isinstance(st, str) else None))
    payload = {
        "generated_at": _now_iso(),
        "count": len(rows),
        "events": rows,
    }
    write_json(P.EVENTS_INDEX_PATH, payload, compact=False)
    return payload


def load_events_index() -> dict[str, Any]:
    data = read_json(P.EVENTS_INDEX_PATH, default=None)
    if isinstance(data, dict) and isinstance(data.get("events"), list):
        return data
    return {"generated_at": None, "count": 0, "events": []}


def load_series_map() -> dict[str, dict[str, Any]]:
    data = read_json(P.SERIES_PATH, default=None)
    if not isinstance(data, dict):
        return {}
    series = data.get("series")
    if not isinstance(series, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for s in series:
        if isinstance(s, dict) and isinstance(s.get("ticker"), str):
            out[s["ticker"]] = s
    return out


def series_metadata_info() -> dict[str, Any]:
    data = read_json(P.SERIES_PATH, default=None)
    if not isinstance(data, dict):
        return {"available": False, "retrieved_at": None, "freshness": "unknown", "degraded": True}
    return {
        "available": True,
        "retrieved_at": data.get("retrieved_at"),
        "freshness": data.get("freshness") or "unknown",
        "degraded": bool(data.get("degraded")),
        "count": len(data.get("series") or []) if isinstance(data.get("series"), list) else None,
        "error": data.get("error"),
    }


def content_fingerprint(payload: Any) -> str:
    text = repr(payload).encode("utf-8", errors="replace")
    return hashlib.sha256(text).hexdigest()[:16]

"""Evidence capture and complete pagination over public Kalshi / ESPN endpoints.

Every request goes through :class:`Evidence`, which keeps the exact response bytes in a per-run raw
cache and logs (url, params, status, sha256, requested_at, received_at). Replay serves the same
requests from those bytes after re-hashing them (:class:`SavedEvidence`); any request that was not
recorded, or whose bytes changed, is refused.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import adapters as ad
from ..core import Fetcher, Raw, request_key, sha256_bytes
from . import config as C


class EvidenceError(RuntimeError):
    pass


def _iso_us(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Evidence:
    """Network fetcher that records every request it serves (single flight, rate limited)."""

    def __init__(self, root: Path, now_fn, session=None):
        self.root = root
        self.now_fn = now_fn
        self.fetcher = Fetcher(root=root, min_interval=C.NETWORK["min_interval_s"],
                               max_retries=C.NETWORK["max_retries"], timeout=C.NETWORK["timeout_s"],
                               session=session)
        self.entries: dict[str, dict] = {}

    def get(self, source: str, url: str, params: dict) -> Raw:
        key = request_key(url, params)
        if key in self.entries:
            raw = self.fetcher.cached(source, url, params)
            if raw is not None:
                return raw
        t0 = self.now_fn()
        raw = self.fetcher.get(source, url, params, cache_statuses=(200,))
        t1 = self.now_fn()
        self.entries[key] = {"key": key, "source": source, "url": url, "params": params, "status": raw.status,
                             "sha256": sha256_bytes(raw.body), "bytes": len(raw.body),
                             "requested_at": _iso_us(t0), "received_at": _iso_us(t1)}
        return raw

    def refs(self, keys) -> list[dict]:
        return [self.entries[k] for k in keys]


class SavedEvidence:
    """Replay-side getter: only recorded requests, served from saved bytes whose hash must match."""

    def __init__(self, root: Path, entries: list[dict]):
        self.root = root
        self.entries = {e["key"]: e for e in entries}

    def get(self, source: str, url: str, params: dict) -> Raw:
        key = request_key(url, params)
        e = self.entries.get(key)
        if e is None:
            raise EvidenceError(f"request not in the recorded evidence: {url} {params}")
        body_p = self.root / source / key[:2] / f"{key}.body"
        if not body_p.exists():
            raise EvidenceError(f"evidence bytes missing: {body_p}")
        body = body_p.read_bytes()
        if sha256_bytes(body) != e["sha256"]:
            raise EvidenceError(f"evidence bytes changed since capture: {body_p}")
        return Raw(url, params, e["status"], body, {"sha256": e["sha256"]})


class Tracker:
    """Wraps a getter callable and remembers which request keys a computation used, in order."""

    def __init__(self, getter):
        self.getter = getter
        self.keys: list[str] = []

    def get(self, source: str, url: str, params: dict) -> Raw:
        raw = self.getter(source, url, params)
        k = request_key(url, params)
        if k not in self.keys:
            self.keys.append(k)
        return raw


def paginate(get, source: str, url: str, params: dict, key: str) -> tuple[list[dict], dict]:
    """Follow ``cursor`` to the end. Returns (items, {complete, pages, error})."""
    items, cursor, pages = [], None, 0
    while True:
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        raw = get(source, url, p)
        pages += 1
        if raw.status != 200:
            return items, {"complete": False, "pages": pages, "error": f"HTTP {raw.status} on page {pages}"}
        try:
            body = raw.json()
        except (ValueError, UnicodeDecodeError):
            return items, {"complete": False, "pages": pages, "error": f"unparseable page {pages}"}
        batch = body.get(key) or []
        items += batch
        cursor = body.get("cursor")
        if not cursor or not batch:
            return items, {"complete": True, "pages": pages, "error": None}
        if pages >= C.MAX_PAGES:
            return items, {"complete": False, "pages": pages, "error": f"page limit {C.MAX_PAGES} reached"}


def milestone_row(m: dict) -> dict:
    """Same field extraction as adapters.load_milestones (Phase 1), applied to a live page item."""
    d = m.get("details") or {}
    home = d.get("home_team_id") or d.get("home_competitor_id") or d.get("first_competitor_id") \
        or d.get("first_fighter_id") or d.get("home_player_id")
    away = d.get("away_team_id") or d.get("away_competitor_id") or d.get("second_competitor_id") \
        or d.get("second_fighter_id") or d.get("away_player_id")
    return {"milestone_id": m["id"], "type": m.get("type"), "league": d.get("league"), "title": m.get("title"),
            "start": m.get("start_date"), "end": m.get("end_date"), "home_id": home, "away_id": away,
            "event_tickers": sorted(set((m.get("primary_event_tickers") or []) + (m.get("related_event_tickers") or [])))}


def milestones(get) -> tuple[list[dict], dict]:
    items, st = paginate(get, "kalshi", f"{ad.KALSHI_BASE}/milestones",
                         {"limit": C.PAGE_LIMITS["milestones"], "category": "Sports",
                          "minimum_start_date": C.MILESTONE_MIN_START}, "milestones")
    return [milestone_row(m) for m in items], st


def event_markets(get, event_ticker: str) -> tuple[list[dict], dict]:
    return paginate(get, "kalshi", f"{ad.KALSHI_BASE}/markets",
                    {"event_ticker": event_ticker, "limit": C.PAGE_LIMITS["markets"]}, "markets")


def settled_markets(get, series: str, min_close_ts: int) -> tuple[list[dict], dict]:
    return paginate(get, "kalshi", f"{ad.KALSHI_BASE}/markets",
                    {"series_ticker": series, "status": "settled", "min_close_ts": min_close_ts,
                     "limit": C.PAGE_LIMITS["markets"]}, "markets")


def candle_request(series: str, ticker: str, request_ts: int) -> tuple[str, dict]:
    lb = C.QUOTE["lookback_hours"] * 3600
    return (f"{ad.KALSHI_BASE}/series/{series}/markets/{ticker}/candlesticks",
            {"start_ts": request_ts - lb, "end_ts": request_ts, "period_interval": C.QUOTE["period_interval_min"]})


def normalize(raw_markets: list[dict]) -> tuple[list[dict], dict]:
    """Phase 1 market normalization (live tier) plus the raw dict per ticker for diagnostics."""
    rows = [{**m, "_tier": "live"} for m in raw_markets]
    norm, _ = ad.normalize_kalshi_markets(rows)
    by_t = {m["ticker"]: m for m in raw_markets}
    return norm, by_t


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))

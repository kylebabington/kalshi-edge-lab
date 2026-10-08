"""Read-only, resumable inventory crawl of Kalshi sports offerings.

Public unauthenticated endpoints only. Writes raw/aggregated JSON under
``data/cache/sports/kalshi_discovery/<run_id>/``; never places orders and
never touches weather state.

Usage:
    python -m research.sports.kalshi_discovery --run-id 20261008 [--workers 3]
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from kalshi import cache
from kalshi.client import KalshiAPIError, KalshiClient

DISCOVERY_ROOT = cache.CACHE_ROOT / "sports" / "kalshi_discovery"
SPORTS_CATEGORY = "Sports"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class QueryLog:
    """Thread-safe append-only log of every request issued by the crawl."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, row: dict) -> None:
        with self._lock:
            cache.append_jsonl(self.path, row)


def paginate(
    client: KalshiClient,
    log: QueryLog,
    path: str,
    *,
    item_key: str,
    params: dict[str, Any],
    cursor_param: str = "cursor",
    on_page=None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Full cursor pagination, streaming pages to ``on_page``; returns stats."""
    cursor: str | None = None
    pages = items = 0
    started = utc_now()
    error: str | None = None
    while True:
        q = dict(params)
        if cursor:
            q[cursor_param] = cursor
        try:
            data = client.get(path, params=q)
        except KalshiAPIError as exc:
            error = f"{exc.failure_kind or 'error'}:{exc.status_code}:{str(exc)[:160]}"
            break
        page = data.get(item_key) or []
        pages += 1
        items += len(page)
        if on_page is not None:
            on_page(page, data)
        cursor = data.get("cursor") or None
        if not page or not cursor:
            cursor = None
            break
        if max_pages is not None and pages >= max_pages:
            break
    stats = {
        "path": path,
        "params": params,
        "pages": pages,
        "items": items,
        "complete": error is None and cursor is None,
        "truncated_cursor": cursor,
        "error": error,
        "started_utc": started,
        "finished_utc": utc_now(),
    }
    log.write(stats)
    return stats


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class MarketAgg:
    """Streaming aggregate over market objects (live or historical tier)."""

    def __init__(self) -> None:
        self.n = 0
        self.status = collections.Counter()
        self.result = collections.Counter()
        self.market_type = collections.Counter()
        self.strike_type = collections.Counter()
        self.events: set[str] = set()
        self.volume = 0.0
        self.n_traded = 0
        self.min_open: str | None = None
        self.max_close: str | None = None
        self.min_close: str | None = None
        self.rules_secondary: dict[str, str] = {}
        self.early_close: dict[str, str] = {}
        self.samples: list[dict] = []
        self.has_last_price = 0
        self.settled_examples: list[str] = []

    def add(self, m: dict) -> None:
        self.n += 1
        self.status[m.get("status") or ""] += 1
        self.result[m.get("result") or ""] += 1
        self.market_type[m.get("market_type") or ""] += 1
        self.strike_type[m.get("strike_type") or ""] += 1
        if m.get("event_ticker"):
            self.events.add(m["event_ticker"])
        vol = _f(m.get("volume_fp", m.get("volume")))
        self.volume += vol
        if vol > 0:
            self.n_traded += 1
        if _f(m.get("last_price_dollars")) > 0:
            self.has_last_price += 1
        ot, ct = m.get("open_time"), m.get("close_time")
        if ot and (self.min_open is None or ot < self.min_open):
            self.min_open = ot
        if ct and (self.max_close is None or ct > self.max_close):
            self.max_close = ct
        if ct and (self.min_close is None or ct < self.min_close):
            self.min_close = ct
        rs = (m.get("rules_secondary") or "").strip()
        if rs and len(self.rules_secondary) < 3:
            h = hashlib.sha1(rs.encode("utf-8")).hexdigest()[:10]
            self.rules_secondary.setdefault(h, rs)
        ec = (m.get("early_close_condition") or "").strip()
        if ec and len(self.early_close) < 2:
            self.early_close.setdefault(hashlib.sha1(ec.encode()).hexdigest()[:10], ec)
        if len(self.samples) < 2:
            self.samples.append(m)
        if m.get("result") in ("yes", "no") and vol > 0 and len(self.settled_examples) < 3:
            self.settled_examples.append(m.get("ticker"))

    def to_dict(self) -> dict:
        return {
            "n_markets": self.n,
            "n_events": len(self.events),
            "status": dict(self.status),
            "result": dict(self.result),
            "market_type": dict(self.market_type),
            "strike_type": dict(self.strike_type),
            "volume_contracts": round(self.volume, 2),
            "n_traded_markets": self.n_traded,
            "n_with_last_price": self.has_last_price,
            "min_open_time": self.min_open,
            "min_close_time": self.min_close,
            "max_close_time": self.max_close,
            "rules_secondary_samples": self.rules_secondary,
            "early_close_samples": self.early_close,
            "settled_traded_examples": self.settled_examples,
            "sample_markets": self.samples,
        }


def crawl_series(client: KalshiClient, log: QueryLog, series: dict, out_dir: Path) -> dict:
    ticker = series["ticker"]
    out = out_dir / "series" / f"{ticker}.json"
    existing = cache.read_json(out)
    if existing and existing.get("complete"):
        return existing

    events_n = 0
    competitions = collections.Counter()
    scopes = collections.Counter()
    first_events: list[dict] = []
    event_tickers: list[str] = []

    def on_events(page: list[dict], _data: dict) -> None:
        nonlocal events_n
        for e in page:
            events_n += 1
            pm = e.get("product_metadata") or {}
            competitions[pm.get("competition") or ""] += 1
            scopes[pm.get("competition_scope") or ""] += 1
            event_tickers.append(e.get("event_ticker") or "")
            if len(first_events) < 2:
                first_events.append(e)

    ev_stats = paginate(client, log, "/events", item_key="events",
                        params={"series_ticker": ticker, "limit": 200}, on_page=on_events)

    live = MarketAgg()
    live_stats = paginate(client, log, "/markets", item_key="markets",
                          params={"series_ticker": ticker, "limit": 1000},
                          on_page=lambda p, _d: [live.add(m) for m in p])
    hist = MarketAgg()
    hist_stats = paginate(client, log, "/historical/markets", item_key="markets",
                          params={"series_ticker": ticker, "limit": 1000},
                          on_page=lambda p, _d: [hist.add(m) for m in p])

    record = {
        "series_ticker": ticker,
        "crawled_utc": utc_now(),
        "series": series,
        "events": {
            "n_events": events_n,
            "competitions": dict(competitions),
            "competition_scopes": dict(scopes),
            "sample_events": first_events,
            "event_tickers_head": event_tickers[:5],
            "event_tickers_tail": event_tickers[-5:],
        },
        "markets_live": live.to_dict(),
        "markets_historical": hist.to_dict(),
        "pagination": {"events": ev_stats, "markets_live": live_stats,
                       "markets_historical": hist_stats},
        "complete": all(s["complete"] for s in (ev_stats, live_stats, hist_stats)),
    }
    cache.write_json(out, record)
    return record


def crawl_collection(client: KalshiClient, log: QueryLog, out_dir: Path, name: str,
                     path: str, item_key: str, params: dict, keep=None,
                     cursor_param: str = "cursor") -> None:
    rows: list[dict] = []

    def on_page(page: list[dict], _d: dict) -> None:
        for row in page:
            if keep is None or keep(row):
                rows.append(row)

    stats = paginate(client, log, path, item_key=item_key, params=params,
                     on_page=on_page, cursor_param=cursor_param)
    cache.write_json(out_dir / f"{name}.json", {"stats": stats, "rows": rows}, compact=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--min-delay", type=float, default=0.25)
    ap.add_argument("--only", nargs="*", help="series tickers subset")
    ap.add_argument("--skip-series", action="store_true")
    ap.add_argument("--skip-collections", action="store_true")
    args = ap.parse_args()

    out_dir = DISCOVERY_ROOT / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    log = QueryLog(out_dir / "query_log.jsonl")
    base = KalshiClient(min_delay_sec=args.min_delay, progress=print)

    manifest_path = out_dir / "manifest.json"
    manifest = cache.read_json(manifest_path, {}) or {}
    manifest.setdefault("run_id", args.run_id)
    manifest.setdefault("started_utc", utc_now())
    manifest["base_url"] = base.base_url
    manifest["authenticated"] = False

    for name, path, params in (
        ("exchange_status", "/exchange/status", None),
        ("historical_cutoff", "/historical/cutoff", None),
        ("tags_by_categories", "/search/tags_by_categories", None),
        ("filters_by_sport", "/search/filters_by_sport", None),
        ("series_sports", "/series", {"category": SPORTS_CATEGORY, "include_volume": "true",
                                      "include_product_metadata": "true"}),
    ):
        target = out_dir / f"{name}.json"
        if not target.exists():
            payload = base.get(path, params=params)
            cache.write_json(target, {"fetched_utc": utc_now(), "path": path,
                                      "params": params, "payload": payload}, compact=True)
            log.write({"path": path, "params": params, "pages": 1, "complete": True,
                       "finished_utc": utc_now()})

    series = cache.read_json(out_dir / "series_sports.json")["payload"]["series"]
    if args.only:
        series = [s for s in series if s["ticker"] in set(args.only)]

    if not args.skip_collections:
        coll = (
            ("milestones_sports", "/milestones", "milestones",
             {"limit": 500, "category": SPORTS_CATEGORY}, None, "cursor"),
            ("structured_targets_all", "/structured_targets", "structured_targets",
             {"page_size": 2000}, None, "cursor"),
            ("mve_collections", "/multivariate_event_collections", "multivariate_contracts",
             {"limit": 200}, None, "cursor"),
        )
        for name, path, key, params, keep, cparam in coll:
            if not (out_dir / f"{name}.json").exists():
                print(f"collection {name} ...")
                crawl_collection(base, log, out_dir, name, path, key, params, keep, cparam)

    if not args.skip_series:
        local = threading.local()

        def worker(s: dict) -> tuple[str, bool]:
            if not hasattr(local, "client"):
                local.client = KalshiClient(min_delay_sec=args.min_delay, progress=print)
            rec = crawl_series(local.client, log, s, out_dir)
            return s["ticker"], bool(rec.get("complete"))

        done = 0
        incomplete: list[str] = []
        t0 = time.monotonic()
        with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for ticker, ok in pool.map(worker, series):
                done += 1
                if not ok:
                    incomplete.append(ticker)
                if done % 100 == 0:
                    print(f"series {done}/{len(series)} ({time.monotonic() - t0:.0f}s)")
        manifest["series_total"] = len(series)
        manifest["series_incomplete"] = incomplete

    manifest["finished_utc"] = utc_now()
    cache.write_json(manifest_path, manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k != "series_incomplete"}))


if __name__ == "__main__":
    main()

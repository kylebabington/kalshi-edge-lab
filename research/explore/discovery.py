"""Explicit Refresh markets job: metadata, open crawl, prioritized ticker lookups."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from kalshi.cache import read_json, write_json
from kalshi.client import KalshiAPIError, KalshiClient, KalshiNotFoundError
from research.explore import paths as P
from research.explore import store as ST
from research.sports.ops import lock as LK

log = logging.getLogger(__name__)

_job_lock = threading.Lock()
_job: dict[str, Any] | None = None  # in-process handle


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clip(err: Any) -> str:
    text = str(err)
    return text if len(text) <= P.ERROR_MAX_CHARS else text[: P.ERROR_MAX_CHARS - 3] + "..."


class CoordinatedClient:
    """KalshiClient with weather/sports cycle lock awareness (mirrors CoordinatedSession)."""

    SHARED_MIN_INTERVAL_S = 1.0
    RECHECK_S = 30.0

    def __init__(self, client: KalshiClient):
        self.client = client
        self._last_check = 0.0
        self._busy = False

    def _maybe_slow(self) -> None:
        now = time.monotonic()
        if now - self._last_check < self.RECHECK_S:
            if self._busy:
                time.sleep(max(0.0, self.SHARED_MIN_INTERVAL_S - self.client.min_delay_sec))
            return
        self._last_check = now
        weather = LK.lock_state(LK.WEATHER_LOCK_PATH)
        sports = LK.lock_state(LK.LOCK_PATH)
        self._busy = bool(weather.get("held") or sports.get("held"))
        if self._busy:
            time.sleep(max(0.0, self.SHARED_MIN_INTERVAL_S - self.client.min_delay_sec))

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._maybe_slow()
        return self.client.get(path, params=params)


def recover_stale_job() -> dict[str, Any] | None:
    """On API start: clear dead lock and mark a running run interrupted. Never starts a crawl."""
    P.ensure_explore_dirs()
    status = read_json(P.REFRESH_STATUS_PATH, default=None)
    owner = LK.read_owner(P.LOCK_PATH)
    state = LK.owner_state(owner) if owner else "absent"
    if state == "alive":
        return status if isinstance(status, dict) else None
    if owner is not None:
        try:
            P.LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
    if isinstance(status, dict) and status.get("state") == "running":
        status = {
            **status,
            "state": "interrupted",
            "finished_at": _iso(),
            "error": "API process restarted or lock owner died; completed pages were kept",
        }
        write_json(P.REFRESH_STATUS_PATH, status)
        run_id = status.get("run_id")
        if run_id:
            manifest_path = P.RUNS_DIR / str(run_id) / "manifest.json"
            manifest = read_json(manifest_path, default=None)
            if isinstance(manifest, dict) and manifest.get("state") == "running":
                manifest["state"] = "interrupted"
                manifest["finished_at"] = status["finished_at"]
                manifest["error"] = status["error"]
                write_json(manifest_path, manifest)
                write_json(P.LATEST_MANIFEST_PATH, manifest)
        return status
    return status if isinstance(status, dict) else None


def get_refresh_status() -> dict[str, Any]:
    P.ensure_explore_dirs()
    data = read_json(P.REFRESH_STATUS_PATH, default=None)
    if isinstance(data, dict):
        return data
    return {"state": "idle", "message": "No refresh has been run yet"}


def refresh_busy() -> bool:
    status = get_refresh_status()
    if status.get("state") != "running":
        return False
    owner = LK.read_owner(P.LOCK_PATH)
    return LK.owner_state(owner) == "alive"


def start_refresh(
    *,
    watched_event_tickers: list[str] | None = None,
    client_factory: Callable[[], KalshiClient] | None = None,
) -> dict[str, Any]:
    """Start the background refresh. Returns 409-shaped status if already running."""
    P.ensure_explore_dirs()
    recover_stale_job()
    with _job_lock:
        if refresh_busy():
            status = get_refresh_status()
            return {"accepted": False, "conflict": True, "status": status}
        watched = []
        for t in watched_event_tickers or []:
            if isinstance(t, str) and t.strip() and t.strip() not in watched:
                watched.append(t.strip())
            if len(watched) >= P.MAX_WATCHED:
                break
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        status = {
            "state": "running",
            "run_id": run_id,
            "started_at": _iso(),
            "finished_at": None,
            "step": "starting",
            "pages": 0,
            "events": 0,
            "lookups": {
                "attempted": 0,
                "refreshed_ok": 0,
                "not_found": 0,
                "failed": 0,
                "skipped": 0,
                "deferred": 0,
            },
            "error": None,
            "heartbeat_at": _iso(),
            "watched_count": len(watched),
        }
        write_json(P.REFRESH_STATUS_PATH, status)
        thread = threading.Thread(
            target=_run_refresh,
            name=f"explore-refresh-{run_id}",
            kwargs={"run_id": run_id, "watched": watched, "client_factory": client_factory},
            daemon=True,
        )
        global _job
        _job = {"run_id": run_id, "thread": thread}
        thread.start()
        return {"accepted": True, "conflict": False, "status": status}


def _write_status(status: dict[str, Any]) -> None:
    status["heartbeat_at"] = _iso()
    write_json(P.REFRESH_STATUS_PATH, status)


def _run_refresh(
    *,
    run_id: str,
    watched: list[str],
    client_factory: Callable[[], KalshiClient] | None,
) -> None:
    P.ensure_explore_dirs()
    run_dir = P.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    status = get_refresh_status()
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "started_at": status.get("started_at") or _iso(),
        "finished_at": None,
        "state": "running",
        "steps": {},
        "metrics": {},
        "error": None,
    }
    write_json(run_dir / "manifest.json", manifest)
    lock_payload = None
    pages_written = 0
    shards_written = 0
    try:
        lock_payload = LK.acquire("explore-refresh", P.LOCK_PATH, log=log.info)
        factory = client_factory or (lambda: KalshiClient(min_delay_sec=0.5))
        raw_client = factory()
        client = CoordinatedClient(raw_client)

        # --- 1. Metadata ---
        status["step"] = "metadata"
        _write_status(status)
        meta_step = _fetch_metadata(client, run_dir)
        manifest["steps"]["metadata"] = meta_step
        write_json(run_dir / "manifest.json", manifest)
        pages_written += meta_step.get("pages_written", 0)

        # --- 2. Open crawl ---
        status["step"] = "open_crawl"
        _write_status(status)
        crawl_step, seen_open, n_pages, n_events, n_shards = _open_crawl(
            client, run_dir, run_id, status
        )
        manifest["steps"]["open_crawl"] = crawl_step
        pages_written += n_pages
        shards_written += n_shards
        status["pages"] = n_pages
        status["events"] = n_events
        _write_status(status)
        write_json(run_dir / "manifest.json", manifest)

        # Rebuild index after crawl
        ST.rebuild_events_index(ST.load_series_map())

        # --- 3. Lookups ---
        status["step"] = "lookups"
        _write_status(status)
        lookup_step, lookup_counts, n_lookup_shards = _run_lookups(
            client, run_dir, run_id, watched, seen_open, status
        )
        manifest["steps"]["lookups"] = lookup_step
        shards_written += n_lookup_shards
        status["lookups"] = lookup_counts
        _write_status(status)

        ST.rebuild_events_index(ST.load_series_map())

        crawl_complete = bool(crawl_step.get("complete")) and not crawl_step.get("truncated")
        lookups_done = lookup_counts.get("deferred", 0) == 0 and lookup_counts.get("failed", 0) == 0
        if crawl_complete and meta_step.get("complete") and lookups_done:
            manifest["state"] = "complete"
        else:
            manifest["state"] = "partial"
        status["state"] = manifest["state"]
    except Exception as exc:
        log.exception("explore refresh failed")
        manifest["state"] = "failed"
        manifest["error"] = _clip(exc)
        status["state"] = "failed"
        status["error"] = _clip(exc)
        status["traceback"] = _clip(traceback.format_exc())
    finally:
        manifest["finished_at"] = _iso()
        manifest["metrics"] = {
            "duration_s": round(time.monotonic() - t0, 3),
            "pages_written": pages_written,
            "shards_written": shards_written,
        }
        status["finished_at"] = manifest["finished_at"]
        status["metrics"] = manifest["metrics"]
        write_json(run_dir / "manifest.json", manifest)
        write_json(P.LATEST_MANIFEST_PATH, manifest)
        _write_status(status)
        if lock_payload is not None:
            LK.release(lock_payload, P.LOCK_PATH)
        with _job_lock:
            global _job
            if _job and _job.get("run_id") == run_id:
                _job = None


def _fetch_metadata(client: CoordinatedClient, run_dir: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "scope": {"endpoints": ["/search/tags_by_categories", "/series"]},
        "started_at": _iso(),
        "complete": False,
        "pages_written": 0,
        "degraded": False,
        "error": None,
    }
    prev_series = read_json(P.SERIES_PATH, default=None)
    prev_tags = read_json(P.TAGS_PATH, default=None)
    try:
        tags = client.get("/search/tags_by_categories")
        write_json(run_dir / "tags_by_categories.json", {"retrieved_at": _iso(), "payload": tags})
        write_json(
            P.TAGS_PATH,
            {"retrieved_at": _iso(), "freshness": "fresh", "degraded": False, "payload": tags},
        )
        step["pages_written"] += 1
        series_list = client.client.get_series_list() if hasattr(client.client, "get_series_list") else []
        # Prefer coordinated get for consistency
        try:
            series_payload = client.get("/series", params={"include_product_metadata": "true", "include_volume": "true"})
            series_list = series_payload.get("series") or series_list
        except KalshiAPIError:
            pass
        write_json(run_dir / "series.json", {"retrieved_at": _iso(), "series": series_list})
        write_json(
            P.SERIES_PATH,
            {
                "retrieved_at": _iso(),
                "freshness": "fresh",
                "degraded": False,
                "series": series_list,
            },
        )
        step["pages_written"] += 1
        step["complete"] = True
        step["items"] = len(series_list) if isinstance(series_list, list) else None
    except Exception as exc:
        step["error"] = _clip(exc)
        step["degraded"] = True
        # Retain previous metadata; label stale.
        if isinstance(prev_series, dict):
            write_json(
                P.SERIES_PATH,
                {
                    **prev_series,
                    "freshness": "stale",
                    "degraded": True,
                    "error": _clip(exc),
                    "stale_since": _iso(),
                },
            )
        if isinstance(prev_tags, dict):
            write_json(
                P.TAGS_PATH,
                {
                    **prev_tags,
                    "freshness": "stale",
                    "degraded": True,
                    "error": _clip(exc),
                    "stale_since": _iso(),
                },
            )
    step["finished_at"] = _iso()
    return step


def _complete_markets(
    client: CoordinatedClient,
    event: dict[str, Any],
    *,
    nested: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, str]:
    """Ensure market list is complete via GET /markets?event_ticker= when needed."""
    event_ticker = event.get("event_ticker") or event.get("ticker")
    if not isinstance(event_ticker, str):
        return nested, False, "missing event_ticker"

    # Nested markets for open events are usually complete; still verify with /markets
    # when nested is empty (unexpected for open) or when a cursor would be needed.
    # Docs: nested omits pre-cutoff settled markets — for open crawl that is fine;
    # for finality we require an explicit complete enumeration.
    markets = list(nested)
    cursor: str | None = None
    pages = 0
    try:
        while pages < 50:
            params: dict[str, Any] = {
                "event_ticker": event_ticker,
                "limit": P.MARKETS_PAGE_LIMIT,
                "mve_filter": "exclude",
            }
            if cursor:
                params["cursor"] = cursor
            data = client.get("/markets", params=params)
            page = data.get("markets") or []
            if not isinstance(page, list):
                page = []
            markets.extend(page)
            pages += 1
            cursor = data.get("cursor") or None
            if not cursor:
                break
        # Deduplicate by ticker keeping last
        by_t: dict[str, dict[str, Any]] = {}
        for m in markets:
            if isinstance(m, dict) and isinstance(m.get("ticker"), str):
                by_t[m["ticker"]] = m
        return list(by_t.values()), True, f"Enumerated via GET /markets ({pages} page(s))"
    except KalshiAPIError as exc:
        # Fall back to nested; mark incomplete
        if nested:
            return nested, False, f"GET /markets failed ({_clip(exc)}); nested markets retained incomplete"
        return [], False, f"GET /markets failed ({_clip(exc)}); no nested markets"


def _open_crawl(
    client: CoordinatedClient,
    run_dir: Any,
    run_id: str,
    status: dict[str, Any],
) -> tuple[dict[str, Any], set[str], int, int, int]:
    step: dict[str, Any] = {
        "scope": {
            "endpoint": "/events",
            "params": {"status": "open", "with_nested_markets": True, "limit": P.OPEN_PAGE_LIMIT},
        },
        "started_at": _iso(),
        "pages": 0,
        "items": 0,
        "first_page_at": None,
        "last_page_at": None,
        "complete": False,
        "truncated": False,
        "error": None,
    }
    seen: set[str] = set()
    cursor: str | None = None
    shards = 0
    pages_dir = run_dir / "open_pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    try:
        while step["pages"] < P.OPEN_PAGE_CAP:
            params: dict[str, Any] = {
                "status": "open",
                "with_nested_markets": "true",
                "limit": P.OPEN_PAGE_LIMIT,
            }
            if cursor:
                params["cursor"] = cursor
            retrieved_at = _iso()
            data = client.get("/events", params=params)
            events = data.get("events") or []
            if not isinstance(events, list):
                events = []
            page_index = step["pages"]
            write_json(
                pages_dir / f"page_{page_index:05d}.json",
                {"retrieved_at": retrieved_at, "params": params, "count": len(events), "cursor_next": data.get("cursor")},
            )
            if step["first_page_at"] is None:
                step["first_page_at"] = retrieved_at
            step["last_page_at"] = retrieved_at
            step["pages"] += 1
            step["items"] += len(events)
            status["pages"] = step["pages"]
            status["events"] = step["items"]
            status["step"] = "open_crawl"
            _write_status(status)

            for ev in events:
                if not isinstance(ev, dict):
                    continue
                ticker = ev.get("event_ticker") or ev.get("ticker")
                if not isinstance(ticker, str):
                    continue
                seen.add(ticker)
                nested = ev.get("markets") if isinstance(ev.get("markets"), list) else []
                # For open listing, nested markets are usually enough; mark complete when nonempty.
                # Empty nested → try /markets once.
                if nested:
                    markets_complete = True
                    basis = "Nested markets present on open crawl page"
                    extra = None
                else:
                    extra, markets_complete, basis = _complete_markets(client, ev, nested=nested)
                ST.upsert_event(
                    ev,
                    retrieved_at=retrieved_at,
                    source="open_crawl",
                    run_id=run_id,
                    listed_open=True,
                    markets_complete=markets_complete,
                    markets_complete_basis=basis,
                    extra_markets=extra,
                    mark_missing_markets=False,  # open crawl does not imply prior markets vanished
                )
                shards += 1

            if step["pages"] % P.INDEX_REBUILD_EVERY_PAGES == 0:
                ST.rebuild_events_index(ST.load_series_map())

            cursor = data.get("cursor") or None
            if not cursor:
                step["complete"] = True
                break
        else:
            step["truncated"] = True
            step["error"] = f"Stopped after {P.OPEN_PAGE_CAP} pages safety cap"
    except Exception as exc:
        step["error"] = _clip(exc)
        if step["pages"] > 0:
            step["truncated"] = True
        else:
            raise
    step["finished_at"] = _iso()
    return step, seen, step["pages"], step["items"], shards


def _lookup_targets(watched: list[str], seen_open: set[str]) -> tuple[list[str], list[str], int]:
    """Return (to_attempt, deferred, next_rotation_offset). Watched first, then rotated rest."""
    index = ST.load_events_index()
    non_final: list[str] = []
    for row in index.get("events") or []:
        if not isinstance(row, dict):
            continue
        t = row.get("event_ticker")
        if not isinstance(t, str):
            continue
        if t in seen_open:
            continue
        life = row.get("lifecycle")
        if life == "final":
            continue
        # Skip if markets_complete and all finalized (lifecycle final)
        non_final.append(t)

    watched_priority = [t for t in watched if t not in seen_open]
    # Deduplicate while preserving order: watched first
    ordered: list[str] = []
    seen: set[str] = set()
    for t in watched_priority + non_final:
        if t not in seen:
            seen.add(t)
            ordered.append(t)

    rotation = read_json(P.LOOKUP_ROTATION_PATH, default={}) or {}
    offset = int(rotation.get("offset") or 0)
    if offset >= len(ordered):
        offset = 0

    # Rotate the non-watched portion only; watched always at front.
    watched_set = set(watched_priority)
    head = [t for t in ordered if t in watched_set]
    rest = [t for t in ordered if t not in watched_set]
    if rest:
        rot = offset % len(rest)
        rest = rest[rot:] + rest[:rot]
    combined = head + rest
    to_attempt = combined[: P.LOOKUP_CAP]
    deferred = combined[P.LOOKUP_CAP :]
    # Advance rotation by how many rest items we attempted
    rest_attempted = max(0, len(to_attempt) - len(head))
    next_offset = (offset + rest_attempted) if rest else 0
    write_json(
        P.LOOKUP_ROTATION_PATH,
        {"offset": next_offset, "updated_at": _iso(), "pool_size": len(rest)},
    )
    return to_attempt, deferred, next_offset


def _run_lookups(
    client: CoordinatedClient,
    run_dir: Any,
    run_id: str,
    watched: list[str],
    seen_open: set[str],
    status: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int], int]:
    to_attempt, deferred, next_offset = _lookup_targets(watched, seen_open)
    counts = {
        "attempted": 0,
        "refreshed_ok": 0,
        "not_found": 0,
        "failed": 0,
        "skipped": 0,
        "deferred": len(deferred),
    }
    step: dict[str, Any] = {
        "scope": {
            "endpoints": ["/events?tickers=", "/events/{event_ticker}", "/markets?event_ticker="],
            "cap": P.LOOKUP_CAP,
            "batch": P.LOOKUP_BATCH,
        },
        "started_at": _iso(),
        "targets": len(to_attempt),
        "deferred": len(deferred),
        "rotation_next_offset": next_offset,
        "complete": False,
        "error": None,
    }
    shards = 0
    remaining = list(to_attempt)

    # Batch lookups
    while remaining:
        batch = remaining[: P.LOOKUP_BATCH]
        remaining = remaining[P.LOOKUP_BATCH :]
        retrieved_at = _iso()
        returned: set[str] = set()
        try:
            data = client.get(
                "/events",
                params={
                    "tickers": ",".join(batch),
                    "with_nested_markets": "true",
                    "limit": P.OPEN_PAGE_LIMIT,
                },
            )
            events = data.get("events") or []
            if not isinstance(events, list):
                events = []
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                ticker = ev.get("event_ticker") or ev.get("ticker")
                if not isinstance(ticker, str):
                    continue
                returned.add(ticker)
                nested = ev.get("markets") if isinstance(ev.get("markets"), list) else []
                extra, complete, basis = _complete_markets(client, ev, nested=nested)
                ST.upsert_event(
                    ev,
                    retrieved_at=retrieved_at,
                    source="lookup",
                    run_id=run_id,
                    markets_complete=complete,
                    markets_complete_basis=basis,
                    extra_markets=extra,
                    mark_missing_markets=True,
                    last_lookup={"at": retrieved_at, "outcome": "ok", "reason": None},
                )
                counts["attempted"] += 1
                counts["refreshed_ok"] += 1
                shards += 1
        except Exception as exc:
            # Fall through to singles for the whole batch
            step.setdefault("batch_errors", [])
            if len(step["batch_errors"]) < 20:
                step["batch_errors"].append(_clip(exc))
            returned = set()

        for ticker in batch:
            if ticker in returned:
                continue
            counts["attempted"] += 1
            try:
                retrieved_at = _iso()
                ev = client.client.get_event(ticker, with_nested_markets=True)
                if not isinstance(ev, dict):
                    raise KalshiAPIError("unexpected event payload", status_code=500, path=f"/events/{ticker}")
                # Use coordinated get for markets completeness
                nested = ev.get("markets") if isinstance(ev.get("markets"), list) else []
                extra, complete, basis = _complete_markets(client, ev, nested=nested)
                ST.upsert_event(
                    ev,
                    retrieved_at=retrieved_at,
                    source="lookup",
                    run_id=run_id,
                    markets_complete=complete,
                    markets_complete_basis=basis,
                    extra_markets=extra,
                    mark_missing_markets=True,
                    last_lookup={"at": retrieved_at, "outcome": "ok", "reason": None},
                )
                counts["refreshed_ok"] += 1
                shards += 1
            except KalshiNotFoundError as exc:
                ST.record_lookup_failure(ticker, outcome="not_found", reason=_clip(exc))
                counts["not_found"] += 1
                shards += 1
            except Exception as exc:
                ST.record_lookup_failure(ticker, outcome="failed", reason=_clip(exc))
                counts["failed"] += 1
                shards += 1

        status["lookups"] = dict(counts)
        _write_status(status)

    step["counts"] = counts
    step["complete"] = counts["deferred"] == 0
    step["finished_at"] = _iso()
    write_json(run_dir / "lookups.json", {"targets": to_attempt, "deferred": deferred, "counts": counts})
    return step, counts, shards

"""Read-only query layer over the explorer shard store. No network."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from kalshi.cache import read_json
from research.explore import coverage as CV
from research.explore import paths as P
from research.explore import store as ST
from research.explore.discovery import get_refresh_status, recover_stale_job
from research.explore.freshness import market_is_open, retrieval_freshness


def _now() -> datetime:
    return datetime.now(timezone.utc)


def summary() -> dict[str, Any]:
    recover_stale_job()
    index = ST.load_events_index()
    series_info = ST.series_metadata_info()
    tags = read_json(P.TAGS_PATH, default=None)
    manifest = read_json(P.LATEST_MANIFEST_PATH, default=None)
    refresh = get_refresh_status()

    categories: dict[str, int] = {}
    lifecycle_totals: dict[str, int] = {}
    for row in index.get("events") or []:
        if not isinstance(row, dict):
            continue
        cat = row.get("category") or "unknown"
        categories[cat] = categories.get(cat, 0) + 1
        life = row.get("lifecycle") or "unknown"
        lifecycle_totals[life] = lifecycle_totals.get(life, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "RESEARCH_ONLY",
        "event_count": index.get("count") or 0,
        "categories": [{"name": k, "event_count": v} for k, v in sorted(categories.items())],
        "lifecycle_totals": lifecycle_totals,
        "series_metadata": series_info,
        "tags_available": isinstance(tags, dict),
        "tags_retrieved_at": tags.get("retrieved_at") if isinstance(tags, dict) else None,
        "tags_freshness": (tags or {}).get("freshness") if isinstance(tags, dict) else None,
        "latest_run": manifest if isinstance(manifest, dict) else None,
        "refresh": refresh,
        "notes": [
            "GET endpoints read the local explorer cache only.",
            "Refresh markets is the only action that contacts Kalshi.",
            "Multivariate (combo) events are excluded from discovery.",
            "Model supported and saved forecast available are separate states.",
        ],
    }


def list_events(
    *,
    category: str | None = None,
    q: str | None = None,
    contract_status: str | None = None,
    lifecycle: str | None = None,
    listed_open: str | None = "latest",
    closes_after: str | None = None,
    closes_before: str | None = None,
    quote: str | None = None,
    min_volume_24h: float | None = None,
    min_open_interest: float | None = None,
    coverage: str | None = None,
    sort: str = "close_asc",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    recover_stale_job()
    index = ST.load_events_index()
    manifest = read_json(P.LATEST_MANIFEST_PATH, default=None)
    latest_run_id = (manifest or {}).get("run_id") if isinstance(manifest, dict) else None
    now = _now()
    # Preload sports coverage once — building the ticker map per event was O(events × captures).
    sports_ready = CV.sports_index_ready()
    sports_map = CV.sports_ticker_map() if sports_ready else {}
    rows: list[dict[str, Any]] = []

    for row in index.get("events") or []:
        if not isinstance(row, dict):
            continue
        if category:
            cats = row.get("categories") or []
            if row.get("category") != category and category not in cats:
                continue
        if lifecycle and row.get("lifecycle") != lifecycle:
            continue
        if listed_open == "latest":
            # Default view: open contracts, or ever listed open, or lifecycle open.
            open_n = int(row.get("open_contracts") or 0)
            listed = row.get("listed_open_at")
            if open_n <= 0 and not listed and row.get("lifecycle") != "open":
                continue
        # listed_open == "any": no filter
        if contract_status:
            counts = row.get("contract_status_counts") or {}
            if contract_status == "open":
                if (counts.get("active") or 0) <= 0:
                    continue
            elif contract_status == "other":
                known = {"active", "closed", "determined", "finalized", "inactive", "initialized", "disputed", "amended"}
                if not any(k not in known and v for k, v in counts.items()):
                    # also allow if only unknown statuses — skip filter edge
                    pass
            else:
                key = "active" if contract_status == "open" else contract_status
                if (counts.get(key) or 0) <= 0:
                    continue
        if closes_after and (row.get("close_time_max") or "") < closes_after:
            continue
        if closes_before and (row.get("close_time_min") or "9999") > closes_before:
            continue
        if quote:
            qs = row.get("quote") or {}
            if quote == "two_sided" and (qs.get("two_sided") or 0) <= 0:
                continue
            if quote == "one_sided" and (qs.get("one_sided") or 0) <= 0:
                continue
            if quote == "none" and (qs.get("none") or 0) <= 0:
                continue
        if min_volume_24h is not None:
            vol = row.get("volume_24h_total")
            if vol is None or vol < min_volume_24h:
                continue
        if q:
            hay = " ".join(
                str(x)
                for x in (
                    row.get("title"),
                    row.get("sub_title"),
                    row.get("event_ticker"),
                    row.get("series_ticker"),
                    row.get("category"),
                )
                if x
            ).lower()
            if q.strip().lower() not in hay:
                continue

        cov = CV.coverage_for(
            str(row.get("event_ticker") or ""),
            row.get("series_ticker"),
            sports_ready=sports_ready,
            sports_map=sports_map,
        )
        if coverage == "model" and cov.get("model_supported") == "none":
            continue
        if coverage == "none" and cov.get("model_supported") != "none":
            continue
        if min_open_interest is not None:
            # open interest is per-market; approximate from detail only — skip if unknown at index
            pass

        fresh = retrieval_freshness(row.get("retrieved_at"), now=now)
        item = {
            **row,
            "retrieval_freshness": fresh,
            "coverage": {
                "model_supported": cov["model_supported"],
                "model_supported_basis": cov["model_supported_basis"],
                "saved_forecast": cov["saved_forecast"],
                "saved_forecast_basis": cov["saved_forecast_basis"],
                "saved_forecast_href": cov["saved_forecast_href"],
                "sports_event_id": cov.get("sports_event_id"),
            },
        }
        rows.append(item)

    reverse = False
    key_fn: Any
    if sort == "close_desc":
        key_fn = lambda r: r.get("close_time_max") or ""
        reverse = True
    elif sort == "volume_desc":
        key_fn = lambda r: r.get("volume_24h_total") if r.get("volume_24h_total") is not None else -1
        reverse = True
    elif sort == "title":
        key_fn = lambda r: (r.get("title") or "").lower()
    elif sort == "retrieved_desc":
        key_fn = lambda r: r.get("retrieved_at") or ""
        reverse = True
    else:  # close_asc
        key_fn = lambda r: r.get("close_time_min") or "9999"

    rows.sort(key=key_fn, reverse=reverse)

    page = max(1, page)
    page_size = max(1, min(200, page_size))
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    slice_ = rows[start : start + page_size]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "RESEARCH_ONLY",
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "latest_run_id": latest_run_id,
        "partial_crawl": bool(
            isinstance(manifest, dict)
            and (manifest.get("state") in ("partial", "interrupted", "failed")
                 or ((manifest.get("steps") or {}).get("open_crawl") or {}).get("truncated"))
        ),
        "events": slice_,
    }


def get_event(event_ticker: str) -> dict[str, Any] | None:
    recover_stale_job()
    entry = ST.load_event(event_ticker)
    if entry is None:
        return None
    series_map = ST.load_series_map()
    series = series_map.get(entry.get("series_ticker") or "") or {}
    cov = CV.coverage_for(event_ticker, entry.get("series_ticker"))
    markets = []
    for m in entry.get("markets") or []:
        mm = dict(m)
        mm["is_open"] = market_is_open(m.get("status"))
        mm["retrieval_freshness"] = retrieval_freshness(m.get("retrieved_at"))
        markets.append(mm)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "RESEARCH_ONLY",
        "event_ticker": event_ticker,
        "series_ticker": entry.get("series_ticker"),
        "title": entry.get("title"),
        "sub_title": entry.get("sub_title"),
        "category": series.get("category") or entry.get("event_category_deprecated"),
        "categories": series.get("categories") or [],
        "series_title": series.get("title"),
        "settlement_sources": entry.get("settlement_sources") or series.get("settlement_sources") or [],
        "contract_url": series.get("contract_url"),
        "contract_terms_url": series.get("contract_terms_url"),
        "listed_open_at": entry.get("listed_open_at"),
        "retrieved_at": entry.get("retrieved_at"),
        "retrieval_freshness": retrieval_freshness(entry.get("retrieved_at")),
        "last_lookup": entry.get("last_lookup"),
        "lifecycle": (
            (entry.get("lifecycle") or {}).get("state")
            if isinstance(entry.get("lifecycle"), dict)
            else entry.get("lifecycle")
        ),
        "lifecycle_detail": entry.get("lifecycle") if isinstance(entry.get("lifecycle"), dict) else None,
        "markets_complete": entry.get("markets_complete"),
        "markets_complete_basis": entry.get("markets_complete_basis"),
        "markets": markets,
        "coverage": cov,
        "quote_section_label": "Latest retrieved quotes",
        "notes": [
            "Quotes are the latest values retrieved into the explorer cache, not a live stream.",
            "A contract is open only when its own status is active.",
            "Saved forecasts are separate from model support.",
        ],
    }

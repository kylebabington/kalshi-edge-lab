"""Build the per-series sports inventory CSV and sport x family summary.

Usage:
    python -m research.sports.inventory --run-id 20261008T2225Z \
        --csv docs/research/sports_inventory_v1.csv \
        --summary data/results/sports_inventory_summary_v1.json
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from kalshi import cache
from research.sports.families import GAME_LEVEL, classify_family, classify_sport
from research.sports.kalshi_discovery import DISCOVERY_ROOT

FIELD_SPORTS = {"Golf", "Motorsport", "Olympics", "Cycling", "Other", "Rowing"}

CSV_COLUMNS = [
    "series_ticker", "series_title", "sport", "sport_basis", "competition_top", "kalshi_scope",
    "contract_family", "contract_subfamily", "game_level", "frequency", "fee_type", "fee_multiplier",
    "exchange_index", "settlement_sources", "contract_terms_url", "n_events",
    "live_markets", "live_open_or_active", "live_initialized_or_unopened", "live_closed",
    "live_determined_or_settled", "hist_markets", "hist_events", "settled_events_est", "markets_total",
    "markets_void_or_scalar", "traded_markets", "volume_fp_total", "first_open_time",
    "last_close_time", "offering_status", "kalshi_price_history", "pagination_complete",
    "pagination_errors", "crawled_utc",
]


def _parse(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _min(*vals: str | None) -> str | None:
    vals = [v for v in vals if v]
    return min(vals) if vals else None


def _max(*vals: str | None) -> str | None:
    vals = [v for v in vals if v]
    return max(vals) if vals else None


def offering_status(live: dict, hist: dict, n_events: int) -> str:
    st = live.get("status") or {}
    if st.get("active") or st.get("open"):
        return "active"
    if st.get("initialized") or st.get("unopened"):
        return "upcoming"
    if live.get("n_markets") or hist.get("n_markets"):
        return "historical_only"
    if n_events:
        return "events_without_markets"
    return "no_events_or_markets"


def build_rows(run_dir: Path) -> tuple[list[dict], dict]:
    filters = cache.read_json(run_dir / "filters_by_sport.json")["payload"]["filters_by_sports"]
    comp_to_sport: dict[str, str] = {}
    for sport, block in filters.items():
        if sport == "All sports":
            continue
        for comp in (block.get("competitions") or {}):
            comp_to_sport.setdefault(comp, sport)

    rows: list[dict] = []
    for path in sorted((run_dir / "series").glob("*.json")):
        rec = cache.read_json(path)
        s = rec["series"]
        ev = rec["events"]
        live, hist = rec["markets_live"], rec["markets_historical"]
        scope = (s.get("product_metadata") or {}).get("scope", "")
        sport, basis = classify_sport(s.get("tags"), ev.get("competitions"), comp_to_sport,
                                      s["ticker"], s.get("title", ""))
        family, sub = classify_family(scope, s.get("title", ""), s["ticker"])
        if family == "game_winner" and sport in FIELD_SPORTS:
            family, sub = "field_finish", "event_winner"
        comps = {k: v for k, v in (ev.get("competitions") or {}).items() if k}
        comp_top = max(comps, key=comps.get) if comps else ""
        lst = live.get("status") or {}
        res_l, res_h = live.get("result") or {}, hist.get("result") or {}
        void = sum(v for k, v in list(res_l.items()) + list(res_h.items()) if k not in ("yes", "no", ""))
        traded = live.get("n_traded_markets", 0) + hist.get("n_traded_markets", 0)
        pag = rec.get("pagination") or {}
        errors = [f"{k}:{v.get('error')}" for k, v in pag.items() if v.get("error")]
        status = offering_status(live, hist, ev.get("n_events", 0))
        live_settled = sum(lst.get(k, 0) for k in ("determined", "settled", "finalized", "amended"))
        live_events_settled = (live.get("n_events", 0) * live_settled / live["n_markets"]
                               if live.get("n_markets") else 0)
        settled_events_est = round(hist.get("n_events", 0) + live_events_settled)
        if traded and (hist.get("n_markets") or lst.get("finalized") or lst.get("settled")
                       or lst.get("determined") or lst.get("closed")):
            price_hist = "candles_expected_for_traded_settled_markets"
        elif traded:
            price_hist = "live_only_so_far"
        else:
            price_hist = "no_traded_markets"
        rows.append({
            "series_ticker": s["ticker"],
            "series_title": s.get("title", ""),
            "sport": sport,
            "sport_basis": basis,
            "competition_top": comp_top,
            "kalshi_scope": scope,
            "contract_family": family,
            "contract_subfamily": sub,
            "game_level": family in GAME_LEVEL,
            "frequency": s.get("frequency", ""),
            "fee_type": s.get("fee_type", ""),
            "fee_multiplier": s.get("fee_multiplier", ""),
            "exchange_index": s.get("exchange_index", ""),
            "settlement_sources": "; ".join(f"{x.get('name')} <{x.get('url')}>"
                                            for x in (s.get("settlement_sources") or [])),
            "contract_terms_url": s.get("contract_terms_url", ""),
            "n_events": ev.get("n_events", 0),
            "live_markets": live.get("n_markets", 0),
            "live_open_or_active": lst.get("active", 0) + lst.get("open", 0),
            "live_initialized_or_unopened": lst.get("initialized", 0) + lst.get("unopened", 0),
            "live_closed": lst.get("closed", 0) + lst.get("inactive", 0),
            "live_determined_or_settled": sum(lst.get(k, 0) for k in ("determined", "settled",
                                                                      "finalized", "amended")),
            "hist_markets": hist.get("n_markets", 0),
            "hist_events": hist.get("n_events", 0),
            "settled_events_est": settled_events_est,
            "markets_total": live.get("n_markets", 0) + hist.get("n_markets", 0),
            "markets_void_or_scalar": void,
            "traded_markets": traded,
            "volume_fp_total": round(live.get("volume_contracts", 0) + hist.get("volume_contracts", 0), 2),
            "first_open_time": _min(live.get("min_open_time"), hist.get("min_open_time")) or "",
            "last_close_time": _max(live.get("max_close_time"), hist.get("max_close_time")) or "",
            "offering_status": status,
            "kalshi_price_history": price_hist,
            "pagination_complete": rec.get("complete", False),
            "pagination_errors": " | ".join(errors),
            "crawled_utc": rec.get("crawled_utc", ""),
            "_live_status": lst,
            "_results": dict(collections.Counter(res_l) + collections.Counter(res_h)),
            "_rules_secondary": {**(live.get("rules_secondary_samples") or {}),
                                 **(hist.get("rules_secondary_samples") or {})},
            "_early_close": {**(live.get("early_close_samples") or {}),
                             **(hist.get("early_close_samples") or {})},
            "_settled_examples": (hist.get("settled_traded_examples") or [])
                                 + (live.get("settled_traded_examples") or []),
            "_competitions": comps,
        })
    return rows, comp_to_sport


def summarize(rows: list[dict]) -> dict[str, Any]:
    by_cell: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["sport"], r["contract_family"])
        c = by_cell.setdefault(key, {
            "sport": key[0], "contract_family": key[1], "series": 0, "series_active_or_upcoming": 0,
            "events": 0, "markets": 0, "hist_markets": 0, "traded_markets": 0, "volume_fp": 0.0,
            "void_or_scalar": 0, "first_open": None, "last_close": None, "competitions": collections.Counter(),
            "status": collections.Counter(), "top_series": [], "contract_terms_urls": collections.Counter(),
            "settlement_sources": collections.Counter(), "incomplete_series": 0,
        })
        c["series"] += 1
        c["series_active_or_upcoming"] += r["offering_status"] in ("active", "upcoming")
        c["settled_events_est"] = c.get("settled_events_est", 0) + r["settled_events_est"]
        c["events"] += r["n_events"]
        c["markets"] += r["markets_total"]
        c["hist_markets"] += r["hist_markets"]
        c["traded_markets"] += r["traded_markets"]
        c["volume_fp"] += r["volume_fp_total"]
        c["void_or_scalar"] += r["markets_void_or_scalar"]
        c["first_open"] = _min(c["first_open"], r["first_open_time"])
        c["last_close"] = _max(c["last_close"], r["last_close_time"])
        c["competitions"].update(r["_competitions"])
        c["status"][r["offering_status"]] += 1
        c["top_series"].append((r["volume_fp_total"], r["series_ticker"]))
        if r["contract_terms_url"]:
            c["contract_terms_urls"][r["contract_terms_url"]] += 1
        for src in r["settlement_sources"].split("; "):
            if src:
                c["settlement_sources"][src] += 1
        c["incomplete_series"] += not r["pagination_complete"]
    cells = []
    for c in by_cell.values():
        c["top_series"] = [t for _v, t in sorted(c["top_series"], reverse=True)[:6]]
        c["competitions"] = dict(c["competitions"].most_common(8))
        c["status"] = dict(c["status"])
        c["contract_terms_urls"] = dict(c["contract_terms_urls"].most_common(4))
        c["settlement_sources"] = dict(c["settlement_sources"].most_common(5))
        c["volume_fp"] = round(c["volume_fp"], 1)
        cells.append(c)
    cells.sort(key=lambda c: (c["sport"], -c["volume_fp"]))
    sports = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        sp = sports[r["sport"]]
        sp["series"] += 1
        sp["markets"] += r["markets_total"]
        sp["traded_markets"] += r["traded_markets"]
        sp["volume_fp"] += r["volume_fp_total"]
        sp["active_or_upcoming_series"] += r["offering_status"] in ("active", "upcoming")
    return {
        "n_series": len(rows),
        "n_incomplete": sum(not r["pagination_complete"] for r in rows),
        "status": dict(collections.Counter(r["offering_status"] for r in rows)),
        "families": dict(collections.Counter(r["contract_family"] for r in rows)),
        "sports": {k: dict(v) for k, v in sorted(sports.items(), key=lambda kv: -kv[1]["volume_fp"])},
        "cells": cells,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--summary", required=True)
    args = ap.parse_args()
    run_dir = DISCOVERY_ROOT / args.run_id
    rows, _ = build_rows(run_dir)
    rows.sort(key=lambda r: (r["sport"], r["contract_family"], -r["volume_fp_total"], r["series_ticker"]))
    out = cache.REPO_ROOT / args.csv
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        w = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    summary = summarize(rows)
    summary["run_id"] = args.run_id
    summary["rules_samples"] = {
        r["series_ticker"]: {"rules_secondary": list(r["_rules_secondary"].values())[:2],
                             "early_close": list(r["_early_close"].values())[:1],
                             "results": r["_results"], "settled_examples": r["_settled_examples"][:3],
                             "sport": r["sport"], "family": r["contract_family"],
                             "volume": r["volume_fp_total"]}
        for r in rows
    }
    cache.write_json(cache.REPO_ROOT / args.summary, summary)
    print(json.dumps({k: summary[k] for k in ("n_series", "n_incomplete", "status", "families")}, indent=1))


if __name__ == "__main__":
    main()

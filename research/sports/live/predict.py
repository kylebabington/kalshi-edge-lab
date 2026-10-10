"""The four frozen Phase 2 candidates for one live event, as of ``prediction_as_of``.

* frozen     - Phase 1 v2 primary model (``phase2.folds.primary_row``: elo for game winners when it
               exists, else the score model; score model for other families) with the frozen v2
               parameters, run by the unmodified Phase 1 predictor on the as-of view of history.
* calibrated - the frozen ``sports_phase2_v1`` calibration map of the competition x family group.
* market     - mid of the latest hourly candle ended by prediction_as_of (age <= 3 h, two-sided book),
               only for contracts chosen by the frozen per-event contract rule.
* blend      - the frozen blend map applied to (frozen p, candle mid). Never a current snapshot.

Each candidate is either ``{"p": ...}`` or ``{"unavailable": reason}``.
"""

from __future__ import annotations

import collections
import math
from datetime import datetime, timezone

from .. import models as M
from ..benchmark import quote_at_cutoff
from ..build import MilestoneIndex
from ..phase2 import calib as CAL
from ..phase2 import config as P2
from ..phase2 import folds as F
from ..phase2.market import listing_ts, pick_contracts
from . import config as C
from . import discover as D
from . import sources as S

CANDIDATES = ["frozen", "calibrated", "market", "blend"]


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- context (shared by capture and replay)

def build_context(comp, get, event_tickers: list[str], history_end: str, series_spec: dict) -> dict:
    """Milestones, live markets, linked contracts and merged history for one competition."""
    problems = []
    ms_rows, st = D.milestones(get)
    if not st["complete"]:
        problems.append(f"milestone discovery incomplete: {st['error']}")
    mindex = MilestoneIndex(ms_rows)
    raw_markets = []
    for et in event_tickers:
        items, st = D.event_markets(get, et)
        if not st["complete"]:
            problems.append(f"markets of {et} incomplete: {st['error']}")
        raw_markets += items
    norm, raw_by_t = D.normalize(raw_markets)
    norm = [m for m in norm if m["series_ticker"] in series_spec]
    specs, excluded = S.parse_specs(comp, norm, series_spec)
    frozen = S.frozen_events(comp)
    end = datetime.fromisoformat(history_end.replace("Z", "+00:00"))
    hist_stats = {}
    if comp.source == "kalshi_only":
        min_close = int(S.history_start(comp, frozen).timestamp())
        settled = []
        for series, _, _ in comp.explicit_series:
            items, st = D.settled_markets(get, series, min_close)
            if not st["complete"]:
                problems.append(f"settled markets of {series} incomplete: {st['error']}")
            settled += items
        live, hist_stats = S.kalshi_only_history(comp, settled, mindex)
    else:
        raws = []
        for url, params in S.espn_history_requests(comp, frozen, end):
            raw = get("espn", url, params)
            if raw.status != 200:
                problems.append(f"ESPN history HTTP {raw.status}: {url} {params}")
            raws.append(raw)
        live = {"espn_team": S.team_games, "espn_fight": S.fights, "espn_golf": S.golf_events}[comp.source](raws, comp)
        hist_stats = {"source_rows": len(live)}
    events = S.merge(frozen, live)
    cw = S.crosswalk(comp)
    if comp.source == "espn_team":
        contracts = S.link_team(comp, specs, mindex, events, cw)
    elif comp.source == "espn_fight":
        contracts = S.link_fight(comp, specs, mindex, events, cw)
    elif comp.source == "espn_golf":
        contracts = S.link_field(comp, specs, ms_rows, events, cw)
    else:
        contracts = S.link_kalshi_only(comp, specs, mindex)
    return {"problems": problems, "events": events, "contracts": contracts, "excluded_markets": excluded,
            "raw_by_ticker": raw_by_t, "history": hist_stats}


def clusters(ctx) -> dict:
    by = collections.defaultdict(list)
    for c in ctx["contracts"]:
        if c.get("cluster") and c.get("start"):
            by[c["cluster"]].append(c)
    return by


# ---------------------------------------------------------------- frozen model

def cohort_of(comp, entry: dict, series: str) -> str | None:
    if comp.source == "kalshi_only":
        return "kalshi_settlement_only"
    g = (entry.get("gate") or {}).get(series)
    return g["cohort"] if g and g.get("cohort") != "failed" else None


def predict_frozen(comp, entry: dict, events, contracts, as_of: float) -> tuple[dict, dict]:
    """ticker -> {"p","model"} | {"unavailable"} using the Phase 1 predictor on the as-of view."""
    ok = [c for c in contracts if c["status"] == "ok"]
    view, vstats = S.as_of_view(comp, events, as_of)
    with F.fold_window(M.T_TEST, M.ts(C.LIVE_T_END)):
        preds = M.PREDICT[comp.source](comp, view, ok, entry["params"]) if ok else []
    by = collections.defaultdict(list)
    for p in preds:
        by[p["ticker"]].append(p)
    out = {}
    for c in contracts:
        if c["status"] != "ok":
            out[c["ticker"]] = {"unavailable": f"contract not linked: {c.get('reason')}"}
            continue
        r = F.primary_row(by.get(c["ticker"], []), c["family"])
        if r is None:
            why = next((p.get("reason") for p in by.get(c["ticker"], []) if p.get("reason")), None)
            out[c["ticker"]] = {"unavailable": f"no primary-model forecast ({why or 'model not available'})"}
        else:
            out[c["ticker"]] = {"p": r["p"], "model": r["model"]}
    return out, vstats


# ---------------------------------------------------------------- market rule

def market_picks(comp, entry, contracts, frozen: dict, raw_by_t: dict, t_pick: float) -> list[str]:
    """Frozen per-event contract rule over contracts listed by ``t_pick`` with a frozen forecast."""
    fam = collections.defaultdict(list)
    for c in contracts:
        if c["status"] != "ok" or c["family"] not in C.MARKET_FAMILIES or "p" not in frozen[c["ticker"]]:
            continue
        if cohort_of(comp, entry, c["series"]) is None:
            continue
        lt = listing_ts(raw_by_t.get(c["ticker"]) or {})
        if lt is None or lt > t_pick:
            continue
        fam[c["family"]].append(c)
    return sorted(c["ticker"] for f, cs in fam.items() for c in pick_contracts(f, cs))


def quote(candles_raw, as_of: float, request_ts: int) -> dict:
    if candles_raw is None:
        return {"unavailable": "not selected by the frozen per-event contract rule"}
    if math.floor(request_ts / 3600) != math.floor(as_of / 3600):
        return {"unavailable": "an hour boundary passed between the candle request and prediction_as_of"}
    q = quote_at_cutoff(candles_raw.json().get("candlesticks") or [], int(as_of))
    if q["status"] != "ok":
        return {"unavailable": f"market quote {q['status']}", **{k: q[k] for k in ("candle_end", "age_s") if k in q}}
    return {"p": q["mid"], "bid": q["bid"], "ask": q["ask"], "candle_end": q["candle_end"], "age_s": q["age_s"]}


# ---------------------------------------------------------------- candidate assembly

def assemble(comp, entry, groups: dict, contracts, frozen: dict, picks: list[str], quotes: dict,
             raw_by_t: dict) -> list[dict]:
    out = []
    for c in sorted(contracts, key=lambda c: c["ticker"]):
        t, fam = c["ticker"], c["family"]
        cohort = cohort_of(comp, entry, c["series"])
        g = groups.get(f"{comp.key}|{fam}")
        cand = {}
        if cohort is None and c["status"] == "ok":
            reason = "series failed or unreconciled in the sports_phase1_v2 gate (never scored)"
            cand = {k: {"unavailable": reason} for k in CANDIDATES}
        else:
            fz = frozen[t]
            cand["frozen"] = fz
            if "p" not in fz:
                cand["calibrated"] = {"unavailable": "frozen forecast unavailable"}
            elif g is None:
                cand["calibrated"] = {"unavailable": f"no {C.PARENT_PROTOCOL} group for {comp.key}|{fam}"}
            elif g["calibration"]["status"] != "ok":
                cand["calibrated"] = {"unavailable": g["calibration"]["status"]}
            else:
                cand["calibrated"] = {"p": CAL.apply_calibration(g["calibration"]["params"], fz["p"]),
                                      "map": g["calibration"]["selected"]}
            if fam not in C.MARKET_FAMILIES:
                key = "segment" if fam == "segment" else fam
                why = P2.NO_NEW_QUOTES.get(key, "family outside the market scope")
                cand["market"] = {"unavailable": why}
                cand["blend"] = {"unavailable": why}
            else:
                mk = quotes.get(t) if t in picks else {"unavailable": "not selected by the frozen per-event contract rule"}
                if mk is None:
                    mk = {"unavailable": "not selected by the frozen per-event contract rule"}
                cand["market"] = mk
                bl = (g or {}).get("blend") or {"status": f"no {C.PARENT_PROTOCOL} group"}
                if bl["status"] != "ok":
                    cand["blend"] = {"unavailable": bl["status"]}
                elif "p" not in fz or "p" not in mk:
                    cand["blend"] = {"unavailable": "needs both a frozen forecast and a market candle quote"}
                else:
                    cand["blend"] = {"p": CAL.apply_blend(bl["params"], fz["p"], mk["p"]), "map": bl["selected"]}
        raw = raw_by_t.get(t) or {}
        lt = listing_ts(raw)
        out.append({"ticker": t, "series": c["series"], "family": fam, "kind": c["kind"], "segment": c.get("segment"),
                    "side": c.get("side"), "strike": c.get("strike"), "tie": c.get("tie"), "top_n": c.get("top_n"),
                    "participant": c.get("participant"), "status": c["status"], "reason": c.get("reason"),
                    "cohort": cohort, "listing_time": _iso(lt) if lt is not None else None,
                    "market_pick": t in picks, "candidates": cand,
                    "diagnostics": {"current_yes_bid": raw.get("yes_bid_dollars"),
                                    "current_yes_ask": raw.get("yes_ask_dollars"),
                                    "note": "listing snapshot; never a candidate or blend input"}})
    return out

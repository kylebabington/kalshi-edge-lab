"""Live source adapters behind one interface (history, linking, release times, as-of view).

Only pure Phase 1 helpers are reused (``parse_market``, the ESPN parsers, ``match_games``,
``MilestoneIndex``); the Phase 1 ``build_*`` functions are never called because they write
crosswalk and normalized files. Participant crosswalks are read from the frozen files only; an
unmapped participant makes the contract unavailable (crosswalks are never extended live).
"""

from __future__ import annotations

import collections
import copy
import json
from datetime import datetime, timedelta, timezone

from .. import adapters as ad
from .. import build as B
from .. import models as M
from ..contracts import parse_market
from ..core import CROSSWALK, NORMALIZED, parse_ts, read_jsonl
from . import config as C
from . import discover as D

OUTCOME_FIELDS = {
    "espn_team": {"completed": False, "home_score": None, "away_score": None, "winner": None,
                  "home_periods": [], "away_periods": [], "no_extra_time": None},
    "espn_fight": {"completed": False, "winner": None, "method": None},
    "field": {"completed": False},
    "kalshi_only": {"completed": False, "winner_id": None, "available_at": None},
}
HIDDEN_STATUS = "HIDDEN_AS_OF"


def kind_of(source: str) -> str:
    return "field" if source in ("espn_golf", "jolpica") else source


def frozen_events(comp) -> list[dict]:
    return list(read_jsonl(NORMALIZED / comp.key / "events.jsonl"))


def crosswalk(comp) -> dict:
    p = CROSSWALK / f"{comp.key}_participants.json"
    return json.loads(p.read_text(encoding="utf-8"))["mapping"] if p.exists() else {}


# ---------------------------------------------------------------- release times and the as-of view

def release_time(comp, e: dict) -> float | None:
    """When a completed result may first be used (frozen Phase 1 rules); None if never released."""
    k = kind_of(comp.source)
    if k == "kalshi_only":
        return M.ts(e["available_at"]) if e.get("available_at") and e.get("winner_id") else None
    if not e.get("completed") or not e.get("start"):
        return None
    if k == "field":
        return M._field_avail(comp, e)
    return M.ts(e["start"]) + comp.result_lag_hours * 3600


def hide(comp, e: dict) -> dict:
    """Copy of ``e`` with every outcome-derived field removed; pregame fields are kept."""
    k = kind_of(comp.source)
    out = copy.deepcopy(e)
    out.update(copy.deepcopy(OUTCOME_FIELDS[k]))
    if "status" in out:
        out["status"] = HIDDEN_STATUS
    if k == "espn_fight":
        out["fighter_records"] = [{"espn_athlete_id": x.get("espn_athlete_id"), "name": x.get("name"), "winner": None}
                                  for x in e.get("fighter_records") or []]
    if k == "field":
        out["entrants"] = [{"id": x["id"], "name": x.get("name"), "position": None, "finished": None, "won": None}
                           for x in e.get("entrants") or []]
    return out


def as_of_view(comp, events: list[dict], as_of: float) -> tuple[list[dict], dict]:
    """Events as known at ``as_of``: results released later (or never) are stripped of outcome fields."""
    view, used, hidden, latest = [], 0, 0, None
    for e in events:
        r = release_time(comp, e)
        if r is not None and r <= as_of:
            view.append(e)
            used += 1
            latest = r if latest is None or r > latest else latest
        else:
            view.append(hide(comp, e))
            hidden += 1
    return view, {"results_used": used, "events_hidden": hidden,
                  "latest_release_used": None if latest is None else
                  datetime.fromtimestamp(latest, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


# ---------------------------------------------------------------- history since the freeze

def history_start(comp, events: list[dict]) -> datetime:
    done = [M.ts(e["start"]) for e in events if e.get("start") and release_time(comp, e) is not None]
    last = datetime.fromtimestamp(max(done), timezone.utc) if done else datetime(2026, 10, 1, tzinfo=timezone.utc)
    return last - timedelta(days=C.HISTORY_OVERLAP_DAYS)


def espn_history_requests(comp, events: list[dict], end: datetime) -> list[tuple[str, dict]]:
    start = history_start(comp, events)
    return ad.espn_requests(comp.espn_path, comp.espn_mode, start.date(), end.date(), comp.espn_groups,
                            comp.months_active, comp.weekdays, comp.espn_limit)


def team_games(raws, comp) -> list[dict]:
    games = {}
    for raw in raws:
        for g in ad.parse_espn_team_games(raw, comp.espn_path):
            prev = games.get(g["espn_id"])
            if prev is None or (g["completed"] and not prev["completed"]):
                games[g["espn_id"]] = g
    out = []
    for g in sorted(games.values(), key=lambda g: (g["start"], g["espn_id"])):
        h, a = g["home"], g["away"]
        winner = None
        if g["completed"] and h["score"] is not None and a["score"] is not None:
            if h["winner"] and not a["winner"]:
                winner = "home"
            elif a["winner"] and not h["winner"]:
                winner = "away"
            elif h["score"] == a["score"]:
                winner = "tie"
            else:
                winner = "home" if h["score"] > a["score"] else "away"
        out.append({"source_game_id": f"espn:{g['espn_id']}", "start": g["start"], "status": g["status"],
                    "completed": g["completed"], "neutral": g["neutral"], "season_year": g["season_year"],
                    "season_type": g["season_type"], "sport": comp.sport,
                    "home": f"espn:{h['espn_team_id']}", "away": f"espn:{a['espn_team_id']}",
                    "home_name": h["name"], "away_name": a["name"], "home_abbr": h["abbr"], "away_abbr": a["abbr"],
                    "home_location": h["location"], "away_location": a["location"],
                    "home_score": h["score"], "away_score": a["score"], "winner": winner,
                    "home_periods": h["periods"], "away_periods": a["periods"],
                    "no_extra_time": g.get("no_extra_time")})
    return out


def fights(raws, comp) -> list[dict]:
    fs = {}
    for raw in raws:
        for f in ad.parse_espn_fights(raw, comp.espn_path):
            prev = fs.get(f["espn_id"])
            if prev is None or (f["completed"] and not prev["completed"]):
                fs[f["espn_id"]] = f
    out = []
    for f in sorted(fs.values(), key=lambda f: (f["start"], f["espn_id"])):
        ids = [f"espn:{x['espn_athlete_id']}" for x in f["fighters"]]
        winners = [i for i, x in zip(ids, f["fighters"]) if x.get("winner")]
        out.append({"source_game_id": f"espn:{f['espn_id']}", "start": f["start"], "completed": f["completed"],
                    "status": f["status"], "fighters": ids,
                    "winner": winners[0] if len(winners) == 1 and f["completed"] else None,
                    "method": f["method"], "scheduled_rounds": f["scheduled_rounds"],
                    "weight_class": f["weight_class"], "event_name": f["event_name"],
                    "fighter_records": f["fighters"], "sport": comp.sport})
    return out


def golf_events(raws, comp) -> list[dict]:
    """Completed tournaments keep their result field; upcoming ones carry the listed field without outcomes."""
    evs = {}
    for raw in raws:
        for e in ad.parse_espn_golf(raw, comp.espn_path):
            prev = evs.get(e["espn_id"])
            if prev is None or (e["completed"] and not prev["completed"]):
                evs[e["espn_id"]] = e
    out = []
    for e in sorted(evs.values(), key=lambda e: (e["start"], e["espn_id"])):
        if e["completed"]:
            entrants = [{"id": f"espn:{p['espn_athlete_id']}", "name": p["name"], "position": p["position"],
                         "finished": p["made_cut"], "won": p["espn_athlete_id"] == e["winner_athlete_id"]}
                        for p in e["players"]]
        else:
            entrants = [{"id": f"espn:{p['espn_athlete_id']}", "name": p["name"], "position": None,
                         "finished": None, "won": None} for p in e["players"]]
        out.append({"source_game_id": f"espn:{e['espn_id']}", "name": e["name"], "start": e["start"],
                    "end": e["end"], "completed": e["completed"], "entrants": entrants,
                    "field_source": "result" if e["completed"] else "prediction_time_listing"})
    return out


def merge(frozen: list[dict], live: list[dict]) -> list[dict]:
    """Frozen history, with live rows added or replacing frozen rows that were not yet completed."""
    by = {e["source_game_id"]: e for e in frozen}
    for e in live:
        prev = by.get(e["source_game_id"])
        if prev is None or not prev.get("completed"):
            by[e["source_game_id"]] = e
    return sorted(by.values(), key=lambda e: (e.get("start") or "", e["source_game_id"]))


def kalshi_only_history(comp, settled: list[dict], mindex: B.MilestoneIndex) -> tuple[list[dict], dict]:
    """Settled two-way events since the freeze, built with the Phase 1 build_kalshi_only rules."""
    norm, _ = D.normalize(settled)
    by_event = collections.defaultdict(list)
    for m in norm:
        by_event[m["event_ticker"]].append(m)
    events, why = [], collections.Counter()
    for et, rows in sorted(by_event.items()):
        specs = []
        for m in rows:
            spec, _ = parse_market(m, "winner", None, sport=comp.sport)
            if spec is not None:
                out_kind, _ = ad.kalshi_settlement_outcome(m)
                specs.append({**spec, "kalshi_outcome": out_kind, "settlement_ts": m["settlement_ts"]})
        ms, _ = mindex.resolve(et)
        parts = sorted({s["participant"] for s in specs if s.get("participant")})
        if ms is None or not ms.get("start"):
            why["no milestone start"] += 1
            continue
        if any(s.get("tie") for s in specs) or len(specs) != 2 or len(parts) != 2:
            why["not two-market two-participant"] += 1
            continue
        yes = [s for s in specs if s["kalshi_outcome"] == "yes"]
        if any(s["kalshi_outcome"] in ("void", "unsettled") for s in specs) or len(yes) != 1:
            why["not exactly one settled winner"] += 1
            continue
        avail = max((s["settlement_ts"] for s in specs if s.get("settlement_ts")), default=None)
        if avail is None:
            why["no settlement time"] += 1
            continue
        events.append({"source_game_id": f"kalshi:{ms['milestone_id']}", "start": ms["start"], "completed": True,
                       "home": parts[0], "away": parts[1], "winner_id": yes[0]["participant"],
                       "available_at": avail, "neutral": True, "sport": comp.sport})
        why["events"] += 1
    return events, dict(why)


# ---------------------------------------------------------------- linking live contracts

def parse_specs(comp, markets: list[dict], series_spec: dict) -> tuple[list[dict], list[dict]]:
    specs, excluded = [], []
    for m in markets:
        kind, seg = series_spec[m["series_ticker"]]
        top_n = seg if kind == "field_top" else None
        spec, why = parse_market(m, kind, None if kind == "field_top" else seg, sport=comp.sport, top_n=top_n)
        if spec is None:
            excluded.append({"ticker": m["ticker"], "reason": why})
            continue
        if m.get("status") not in ("active", "open", "initialized", "unopened"):
            excluded.append({"ticker": m["ticker"], "reason": f"market status {m.get('status')}"})
            continue
        specs.append(spec)
    return specs, excluded


def _excluded(spec, reason, cluster=None):
    return {**spec, "status": "excluded", "reason": reason, "cluster": cluster}


def link_team(comp, specs, mindex, events, cw) -> list[dict]:
    kg = {}
    ms_of = {}
    for s in specs:
        ms, why = mindex.resolve(s["event_ticker"])
        ms_of[s["ticker"]] = (ms, why)
        if ms and ms["home_id"] and ms["away_id"]:
            kg[ms["milestone_id"]] = {"milestone_id": ms["milestone_id"], "start": ms["start"],
                                      "home": ms["home_id"], "away": ms["away_id"]}
    matched, reasons = B.match_games(sorted(kg.values(), key=lambda g: (g["start"], g["milestone_id"])), events, cw)
    by_id = {e["source_game_id"]: e for e in events}
    out = []
    for s in specs:
        ms, why = ms_of[s["ticker"]]
        if ms is None:
            out.append(_excluded(s, why))
            continue
        gid = matched.get(ms["milestone_id"])
        if gid is None:
            out.append(_excluded(s, reasons.get(ms["milestone_id"], "no Kalshi game identity"), ms["milestone_id"]))
            continue
        g = by_id[gid]
        side_of = {k: side for side in ("home", "away") for k, e in cw.items() if e == g[side]}
        row = {**s, "cluster": gid, "source_game_id": gid, "start": g["start"], "season_type": g["season_type"],
               "home": g["home"], "away": g["away"], "neutral": g["neutral"], "milestone_id": ms["milestone_id"],
               "side": side_of.get(s["participant"]) if s.get("participant") else None}
        if g["season_type"] in comp.exclude_season_types:
            out.append({**row, "status": "excluded", "reason": "preseason game (excluded by protocol)"})
        elif s.get("participant") and row["side"] is None:
            out.append({**row, "status": "excluded", "reason": "participant not in the frozen crosswalk"})
        else:
            out.append({**row, "status": "ok"})
    return out


def link_fight(comp, specs, mindex, events, cw) -> list[dict]:
    pair_idx = collections.defaultdict(list)
    for e in events:
        pair_idx[frozenset(e["fighters"])].append(e)
    out = []
    for s in specs:
        ms, why = mindex.resolve(s["event_ticker"])
        if ms is None:
            out.append(_excluded(s, why))
            continue
        a, b = cw.get(ms["home_id"]), cw.get(ms["away_id"])
        cands = []
        if a and b:
            t = parse_ts(ms["start"])
            cands = [e for e in pair_idx.get(frozenset((a, b)), [])
                     if abs((parse_ts(e["start"]) - t).total_seconds()) <= 36 * 3600]
        if len(cands) != 1:
            out.append(_excluded(s, "fighter not in the frozen crosswalk" if not (a and b) else
                                 ("no source fight within 36h" if not cands else "ambiguous source fight"),
                                 ms["milestone_id"]))
            continue
        e = cands[0]
        ps = cw.get(s.get("participant")) if s.get("participant") else None
        row = {**s, "cluster": e["source_game_id"], "source_game_id": e["source_game_id"], "start": e["start"],
               "fighters": e["fighters"], "participant_source": ps, "scheduled_rounds": e["scheduled_rounds"],
               "milestone_id": ms["milestone_id"]}
        if s["kind"] == "winner" and ps not in e["fighters"]:
            out.append({**row, "status": "excluded", "reason": "participant not in the frozen crosswalk"})
        elif s["kind"] == "distance" and not e["scheduled_rounds"]:
            out.append({**row, "status": "excluded", "reason": "scheduled rounds not published by prediction time"})
        else:
            out.append({**row, "status": "ok"})
    return out


def link_field(comp, specs, milestones, events, cw) -> list[dict]:
    ms_by_event = {}
    for m in milestones:
        if m["type"] in ("golf_tournament", "racing_tournament"):
            for t in m["event_tickers"]:
                ms_by_event.setdefault(t, m)
    groups = collections.defaultdict(list)
    for s in specs:
        groups[B._field_key(s["event_ticker"])].append(s)
    upcoming = [e for e in events if not e.get("completed")]
    out = []
    for key, rows in sorted(groups.items()):
        ms = next((ms_by_event[r["event_ticker"]] for r in rows if r["event_ticker"] in ms_by_event), None)
        mapped = {cw[r["participant"]] for r in rows if r["participant"] in cw}
        ev, reason = None, None
        if ms is None:
            reason = "no Kalshi milestone (start time) for tournament"
        else:
            start = parse_ts(ms["start"])
            cands = []
            for e in upcoming:
                if abs((parse_ts(e["start"]) - start).total_seconds()) > 3 * 86400:
                    continue
                ids = {x["id"] for x in e["entrants"]}
                if not ids:
                    continue
                if len(mapped & ids) / max(1, len(mapped)) >= 0.7:
                    cands.append(e)
            if len(cands) == 1:
                ev = cands[0]
            else:
                listed = [e for e in upcoming if abs((parse_ts(e["start"]) - start).total_seconds()) <= 3 * 86400]
                reason = ("no prediction-time field published by the source" if not any(e["entrants"] for e in listed)
                          else "no unique source event (date window + field overlap >= 70%)")
        for s in rows:
            if ev is None:
                out.append(_excluded(s, reason, f"kalshi:{key}"))
                continue
            src = cw.get(s["participant"])
            row = {**s, "cluster": ev["source_game_id"], "source_game_id": ev["source_game_id"], "start": ev["start"],
                   "participant_source": src, "milestone_id": ms["milestone_id"]}
            if src is None:
                out.append({**row, "status": "excluded", "reason": "competitor not in the frozen crosswalk"})
            elif src not in {x["id"] for x in ev["entrants"]}:
                out.append({**row, "status": "excluded", "reason": "competitor not in the prediction-time field"})
            else:
                out.append({**row, "status": "ok"})
    return out


def link_kalshi_only(comp, specs, mindex) -> list[dict]:
    by_event = collections.defaultdict(list)
    for s in specs:
        by_event[s["event_ticker"]].append(s)
    out = []
    for et, rows in sorted(by_event.items()):
        ms, why = mindex.resolve(et)
        parts = sorted({r["participant"] for r in rows if r.get("participant")})
        reason = None
        if ms is None or not ms.get("start"):
            reason = why or "no Kalshi milestone start"
        elif any(r.get("tie") for r in rows) or len(rows) != 2 or len(parts) != 2:
            reason = "not a two-market two-participant event (three-way or incomplete)"
        cl = f"kalshi:{ms['milestone_id']}" if ms else f"kalshi:{et}"
        for r in rows:
            row = {**r, "cluster": cl, "source_game_id": None, "start": ms["start"] if ms else None,
                   "home": parts[0] if len(parts) == 2 else None, "away": parts[1] if len(parts) == 2 else None,
                   "milestone_id": ms["milestone_id"] if ms else None}
            out.append({**row, "status": "excluded", "reason": reason} if reason else {**row, "status": "ok"})
    return out

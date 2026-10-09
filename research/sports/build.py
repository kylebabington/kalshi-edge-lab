"""Fetch and build stages: raw sources -> normalized events, crosswalks, contracts, outcomes.

Identity rules
--------------
* A Kalshi game is the Kalshi milestone UUID linked to the market's event ticker. Schedule
  changes never change it; an event linked to several milestones that disagree is excluded.
* An independent game is the source's own ID (ESPN event/competition ID, Jolpica season-round).
* Participant crosswalks (Kalshi structured-target UUID -> source ID) are accepted only on
  unambiguous evidence and persisted with their evidence counts. Ambiguous participants or games
  are excluded with a reason, never merged.
"""

from __future__ import annotations

import collections
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import adapters as ad
from .competitions import Competition
from .contracts import parse_market, payoff_field, payoff_fight, payoff_team_game, family
from .core import CROSSWALK, NORMALIZED, Fetcher, iso, parse_ts, read_jsonl, sha256_json, write_json, write_jsonl

FETCH_END = date(2026, 10, 7)
JOLPICA_YEARS = range(2022, 2027)
MATCH_WINDOW_H = 18.0
TEAM_MIN_SUPPORT = 4
TEAM_DOMINANCE = 2.5


_TRANSLIT = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ß": "ss", "ł": "l", "Ł": "L", "đ": "d",
                           "Đ": "D", "ı": "i", "þ": "th", "ð": "d"})


def norm_name(s: str | None) -> str:
    if not s:
        return ""
    s = s.translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return re.sub(r"\s+(jr|sr|ii|iii|iv)$", "", s)


def espn_reqs(comp: Competition) -> list[tuple[str, dict]]:
    return ad.espn_requests(comp.espn_path, comp.espn_mode, comp.history_start, FETCH_END, comp.espn_groups,
                            comp.months_active, comp.weekdays, comp.espn_limit)


# --------------------------------------------------------------------------------------------
# Fetch stage
# --------------------------------------------------------------------------------------------

def fetch_competition(comp: Competition, series_specs: list[tuple], fetcher: Fetcher, log) -> dict:
    stats: dict[str, Any] = {"competition": comp.key, "series": {}}
    for series, _kind, _seg in series_specs:
        _, st = ad.fetch_kalshi_series_markets(fetcher, series)
        stats["series"][series] = st
    if comp.source in ("espn_team", "espn_fight", "espn_golf"):
        reqs = espn_reqs(comp)
        bad = 0
        for url, params in reqs:
            raw = fetcher.get("espn", url, params)
            bad += 0 if raw.ok else 1
        stats["espn_requests"] = len(reqs)
        stats["espn_non_200"] = bad
    if comp.source == "jolpica":
        for y in JOLPICA_YEARS:
            ad.fetch_jolpica_season(fetcher, y)
    if comp.key == "nfl":
        fetcher.get("nflverse", ad.NFLVERSE_GAMES)
    log(f"fetched {comp.key}: {fetcher.stats}")
    return stats


# --------------------------------------------------------------------------------------------
# Kalshi side
# --------------------------------------------------------------------------------------------

class MilestoneIndex:
    def __init__(self, milestones: list[dict]):
        self.by_id = {m["milestone_id"]: m for m in milestones}
        self.by_event: dict[str, list[str]] = collections.defaultdict(list)
        for m in milestones:
            for t in m["event_tickers"]:
                self.by_event[t].append(m["milestone_id"])

    def resolve(self, event_ticker: str) -> tuple[dict | None, str | None]:
        ids = sorted(set(self.by_event.get(event_ticker, [])))
        if not ids:
            return None, "no Kalshi milestone linked to event"
        ms = [self.by_id[i] for i in ids]
        if len(ms) > 1:
            starts = [parse_ts(m["start"]) for m in ms]
            pairs = {frozenset(x for x in (m["home_id"], m["away_id"]) if x) for m in ms}
            if None in starts or max(starts) - min(starts) > timedelta(hours=1) or len(pairs) > 1:
                return None, "event linked to conflicting milestones"
        return ms[0], None


def load_kalshi_markets(comp: Competition, series_specs: list[tuple], fetcher: Fetcher) -> tuple[list[dict], dict]:
    rows, dedupe = [], {}
    for series, kind, seg in series_specs:
        raw, _ = ad.fetch_kalshi_series_markets(fetcher, series)
        norm, st = ad.normalize_kalshi_markets(raw)
        dedupe[series] = st
        for m in norm:
            m["_kind"], m["_seg"] = kind, seg
            rows.append(m)
    return rows, dedupe


def parse_contracts(comp: Competition, markets: list[dict], milestones: MilestoneIndex) -> tuple[list[dict], list[dict]]:
    specs, excluded = [], []
    for m in markets:
        out_kind, sett_val = ad.kalshi_settlement_outcome(m)
        common = {"competition": comp.key, "ticker": m["ticker"], "series": m["series_ticker"],
                  "family": family(m["_kind"], m["_seg"]), "kind": m["_kind"], "segment": m["_seg"],
                  "kalshi_outcome": out_kind, "volume_fp": m["volume_fp"], "tier": m["tier"]}
        if out_kind == "unsettled":
            excluded.append({**common, "reason": "not settled at fetch time"})
            continue
        top_n = m["_seg"] if m["_kind"] == "field_top" else None
        spec, why = parse_market(m, m["_kind"], None if m["_kind"] == "field_top" else m["_seg"],
                                 sport=comp.sport, top_n=top_n)
        if spec is None:
            excluded.append({**common, "reason": why})
            continue
        ms, why = milestones.resolve(m["event_ticker"])
        if ms is None and comp.source not in ("espn_golf", "jolpica"):
            excluded.append({**common, "reason": why})
            continue
        spec.update({"competition": comp.key, "kalshi_outcome": out_kind, "kalshi_value": sett_val,
                     "volume_fp": m["volume_fp"], "tier": m["tier"], "settlement_ts": m["settlement_ts"],
                     "close_time": m["close_time"], "open_time": m["open_time"],
                     "milestone_id": ms["milestone_id"] if ms else None,
                     "kalshi_start": ms["start"] if ms else None,
                     "kalshi_home": ms["home_id"] if ms else None, "kalshi_away": ms["away_id"] if ms else None})
        specs.append(spec)
    return specs, excluded


# --------------------------------------------------------------------------------------------
# ESPN team sports
# --------------------------------------------------------------------------------------------

def load_espn_team_games(comp: Competition, fetcher: Fetcher) -> list[dict]:
    games: dict[str, dict] = {}
    for url, params in espn_reqs(comp):
        raw = fetcher.get("espn", url, params)
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


def crosswalk_teams(kgames: list[dict], sgames: list[dict], prior: dict | None) -> tuple[dict, dict]:
    """Map Kalshi team UUIDs to source team IDs from co-scheduling evidence.

    Pass 1 counts source teams playing within 3 h of each Kalshi game start. Pass 2 recounts using
    games whose opponent is already mapped, which isolates the true counterpart. A mapping is
    accepted with at least TEAM_MIN_SUPPORT supporting games and a TEAM_DOMINANCE ratio over the
    runner-up, or (low-volume teams) at least two anchored games, no competing candidate and agreeing
    names; assignments must be one-to-one. Changes versus the persisted crosswalk are reported as
    conflicts and dropped.
    """
    by_day: dict[str, list[dict]] = collections.defaultdict(list)
    for g in sgames:
        d = parse_ts(g["start"])
        for off in (-1, 0, 1):
            by_day[(d + timedelta(days=off)).strftime("%Y-%m-%d")].append(g)

    def near(kg, hours):
        t = parse_ts(kg["start"])
        return [g for g in by_day.get(t.strftime("%Y-%m-%d"), [])
                if abs((parse_ts(g["start"]) - t).total_seconds()) <= hours * 3600]

    from .adapters import load_structured_targets
    targets = load_structured_targets()
    src_tokens: dict[str, set] = {}
    for g in sgames:
        for side in ("home", "away"):
            toks = set()
            for f in ("name", "abbr", "location"):
                toks |= {w for w in norm_name(g.get(f"{side}_{f}")).split() if len(w) >= 3}
            src_tokens.setdefault(g[side], set()).update(toks)

    def name_score(k, e):
        t = targets.get(k) or {}
        toks = set()
        for f in ("name", "abbreviation", "market"):
            toks |= {w for w in norm_name(t.get(f)).split() if len(w) >= 3}
        return len(toks & src_tokens.get(e, set()))

    # Round 0 seeds: among source teams co-scheduled (+-3 h) with a Kalshi team at least
    # TEAM_MIN_SUPPORT times, the unique best name/abbreviation match. Later rounds count only games
    # whose opponent is already mapped (opponent-anchored), and the final mapping must satisfy the
    # support / dominance / one-to-one rules on that anchored evidence.
    co: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for kg in kgames:
        for g in near(kg, 3.0):
            for k in (kg["home"], kg["away"]):
                co[k][g["home"]] += 1
                co[k][g["away"]] += 1
    mapping: dict[str, str] = {}
    for k, c in co.items():
        cands = [(name_score(k, e), e) for e, n in c.items() if n >= TEAM_MIN_SUPPORT]
        cands = sorted((s, e) for s, e in cands if s > 0)
        if cands and (len(cands) == 1 or cands[-1][0] > cands[-2][0]):
            mapping[k] = cands[-1][1]
    rev0 = collections.Counter(mapping.values())
    mapping = {k: e for k, e in mapping.items() if rev0[e] == 1}
    evidence: dict[str, dict] = {}
    for rnd in range(1, 4):
        counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for kg in kgames:
            pair = [kg["home"], kg["away"]]
            for g in near(kg, 3.0):
                teams = [g["home"], g["away"]]
                for i, k in enumerate(pair):
                    other = pair[1 - i]
                    if other in mapping and mapping[other] in teams:
                        counts[k][teams[1 - teams.index(mapping[other])]] += 1
        proposal = {}
        for k, c in counts.items():
            top = c.most_common(2)
            if not top:
                continue
            (e1, n1), n2 = top[0], (top[1][1] if len(top) > 1 else 0)
            strong = n1 >= TEAM_MIN_SUPPORT and n1 >= TEAM_DOMINANCE * max(n2, 1)
            # low-volume teams (e.g. European cups): every anchored game agrees and the names agree
            sparse = n1 >= 2 and n2 == 0 and name_score(k, e1) >= 1
            if strong or sparse:
                proposal[k] = (e1, n1, n2)
        rev = collections.Counter(e for e, _, _ in proposal.values())
        mapping = {k: e for k, (e, _, _) in proposal.items() if rev[e] == 1}
        evidence = {k: {"source_id": e, "support": n1, "runner_up": n2, "round": rnd,
                        "name_tokens_shared": name_score(k, e)}
                    for k, (e, n1, n2) in proposal.items() if rev[e] == 1}
    conflicts = []
    for k, prev in ((prior or {}).get("mapping") or {}).items():
        if k in mapping and mapping[k] != prev:
            conflicts.append({"kalshi_id": k, "previous": prev, "now": mapping[k]})
            del mapping[k]
            evidence.pop(k, None)
    return mapping, {"evidence": evidence, "conflicts": conflicts}


def match_games(kgames: list[dict], sgames: list[dict], team_map: dict[str, str]) -> tuple[dict, dict]:
    """Kalshi milestone -> source game; exact team pair and start within MATCH_WINDOW_H, unique."""
    idx: dict[frozenset, list[dict]] = collections.defaultdict(list)
    for g in sgames:
        idx[frozenset((g["home"], g["away"]))].append(g)
    matched, reasons = {}, {}
    for kg in kgames:
        h, a = team_map.get(kg["home"]), team_map.get(kg["away"])
        if not h or not a:
            reasons[kg["milestone_id"]] = "participant crosswalk unresolved"
            continue
        t = parse_ts(kg["start"])
        cands = [g for g in idx.get(frozenset((h, a)), [])
                 if abs((parse_ts(g["start"]) - t).total_seconds()) <= MATCH_WINDOW_H * 3600]
        if len(cands) > 1:
            close = [g for g in cands if abs((parse_ts(g["start"]) - t).total_seconds()) <= 3 * 3600]
            cands = close if len(close) == 1 else cands
        if len(cands) == 1:
            matched[kg["milestone_id"]] = cands[0]["source_game_id"]
        else:
            reasons[kg["milestone_id"]] = "no source game within window" if not cands else "ambiguous source game (doubleheader or duplicate)"
    return matched, reasons


def build_team_competition(comp: Competition, specs: list[dict], fetcher: Fetcher) -> dict:
    sgames = load_espn_team_games(comp, fetcher)
    kg_by_ms: dict[str, dict] = {}
    for s in specs:
        if s["milestone_id"] and s["kalshi_home"] and s["kalshi_away"]:
            kg_by_ms[s["milestone_id"]] = {"milestone_id": s["milestone_id"], "start": s["kalshi_start"],
                                           "home": s["kalshi_home"], "away": s["kalshi_away"]}
    kgames = sorted(kg_by_ms.values(), key=lambda g: (g["start"], g["milestone_id"]))
    prior_path = CROSSWALK / f"{comp.key}_participants.json"
    prior = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path.exists() else None
    team_map, cw_meta = crosswalk_teams(kgames, sgames, prior)
    matched, unmatched = match_games(kgames, sgames, team_map)
    by_id = {g["source_game_id"]: g for g in sgames}
    write_json(prior_path, {"competition": comp.key, "mapping": dict(sorted(team_map.items())), **cw_meta})
    write_json(CROSSWALK / f"{comp.key}_games.json", {"matched": dict(sorted(matched.items())),
                                                      "unmatched": dict(sorted(unmatched.items()))})
    contracts = []
    for s in specs:
        ms = s["milestone_id"]
        row = dict(s)
        gid = matched.get(ms)
        if gid is None:
            row.update(status="excluded", reason=unmatched.get(ms, "no Kalshi game identity"), cluster=ms,
                       source_game_id=None, start=s["kalshi_start"])
            contracts.append(row)
            continue
        g = by_id[gid]
        side_of = {}
        for side in ("home", "away"):
            for k, e in team_map.items():
                if e == g[side]:
                    side_of[k] = side
        payoff = payoff_team_game(s, g, side_of, comp.segments, comp.regulation_periods) if g["completed"] else None
        row.update(cluster=gid, source_game_id=gid, start=g["start"], season_type=g["season_type"],
                   home=g["home"], away=g["away"], neutral=g["neutral"],
                   side=side_of.get(s["participant"]) if s.get("participant") else None,
                   independent_payoff=payoff)
        if g["season_type"] in comp.exclude_season_types:
            row.update(status="excluded", reason="preseason game (excluded by protocol)")
        elif not g["completed"]:
            row.update(status="excluded", reason=f"source game not completed ({g['status']})")
        elif payoff is None:
            row.update(status="excluded", reason="independent payoff not derivable (missing periods/participant)")
        elif s["kalshi_outcome"] == "void":
            row.update(status="excluded", reason="Kalshi void / fair-price settlement")
        else:
            row.update(status="ok", agree=int(payoff == (1 if s["kalshi_outcome"] == "yes" else 0)))
        contracts.append(row)
    events = [g for g in sgames]
    return {"contracts": contracts, "events": events, "participants": len(team_map),
            "kalshi_games": len(kgames), "matched_games": len(matched),
            "crosswalk_conflicts": cw_meta["conflicts"]}


# --------------------------------------------------------------------------------------------
# Name-based crosswalks (fighters, golfers, drivers)
# --------------------------------------------------------------------------------------------

def name_crosswalk(kalshi_ids: set[str], targets: dict[str, dict], source_names: dict[str, str]) -> tuple[dict, dict]:
    by_name: dict[str, list[str]] = collections.defaultdict(list)
    for sid, nm in source_names.items():
        by_name[norm_name(nm)].append(sid)
    mapping, reasons = {}, {}
    for k in sorted(kalshi_ids):
        t = targets.get(k)
        nm = norm_name(t["name"] if t else None)
        cands = sorted(set(by_name.get(nm, []))) if nm else []
        if len(cands) == 1:
            mapping[k] = cands[0]
        else:
            reasons[k] = "no source participant with this exact name" if not cands else "name matches several source participants"
    return mapping, reasons


def _opponent_anchored(specs, events, fmap, targets, names) -> dict[str, str]:
    by_fighter: dict[str, list[dict]] = collections.defaultdict(list)
    for e in events:
        for f in e["fighters"]:
            by_fighter[f].append(e)
    proposals: dict[str, set] = collections.defaultdict(set)
    seen = set()
    for s in specs:
        pair = (s.get("kalshi_home"), s.get("kalshi_away"))
        if None in pair or pair in seen or not s.get("kalshi_start"):
            continue
        seen.add(pair)
        t = parse_ts(s["kalshi_start"])
        for known, unknown in (pair, pair[::-1]):
            if known not in fmap or unknown in fmap:
                continue
            near = [e for e in by_fighter[fmap[known]] if abs((parse_ts(e["start"]) - t).total_seconds()) <= 36 * 3600]
            if len(near) != 1:
                continue
            opp = [f for f in near[0]["fighters"] if f != fmap[known]][0]
            a = {w for w in norm_name((targets.get(unknown) or {}).get("name")).split() if len(w) >= 3}
            b = {w for w in norm_name(names.get(opp)).split() if len(w) >= 3}
            if a & b:
                proposals[unknown].add(opp)
    used = collections.Counter(next(iter(v)) for v in proposals.values() if len(v) == 1)
    taken = set(fmap.values())
    return {k: next(iter(v)) for k, v in sorted(proposals.items())
            if len(v) == 1 and used[next(iter(v))] == 1 and next(iter(v)) not in taken}


def build_fight_competition(comp: Competition, specs: list[dict], fetcher: Fetcher) -> dict:
    fights: dict[str, dict] = {}
    for url, params in espn_reqs(comp):
        for f in ad.parse_espn_fights(fetcher.get("espn", url, params), comp.espn_path):
            prev = fights.get(f["espn_id"])
            if prev is None or (f["completed"] and not prev["completed"]):
                fights[f["espn_id"]] = f
    events = []
    names = {}
    for f in sorted(fights.values(), key=lambda f: (f["start"], f["espn_id"])):
        ids = [f"espn:{x['espn_athlete_id']}" for x in f["fighters"]]
        for x, i in zip(f["fighters"], ids):
            names[i] = x["name"]
        winners = [i for i, x in zip(ids, f["fighters"]) if x.get("winner")]
        events.append({"source_game_id": f"espn:{f['espn_id']}", "start": f["start"], "completed": f["completed"],
                       "status": f["status"], "fighters": ids, "winner": winners[0] if len(winners) == 1 else None,
                       "method": f["method"], "scheduled_rounds": f["scheduled_rounds"],
                       "weight_class": f["weight_class"], "event_name": f["event_name"],
                       "fighter_records": f["fighters"], "sport": comp.sport})
    targets = ad.load_structured_targets()
    kids = {x for s in specs for x in (s.get("kalshi_home"), s.get("kalshi_away"), s.get("participant")) if x}
    fmap, freasons = name_crosswalk(kids, targets, names)
    anchored = _opponent_anchored(specs, events, fmap, targets, names)
    for k, v in anchored.items():
        fmap[k] = v
        freasons.pop(k, None)
    write_json(CROSSWALK / f"{comp.key}_participants.json",
               {"competition": comp.key, "mapping": fmap, "unresolved": freasons,
                "opponent_anchored": anchored,
                "method": "exact normalized name, unique; else opponent-anchored (the mapped opponent has exactly "
                          "one source fight within 36h and the names share a token), one-to-one"})
    pair_idx: dict[frozenset, list[dict]] = collections.defaultdict(list)
    for e in events:
        pair_idx[frozenset(e["fighters"])].append(e)
    contracts = []
    for s in specs:
        row = dict(s)
        a, b = fmap.get(s.get("kalshi_home")), fmap.get(s.get("kalshi_away"))
        cands = []
        if a and b:
            t = parse_ts(s["kalshi_start"])
            cands = [e for e in pair_idx.get(frozenset((a, b)), [])
                     if abs((parse_ts(e["start"]) - t).total_seconds()) <= 36 * 3600]
        if len(cands) != 1:
            row.update(status="excluded", cluster=s["milestone_id"], source_game_id=None, start=s["kalshi_start"],
                       reason="fighter crosswalk unresolved" if not (a and b) else
                       ("no source fight within 36h" if not cands else "ambiguous source fight"))
            contracts.append(row)
            continue
        e = cands[0]
        side = {k: e["fighters"].index(v) for k, v in fmap.items() if v in e["fighters"]}
        fight_rec = {"completed": e["completed"], "method": e["method"],
                     "fighters": [{"winner": e["winner"] == fid} for fid in e["fighters"]]}
        payoff = payoff_fight(s, fight_rec, side)
        row.update(cluster=e["source_game_id"], source_game_id=e["source_game_id"], start=e["start"],
                   fighters=e["fighters"], participant_source=fmap.get(s.get("participant")) if s.get("participant") else None,
                   scheduled_rounds=e["scheduled_rounds"], independent_payoff=payoff)
        if payoff is None:
            row.update(status="excluded", reason="fight outcome not derivable (no result, draw/NC, or method unknown)")
        elif s["kalshi_outcome"] == "void":
            row.update(status="excluded", reason="Kalshi void / fair-price settlement")
        else:
            row.update(status="ok", agree=int(payoff == (1 if s["kalshi_outcome"] == "yes" else 0)))
        contracts.append(row)
    return {"contracts": contracts, "events": events, "participants": len(fmap)}


def _field_anchored(groups, matched_ev, pmap, targets) -> dict[str, str]:
    taken = set(pmap.values())
    cand: dict[str, set | None] = {}
    for key, rows in groups.items():
        ev = matched_ev[key][1]
        if ev is None:
            continue
        free = [x for x in ev["entrants"] if x["id"] not in taken]
        for k in {r["participant"] for r in rows if r["participant"] not in pmap}:
            words = norm_name((targets.get(k) or {}).get("name")).split()
            if not words:
                continue
            here = {x["id"] for x in free if norm_name(x["name"]).split()[-1:] == words[-1:]}
            cand[k] = here if cand.get(k) is None else cand[k] & here
    props = {k: next(iter(v)) for k, v in cand.items() if v and len(v) == 1}
    used = collections.Counter(props.values())
    return {k: v for k, v in sorted(props.items()) if used[v] == 1}


def _field_key(event_ticker: str) -> str:
    return event_ticker.split("-", 1)[1] if "-" in event_ticker else event_ticker


def build_field_competition(comp: Competition, specs: list[dict], fetcher: Fetcher,
                            milestones: list[dict]) -> dict:
    if comp.source == "espn_golf":
        evs: dict[str, dict] = {}
        for url, params in espn_reqs(comp):
            for e in ad.parse_espn_golf(fetcher.get("espn", url, params), comp.espn_path):
                prev = evs.get(e["espn_id"])
                if prev is None or (e["completed"] and not prev["completed"]):
                    evs[e["espn_id"]] = e
        events = []
        for e in sorted(evs.values(), key=lambda e: (e["start"], e["espn_id"])):
            entrants = [{"id": f"espn:{p['espn_athlete_id']}", "name": p["name"], "position": p["position"],
                         "finished": p["made_cut"], "won": p["espn_athlete_id"] == e["winner_athlete_id"]}
                        for p in e["players"]]
            events.append({"source_game_id": f"espn:{e['espn_id']}", "name": e["name"], "start": e["start"],
                           "end": e["end"], "completed": e["completed"], "entrants": entrants})
    else:
        raws = []
        for y in JOLPICA_YEARS:
            raws += ad.fetch_jolpica_season(fetcher, y)
        events = []
        for r in ad.parse_jolpica(raws):
            entrants = [{"id": f"jolpica:{x['driver_id']}", "name": x["name"],
                         "position": x["position"] if x["classified"] else None, "finished": x["classified"],
                         "won": x["position"] == 1} for x in r["results"]]
            events.append({"source_game_id": f"jolpica:{r['race_id']}", "name": r["name"], "start": r["start"],
                           "end": None, "completed": bool(r["results"]), "entrants": entrants})
    names = {x["id"]: x["name"] for e in events for x in e["entrants"]}
    targets = ad.load_structured_targets()
    pmap, preasons = name_crosswalk({s["participant"] for s in specs}, targets, names)
    ms_by_event: dict[str, dict] = {}
    for m in milestones:
        if m["type"] in ("golf_tournament", "racing_tournament"):
            for t in m["event_tickers"]:
                ms_by_event.setdefault(t, m)
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for s in specs:
        groups[_field_key(s["event_ticker"])].append(s)
    matched_ev: dict[str, tuple] = {}
    for key, rows in sorted(groups.items()):
        ms = next((ms_by_event[r["event_ticker"]] for r in rows if r["event_ticker"] in ms_by_event), None)
        mapped = {pmap[r["participant"]] for r in rows if r["participant"] in pmap}
        start = parse_ts(ms["start"]) if ms else None
        cands = []
        for e in events:
            es = parse_ts(e["start"])
            if start is not None and abs((es - start).total_seconds()) > (3 * 86400 if comp.source == "espn_golf" else 36 * 3600):
                continue
            if start is None:
                continue
            ids = {x["id"] for x in e["entrants"]}
            overlap = len(mapped & ids) / max(1, len(mapped))
            if overlap >= 0.7:
                cands.append(e)
        ev = cands[0] if len(cands) == 1 else None
        reason = None
        if ms is None:
            reason = "no Kalshi milestone (start time) for tournament"
        elif ev is None:
            reason = "no unique source event (date window + field overlap >= 70%)"
        matched_ev[key] = (ms, ev, reason)
    anchored = _field_anchored(groups, matched_ev, pmap, targets)
    for k, v in anchored.items():
        pmap[k] = v
        preasons.pop(k, None)
    write_json(CROSSWALK / f"{comp.key}_participants.json",
               {"competition": comp.key, "mapping": pmap, "unresolved": preasons, "field_anchored": anchored,
                "method": "exact normalized name, unique; else field-anchored (in every matched event field the "
                          "same single unmapped entrant shares the surname), one-to-one"})
    contracts, ev_match = [], {}
    for key, rows in sorted(groups.items()):
        ms, ev, reason = matched_ev[key]
        ev_match[key] = ev["source_game_id"] if ev else reason
        for s in rows:
            row = dict(s)
            row["kalshi_start"] = ms["start"] if ms else None
            row["milestone_id"] = ms["milestone_id"] if ms else None
            if ev is None:
                row.update(status="excluded", reason=reason, cluster=f"kalshi:{key}", source_game_id=None,
                           start=row["kalshi_start"])
                contracts.append(row)
                continue
            src = pmap.get(s["participant"])
            ent = next((x for x in ev["entrants"] if x["id"] == src), None) if src else None
            row.update(cluster=ev["source_game_id"], source_game_id=ev["source_game_id"], start=ev["start"],
                       participant_source=src)
            if src is None:
                row.update(status="excluded", reason=preasons.get(s["participant"], "competitor crosswalk unresolved"))
            elif ent is None:
                row.update(status="excluded", reason="competitor not in source field (withdrawal/void)")
            elif not ev["completed"]:
                row.update(status="excluded", reason="source event not completed")
            else:
                if s["kind"] == "field_win":
                    payoff = int(bool(ent["won"]))
                else:
                    payoff = payoff_field(s, ent["position"])
                row["independent_payoff"] = payoff
                if s["kalshi_outcome"] == "void":
                    row.update(status="excluded", reason="Kalshi void / fair-price settlement")
                else:
                    row.update(status="ok", agree=int(payoff == (1 if s["kalshi_outcome"] == "yes" else 0)))
            contracts.append(row)
    write_json(CROSSWALK / f"{comp.key}_games.json", {"matched": ev_match})
    return {"contracts": contracts, "events": events, "participants": len(pmap)}


# --------------------------------------------------------------------------------------------
# Kalshi-settlement-only two-way competitions
# --------------------------------------------------------------------------------------------

def build_kalshi_only(comp: Competition, specs: list[dict]) -> dict:
    by_event: dict[str, list[dict]] = collections.defaultdict(list)
    for s in specs:
        by_event[s["event_ticker"]].append(s)
    contracts, events = [], []
    for ev, rows in sorted(by_event.items()):
        parts = sorted({r["participant"] for r in rows if r.get("participant")})
        ms = rows[0]["milestone_id"]
        start = rows[0]["kalshi_start"]
        reason = None
        if any(r.get("tie") for r in rows) or len(rows) != 2 or len(parts) != 2:
            reason = "not a two-market two-participant event (three-way or incomplete)"
        yes = [r for r in rows if r["kalshi_outcome"] == "yes"]
        void = any(r["kalshi_outcome"] == "void" for r in rows)
        if reason is None and void:
            reason = "Kalshi void / fair-price settlement"
        elif reason is None and len(yes) != 1:
            reason = "Kalshi settlement not exactly one winner"
        avail = max((r["settlement_ts"] for r in rows if r.get("settlement_ts")), default=None)
        if reason is None:
            events.append({"source_game_id": f"kalshi:{ms or ev}", "start": start, "completed": True,
                           "home": parts[0], "away": parts[1], "winner_id": yes[0]["participant"],
                           "available_at": avail, "neutral": True, "sport": comp.sport})
        for r in rows:
            row = dict(r)
            row.update(cluster=f"kalshi:{ms or ev}", source_game_id=None, start=start,
                       home=parts[0] if len(parts) == 2 else None, away=parts[1] if len(parts) == 2 else None)
            if reason:
                row.update(status="excluded", reason=reason)
            else:
                row.update(status="ok", independent_payoff=None, agree=None, label_source="kalshi_settlement")
            contracts.append(row)
    return {"contracts": contracts, "events": events, "participants": len({p for e in events for p in (e["home"], e["away"])})}


def build_competition(comp: Competition, series_specs: list[tuple], fetcher: Fetcher,
                      milestones: list[dict], mindex: MilestoneIndex) -> dict:
    markets, dedupe = load_kalshi_markets(comp, series_specs, fetcher)
    specs, excluded = parse_contracts(comp, markets, mindex)
    if comp.source == "espn_team":
        res = build_team_competition(comp, specs, fetcher)
    elif comp.source == "espn_fight":
        res = build_fight_competition(comp, specs, fetcher)
    elif comp.source in ("espn_golf", "jolpica"):
        res = build_field_competition(comp, specs, fetcher, milestones)
    else:
        res = build_kalshi_only(comp, specs)
    contracts = res["contracts"] + [{**e, "status": "excluded"} for e in excluded]
    out_dir = NORMALIZED / comp.key
    h_contracts = write_jsonl(out_dir / "contracts.jsonl", sorted(contracts, key=lambda r: r["ticker"]))
    h_events = write_jsonl(out_dir / "events.jsonl", res["events"])
    summary = {
        "competition": comp.key, "sport": comp.sport, "source": comp.source,
        "markets": len(markets), "dedupe": dedupe,
        "contracts_ok": sum(1 for c in contracts if c["status"] == "ok"),
        "contracts_excluded": collections.Counter(c.get("reason") for c in contracts if c["status"] != "ok"),
        "events": len(res["events"]), "participants": res.get("participants"),
        "kalshi_games": res.get("kalshi_games"), "matched_games": res.get("matched_games"),
        "crosswalk_conflicts": res.get("crosswalk_conflicts", []),
        "hash_contracts": h_contracts, "hash_events": h_events,
    }
    write_json(out_dir / "build_summary.json", summary)
    return summary

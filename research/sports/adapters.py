"""Source adapters: fetch raw bytes through :class:`core.Fetcher`, parse into plain dict records.

Adapters never invent identifiers: every record keeps the source's own ID (Kalshi ticker /
milestone UUID / structured-target UUID, ESPN event and team IDs, Jolpica race and driver IDs).
Parsing is pure (raw bytes in, records out) so fixtures can exercise it offline.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Iterable

from .core import DISCOVERY_RUN, Fetcher, Raw, parse_ts, iso

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/"
JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1/"
NFLVERSE_GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"


# --------------------------------------------------------------------------------------------
# Kalshi
# --------------------------------------------------------------------------------------------

def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_kalshi_series_markets(fetcher: Fetcher, series: str) -> tuple[list[dict], dict]:
    """All markets of a series from the live and historical tiers (raw dicts + pagination stats)."""
    out: list[dict] = []
    stats: dict[str, Any] = {"series": series}
    for tier, path in (("live", "/markets"), ("historical", "/historical/markets")):
        cursor, pages, complete = None, 0, False
        while True:
            params = {"series_ticker": series, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            raw = fetcher.get("kalshi", KALSHI_BASE + path, params)
            pages += 1
            if not raw.ok:
                stats[f"{tier}_error"] = raw.status
                break
            body = raw.json()
            for m in body.get("markets") or []:
                m = dict(m)
                m["_tier"] = tier
                out.append(m)
            cursor = body.get("cursor")
            if not cursor:
                complete = True
                break
        stats[f"{tier}_pages"] = pages
        stats[f"{tier}_complete"] = complete
    return out, stats


def normalize_kalshi_markets(raw_markets: Iterable[dict]) -> tuple[list[dict], dict]:
    """Deduplicate live/historical copies by ticker; keep the settled copy and note both tiers."""
    by_ticker: dict[str, dict] = {}
    tiers: dict[str, set] = {}
    for m in raw_markets:
        t = m["ticker"]
        tiers.setdefault(t, set()).add(m["_tier"])
        prev = by_ticker.get(t)
        if prev is None or (_settled(m) and not _settled(prev)) or (
                _settled(m) == _settled(prev) and m["_tier"] == "historical"):
            by_ticker[t] = m
    rows = []
    for t in sorted(by_ticker):
        m = by_ticker[t]
        rows.append({
            "ticker": t,
            "event_ticker": m.get("event_ticker"),
            "series_ticker": t.split("-")[0],
            "title": m.get("title"),
            "yes_sub_title": m.get("yes_sub_title"),
            "rules_primary": m.get("rules_primary"),
            "strike_type": m.get("strike_type"),
            "floor_strike": _num(m.get("floor_strike")),
            "cap_strike": _num(m.get("cap_strike")),
            "custom_strike": m.get("custom_strike"),
            "market_type": m.get("market_type"),
            "status": m.get("status"),
            "result": m.get("result"),
            "settlement_value": _num(m.get("settlement_value_dollars")),
            "open_time": m.get("open_time"),
            "close_time": m.get("close_time"),
            "settlement_ts": m.get("settlement_ts"),
            "occurrence_datetime": m.get("occurrence_datetime"),
            "volume_fp": _num(m.get("volume_fp")),
            "tier": m["_tier"],
            "tiers_seen": sorted(tiers[t]),
        })
    overlap = sum(1 for t in tiers if len(tiers[t]) > 1)
    return rows, {"unique_tickers": len(rows), "raw_rows": sum(len(v) for v in tiers.values()),
                  "tickers_in_both_tiers": overlap}


def _settled(m: dict) -> bool:
    return m.get("status") in ("finalized", "settled") and m.get("result") not in (None, "")


def kalshi_settlement_outcome(m: dict) -> tuple[str, float | None]:
    """('yes'|'no'|'void'|'unsettled', value). Non-binary settlements are fair-price voids."""
    if m.get("status") not in ("finalized", "settled"):
        return "unsettled", None
    res = (m.get("result") or "").lower()
    val = m.get("settlement_value")
    if res == "yes" and (val is None or val >= 0.999):
        return "yes", 1.0
    if res == "no" and (val is None or val <= 0.001):
        return "no", 0.0
    return "void", val


def load_milestones(run_dir=DISCOVERY_RUN) -> list[dict]:
    """Reuse the audit's milestone crawl (no re-crawl)."""
    rows = json.loads((run_dir / "milestones_sports.json").read_text(encoding="utf-8"))["rows"]
    out = []
    for m in rows:
        d = m.get("details") or {}
        home = d.get("home_team_id") or d.get("home_competitor_id") or d.get("first_competitor_id") \
            or d.get("first_fighter_id") or d.get("home_player_id")
        away = d.get("away_team_id") or d.get("away_competitor_id") or d.get("second_competitor_id") \
            or d.get("second_fighter_id") or d.get("away_player_id")
        out.append({
            "milestone_id": m["id"],
            "type": m.get("type"),
            "league": d.get("league"),
            "title": m.get("title"),
            "start": m.get("start_date"),
            "end": m.get("end_date"),
            "home_id": home,
            "away_id": away,
            "competitor_ids": d.get("competitor_ids"),
            "event_tickers": sorted(set((m.get("primary_event_tickers") or []) + (m.get("related_event_tickers") or []))),
            "source_ids": m.get("source_ids") or {},
            "status": d.get("status"),
        })
    return out


@lru_cache(maxsize=1)
def load_structured_targets(run_dir=DISCOVERY_RUN) -> dict[str, dict]:
    rows = json.loads((run_dir / "structured_targets_all.json").read_text(encoding="utf-8"))["rows"]
    keep = {"basketball_team", "football_team", "baseball_team", "hockey_team", "soccer_team",
            "golf_competitor", "racing_competitor", "ufc_competitor", "tennis_competitor",
            "tennis_doubles_competitor", "table_tennis_competitor", "esports_competitor", "cricket_team",
            "volleyball_team", "boxing_competitor"}
    out = {}
    for x in rows:
        if x.get("type") not in keep:
            continue
        d = x.get("details") or {}
        out[x["id"]] = {"id": x["id"], "type": x["type"], "name": x.get("name"),
                        "abbreviation": d.get("abbreviation"), "market": d.get("market"),
                        "league": d.get("league"), "first_name": d.get("first_name"),
                        "last_name": d.get("last_name"), "short_name": d.get("short_name")}
    return out


def kalshi_candles(fetcher: Fetcher, series: str, ticker: str, start_ts: int, end_ts: int,
                   historical: bool) -> tuple[list[dict], int]:
    if historical:
        url = f"{KALSHI_BASE}/historical/markets/{ticker}/candlesticks"
    else:
        url = f"{KALSHI_BASE}/series/{series}/markets/{ticker}/candlesticks"
    raw = fetcher.get("kalshi", url, {"start_ts": start_ts, "end_ts": end_ts, "period_interval": 60})
    if not raw.ok:
        return [], raw.status
    return raw.json().get("candlesticks") or [], raw.status


# --------------------------------------------------------------------------------------------
# ESPN site API (undocumented; cached byte-exact)
# --------------------------------------------------------------------------------------------

def _months(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def espn_requests(path: str, mode: str, start: date, end: date, groups: str | None = None,
                  months_active: tuple[int, ...] | None = None, weekdays: tuple[int, ...] | None = None,
                  limit: int = 1000) -> list[tuple[str, dict]]:
    """Scoreboard requests. ``groups`` may be comma-separated (one request per group).

    College football ignores ``groups`` when ``limit`` is 1000 (it returns the top-25 default),
    so that competition uses a smaller limit.
    """
    url = f"{ESPN_BASE}{path}/scoreboard"
    glist = [g.strip() for g in groups.split(",")] if groups else [None]
    keys = []
    if mode == "month":
        keys = [ym for ym in _months(start, end) if not months_active or int(ym[4:]) in months_active]
    else:
        d = start
        while d <= end:
            if (not months_active or d.month in months_active) and (not weekdays or d.weekday() in weekdays):
                keys.append(d.strftime("%Y%m%d"))
            d += timedelta(days=1)
    reqs = []
    for k in keys:
        for g in glist:
            p = {"dates": k, "limit": limit}
            if g:
                p["groups"] = g
            reqs.append((url, p))
    return reqs


def _int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_espn_team_games(raw: Raw, league_path: str) -> list[dict]:
    if not raw.ok:
        return []
    games = []
    for e in raw.json().get("events") or []:
        for c in e.get("competitions") or []:
            comps = c.get("competitors") or []
            if len(comps) != 2:
                continue
            side = {}
            for x in comps:
                t = x.get("team") or {}
                side[x.get("homeAway")] = {
                    "espn_team_id": str(t.get("id")) if t.get("id") is not None else None,
                    "abbr": t.get("abbreviation"), "name": t.get("displayName"),
                    "location": t.get("location"), "short": t.get("shortDisplayName"),
                    "score": _int(x.get("score")), "winner": x.get("winner"),
                    "periods": [_int(ls.get("value")) for ls in (x.get("linescores") or [])],
                }
            if set(side) != {"home", "away"}:
                continue
            status = c.get("status") or e.get("status") or {}
            st = status.get("type") or {}
            season = e.get("season") or {}
            no_et = None
            if league_path.startswith("soccer/"):
                no_et = st.get("name") in ("STATUS_FULL_TIME", "STATUS_FINAL") and (status.get("period") or 0) <= 2
                halves = _soccer_halves(c, comps, side)
                if halves:
                    side["home"]["periods"], side["away"]["periods"] = halves
            games.append({
                "no_extra_time": no_et,
                "source": "espn", "league_path": league_path, "espn_id": str(c.get("id") or e.get("id")),
                "start": iso(parse_ts(c.get("date") or e.get("date"))),
                "status": st.get("name"), "completed": bool(st.get("completed")),
                "neutral": bool(c.get("neutralSite")), "season_year": season.get("year"),
                "season_type": season.get("type"), "home": side["home"], "away": side["away"],
            })
    return games


_MINUTE = re.compile(r"^(\d+)'(?:\+(\d+)')?")


def _soccer_halves(c: dict, comps: list, side: dict) -> tuple[list, list] | None:
    """[1H, 2H(, ET)] goals per side from timed scoring plays; None unless they sum to the final score."""
    team_side = {str((x.get("team") or {}).get("id")): x.get("homeAway") for x in comps}
    goals = {"home": [0, 0, 0], "away": [0, 0, 0]}
    for d in c.get("details") or []:
        if not d.get("scoringPlay") or d.get("shootout"):
            continue
        m = _MINUTE.match(((d.get("clock") or {}).get("displayValue") or "").strip())
        s = team_side.get(str((d.get("team") or {}).get("id")))
        if not m or s not in goals:
            return None
        base = int(m.group(1))
        goals[s][0 if base <= 45 else 1 if base <= 90 else 2] += int(d.get("scoreValue") or 1)
    if sum(goals["home"]) != side["home"]["score"] or sum(goals["away"]) != side["away"]["score"]:
        return None
    n = 3 if goals["home"][2] or goals["away"][2] else 2
    return goals["home"][:n], goals["away"][:n]


def parse_espn_fights(raw: Raw, league_path: str) -> list[dict]:
    if not raw.ok:
        return []
    fights = []
    for e in raw.json().get("events") or []:
        for c in e.get("competitions") or []:
            comps = c.get("competitors") or []
            if len(comps) != 2:
                continue
            st = c.get("status") or {}
            stype = st.get("type") or {}
            periods = ((c.get("format") or {}).get("regulation") or {}).get("periods")
            fighters = []
            for x in sorted(comps, key=lambda x: x.get("order") or 0):
                a = x.get("athlete") or {}
                fighters.append({"espn_athlete_id": str(x.get("id")), "name": a.get("displayName") or a.get("fullName"),
                                 "winner": x.get("winner")})
            completed = bool(stype.get("completed"))
            method = None
            if completed and periods:
                ended_period, clock = st.get("period"), st.get("clock")
                if ended_period == periods and clock is not None and float(clock) >= 300.0:
                    method = "distance"
                elif ended_period is not None:
                    method = "finish"
            fights.append({
                "source": "espn", "league_path": league_path, "espn_id": str(c.get("id")),
                "event_name": e.get("name"), "start": iso(parse_ts(c.get("date") or e.get("date"))),
                "status": stype.get("name"), "completed": completed,
                "weight_class": (c.get("type") or {}).get("abbreviation"), "scheduled_rounds": periods,
                "ended_round": st.get("period"), "ended_clock": st.get("clock"), "method": method,
                "fighters": fighters,
            })
    return fights


def parse_espn_golf(raw: Raw, league_path: str) -> list[dict]:
    if not raw.ok:
        return []
    events = []
    for e in raw.json().get("events") or []:
        st = (e.get("status") or {}).get("type") or {}
        for c in e.get("competitions") or []:
            players = []
            for x in c.get("competitors") or []:
                a = x.get("athlete") or {}
                rounds = [r for r in (x.get("linescores") or []) if r.get("value") is not None]
                players.append({"espn_athlete_id": str(x.get("id")), "name": a.get("displayName"),
                                "order": x.get("order"), "to_par": _to_par(x.get("score")),
                                "rounds_played": len(rounds)})
            if not players:
                continue
            # Playoff holes can appear as an extra "round" for the tied leaders only.
            max_rounds = min(max(p["rounds_played"] for p in players), 4)
            finishers = [p for p in players if p["rounds_played"] >= max_rounds and p["to_par"] is not None]
            for p in players:
                if p in finishers:
                    p["position"] = 1 + sum(1 for q in finishers if q["to_par"] < p["to_par"])
                else:
                    p["position"] = None
                p["made_cut"] = p in finishers
            winner = min(players, key=lambda p: p["order"] or 10 ** 6)
            events.append({"source": "espn", "league_path": league_path, "espn_id": str(e.get("id")),
                           "name": e.get("name"), "start": iso(parse_ts(e.get("date"))),
                           "end": iso(parse_ts(e.get("endDate"))) if e.get("endDate") else None,
                           "status": st.get("name"), "completed": bool(st.get("completed")),
                           "max_rounds": max_rounds, "winner_athlete_id": winner["espn_athlete_id"],
                           "players": players})
    return events


def _to_par(v: Any) -> int | None:
    if v in (None, "", "-"):
        return None
    s = str(v).strip()
    if s.upper() == "E":
        return 0
    try:
        return int(s.replace("+", ""))
    except ValueError:
        return None


# --------------------------------------------------------------------------------------------
# Jolpica (Ergast-compatible) F1 results
# --------------------------------------------------------------------------------------------

def fetch_jolpica_season(fetcher: Fetcher, year: int) -> list[Raw]:
    out, offset = [], 0
    while True:
        raw = fetcher.get("jolpica", f"{JOLPICA_BASE}{year}/results.json", {"limit": 100, "offset": offset})
        out.append(raw)
        if not raw.ok:
            break
        mr = raw.json()["MRData"]
        offset += int(mr["limit"])
        if offset >= int(mr["total"]):
            break
    return out


def parse_jolpica(raws: list[Raw]) -> list[dict]:
    races: dict[tuple, dict] = {}
    for raw in raws:
        if not raw.ok:
            continue
        for r in raw.json()["MRData"]["RaceTable"]["Races"]:
            key = (int(r["season"]), int(r["round"]))
            race = races.setdefault(key, {
                "source": "jolpica", "season": key[0], "round": key[1], "race_id": f"{key[0]}-{key[1]:02d}",
                "name": r["raceName"], "circuit": (r.get("Circuit") or {}).get("circuitId"),
                "start": iso(parse_ts(f"{r['date']}T{r.get('time', '12:00:00Z')}")), "results": []})
            for x in r.get("Results") or []:
                d = x["Driver"]
                race["results"].append({
                    "driver_id": d["driverId"], "name": f"{d.get('givenName', '')} {d.get('familyName', '')}".strip(),
                    "position": _int(x.get("position")), "position_text": x.get("positionText"),
                    "grid": _int(x.get("grid")), "status": x.get("status"),
                    "classified": str(x.get("positionText", "")).isdigit()})
    return [races[k] for k in sorted(races)]


# --------------------------------------------------------------------------------------------
# nflverse games (closing lines; used only for the separate closing-line comparison)
# --------------------------------------------------------------------------------------------

def parse_nflverse_games(raw: Raw) -> list[dict]:
    if not raw.ok:
        return []
    rows = []
    for r in csv.DictReader(io.StringIO(raw.body.decode("utf-8"))):
        rows.append({"game_id": r["game_id"], "season": _int(r["season"]), "gameday": r["gameday"],
                     "gametime": r.get("gametime"), "home_team": r["home_team"], "away_team": r["away_team"],
                     "home_score": _int(r.get("home_score")), "away_score": _int(r.get("away_score")),
                     "home_moneyline": _int(r.get("home_moneyline")), "away_moneyline": _int(r.get("away_moneyline")),
                     "spread_line": _num(r.get("spread_line")), "total_line": _num(r.get("total_line")),
                     "espn": r.get("espn")})
    return rows

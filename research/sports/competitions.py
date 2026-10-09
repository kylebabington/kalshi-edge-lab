"""Competition registry: which Kalshi series are evaluated, against which independent source.

Contract specifications are parsed from the series-ticker suffix after the competition prefix
(``KXNBA`` + ``1HTOTAL``) and then validated against each market's strike fields and rules text
(``contracts.py``). Suffixes without a parser become explicit exclusions, never guesses.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .core import REPO_ROOT

INVENTORY_CSV = REPO_ROOT / "docs" / "research" / "sports_inventory_v1.csv"

# Period indexes (1-based ESPN linescores) per segment code.
BASKETBALL_Q = {"1H": (1, 2), "2H": (3, 4), "Q1": (1,), "Q2": (2,), "Q3": (3,), "Q4": (4,)}
BASKETBALL_HALVES = {"1H": (1,), "2H": (2,)}
FOOTBALL_Q = BASKETBALL_Q
HOCKEY_P = {"P1": (1,), "P2": (2,), "P3": (3,)}
BASEBALL_F = {"F3": (1, 2, 3), "F5": (1, 2, 3, 4, 5), "F7": (1, 2, 3, 4, 5, 6, 7)}
SOCCER_H = {"1H": (1,), "2H": (2,)}

# Ordered suffix rules: regex on the ticker suffix -> (contract kind, segment code or None).
SUFFIX_RULES: list[tuple[str, str, str | None]] = [
    (r"GAME", "winner", None),
    (r"SPREAD", "spread", None),
    (r"TOTAL", "total", None),
    (r"TEAMTOTAL", "team_total", None),
    (r"BTTS", "btts", None),
    (r"(?P<seg>[12]H)(WINNER)?", "winner", "{seg}"),
    (r"(?P<seg>[12]H)SPREAD", "spread", "{seg}"),
    (r"(?P<seg>[12]H)TOTAL", "total", "{seg}"),
    (r"(?P<seg>[12]H)TEAMTOTAL", "team_total", "{seg}"),
    (r"(?P<seg>1H)BTTS", "btts", "{seg}"),
    (r"(?P<n>[1-4])Q(WINNER)?", "winner", "Q{n}"),
    (r"(?P<n>[1-4])QSPREAD", "spread", "Q{n}"),
    (r"(?P<n>[1-4])QTOTAL", "total", "Q{n}"),
    (r"(?P<n>[1-3])P", "winner", "P{n}"),
    (r"(?P<n>[1-3])PSPREAD", "spread", "P{n}"),
    (r"(?P<n>[1-3])PTOTAL", "total", "P{n}"),
    (r"F(?P<n>[357])", "winner", "F{n}"),
    (r"F(?P<n>5)SPREAD", "spread", "F{n}"),
    (r"F(?P<n>5)TOTAL", "total", "F{n}"),
]


def parse_suffix(suffix: str) -> tuple[str, str | None] | None:
    for rx, kind, seg in SUFFIX_RULES:
        m = re.fullmatch(rx, suffix)
        if m:
            return kind, (seg.format(**m.groupdict()) if seg else None)
    return None


@dataclass(frozen=True)
class Competition:
    key: str
    sport: str
    name: str
    source: str                       # espn_team | espn_fight | espn_golf | jolpica | kalshi_only
    prefix: str | None = None
    kalshi_competitions: tuple[str, ...] = ()
    explicit_series: tuple[tuple[str, str, str | None], ...] = ()   # (series, kind, segment)
    espn_path: str | None = None
    espn_mode: str = "month"
    espn_groups: str | None = None
    espn_limit: int = 1000
    months_active: tuple[int, ...] | None = None
    weekdays: tuple[int, ...] | None = None
    history_start: date = date(2022, 7, 1)
    score_model: str | None = None    # normal | poisson | negbin
    three_way: bool = False           # winner market includes a Tie outcome at full time
    result_lag_hours: float = 4.0     # conservative: start + lag before a result may be used
    segments: dict = field(default_factory=dict)
    exclude_season_types: tuple[int, ...] = ()   # ESPN season types not used (1 = preseason)
    regulation_periods: tuple[int, ...] | None = None  # soccer: score after 90 min from halves


def _soccer(key, espn, prefix, comps, name):
    return Competition(key=key, sport="Soccer", name=name, source="espn_team", prefix=prefix,
                       kalshi_competitions=comps, espn_path=f"soccer/{espn}", score_model="poisson",
                       three_way=True, result_lag_hours=3.0, segments=SOCCER_H, regulation_periods=(1, 2))


ESPN_COMPETITIONS: list[Competition] = [
    Competition("nba", "Basketball", "NBA", "espn_team", "KXNBA", ("Pro Basketball (M)",),
                espn_path="basketball/nba", months_active=(10, 11, 12, 1, 2, 3, 4, 5, 6),
                score_model="normal", result_lag_hours=3.5, segments=BASKETBALL_Q, exclude_season_types=(1,)),
    Competition("wnba", "Basketball", "WNBA", "espn_team", "KXWNBA", ("Pro Basketball (W)",),
                espn_path="basketball/wnba", months_active=(5, 6, 7, 8, 9, 10),
                score_model="normal", result_lag_hours=3.5, segments=BASKETBALL_Q, exclude_season_types=(1,)),
    Competition("ncaamb", "Basketball", "NCAA men's basketball", "espn_team", "KXNCAAMB", ("College Basketball (M)",),
                espn_path="basketball/mens-college-basketball", espn_mode="day", espn_groups="50",
                months_active=(11, 12, 1, 2, 3, 4), score_model="normal", result_lag_hours=3.5,
                segments=BASKETBALL_HALVES),
    Competition("ncaawb", "Basketball", "NCAA women's basketball", "espn_team", "KXNCAAWB", ("College Basketball (W)",),
                espn_path="basketball/womens-college-basketball", espn_mode="day", espn_groups="50",
                months_active=(11, 12, 1, 2, 3, 4), score_model="normal", result_lag_hours=3.5,
                segments=BASKETBALL_Q),
    Competition("nfl", "Football", "NFL", "espn_team", "KXNFL", ("Pro Football",),
                espn_path="football/nfl", months_active=(8, 9, 10, 11, 12, 1, 2), score_model="normal",
                result_lag_hours=4.5, segments=FOOTBALL_Q, exclude_season_types=(1,)),
    Competition("ncaaf", "Football", "NCAA football", "espn_team", "KXNCAAF", ("NCAA Football",),
                espn_path="football/college-football", espn_mode="day", espn_groups="80,81", espn_limit=300,
                months_active=(8, 9, 10, 11, 12, 1), score_model="normal", result_lag_hours=4.5,
                segments=FOOTBALL_Q),
    Competition("mlb", "Baseball", "MLB", "espn_team", "KXMLB", ("Pro Baseball",),
                espn_path="baseball/mlb", months_active=(2, 3, 4, 5, 6, 7, 8, 9, 10, 11), score_model="negbin",
                result_lag_hours=5.0, segments=BASEBALL_F, exclude_season_types=(1,)),
    Competition("nhl", "Hockey", "NHL", "espn_team", "KXNHL", ("Pro Hockey",),
                espn_path="hockey/nhl", months_active=(9, 10, 11, 12, 1, 2, 3, 4, 5, 6), score_model="poisson",
                result_lag_hours=4.0, segments=HOCKEY_P, exclude_season_types=(1,)),
    _soccer("epl", "eng.1", "KXEPL", ("EPL",), "English Premier League"),
    _soccer("laliga", "esp.1", "KXLALIGA", ("La Liga",), "La Liga"),
    _soccer("seriea", "ita.1", "KXSERIEA", ("Serie A",), "Serie A"),
    _soccer("bundesliga", "ger.1", "KXBUNDESLIGA", ("Bundesliga",), "Bundesliga"),
    _soccer("ligue1", "fra.1", "KXLIGUE1", ("Ligue 1",), "Ligue 1"),
    _soccer("mls", "usa.1", "KXMLS", ("MLS",), "MLS"),
    _soccer("eflc", "eng.2", "KXEFLCHAMPIONSHIP", ("EFL Championship",), "EFL Championship"),
    _soccer("ligamx", "mex.1", "KXLIGAMX", ("Liga MX",), "Liga MX"),
    _soccer("eredivisie", "ned.1", "KXEREDIVISIE", ("Eredivisie",), "Eredivisie"),
    _soccer("ligaportugal", "por.1", "KXLIGAPORTUGAL", ("Liga Portugal",), "Liga Portugal"),
    _soccer("brasileiro", "bra.1", "KXBRASILEIRO", ("Brasileiro Serie A", "Brasileiro"), "Brasileiro Serie A"),
    _soccer("argentina", "arg.1", "KXARGPREMDIV", ("Argentina Primera Division",), "Argentina Primera Division"),
    _soccer("saudi", "ksa.1", "KXSAUDIPL", ("Saudi Pro League",), "Saudi Pro League"),
    _soccer("ucl", "uefa.champions", "KXUCL", ("Champions League",), "UEFA Champions League"),
    _soccer("uel", "uefa.europa", "KXUEL", ("Europa League",), "UEFA Europa League"),
    _soccer("uecl", "uefa.europa.conf", "KXUECL", ("Conference League",), "UEFA Conference League"),
    _soccer("bundesliga2", "ger.2", "KXBUNDESLIGA2", ("Bundesliga 2",), "2. Bundesliga"),
    Competition("ufc", "MMA", "UFC", "espn_fight", explicit_series=(("KXUFCFIGHT", "winner", None),
                                                                     ("KXUFCDISTANCE", "distance", None)),
                espn_path="mma/ufc", result_lag_hours=8.0),
    Competition("pga", "Golf", "PGA Tour", "espn_golf",
                explicit_series=(("KXPGATOUR", "field_win", None), ("KXPGATOP5", "field_top", "5"),
                                 ("KXPGATOP10", "field_top", "10"), ("KXPGATOP20", "field_top", "20")),
                espn_path="golf/pga", espn_mode="day", weekdays=(6,), result_lag_hours=12.0),
    Competition("f1", "Motorsport", "Formula 1", "jolpica",
                explicit_series=(("KXF1RACE", "field_win", None), ("KXF1RACEPODIUM", "field_top", "3"),
                                 ("KXF1TOP5", "field_top", "5"), ("KXF1TOP10", "field_top", "10")),
                result_lag_hours=4.0),
]


def load_inventory() -> list[dict]:
    with open(INVENTORY_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def espn_series_specs(comp: Competition, inventory: list[dict]) -> tuple[list[tuple[str, str, str | None]], list[dict]]:
    """(supported series specs, excluded series with reasons) for a prefix-based competition."""
    if comp.explicit_series:
        return list(comp.explicit_series), []
    supported, excluded = [], []
    for row in inventory:
        t = row["series_ticker"]
        if not t.startswith(comp.prefix) or row["competition_top"] not in comp.kalshi_competitions:
            continue
        if row["game_level"] not in ("True", "true", "1") and row["contract_family"] not in ("game_winner",):
            continue
        suffix = t[len(comp.prefix):]
        parsed = parse_suffix(suffix)
        if parsed is None:
            excluded.append({"series": t, "family": row["contract_family"], "reason": f"no payoff parser for suffix '{suffix}'"})
            continue
        kind, seg = parsed
        if seg and seg not in comp.segments:
            excluded.append({"series": t, "family": row["contract_family"], "reason": f"segment {seg} not defined for {comp.key}"})
            continue
        if kind == "btts" and comp.score_model != "poisson":
            excluded.append({"series": t, "family": row["contract_family"], "reason": "BTTS only modelled for soccer"})
            continue
        supported.append((t, kind, seg))
    return sorted(supported), excluded


def kalshi_only_competitions(inventory: list[dict], claimed: set[str]) -> list[Competition]:
    """Every remaining two-way game-winner series becomes a Kalshi-settlement-only competition."""
    out = []
    for row in inventory:
        t = row["series_ticker"]
        if t in claimed or row["contract_family"] != "game_winner" or row["sport"].startswith(("Non-sport", "Unassigned")):
            continue
        if int(float(row["traded_markets"] or 0)) == 0:
            continue
        out.append(Competition(key=f"k_{t.lower()}", sport=row["sport"], name=f"{row['series_title']} ({t})",
                               source="kalshi_only", explicit_series=((t, "winner", None),),
                               kalshi_competitions=(row["competition_top"],)))
    return out


def all_competitions(inventory: list[dict] | None = None) -> tuple[list[Competition], dict[str, list[tuple]], dict[str, list[dict]]]:
    inventory = inventory if inventory is not None else load_inventory()
    specs, excl = {}, {}
    claimed: set[str] = set()
    for c in ESPN_COMPETITIONS:
        s, e = espn_series_specs(c, inventory)
        specs[c.key], excl[c.key] = s, e
        claimed |= {x[0] for x in s} | {x["series"] for x in e}
    ko = kalshi_only_competitions(inventory, claimed)
    for c in ko:
        specs[c.key], excl[c.key] = list(c.explicit_series), []
    return ESPN_COMPETITIONS + ko, specs, excl

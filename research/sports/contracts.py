"""Contract adapters: Kalshi market -> payoff specification -> payoff on an observed outcome.

A spec is only produced when the market's strike fields and rules text agree with the parser's
assumptions; anything else returns an exclusion reason. Payoffs are computed from the
independent outcome record (scores, period scores, finishing positions, fight method), never
from Kalshi settlement, so the two can be reconciled.
"""

from __future__ import annotations

import re

FAMILY_OF_KIND = {"winner": "game_winner", "spread": "spread", "total": "total", "team_total": "team_total",
                  "btts": "score_props", "distance": "score_props", "field_win": "field_finish",
                  "field_top": "field_finish"}


def family(kind: str, segment: str | None) -> str:
    if segment and kind in ("winner", "spread", "total", "team_total", "btts"):
        return "segment"
    return FAMILY_OF_KIND[kind]


def _uuid(custom_strike) -> str | None:
    if not isinstance(custom_strike, dict) or len(custom_strike) != 1:
        return None
    v = next(iter(custom_strike.values()))
    return v if isinstance(v, str) and len(v) >= 32 else None


def is_tie_market(m: dict) -> bool:
    return (m.get("yes_sub_title") or "").strip().lower() in ("tie", "draw") or m["ticker"].endswith("-TIE")


def parse_market(m: dict, kind: str, segment: str | None, *, sport: str, top_n: str | None = None
                 ) -> tuple[dict | None, str | None]:
    rules = (m.get("rules_primary") or "").lower()
    base = {"ticker": m["ticker"], "event_ticker": m["event_ticker"], "series": m["series_ticker"],
            "kind": kind, "segment": segment, "family": family(kind, segment)}
    if sport == "Soccer" and kind in ("winner", "spread", "total", "team_total", "btts") and segment is None:
        if "90 minutes" not in rules:
            return None, "soccer full-time scope not stated as 90 minutes"
    if kind == "winner":
        if m.get("strike_type") not in ("structured", None, ""):
            return None, f"winner market with strike_type {m.get('strike_type')}"
        if is_tie_market(m):
            return {**base, "tie": True, "participant": None, "strike": None}, None
        uid = _uuid(m.get("custom_strike"))
        if not uid:
            return None, "winner market without a single structured participant"
        return {**base, "tie": False, "participant": uid, "strike": None}, None
    if kind in ("spread", "total", "team_total"):
        st = m.get("strike_type")
        ok = st == "greater" or (st == "structured" and re.search(r"\bover\b|\bmore than\b|\bby over\b", rules))
        if not ok or m.get("floor_strike") is None:
            return None, f"{kind} market strike_type={st} not a supported 'over floor' contract"
        uid = _uuid(m.get("custom_strike"))
        if kind in ("spread", "team_total") and not uid:
            return None, f"{kind} market without a structured team"
        return {**base, "tie": False, "participant": uid, "strike": m["floor_strike"]}, None
    if kind == "btts":
        if "both" not in rules or "score" not in rules:
            return None, "BTTS rules text not recognised"
        return {**base, "tie": False, "participant": None, "strike": None}, None
    if kind == "distance":
        if "distance" not in rules and "distance" not in (m.get("title") or "").lower():
            return None, "distance rules text not recognised"
        return {**base, "tie": False, "participant": None, "strike": None}, None
    if kind in ("field_win", "field_top"):
        uid = _uuid(m.get("custom_strike"))
        if not uid:
            return None, "field market without a structured competitor"
        n = 1 if kind == "field_win" else int(top_n)
        if kind == "field_win" and not re.search(r"\bwins?\b|first", rules):
            return None, "field winner rules text not recognised"
        if kind == "field_top" and not re.search(rf"top {n}\b|top-{n}\b|podium|first {n}\b|{n} or better|top {n} ", rules):
            return None, f"top-{n} rules text not recognised"
        return {**base, "tie": False, "participant": uid, "strike": None, "top_n": n}, None
    return None, f"unknown kind {kind}"


def _segment_scores(outcome: dict, periods: tuple[int, ...]) -> tuple[int, int] | None:
    hp, ap = outcome.get("home_periods") or [], outcome.get("away_periods") or []
    need = max(periods)
    if len(hp) < need or len(ap) < need:
        return None
    try:
        return sum(hp[i - 1] for i in periods), sum(ap[i - 1] for i in periods)
    except TypeError:
        return None


def team_scores(outcome: dict, segment: str | None, segments: dict, regulation: tuple[int, ...] | None
                ) -> tuple[int, int] | None:
    """(home, away) points for the full game, the regulation window, or a segment."""
    if segment:
        return _segment_scores(outcome, segments[segment])
    if regulation:
        sc = _segment_scores(outcome, regulation)
        if sc is None and outcome.get("no_extra_time") and outcome.get("home_score") is not None:
            return outcome["home_score"], outcome["away_score"]
        return sc
    if outcome.get("home_score") is None or outcome.get("away_score") is None:
        return None
    return outcome["home_score"], outcome["away_score"]


def payoff_team_game(spec: dict, outcome: dict, side_of: dict[str, str], segments: dict,
                     regulation: tuple[int, ...] | None) -> int | None:
    """1/0 payoff of a team-sport contract from the independent outcome; None if not derivable.

    ``side_of`` maps Kalshi participant UUIDs to 'home'/'away' of the independent record.
    """
    seg = spec.get("segment")
    if spec["kind"] == "winner" and seg is None and regulation is None:
        if spec.get("tie"):
            if outcome.get("home_score") is None:
                return None
            return int(outcome["home_score"] == outcome["away_score"])
        side = side_of.get(spec["participant"])
        if side is None or outcome.get("winner") not in ("home", "away", "tie"):
            return None
        return int(outcome["winner"] == side)
    sc = team_scores(outcome, seg, segments, regulation)
    if sc is None:
        return None
    h, a = sc
    kind = spec["kind"]
    if kind == "winner":
        if spec.get("tie"):
            return int(h == a)
        side = side_of.get(spec["participant"])
        if side is None:
            return None
        return int((h > a) if side == "home" else (a > h))
    if kind == "total":
        return int(h + a > spec["strike"])
    if kind == "btts":
        return int(h > 0 and a > 0)
    side = side_of.get(spec["participant"])
    if side is None:
        return None
    mine, theirs = (h, a) if side == "home" else (a, h)
    if kind == "spread":
        return int(mine - theirs > spec["strike"])
    if kind == "team_total":
        return int(mine > spec["strike"])
    return None


def payoff_fight(spec: dict, fight: dict, fighter_side: dict[str, int]) -> int | None:
    if not fight.get("completed"):
        return None
    if spec["kind"] == "distance":
        return None if fight.get("method") is None else int(fight["method"] == "distance")
    idx = fighter_side.get(spec["participant"])
    winners = [i for i, f in enumerate(fight["fighters"]) if f.get("winner")]
    if idx is None or len(winners) != 1:
        return None
    return int(winners[0] == idx)


def payoff_field(spec: dict, position: int | None) -> int:
    """Field payoff for a competitor who started: tie-aware position; missed cut / unclassified lose."""
    if position is None:
        return 0
    return int(position <= spec["top_n"])

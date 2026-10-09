"""Sports Phase 1 pipeline tests (offline; fixtures under tests/fixtures/sports)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.sports import adapters as ad
from research.sports import build as B
from research.sports import engines as E
from research.sports.benchmark import quote_at_cutoff
from research.sports.competitions import BASKETBALL_Q, SOCCER_H, all_competitions, parse_suffix
from research.sports.contracts import parse_market, payoff_field, payoff_fight, payoff_team_game
from research.sports.core import CacheMiss, Fetcher, Raw, WriteOnceError, write_once_jsonl
from research.sports.evaluate import metrics, paired_bootstrap

FIX = Path(__file__).parent / "fixtures" / "sports"


def raw_fixture(name: str) -> Raw:
    return Raw("fixture", {}, 200, (FIX / name).read_bytes(), {})


# ------------------------------------------------------------------ core: cache / write-once

class _Resp:
    def __init__(self, status, body):
        self.status_code, self.content, self.headers = status, body, {"content-type": "application/json"}


class _Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls += 1
        return self.responses.pop(0)


def test_fetcher_caches_bytes_and_cache_only_reproduces(tmp_path):
    s = _Session([_Resp(200, b'{"a": 1}')])
    f = Fetcher(root=tmp_path, min_interval=0, session=s)
    r1 = f.get("src", "https://example.test/x", {"b": 2, "a": 1})
    assert r1.ok and r1.json() == {"a": 1} and s.calls == 1
    off = Fetcher(root=tmp_path, cache_only=True)
    r2 = off.get("src", "https://example.test/x", {"a": 1, "b": 2})   # param order irrelevant
    assert r2.body == r1.body and r2.meta["sha256"] == r1.meta["sha256"]
    with pytest.raises(CacheMiss):
        off.get("src", "https://example.test/other")


def test_fetcher_detects_corrupted_cache(tmp_path):
    f = Fetcher(root=tmp_path, min_interval=0, session=_Session([_Resp(200, b"x")]))
    f.get("src", "https://example.test/y")
    body = next(tmp_path.rglob("*.body"))
    body.write_bytes(b"tampered")
    with pytest.raises(RuntimeError):
        Fetcher(root=tmp_path, cache_only=True).get("src", "https://example.test/y")


def test_fetcher_retries_then_does_not_cache_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    f = Fetcher(root=tmp_path, min_interval=0, max_retries=1, session=_Session([_Resp(503, b""), _Resp(503, b"")]))
    r = f.get("src", "https://example.test/z")
    assert r.status == 503 and f.stats["retries"] == 1
    assert not list(tmp_path.rglob("*.body"))


def test_write_once_journal(tmp_path):
    p = tmp_path / "j.jsonl"
    h = write_once_jsonl(p, [{"a": 1}])
    assert write_once_jsonl(p, [{"a": 1}]) == h
    with pytest.raises(WriteOnceError):
        write_once_jsonl(p, [{"a": 2}])


# ------------------------------------------------------------------ Kalshi normalization

def _mkt(ticker, tier, status="finalized", result="yes", **kw):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "_tier": tier, "status": status,
            "result": result, **kw}


def test_kalshi_dedupe_prefers_settled_copy():
    rows, st = ad.normalize_kalshi_markets([
        _mkt("KXNBAGAME-26JAN10PHXCHI-CHI", "live", status="active", result=""),
        _mkt("KXNBAGAME-26JAN10PHXCHI-CHI", "historical"),
        _mkt("KXNBAGAME-26JAN10PHXCHI-PHX", "historical", result="no"),
    ])
    assert st == {"unique_tickers": 2, "raw_rows": 3, "tickers_in_both_tiers": 1}
    chi = [r for r in rows if r["ticker"].endswith("CHI")][0]
    assert chi["status"] == "finalized" and chi["tiers_seen"] == ["historical", "live"]


def test_settlement_outcome_scalar_is_void():
    assert ad.kalshi_settlement_outcome({"status": "finalized", "result": "yes", "settlement_value": 1.0}) == ("yes", 1.0)
    assert ad.kalshi_settlement_outcome({"status": "finalized", "result": "scalar", "settlement_value": 0.5})[0] == "void"
    assert ad.kalshi_settlement_outcome({"status": "active", "result": ""})[0] == "unsettled"


# ------------------------------------------------------------------ source parsers

def test_parse_espn_team_games_fixture():
    games = ad.parse_espn_team_games(raw_fixture("espn_nba_scoreboard.json"), "basketball/nba")
    assert len(games) == 2
    g = games[0]
    assert g["espn_id"] == "401700001" and g["completed"] and g["home"]["score"] == 112
    assert g["home"]["periods"] == [30, 25, 27, 30] and g["season_type"] == 2
    assert not games[1]["completed"]


def _soccer_raw(details, home_score, away_score, status="STATUS_FULL_TIME", period=2):
    comp = {"id": "1", "date": "2026-03-01T15:00Z",
            "status": {"period": period, "type": {"name": status, "completed": True}},
            "competitors": [{"homeAway": "home", "score": str(home_score), "team": {"id": "10"}},
                            {"homeAway": "away", "score": str(away_score), "team": {"id": "20"}}],
            "details": details}
    return Raw("x", {}, 200, json.dumps({"events": [{"id": "1", "competitions": [comp]}]}).encode(), {})


def _goal(minute, team, **kw):
    return {"scoringPlay": True, "clock": {"displayValue": minute}, "team": {"id": team}, "scoreValue": 1, **kw}


def test_soccer_halves_from_timed_goals():
    g = ad.parse_espn_team_games(_soccer_raw([_goal("45'+2'", "10"), _goal("90'+4'", "20"), _goal("105'", "10")],
                                             2, 1, "STATUS_FINAL_AET", 4), "soccer/uefa.champions")[0]
    assert g["home"]["periods"] == [1, 0, 1] and g["away"]["periods"] == [0, 1, 0] and g["no_extra_time"] is False
    spec = {"kind": "winner", "tie": True, "segment": None}
    assert payoff_team_game(spec, {"home_score": 2, "away_score": 1, "home_periods": g["home"]["periods"],
                                   "away_periods": g["away"]["periods"]}, {}, SOCCER_H, (1, 2)) == 1
    # derived goals that do not add up to the final score are discarded, not guessed
    g = ad.parse_espn_team_games(_soccer_raw([_goal("10'", "10")], 2, 0), "soccer/eng.1")[0]
    assert g["home"]["periods"] == [] and g["no_extra_time"] is True


def test_parse_espn_golf_ties_and_playoff_round():
    ev = ad.parse_espn_golf(raw_fixture("espn_golf_scoreboard.json"), "golf/pga")[0]
    pos = {p["name"]: p["position"] for p in ev["players"]}
    # playoff holes appear as a fifth linescore for the two leaders only; all 4-round players finish
    assert pos == {"Alpha Winner": 1, "Bravo Playoff": 1, "Charlie Tied": 3, "Delta Tied": 3, "Echo Last": 5,
                   "Foxtrot Cut": None}
    assert ev["winner_athlete_id"] == "100"
    assert payoff_field({"top_n": 3}, 3) == 1 and payoff_field({"top_n": 3}, 5) == 0 and payoff_field({"top_n": 3}, None) == 0


def test_parse_espn_fights_distance_vs_finish():
    f = ad.parse_espn_fights(raw_fixture("espn_ufc_scoreboard.json"), "mma/ufc")
    assert [x["method"] for x in f] == ["distance", "finish"]
    dist_spec = {"kind": "distance"}
    win_spec = {"kind": "winner", "participant": "k22"}
    fight = {"completed": True, "method": f[1]["method"], "fighters": [{"winner": False}, {"winner": True}]}
    assert payoff_fight(dist_spec, fight, {}) == 0
    assert payoff_fight(win_spec, fight, {"k22": 1}) == 1


# ------------------------------------------------------------------ contracts

def test_parse_suffix_rules():
    assert parse_suffix("GAME") == ("winner", None)
    assert parse_suffix("1HSPREAD") == ("spread", "1H")
    assert parse_suffix("2QTOTAL") == ("total", "Q2")
    assert parse_suffix("F5TOTAL") == ("total", "F5")
    assert parse_suffix("MVP") is None


def _m(**kw):
    base = {"ticker": "KXT-EV-X", "event_ticker": "KXT-EV", "series_ticker": "KXT", "rules_primary": "", "custom_strike": None}
    base.update(kw)
    return base


UID = "0123456789abcdef0123456789abcdef"


def test_parse_market_specs_and_exclusions():
    spec, why = parse_market(_m(strike_type="structured", custom_strike={"basketball_team": UID}), "winner", None, sport="Basketball")
    assert spec["participant"] == UID and not spec["tie"]
    spec, why = parse_market(_m(strike_type="greater", floor_strike=5.5, custom_strike={"basketball_team": UID}), "spread", None, sport="Basketball")
    assert spec["strike"] == 5.5
    spec, why = parse_market(_m(strike_type="structured", floor_strike=99.5, custom_strike={"basketball_team": UID},
                                rules_primary="If X scores over 99.5 points"), "team_total", None, sport="Basketball")
    assert spec is not None
    spec, why = parse_market(_m(strike_type="greater", floor_strike=2.5), "total", None, sport="Soccer")
    assert spec is None and "90 minutes" in why
    spec, why = parse_market(_m(strike_type="weird", floor_strike=2.5), "total", None, sport="Hockey")
    assert spec is None


def test_team_payoffs_full_segment_and_soccer_regulation():
    out = {"home_score": 112, "away_score": 104, "winner": "home",
           "home_periods": [30, 25, 27, 30], "away_periods": [28, 28, 20, 28]}
    side = {"H": "home", "A": "away"}
    assert payoff_team_game({"kind": "winner", "participant": "A", "segment": None}, out, side, BASKETBALL_Q, None) == 0
    assert payoff_team_game({"kind": "spread", "participant": "H", "strike": 7.5, "segment": None}, out, side, BASKETBALL_Q, None) == 1
    assert payoff_team_game({"kind": "total", "strike": 215.5, "segment": None}, out, side, BASKETBALL_Q, None) == 1
    assert payoff_team_game({"kind": "winner", "participant": "A", "segment": "1H"}, out, side, BASKETBALL_Q, None) == 1
    assert payoff_team_game({"kind": "team_total", "participant": "A", "strike": 47.5, "segment": "2H"}, out, side, BASKETBALL_Q, None) == 1
    # soccer: 1-1 after 90', home wins in extra time -> full-time (90') markets settle as a tie
    soc = {"home_score": 2, "away_score": 1, "winner": "home", "home_periods": [0, 1, 1], "away_periods": [1, 0, 0]}
    assert payoff_team_game({"kind": "winner", "tie": True, "segment": None}, soc, side, SOCCER_H, (1, 2)) == 1
    assert payoff_team_game({"kind": "winner", "participant": "H", "segment": None}, soc, side, SOCCER_H, (1, 2)) == 0
    assert payoff_team_game({"kind": "total", "strike": 2.5, "segment": None}, soc, side, SOCCER_H, (1, 2)) == 0


# ------------------------------------------------------------------ identity

def _sg(gid, start, h, a):
    return {"source_game_id": gid, "start": start, "home": h, "away": a}


def test_doubleheader_is_ambiguous_not_merged():
    sg = [_sg("e1", "2026-05-01T17:00:00Z", "s1", "s2"), _sg("e2", "2026-05-01T23:00:00Z", "s1", "s2")]
    kg = [{"milestone_id": "m1", "start": "2026-05-01T20:00:00Z", "home": "k1", "away": "k2"}]
    matched, why = B.match_games(kg, sg, {"k1": "s1", "k2": "s2"})
    assert matched == {} and "ambiguous" in why["m1"]
    kg[0]["start"] = "2026-05-01T17:30:00Z"   # within 3 h of exactly one game
    matched, _ = B.match_games(kg, sg, {"k1": "s1", "k2": "s2"})
    assert matched == {"m1": "e1"}


def test_unmapped_participant_excluded():
    matched, why = B.match_games([{"milestone_id": "m", "start": "2026-05-01T17:00:00Z", "home": "k1", "away": "kX"}],
                                 [_sg("e", "2026-05-01T17:00:00Z", "s1", "s2")], {"k1": "s1"})
    assert matched == {} and why["m"] == "participant crosswalk unresolved"


def test_milestone_conflict_excluded():
    idx = B.MilestoneIndex([
        {"milestone_id": "a", "start": "2026-01-01T00:00:00Z", "home_id": "x", "away_id": "y", "event_tickers": ["EV"]},
        {"milestone_id": "b", "start": "2026-01-03T00:00:00Z", "home_id": "x", "away_id": "y", "event_tickers": ["EV"]},
    ])
    ms, why = idx.resolve("EV")
    assert ms is None and "conflicting" in why


def test_name_crosswalk_requires_unique_exact_name():
    targets = {"k1": {"name": "José Aldo Jr."}, "k2": {"name": "John Smith"}}
    mapping, why = B.name_crosswalk({"k1", "k2"}, targets, {"s1": "Jose Aldo", "s2": "John Smith", "s3": "John Smith"})
    assert mapping == {"k1": "s1"} and "several" in why["k2"]


# ------------------------------------------------------------------ availability gating & engines

def test_replay_applies_only_results_available_by_cutoff():
    seen = []
    releases = [(100.0, {"source_game_id": "g1"}), (200.0, {"source_game_id": "g2"})]
    state = []
    E.replay(releases, [(150.0, "q1"), (200.0, "q2")], lambda e: state.append(e["source_game_id"]),
             lambda q: seen.append((q, list(state))))
    assert seen == [("q1", ["g1"]), ("q2", ["g1", "g2"])]


def test_unfinished_games_never_release():
    from research.sports import models as M
    comp = next(c for c in all_competitions()[0] if c.key == "nba")
    evs = [{"source_game_id": "a", "start": "2025-01-01T00:00:00Z", "completed": False, "home_score": None,
            "away_score": None, "season_type": 2},
           {"source_game_id": "b", "start": "2025-01-02T00:00:00Z", "completed": True, "home_score": 100,
            "away_score": 90, "season_type": 2},
           {"source_game_id": "c", "start": "2025-01-03T00:00:00Z", "completed": True, "home_score": 100,
            "away_score": 90, "season_type": 1}]
    rel = M.team_releases(comp, evs)
    assert [e["source_game_id"] for _, e in rel] == ["b"]
    assert rel[0][0] == M.ts("2025-01-02T00:00:00Z") + comp.result_lag_hours * 3600


def test_elo_basic_properties():
    e = E.Elo(k=20, home=50)
    assert e.prob("a", "b", 0.0) > 0.5 and abs(e.prob("a", "b", 0.0, neutral=True) - 0.5) < 1e-12
    e.update("a", "b", 0.0, 1.0, neutral=True)
    assert e.prob("a", "b", 1.0, neutral=True) > 0.5
    assert abs(e.r["a"] + e.r["b"] - 3000.0) < 1e-9


def test_count_joint_probabilities_consistent():
    J = E.joint(1.4, 1.1, "poisson", None, 15)
    ph = E.prob_from_joint(J, "winner", "home", None, False, False)
    pa = E.prob_from_joint(J, "winner", "away", None, False, False)
    pt = E.prob_from_joint(J, "winner", None, None, True, False)
    assert abs(ph + pa + pt - 1) < 1e-6 and ph > pa
    assert abs(E.prob_from_joint(J, "winner", "home", None, False, True)
               + E.prob_from_joint(J, "winner", "away", None, False, True) - 1) < 1e-9
    assert E.prob_from_joint(J, "total", None, 1.5, False, False) > E.prob_from_joint(J, "total", None, 3.5, False, False)


def test_empirical_naive():
    emp = E.Empirical([(3, 1), (1, 1), (0, 2), (2, 0)])
    assert 0 < emp.prob("total", None, 2.5, False, False) < 1
    assert abs(emp.prob("winner", "home", None, False, True) + emp.prob("winner", "away", None, False, True) - 1) < 1e-12


def test_field_probs_deterministic_and_normalized():
    s = {"a": -0.1, "b": -0.5, "c": -0.9, "d": -0.9}
    p1 = E.field_probs(s, 6.0, [1, 3], 2000, seed=7)
    p2 = E.field_probs(s, 6.0, [1, 3], 2000, seed=7)
    assert p1 == p2
    assert abs(sum(p1[1].values()) - 1) < 1e-9 and p1[1]["a"] > p1[1]["b"]
    assert 2.9 < sum(p1[3].values()) < 3.1


# ------------------------------------------------------------------ evaluation and benchmark

def test_metrics_and_bootstrap():
    rows = [{"ticker": f"t{i}", "cluster": f"c{i // 2}", "p": p, "y": y}
            for i, (p, y) in enumerate([(0.9, 1), (0.1, 0), (0.6, 1), (0.4, 0), (0.7, 0), (0.3, 1)])]
    m = metrics(rows)
    assert m["n"] == 6 and m["clusters"] == 3
    assert abs(m["brier"] - sum((r["p"] - r["y"]) ** 2 for r in rows) / 6) < 1e-12
    naive = [{**r, "p": 0.5} for r in rows]
    b1, b2 = paired_bootstrap(rows, naive, 1), paired_bootstrap(rows, naive, 1)
    assert b1 == b2 and b1["clusters"] == 3 and b1["d_brier_ci"][0] <= b1["d_brier"] <= b1["d_brier_ci"][1]


def test_quote_uses_only_candles_ended_by_cutoff():
    c = lambda end, bid, ask: {"end_period_ts": end, "yes_bid": {"close": bid}, "yes_ask": {"close": ask}}
    candles = [c(1000, "0.40", "0.44"), c(4600, "0.50", "0.54"), c(8200, "0.90", "0.94")]
    q = quote_at_cutoff(candles, 8000)
    assert q["candle_end"] == 4600 and abs(q["mid"] - 0.52) < 1e-12 and q["age_s"] == 3400 and q["status"] == "ok"
    assert quote_at_cutoff(candles, 500)["status"] == "no candle ended by cutoff"
    assert quote_at_cutoff([c(1000, "0.40", "0.44")], 1000 + 4 * 3600)["status"] == "stale"
    live = [{"end_period_ts": 10, "yes_bid": {"close_dollars": "0"}, "yes_ask": {"close_dollars": "1"}}]
    assert quote_at_cutoff(live, 20)["status"].startswith("no two-sided")


def test_registry_covers_inventory_without_overlap():
    comps, specs, excl = all_competitions()
    seen = {}
    for c in comps:
        for s, _, _ in specs[c.key]:
            assert s not in seen, f"{s} in {seen.get(s)} and {c.key}"
            seen[s] = c.key
    assert {"nba", "nfl", "mlb", "nhl", "epl", "ufc", "pga", "f1"} <= {c.key for c in comps}

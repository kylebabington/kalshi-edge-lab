"""Sports Phase 2 tests: walk-forward availability, frozen calibration rules, market sampling and
budget enforcement, prospective capture modes. Offline; temporary directories only."""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from research.sports import core
from research.sports import evaluate as EV
from research.sports import manifest as MAN
from research.sports import models as M
from research.sports.core import Fetcher, sha256_json
from research.sports.phase2 import calib as CAL
from research.sports.phase2 import capture as CP
from research.sports.phase2 import config as C
from research.sports.phase2 import folds as F
from research.sports.phase2 import market as MK
from research.sports.phase2 import pipeline as P

H = M.HORIZON_MIN * 60


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ Phase 1 stays frozen

def test_phase1_code_hash_pinned():
    assert EV.code_hash() == C.PHASE1_CODE_HASH


def test_phase2_protocol_artifacts_verify():
    if not MAN.manifest_path(C.PROTOCOL).exists():
        pytest.skip("Phase 2 manifest not assembled")
    d = MAN.verify_detail(C.PROTOCOL)
    assert d["research"] == [] and d["docs"] == [] and d["docs_method"].startswith("portable_lf_v1")
    proto = json.loads(P.protocol_path(C.PROTOCOL).read_text(encoding="utf-8"))
    assert proto["phase1"]["code_hash"] == C.PHASE1_CODE_HASH and proto["code_hash_phase2"] == P.code_hash2()


def test_fold_window_restores_globals():
    before = (M.T_TEST, M.T_END)
    with pytest.raises(RuntimeError):
        with F.fold_window(1.0, 2.0):
            assert (M.T_TEST, M.T_END) == (1.0, 2.0)
            raise RuntimeError
    assert (M.T_TEST, M.T_END) == before


# ------------------------------------------------------------------ walk-forward availability (Kalshi-only Elo toy)

KO = SimpleNamespace(key="toy", source="kalshi_only", sport="Toy", result_lag_hours=0)
F2_START = M.ts(C.PERIODS["F2"][0])
B = F.boundary(KO, F2_START)
TEAMS = [f"T{i}" for i in range(10)]


def _world():
    rng = random.Random(7)
    events = []
    t = M.ts("2024-01-01T00:00:00Z")
    i = 0
    while t < B - 2 * 86400:                                                  # >= 300 training events (grid selection)
        a, b = rng.sample(TEAMS, 2)
        w = a if rng.random() < 0.5 + 0.04 * (TEAMS.index(a) - TEAMS.index(b)) else b
        events.append({"source_game_id": f"g{i}", "start": _iso(t), "available_at": _iso(t + 3 * 3600),
                       "home": a, "away": b, "winner_id": w, "completed": True})
        t += 86400 * 1.5
        i += 1
    assert len(events) >= 300
    gap = {"source_game_id": "gap", "start": _iso(B + 3600), "available_at": _iso(B + 4 * 3600), "home": "T1",
           "away": "T2", "winner_id": "T1", "completed": True}
    s_late = F2_START + 86400
    late = {"source_game_id": "late", "start": _iso(s_late), "available_at": _iso(s_late + 3 * 3600), "home": "T3",
            "away": "T4", "winner_id": "T3", "completed": True}
    contracts = []
    for name, s in (("X", s_late + 2 * 3600), ("Y", s_late + 10 * 3600)):    # X cutoff before 'late' settles; Y after
        contracts.append({"ticker": name, "status": "ok", "start": _iso(s), "participant": "T3", "home": "T3",
                          "away": "T4", "cluster": f"k:{name}", "family": "game_winner", "series": "S"})
    return events + [gap, late], contracts


def _flip(events, ids):
    out = []
    for e in events:
        if e["source_game_id"] in ids:
            e = {**e, "winner_id": e["away"] if e["winner_id"] == e["home"] else e["home"]}
        out.append(e)
    return out


def _pred(rows, t):
    return next(r["p"] for r in rows if r["ticker"] == t)


def test_fold_selection_uses_only_labels_available_before_fold_start():
    events, contracts = _world()
    st, rows = F.run_fold(KO, events, contracts, "F2")
    assert st["status"] == "ok" and st["params"]["selection"] == "grid"
    with F.fold_window(B, M.ts(C.PERIODS["F2"][1])):
        assert max(F.selection_label_times(KO, events)) <= F2_START - H
    later = {e["source_game_id"] for e in events if M.ts(e["start"]) >= B}
    st2, _ = F.run_fold(KO, _flip(events, later), contracts, "F2")
    assert st2["params"] == st["params"]


def test_each_prediction_uses_only_results_available_by_its_cutoff():
    events, contracts = _world()
    _, rows = F.run_fold(KO, events, contracts, "F2")
    _, rows2 = F.run_fold(KO, _flip(events, {"late"}), contracts, "F2")
    # 'late' settles after X's cutoff: X cannot change; it settles before Y's cutoff: Y may and does change
    assert _pred(rows, "X") == _pred(rows2, "X")
    assert _pred(rows, "Y") != _pred(rows2, "Y")


def test_unavailable_result_cannot_alter_prediction_but_earlier_in_fold_result_can():
    events, contracts = _world()
    _, base = F.run_fold(KO, events, contracts, "F2")
    # a result in the gap [boundary, fold start) is not a selection label but is a released result for forecasts
    _, flipped_gap = F.run_fold(KO, _flip(events, {"gap"}), contracts, "F2")
    assert _pred(base, "X") == _pred(flipped_gap, "X")                       # gap game involves other teams
    events_late_unsettled = [e if e["source_game_id"] != "late" else {**e, "available_at": _iso(M.ts(e["start"]) + 30 * 86400)}
                             for e in events]
    _, unsettled = F.run_fold(KO, events_late_unsettled, contracts, "F2")
    _, unsettled_flip = F.run_fold(KO, _flip(events_late_unsettled, {"late"}), contracts, "F2")
    assert _pred(unsettled, "Y") == _pred(unsettled_flip, "Y")               # not yet available at Y's cutoff


def test_result_lag_applied_at_fitting_boundary():
    events, contracts = _world()
    # an event starting just before the boundary whose result is only available after the fold's first cutoff
    lagged = {"source_game_id": "lag", "start": _iso(B - 3600), "available_at": _iso(F2_START), "home": "T5",
              "away": "T6", "winner_id": "T5", "completed": True}
    st, rows = F.run_fold(KO, events + [lagged], contracts, "F2")
    assert st["status"].startswith("excluded") and rows == []
    team = SimpleNamespace(source="espn_team", result_lag_hours=30)
    golf = SimpleNamespace(source="espn_golf", result_lag_hours=12)
    assert F.boundary(team, F2_START) == F2_START - H - 30 * 3600
    assert F.boundary(golf, F2_START) == F2_START - H - (12 + 120) * 3600


# ------------------------------------------------------------------ calibration and blend rules

def _rows(n_events, p_fn, y_fn, per_event=1, seed=1):
    rng = random.Random(seed)
    out = []
    for i in range(n_events):
        for j in range(per_event):
            p = p_fn(rng)
            out.append({"ticker": f"e{i}-{j}", "cluster": f"e{i}", "p": p, "y": y_fn(rng, p)})
    return out


def test_calibration_thresholds_and_unavailability():
    tr = _rows(199, lambda r: r.random() * 0.8 + 0.1, lambda r, p: int(r.random() < p))
    va = _rows(150, lambda r: r.random() * 0.8 + 0.1, lambda r, p: int(r.random() < p), seed=2)
    assert CAL.select_calibration(tr, va, tr + va)["status"] == "unavailable: training support below threshold"
    tr = _rows(400, lambda r: r.random() * 0.8 + 0.1, lambda r, p: int(r.random() < p))
    one_class = _rows(150, lambda r: 0.5, lambda r, p: 1, seed=3)
    assert CAL.select_calibration(tr, one_class, tr + one_class)["status"] == \
        "unavailable: validation support below threshold"
    va = _rows(99, lambda r: 0.5, lambda r, p: int(r.random() < p), seed=4)
    assert CAL.select_calibration(tr, va, tr + va)["status"].startswith("unavailable")


def test_calibration_detects_bias_and_is_deterministic():
    shift = lambda r, p: int(r.random() < CAL.sigmoid(CAL.logit(p) + 0.8))   # model under-forecasts
    tr = _rows(600, lambda r: r.random() * 0.8 + 0.1, shift, per_event=2)
    va = _rows(300, lambda r: r.random() * 0.8 + 0.1, shift, seed=5)
    a = CAL.select_calibration(tr, va, tr + va)
    assert a["status"] == "ok" and a["selected"]["form"] != "identity" and a["params"]["a"] > 0.3
    assert a == CAL.select_calibration(tr, va, tr + va)
    assert a["train_support"]["events"] == 600 and a["train_support"]["rows"] == 1200


def test_calibration_ties_prefer_identity_and_larger_lambda():
    tr = _rows(300, lambda r: 0.5, lambda r, p: r.random() < 0.5)
    tr = [{**x, "y": i % 2} for i, x in enumerate(tr)]
    va = [{**x, "y": i % 2} for i, x in enumerate(_rows(200, lambda r: 0.5, lambda r, p: 0, seed=9))]
    out = CAL.select_calibration(tr, va, tr + va)
    assert out["status"] == "ok" and out["selected"] == {"form": "identity", "lambda": 0.0}
    nc = [c for c in out["candidates"] if c["status"] != "ok"]
    assert all(c["lambda"] == 0.0 and c["form"] == "platt" for c in nc)       # singular (x constant) without ridge


def test_newton_matches_closed_form_intercept():
    rows = [{"ticker": f"t{i}", "cluster": f"c{i}", "p": 0.5, "y": int(i % 4 == 0)} for i in range(400)]
    prm = CAL.fit_calibration(rows, "intercept", 0.0)
    assert abs(prm["a"] - CAL.logit(0.25)) < 1e-8


def test_blend_requires_matched_market_inputs_and_outcome_support():
    rows = _rows(400, lambda r: r.random() * 0.8 + 0.1, lambda r, p: int(r.random() < p))
    quotes = {r["ticker"]: 0.5 for r in rows[:140]}                          # only 140 contracts have a quote
    mt = P.matched_rows(rows, quotes)
    assert len(mt) == 140 and all("p_mkt" in r for r in mt)
    va = P.matched_rows(_rows(200, lambda r: 0.5, lambda r, p: 1, seed=3), {f"e{i}-0": 0.5 for i in range(200)})
    assert CAL.select_blend(mt, va, mt + va)["status"] == "unavailable: matched training support below threshold"
    mt = P.matched_rows(rows, {r["ticker"]: 0.5 for r in rows})
    assert CAL.select_blend(mt, va, mt + va)["status"] == "unavailable: matched validation support below threshold"


def test_blend_selection_recovers_market_weight():
    def make(n, seed):
        rng = random.Random(seed)
        out = []
        for i in range(n):
            pm = rng.random() * 0.8 + 0.1
            pk = min(0.95, max(0.05, pm + rng.uniform(-0.3, 0.3)))
            out.append({"ticker": f"t{i}", "cluster": f"c{i}", "p": pm, "p_mkt": pk, "y": int(rng.random() < pk)})
        return out
    tr, va = make(800, 1), make(400, 2)
    out = CAL.select_blend(tr, va, tr + va)
    assert out["status"] == "ok" and out["selected"]["w"] <= 0.3


# ------------------------------------------------------------------ historical market sampling

def test_listing_time_is_later_of_created_and_open():
    assert MK.listing_ts({"created_time": "2026-05-12T16:02:00Z", "open_time": "2026-05-12T10:00:00Z"}) == \
        M.ts("2026-05-12T16:02:00Z")
    assert MK.listing_ts({}) is None


def _c(t, side=None, strike=None, family="spread", start="2026-03-01T20:00:00Z", series="S"):
    return {"ticker": t, "status": "ok", "family": family, "start": start, "cluster": "E1", "series": series,
            "side": side, "strike": strike, "tier": "historical"}


def test_only_contracts_listed_by_cutoff_are_sampled():
    comp = SimpleNamespace(key="x", source="espn_team")
    cs = [_c("A", "home", 1.5), _c("B", "home", 3.5), _c("C", "home", 5.5), _c("D", "away", 2.5)]
    cut = M.ts("2026-03-01T19:00:00Z")
    listing = {"A": cut - 10, "B": cut + 60, "C": cut - 5}                    # B listed after cutoff; D unknown
    cand, why = MK.candidate_events(comp, cs, {"A", "B", "C", "D"}, {"S": {"cohort": "strict"}}, listing)
    elig = [c["ticker"] for c in cand[("spread", "evaluation")]["E1"]]
    assert elig == ["A", "C"] and why == {"listed after cutoff": 1, "listing time unknown": 1, "eligible": 2}
    picks = MK.pick_contracts("spread", cand[("spread", "evaluation")]["E1"])
    assert [p["ticker"] for p in picks] == ["A"]                               # lower median of eligible {1.5, 5.5}


def test_contract_rules_per_family():
    tot = [_c(f"T{k}", None, k, "total") for k in (200.5, 210.5, 220.5, 230.5)]
    assert [c["strike"] for c in MK.pick_contracts("total", tot)] == [210.5, 220.5]
    gw = [_c("G-B", "away", None, "game_winner"), _c("G-A", "home", None, "game_winner")]
    assert [c["ticker"] for c in MK.pick_contracts("game_winner", gw)] == ["G-A"]
    tt = [_c("H1", "home", 100.5, "team_total"), _c("H2", "home", 110.5, "team_total"),
          _c("A1", "away", 99.5, "team_total")]
    assert [c["ticker"] for c in MK.pick_contracts("team_total", tt)] == ["H1", "A1"]


def test_event_sample_is_capped_deterministic_and_label_free():
    comp = SimpleNamespace(key="x", source="kalshi_only")
    cand = {("game_winner", "train"): {f"E{i}": [_c(f"E{i}-A", family="game_winner")] for i in range(400)}}
    a = MK.sample_competition(comp, cand)
    assert len(a) == C.EVENT_CAPS["kalshi_only"]["train"] and a == MK.sample_competition(comp, cand)
    order = [MK.event_order("x", "game_winner", "train", e["cluster"]) for e in a]
    assert order == sorted(order)


def _ev(i, fam, period, tickers):
    return {"competition": "x", "source": "espn_team", "family": fam, "period": period, "cluster": f"{fam}{i}",
            "order": f"{i:04d}", "contracts": [{"ticker": t, "series": "S", "tier": "historical",
                                                "cutoff_ts": 1_700_000_000 + i} for t in tickers]}


def test_budget_truncates_frozen_tiers_in_order():
    evs = [_ev(i, "game_winner", "train", [f"g{i}"]) for i in range(5)] + \
          [_ev(i, "spread", "validation", [f"s{i}a", f"s{i}b"]) for i in range(5)] + \
          [_ev(i, "team_total", "evaluation", [f"t{i}"]) for i in range(3)]
    kept, rep = MK.apply_budget(evs, 9, lambda u, p: False)
    assert rep["tiers"]["1_gw_train_validation"]["events_kept"] == 5
    assert rep["tiers"]["2_spread_total"]["events_kept"] == 2 and rep["tiers"]["2_spread_total"]["truncated"]
    assert rep["tiers"]["3_team_total"]["dropped"] and rep["tiers"]["3_team_total"]["events_kept"] == 0
    assert rep["uncached_requests"] == 9 and len(kept) == 7
    kept2, rep2 = MK.apply_budget(evs, 9, lambda u, p: "/g" in u or "g0" in u)  # cached requests are free
    assert rep2["tiers"]["2_spread_total"]["events_kept"] == 4


class _Resp:
    def __init__(self, status, body=b'{"candlesticks": []}'):
        self.status_code, self.content, self.headers = status, body, {"content-type": "application/json"}


class _Session:
    def __init__(self, fail=(), stop_after=None):
        self.calls, self.fail, self.stop_after = [], set(fail), stop_after

    def get(self, url, params=None, timeout=None, headers=None):
        if self.stop_after is not None and len(self.calls) >= self.stop_after:
            raise KeyboardInterrupt
        self.calls.append(url)
        return _Resp(500 if any(f in url for f in self.fail) else 200)


def _manifest(tmp_path, monkeypatch, n=4, **budget):
    monkeypatch.setattr(C, "MARKET_DIR", tmp_path / "market")
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    evs = [{**_ev(i, "game_winner", "train", [f"K{i}"]), "tier": "1_gw_train_validation"} for i in range(n)]
    man = {"events": evs, "manifest_sha256": sha256_json(evs),
           "budget": {"frozen_unique_requests": n, "max_retries_total": 50, "per_request_max_retries": 1,
                      "min_interval_s": 0.0, **budget}}
    p = MK.manifest_path("pv")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(man))
    return man


def _fetcher(tmp_path, session):
    return Fetcher(root=tmp_path / "raw", min_interval=0.0, max_retries=1, session=session)


def test_fetch_resumes_same_manifest_without_rerequesting(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch)
    s1 = _Session(stop_after=2)
    with pytest.raises(KeyboardInterrupt):
        MK.fetch("pv", lambda m: None, _fetcher(tmp_path, s1))
    s2 = _Session()
    out = MK.fetch("pv", lambda m: None, _fetcher(tmp_path, s2))
    assert len(s1.calls) == 2 and len(s2.calls) == 2 and out["unique_network_requests"] == 4
    assert out["remaining"] == 0 and out["stopped"] is None
    s3 = _Session()
    assert MK.fetch("pv", lambda m: None, _fetcher(tmp_path, s3))["unique_network_requests"] == 4 and not s3.calls


def test_failed_quotes_are_not_retried_or_replaced(tmp_path, monkeypatch):
    man = _manifest(tmp_path, monkeypatch)
    s = _Session(fail={"K1"})
    out = MK.fetch("pv", lambda m: None, _fetcher(tmp_path, s))
    assert out["retries"] == 1 and out["statuses"]["500"] == 1                # one finite retry, then recorded
    s2 = _Session()
    MK.fetch("pv", lambda m: None, _fetcher(tmp_path, s2))
    assert not s2.calls                                                       # resume never re-requests it
    monkeypatch.setattr(core, "RAW_ROOT", tmp_path / "raw")
    monkeypatch.setattr(MK, "Fetcher", lambda cache_only=False: Fetcher(cache_only=True, root=tmp_path / "raw"))
    q = {r["ticker"]: r["status"] for r in MK.quotes(man, lambda m: None)}
    assert q["K1"].startswith("not fetched") and len(q) == 4                  # no replacement contract


def test_retry_and_unique_budgets_stop_the_fetch(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch, max_retries_total=1)
    out = MK.fetch("pv", lambda m: None, _fetcher(tmp_path, _Session(fail={"K0"})))
    assert out["stopped"] == "retry budget exhausted" and out["unique_network_requests"] == 1
    _manifest(tmp_path / "b", monkeypatch, frozen_unique_requests=2)
    out = MK.fetch("pv", lambda m: None, _fetcher(tmp_path / "b", _Session()))
    assert out["stopped"] == "unique-request budget reached" and out["unique_network_requests"] == 2


def test_frozen_manifest_refuses_resampling(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "MARKET_DIR", tmp_path)
    body = {"events": [1], "manifest_sha256": "x", "estimate": {}}
    MK.freeze_manifest("pv", body, lambda m: None)
    MK.freeze_manifest("pv", {**body, "estimate": {"changed": 1}}, lambda m: None)   # estimate is informational
    with pytest.raises(SystemExit, match="never re-sample"):
        MK.freeze_manifest("pv", {**body, "events": [2]}, lambda m: None)


def test_sample_rederivation_checks_frozen_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "MARKET_DIR", tmp_path)
    evs = [_ev(i, "game_winner", "train", [f"g{i}"]) for i in range(3)]
    frozen = sorted(({**e, "tier": MK.tier_of(e["family"], e["period"])} for e in evs), key=P._tier_sort)
    man = {"events": frozen, "manifest_sha256": sha256_json(frozen), "listing_map_sha256": "L", "eligibility": {},
           "preserved_phase1_sample": {"sha256": "V"}, "truncation": {"tiers": {"1_gw_train_validation": {}}}}
    MK.manifest_path("pv").parent.mkdir(parents=True, exist_ok=True)
    MK.manifest_path("pv").write_text(json.dumps(man))
    assert P.verify_sample("pv", evs, {}, "L", "V", lambda m: None)["manifest_sha256"] == man["manifest_sha256"]
    with pytest.raises(SystemExit, match="re-derived events differ"):
        P.verify_sample("pv", evs[:2], {}, "L", "V", lambda m: None)
    with pytest.raises(SystemExit, match="listing map differs"):
        P.verify_sample("pv", evs, {}, "other", "V", lambda m: None)


# ------------------------------------------------------------------ prospective capture modes

START = M.ts("2026-10-20T23:00:00Z")
CUTOFF = START - H


def _capture(tmp_path, monkeypatch, mode, now, retrieved):
    monkeypatch.setattr(C, "CAPTURE_DIR", tmp_path / "records")
    monkeypatch.setattr(C, "CAPTURE_CACHE", tmp_path / "cache" / "x" / "y" / "raw_capture")
    monkeypatch.setattr(CP, "evidence_requests", lambda *a: [])
    monkeypatch.setattr(CP, "gw_series_of", lambda comp: [])
    monkeypatch.setattr(CP, "read_jsonl", lambda p: iter(()))
    monkeypatch.setattr(CP, "sha256_file", lambda p: "h")
    monkeypatch.setattr(CP.EV, "load_protocol", lambda v: {"competitions": {"nba": {"params": {"elo": {"k": 20}}}}})
    seen = {}

    def fake_compute(comp, params, run_root, reqs, as_of_fn):
        seen["as_of"] = as_of_fn(START)
        return [{"kalshi_event_ticker": "KXNBAGAME-X", "source_game_id": "espn:1", "home": "espn:a",
                 "away": "espn:b", "start": _iso(START), "as_of": _iso(as_of_fn(START)), "p_home": 0.6,
                 "market_home": {"ticker": "KXNBAGAME-X-A", "mid": 0.55}}], \
            [{"retrieved_at": _iso(retrieved), "sha256": "e"}]
    monkeypatch.setattr(CP, "compute", fake_compute)
    CP.run_capture(mode, "nba", lambda m: None, now_fn=lambda: datetime.fromtimestamp(now, timezone.utc))
    recs = [json.loads(p.read_text()) for p in sorted((tmp_path / "records").rglob("*.json"))]
    return recs, seen


def test_early_snapshot_records_actual_horizon(tmp_path, monkeypatch):
    now = START - 5 * 3600
    recs, seen = _capture(tmp_path, monkeypatch, "early", now, now + 5)
    assert len(recs) == 1 and recs[0]["status"] == "EARLY_SNAPSHOT" and recs[0]["horizon_minutes"] == 300.0
    assert seen["as_of"] == now and recs[0]["scheduled_cutoff"] == _iso(CUTOFF)
    for f in C.CAPTURE["timing_fields"]:
        assert f in recs[0]


def test_checkpoint_window_and_evidence_cutoff(tmp_path, monkeypatch):
    recs, _ = _capture(tmp_path / "a", monkeypatch, "checkpoint", CUTOFF - 30 * 60, CUTOFF - 29 * 60)
    assert recs == []                                                         # before the window: nothing recorded
    recs, seen = _capture(tmp_path / "b", monkeypatch, "checkpoint", CUTOFF - 10 * 60, CUTOFF - 9 * 60)
    assert recs[0]["status"] == "CHECKPOINT" and seen["as_of"] == CUTOFF and recs[0]["p_home"] == 0.6
    recs, _ = _capture(tmp_path / "c", monkeypatch, "checkpoint", CUTOFF - 60, CUTOFF + 30)
    assert recs[0]["status"] == "MISSED" and recs[0]["p_home"] is None       # evidence after the evidence cutoff
    recs, _ = _capture(tmp_path / "d", monkeypatch, "checkpoint", CUTOFF + 60, CUTOFF + 61)
    assert recs[0]["status"] == "MISSED" and "after the scheduled cutoff" in recs[0]["reason"]


def test_ticker_date_parses_kalshi_event_tickers():
    assert str(CP._ticker_date("KXNBAGAME-26OCT20BOSDET")) == "2026-10-20"
    assert CP._ticker_date("KXNBAGAME") is None


def test_capture_records_are_write_once(tmp_path, monkeypatch):
    now = START - 5 * 3600
    _capture(tmp_path, monkeypatch, "early", now, now)
    with pytest.raises(SystemExit, match="write-once"):
        _capture(tmp_path, monkeypatch, "early", now, now)

"""Settlement scoring of capture v2 window captures: settlement states, metric formulas and event keys,
candidate-specific common sets, fitted-candidate checks, source verification, exclusions and idempotency.

Offline: synthetic records and fake sessions; scores and evidence go to tmp dirs.
"""

from __future__ import annotations

import json
import math

import pytest

from research.sports.live import capture as CP
from research.sports.live import config as LC
from research.sports.live import discover as D
from research.sports.phase2 import calib as CAL
from research.sports.scoring import config as C
from research.sports.scoring import fitted_check as FC
from research.sports.scoring import report as RE
from research.sports.scoring import score as SC
from research.sports.scoring import settle as S
from tests.test_sports_capture_v2 import (  # noqa: F401  (fixtures)
    Clock, FakeResp, LiveSession, PagedSession, _run, fast_net, live_env)


def M(status, result, value):
    return {"ticker": "T", "status": status, "result": result,
            "settlement_value_dollars": value, "settlement_ts": None, "close_time": None}


# ------------------------------------------------------------------ settlement states

@pytest.mark.parametrize("m,state", [
    (M("finalized", "yes", "1.0000"), "SETTLED_YES"),
    (M("finalized", "no", "0.0000"), "SETTLED_NO"),
    (M("settled", "no", "0.0000"), "SETTLED_NO"),
    (M("determined", "yes", "1.0000"), "PENDING"),
    (M("closed", "", None), "PENDING"),
    (M("active", "", None), "PENDING"),
    (M("disputed", "no", "0.0000"), "PENDING"),
    (M("finalized", "yes", "0.0000"), "INCONSISTENT"),
    (M("finalized", "no", "1.0000"), "INCONSISTENT"),
    (M("finalized", "yes", None), "INCONSISTENT"),
    (M("finalized", "", None), "INCONSISTENT"),
    (M("finalized", "yes", "1.5"), "INCONSISTENT"),
    (M("finalized", "no", "abc"), "INCONSISTENT"),
    (M("finalized", "maybe", "1.0"), "INCONSISTENT"),
    (M("finalized", "scalar", "0.5000"), "NONBINARY"),
    (M("finalized", "scalar", "1.0000"), "NONBINARY"),
    (M("finalized", "void", None), "VOID_OR_CANCELLED"),
    (M("finalized", "cancelled", "0.5"), "VOID_OR_CANCELLED"),
])
def test_settlement_states(m, state):
    assert S.classify(m)[0] == state
    assert (state in C.BINARY) == (state in ("SETTLED_YES", "SETTLED_NO"))


class EventSession:
    def __init__(self, by_event, fail=()):
        self.by_event, self.fail = by_event, set(fail)

    def get(self, url, params=None, timeout=None, headers=None):
        ev = (params or {}).get("event_ticker")
        if ev in self.fail:
            return FakeResp(503, b"{}")
        return FakeResp(200, {"markets": self.by_event.get(ev, []), "cursor": ""})


def _ev(session, tmp_path):
    return D.Evidence(tmp_path, Clock(1_791_650_000.0), session)


def test_lookup_duplicates_missing_and_failures(tmp_path, fast_net):
    a = {**M("finalized", "yes", "1.0000"), "ticker": "A"}
    b1 = {**M("finalized", "no", "0.0000"), "ticker": "B"}
    b2 = {**M("finalized", "yes", "1.0000"), "ticker": "B"}
    s = EventSession({"E1": [a, b1], "E2": [a, b2]})
    res, keys = S.lookup(_ev(s, tmp_path / "1").get, ["E1", "E2"], ["A", "B", "C"])
    assert res["A"]["state"] == "SETTLED_YES"            # identical duplicates are harmless
    assert res["B"]["state"] == "CONFLICTING_DUPLICATE"
    assert res["C"]["state"] == "NOT_FOUND"
    assert len(keys) == 2
    res, _ = S.lookup(_ev(EventSession({"E1": [a]}, fail={"E2"}), tmp_path / "2").get, ["E1", "E2"], ["A", "B"])
    assert res["A"]["state"] == "SETTLED_YES" and res["B"]["state"] == "LOOKUP_FAILED"


def test_incomplete_pagination_is_lookup_failure(tmp_path, fast_net):
    s = PagedSession([[{**M("finalized", "yes", "1.0000"), "ticker": "A"}], [{"ticker": "B"}]], fail_at=1)
    res, _ = S.lookup(_ev(s, tmp_path).get, ["E1"], ["A"])
    assert res["A"]["state"] == "LOOKUP_FAILED"


# ------------------------------------------------------------------ metric formulas and event keys

def _score(comp, cluster, ticker, y, **cands):
    full = {k: ({"p": cands[k]} if k in cands else {"unavailable": "test"}) for k in
            ("frozen", "calibrated", "market", "blend")}
    return {"competition": comp, "cluster": cluster, "event_key": f"{comp}|{cluster}", "ticker": ticker,
            "y": y, "candidates": full, "sport": "x", "family": "game_winner", "cohort": "strict",
            "settlement": {"state": "SETTLED_YES" if y else "SETTLED_NO"}}


def test_brier_unclipped_logloss_clipped_and_event_equal():
    sc = [_score("c1", "e1", "t1", 1, frozen=0.0), _score("c1", "e1", "t2", 0, frozen=0.0),
          _score("c1", "e2", "t3", 1, frozen=1.0)]
    m = RE.candidate_metrics(sc, "frozen")
    assert m["contracts"] == 3 and m["events"] == 2
    assert m["brier"] == pytest.approx((1.0 + 0.0 + 0.0) / 3)          # Brier on the original p
    ll = [-math.log(1e-4), -math.log(1 - 1e-4), -math.log(1 - 1e-4)]   # clip only for log loss
    assert m["log_loss"] == pytest.approx(sum(ll) / 3)
    assert m["brier_event"] == pytest.approx(((1.0 + 0.0) / 2 + 0.0) / 2)
    assert m["log_loss_event"] == pytest.approx(((ll[0] + ll[1]) / 2 + ll[2]) / 2)


def test_identical_cluster_ids_in_different_competitions_are_different_events():
    sc = [_score("k_a", "kalshi:same", "t1", 1, frozen=0.6), _score("k_b", "kalshi:same", "t2", 0, frozen=0.6),
          _score("k_a", "kalshi:same", "t3", 0, frozen=0.6)]
    assert RE.candidate_metrics(sc, "frozen")["events"] == 2


def test_candidate_specific_common_sets():
    sc = [_score("c", "e1", "t1", 1, frozen=0.7, market=0.6),
          _score("c", "e2", "t2", 0, frozen=0.4),
          _score("c", "e3", "t3", 1, frozen=0.8, blend=0.75, market=0.7),
          _score("c", "e3", "t4", 0, frozen=0.2, calibrated=0.25)]
    fm = RE.paired(sc, "frozen", "market")
    assert fm["contracts"] == 2 and fm["events"] == 2
    d1 = (0.7 - 1) ** 2 - (0.6 - 1) ** 2
    d3 = (0.8 - 1) ** 2 - (0.7 - 1) ** 2
    assert fm["brier"] == pytest.approx((d1 + d3) / 2)
    assert RE.paired(sc, "calibrated", "frozen")["contracts"] == 1
    assert RE.paired(sc, "blend", "frozen")["contracts"] == 1
    assert RE.paired(sc, "blend", "market")["events"] == 1
    assert RE.candidate_metrics(sc, "market")["contracts"] == 2       # unavailable stays unavailable


def test_unlabelled_scores_never_enter_metrics():
    sc = [_score("c", "e1", "t1", 1, frozen=0.6), {**_score("c", "e2", "t2", None, frozen=0.6), "y": None}]
    assert RE.candidate_metrics(sc, "frozen")["contracts"] == 1


@pytest.mark.parametrize("bad", [1.2, -0.1, float("nan"), float("inf"), True, "0.5", None])
def test_invalid_probabilities_rejected(bad):
    assert not SC.valid_p(bad)
    s = _score("c", "e", "t", 1, frozen=0.5)
    s["candidates"]["frozen"] = {"p": bad}
    with pytest.raises(ValueError):
        RE.labelled_rows([s], "frozen")
    rec = {"contracts": [{"ticker": "t", "candidates": s["candidates"]}]}
    with pytest.raises(SC.SourceRefused):
        SC.check_probabilities(rec)


# ------------------------------------------------------------------ fitted-candidate checks

def _fc_contract(fz, mk, cal_params, blend_params):
    cand = {"frozen": {"p": fz},
            "calibrated": {"p": CAL.apply_calibration(cal_params, fz)},
            "market": {"p": mk, "bid": mk - 0.005, "ask": mk + 0.005, "candle_end": 1000, "age_s": 200},
            "blend": {"p": CAL.apply_blend(blend_params, fz, mk)}}
    g = {"calibration": {"status": "ok", "params": cal_params, "selected": {"form": "x"}},
         "blend": {"status": "ok", "params": blend_params}}
    return {"ticker": "T", "family": "spread", "status": "ok", "candidates": cand}, g


@pytest.mark.parametrize("c,equal", [(0.0, True), (-0.1032, False), (0.25, False)])
def test_zero_weight_blend_equals_market_only_with_zero_intercept(c, equal):
    con, g = _fc_contract(0.62, 0.40, {"a": 0.0, "b": 1.0}, {"w": 0.0, "c": c, "lambda": None})
    res = FC.check_contract(con, g, 1200.0)
    assert res["problems"] == []
    b = res["blend"]
    assert b["zero_model_weight"] is True
    assert b["zero_intercept"] is (c == 0.0)
    assert b["equals_market"] is equal
    assert con["candidates"]["blend"]["p"] == pytest.approx(CAL.sigmoid(c + CAL.logit(0.40)))


def test_zero_weight_zero_intercept_respects_frozen_clip():
    con, g = _fc_contract(0.5, 0.99995, {"a": 0.0, "b": 1.0}, {"w": 0.0, "c": 0.0, "lambda": None})
    con["candidates"]["market"].update(bid=0.9999, ask=0.99995 * 2 - 0.9999)
    res = FC.check_contract(con, g, 1200.0)
    assert not res["blend"]["equals_market"]
    assert con["candidates"]["blend"]["p"] == pytest.approx(1 - 1e-4)
    assert not any("blend differs" in p for p in res["problems"])


def test_identity_calibration_reported_and_wrong_values_flagged():
    con, g = _fc_contract(0.62, 0.40, {"a": 0.0, "b": 1.0}, {"w": 0.3, "c": 0.0, "lambda": None})
    res = FC.check_contract(con, g, 1200.0)
    assert res["calibration"]["identity"] and res["calibration"]["equals_frozen"] and not res["problems"]
    assert not res["blend"]["zero_model_weight"] and res["blend"]["zero_intercept"]
    con["candidates"]["blend"]["p"] += 1e-6
    con["candidates"]["market"]["candle_end"] = 1300
    probs = FC.check_contract(con, g, 1200.0)["problems"]
    assert any("blend" in p for p in probs) and any("after prediction_as_of" in p for p in probs)


# ------------------------------------------------------------------ end to end on a synthetic capture

@pytest.fixture
def score_env(live_env, tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SCORE_DIR", tmp_path / "scores")
    monkeypatch.setattr(C, "EVIDENCE_ROOT", tmp_path / "settle_evidence")
    env = live_env
    _run(env, "window", env["open"] + 300)
    [p] = sorted((LC.CAPTURE_DIR / "accepted" / "checkpoint").glob("*/*.json"))
    env["record"] = p
    env["rec"] = json.loads(p.read_text(encoding="utf-8"))
    env["et"] = env["rec"]["kalshi_event_tickers"][0]
    return env


def _settle_session(env, states):
    ms = [{**M(*states[c["ticker"][-3:]]), "ticker": c["ticker"], "event_ticker": env["et"]}
          for c in env["rec"]["contracts"]]
    return EventSession({env["et"]: ms})


def _settle(env, states, t=1_791_700_000.0):
    logs = []
    SC.run(logs.append, now_fn=Clock(t), session=_settle_session(env, states))
    return logs


def _scores():
    return sorted((C.SCORE_DIR / "finalized").glob("*/*/*.json"))


def _last_run():
    return json.loads(sorted((C.SCORE_DIR / "runs").glob("*.json"))[-1].read_text(encoding="utf-8"))


FINAL = {"AAA": ("finalized", "yes", "1.0000"), "BBB": ("finalized", "no", "0.0000")}


def test_scores_pin_source_and_are_write_once(score_env):
    env = score_env
    _settle(env, FINAL)
    paths = _scores()
    assert len(paths) == len([c for c in env["rec"]["contracts"] if c["status"] == "ok"]) == 2
    by_t = {c["ticker"]: c for c in env["rec"]["contracts"]}
    for p in paths:
        sc = json.loads(p.read_text(encoding="utf-8"))
        assert sc["source_record_sha256"] == env["rec"]["record_sha256"]
        assert sc["candidates"] == by_t[sc["ticker"]]["candidates"]          # copied verbatim
        assert sc["y"] == (1 if sc["ticker"].endswith("AAA") else 0)
        assert sc["event_key"] == f"{env['rec']['competition']}|{env['rec']['cluster']}"
        assert sc["settlement_evidence"] and all(e["status"] == 200 for e in sc["settlement_evidence"])
    before = {p: p.read_bytes() for p in paths}
    _settle(env, {"AAA": ("finalized", "no", "0.0000"), "BBB": ("finalized", "yes", "1.0000")}, t=1_791_800_000.0)
    assert {p: p.read_bytes() for p in _scores()} == before
    assert _last_run()["counts"] == {"already_finalized": 2}
    assert SC.verify_scores(lambda m: None) == 0


def test_pending_then_finalized_and_nonbinary_unlabelled(score_env):
    env = score_env
    _settle(env, {"AAA": ("determined", "yes", "1.0000"), "BBB": ("closed", "", None)})
    assert not _scores()
    assert sorted(d["state"] for d in _last_run()["diagnostics"]) == ["PENDING", "PENDING"]
    _settle(env, {"AAA": ("finalized", "scalar", "0.5000"), "BBB": ("finalized", "yes", "0.0000")},
            t=1_791_710_000.0)
    [p] = _scores()
    sc = json.loads(p.read_text(encoding="utf-8"))
    assert sc["settlement"]["state"] == "NONBINARY" and sc["y"] is None
    assert [d["state"] for d in _last_run()["diagnostics"]] == ["INCONSISTENT"]
    assert RE.candidate_metrics([sc], "frozen")["contracts"] == 0


def test_only_window_captures_are_scored(score_env, monkeypatch):
    env = score_env
    rec = env["rec"]
    for status, slot in (("MISSED", "checkpoint"), ("EARLY_SNAPSHOT", "early")):
        alt = CP.seal({**{k: v for k, v in rec.items() if k != "record_sha256"}, "status": status,
                       "cluster": f"kalshi:{status.lower()}"})
        q = LC.CAPTURE_DIR / "accepted" / slot / rec["competition"] / f"{status}.json"
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(CP._bytes(alt))
    _run(env, "window", env["open"] + 600)                       # DUPLICATE_ATTEMPT
    eligible, excluded = SC.scan()
    assert [r["status"] for _, r in eligible] == ["WINDOW_CAPTURE"]
    assert excluded["accepted/checkpoint:MISSED"] == 1 and excluded["accepted/early:EARLY_SNAPSHOT"] == 1
    assert excluded["attempt:DUPLICATE_ATTEMPT"] == 1
    _settle(env, FINAL)
    assert {json.loads(p.read_text(encoding="utf-8"))["cluster"] for p in _scores()} == {rec["cluster"]}


def test_source_verification_before_first_score(score_env):
    env = score_env
    p, rec = env["record"], env["rec"]
    orig = p.read_bytes()
    edited = json.loads(orig)
    edited["contracts"][0]["candidates"]["frozen"]["p"] = 0.5
    p.write_bytes(CP._bytes(edited))
    _settle(env, FINAL)
    assert not _scores() and {d["state"] for d in _last_run()["diagnostics"]} == {"SOURCE_REFUSED"}
    p.write_bytes(orig)
    ev = rec["evidence"][0]
    body = LC.EVIDENCE_ROOT / rec["run_id"] / ev["source"] / ev["key"][:2] / f"{ev['key']}.body"
    good = body.read_bytes()
    body.write_bytes(good + b" ")
    _settle(env, FINAL, t=1_791_710_000.0)
    assert not _scores() and "evidence bytes changed" in _last_run()["diagnostics"][0]["detail"]
    body.write_bytes(good)
    env["crosswalk"].write_text('{"mapping": {"x": 1}}', encoding="utf-8")
    from research.sports.live import pins as PN
    PN._sha.cache_clear()
    _settle(env, FINAL, t=1_791_720_000.0)
    assert not _scores() and "dependencies changed" in _last_run()["diagnostics"][0]["detail"]


def test_existing_score_checked_before_already_finalized(score_env):
    env = score_env
    _settle(env, FINAL)
    p = _scores()[0]
    sc = json.loads(p.read_text(encoding="utf-8"))
    sc["y"] = 1 - sc["y"]
    tampered = CP._bytes(sc)
    p.write_bytes(tampered)
    _settle(env, FINAL, t=1_791_710_000.0)
    assert p.read_bytes() == tampered                               # never rewritten
    assert [d["state"] for d in _last_run()["diagnostics"]] == ["SCORE_REFUSED"]
    assert SC.verify_scores(lambda m: None) == 1


def test_source_changed_after_scoring_is_refused(score_env):
    env = score_env
    _settle(env, FINAL)
    rec = json.loads(env["record"].read_text(encoding="utf-8"))
    rec["horizon_minutes"] = 61.0
    env["record"].write_bytes(CP._bytes(CP.seal({k: v for k, v in rec.items() if k != "record_sha256"})))
    _settle(env, FINAL, t=1_791_710_000.0)
    assert {d["state"] for d in _last_run()["diagnostics"]} == {"SCORE_REFUSED"}
    assert SC.verify_scores(lambda m: None) == 1


def test_settlement_evidence_corruption_detected(score_env):
    env = score_env
    _settle(env, FINAL)
    sc = json.loads(_scores()[0].read_text(encoding="utf-8"))
    e = sc["settlement_evidence"][0]
    body = C.EVIDENCE_ROOT / sc["settlement_run_id"] / e["source"] / e["key"][:2] / f"{e['key']}.body"
    body.write_bytes(body.read_bytes() + b" ")
    assert SC.verify_scores(lambda m: None) == 1


def test_report_runs_on_synthetic_scores(score_env, monkeypatch, tmp_path):
    env = score_env
    _settle(env, FINAL)
    monkeypatch.setattr(C, "REPORT_CSV", tmp_path / "pilot.csv")
    logs = []
    assert RE.stage_report(logs.append) == 0
    rows = (tmp_path / "pilot.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("group_by,group,kind,name,contracts,events")
    assert any("operational pilot" in m for m in logs)
    cov = RE.coverage([json.loads(p.read_text(encoding="utf-8")) for p in _scores()])
    assert cov["labelled_contracts"] == 2 and cov["labelled_events"] == 1

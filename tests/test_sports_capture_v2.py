"""Sports capture v2 tests: frozen candidates, faithfulness to the frozen journals, as-of view,
market rule, pagination, timing and mode enforcement, idempotency, integrity and replay, matrix.

Offline: the network is a fake session; records, evidence and the capture protocol go to tmp dirs.
"""

from __future__ import annotations

import collections
import copy
import json
import math
from datetime import datetime, timezone

import pytest

from research.sports import evaluate as EV
from research.sports import models as M
from research.sports.competitions import all_competitions
from research.sports.core import NORMALIZED, PREDICTIONS, PROTOCOLS, SPORTS_DATA, read_jsonl
from research.sports.phase2 import calib as CAL
from research.sports.phase2 import folds as F
from research.sports.live import capture as CP
from research.sports.live import config as C
from research.sports.live import discover as D
from research.sports.live import matrix as MX
from research.sports.live import pins as PN
from research.sports.live import predict as P
from research.sports.live import replay as RP
from research.sports.live import sources as S

pytestmark = pytest.mark.skipif(not (NORMALIZED / "k_kxatpmatch" / "events.jsonl").exists(),
                                reason="frozen sports data not present")

COMPS = {c.key: c for c in all_competitions()[0]}


def _proto1():
    return EV.load_protocol("sports_phase1_v2")


def _groups():
    return json.loads((PROTOCOLS / "sports_phase2_v1.json").read_text(encoding="utf-8"))["groups"]


# ------------------------------------------------------------------ frozen primary model

def _journal(key):
    J = collections.defaultdict(list)
    for r in read_jsonl(PREDICTIONS / "sports_phase1_v2" / f"{key}.jsonl"):
        J[r["ticker"]].append(r)
    return J


@pytest.mark.parametrize("key", ["nba", "epl", "ufc", "pga", "k_kxatpmatch"])
def test_live_path_reproduces_frozen_2026_forecasts(key):
    comp, entry = COMPS[key], _proto1()["competitions"][key]
    events = S.frozen_events(comp)
    cs = [c for c in read_jsonl(NORMALIZED / key / "contracts.jsonl") if c["status"] == "ok" and M.in_test(c)]
    by = collections.defaultdict(list)
    for c in cs:
        by[c["cluster"]].append(c)
    J = _journal(key)
    for cl in sorted(by)[:: max(1, len(by) // 3)][:3]:
        cc = by[cl]
        out, _ = P.predict_frozen(comp, entry, events, cc, M.ts(cc[0]["start"]) - 3600)
        for c in cc:
            want = F.primary_row(J[c["ticker"]], c["family"])
            if want is not None:
                assert out[c["ticker"]] == {"p": want["p"], "model": want["model"]}


def test_primary_model_is_not_forced_to_elo():
    comp, entry = COMPS["epl"], _proto1()["competitions"]["epl"]
    cs = [c for c in read_jsonl(NORMALIZED / "epl" / "contracts.jsonl")
          if c["status"] == "ok" and M.in_test(c) and c["family"] == "game_winner"][:6]
    out, _ = P.predict_frozen(comp, entry, S.frozen_events(comp), cs, M.ts(cs[0]["start"]) - 3600)
    assert {v["model"] for v in out.values() if "p" in v} == {"score"}     # three-way soccer winner
    nba = [c for c in read_jsonl(NORMALIZED / "nba" / "contracts.jsonl")
           if c["status"] == "ok" and M.in_test(c) and c["family"] == "game_winner"][:2]
    out, _ = P.predict_frozen(COMPS["nba"], _proto1()["competitions"]["nba"], S.frozen_events(COMPS["nba"]), nba,
                              M.ts(nba[0]["start"]) - 3600)
    assert {v["model"] for v in out.values()} == {"elo"}


# ------------------------------------------------------------------ four candidates

def _ncaaf_contract(family="game_winner", series="KXNCAAFGAME", **kw):
    return {"ticker": f"T-{family}-{kw.get('side', 'home')}", "series": series, "family": family, "kind": "winner",
            "status": "ok", "side": "home", "segment": None, "strike": None, "tie": False, "participant": None, **kw}


def test_all_four_candidates_and_explicit_reasons():
    comp, entry, groups = COMPS["ncaaf"], _proto1()["competitions"]["ncaaf"], _groups()
    gw = _ncaaf_contract()
    seg = _ncaaf_contract("segment", "KXNCAAF1HWINNER", side="away")
    bad = _ncaaf_contract(series="KXNOTGATED", side="neutral")
    frozen = {gw["ticker"]: {"p": 0.62, "model": "elo"}, seg["ticker"]: {"p": 0.4, "model": "score"},
              bad["ticker"]: {"p": 0.5, "model": "elo"}}
    quotes = {gw["ticker"]: {"p": 0.58, "bid": 0.57, "ask": 0.59, "candle_end": 1, "age_s": 10}}
    out = {r["ticker"]: r for r in P.assemble(comp, entry, groups, [gw, seg, bad], frozen, [gw["ticker"]], quotes, {})}
    c = out[gw["ticker"]]["candidates"]
    g = groups["ncaaf|game_winner"]
    assert c["frozen"]["p"] == 0.62
    assert c["calibrated"]["p"] == CAL.apply_calibration(g["calibration"]["params"], 0.62)
    assert c["market"]["p"] == 0.58
    assert c["blend"]["p"] == CAL.apply_blend(g["blend"]["params"], 0.62, 0.58)
    s = out[seg["ticker"]]["candidates"]
    assert "unavailable" in s["market"] and "unavailable" in s["blend"] and "segment" in s["market"]["unavailable"]
    assert all("gate" in v["unavailable"] for v in out[bad["ticker"]]["candidates"].values())
    k = P.assemble(COMPS["k_kxatpmatch"], _proto1()["competitions"]["k_kxatpmatch"], groups,
                   [{**gw, "series": "KXATPMATCH"}], {gw["ticker"]: {"p": 0.5, "model": "elo"}}, [gw["ticker"]],
                   quotes, {})[0]["candidates"]
    assert "p" in k["calibrated"] and k["blend"]["unavailable"].startswith("unavailable: matched training support")


def test_frozen_maps_reproduce_phase2_journal():
    rows = list(read_jsonl(SPORTS_DATA / "phase2" / "predictions" / "sports_phase2_v1" / "ncaaf.jsonl"))
    by = collections.defaultdict(dict)
    for r in rows:
        by[r["ticker"]][(r["candidate"], r.get("stratum"))] = r
    groups, n = _groups(), 0
    for t, d in by.items():
        fz = d.get(("frozen", None))
        g = groups[f"ncaaf|{fz['family']}"] if fz else None
        if fz and ("calibrated", None) in d:
            assert d[("calibrated", None)]["p"] == CAL.apply_calibration(g["calibration"]["params"], fz["p"])
        if fz and ("blend", "phase2_sample") in d:
            mk = d[("market", "phase2_sample")]["p"]
            assert d[("blend", "phase2_sample")]["p"] == CAL.apply_blend(g["blend"]["params"], fz["p"], mk)
            n += 1
    assert n > 50


def test_current_bid_ask_never_feeds_candidates():
    comp, entry, groups = COMPS["ncaaf"], _proto1()["competitions"]["ncaaf"], _groups()
    gw = _ncaaf_contract()
    fz = {gw["ticker"]: {"p": 0.6, "model": "elo"}}
    q = {gw["ticker"]: {"p": 0.55, "bid": 0.54, "ask": 0.56, "candle_end": 1, "age_s": 5}}
    a = P.assemble(comp, entry, groups, [gw], fz, [gw["ticker"]], q, {gw["ticker"]: {"yes_bid_dollars": "0.10",
                                                                                     "yes_ask_dollars": "0.12"}})
    b = P.assemble(comp, entry, groups, [gw], fz, [gw["ticker"]], q, {gw["ticker"]: {"yes_bid_dollars": "0.90",
                                                                                     "yes_ask_dollars": "0.92"}})
    assert a[0]["candidates"] == b[0]["candidates"]
    assert a[0]["diagnostics"]["current_yes_bid"] != b[0]["diagnostics"]["current_yes_bid"]


# ------------------------------------------------------------------ market rule at prediction_as_of

class _Raw:
    def __init__(self, candles):
        self._b = {"candlesticks": candles}
        self.status = 200

    def json(self):
        return self._b


def _candle(end, bid, ask):
    return {"end_period_ts": end, "yes_bid": {"close_dollars": str(bid)}, "yes_ask": {"close_dollars": str(ask)}}


def test_market_quote_rules():
    h = 1_790_000_000 - 1_790_000_000 % 3600
    as_of = h + 1500
    assert P.quote(_Raw([_candle(h, 0.4, 0.44)]), as_of, as_of - 20)["p"] == pytest.approx(0.42)
    assert P.quote(_Raw([_candle(h, 0.4, 0.44), _candle(h + 3600, 0.1, 0.2)]), as_of, as_of - 20)["p"] == pytest.approx(0.42)
    assert "stale" in P.quote(_Raw([_candle(h - 4 * 3600, 0.4, 0.44)]), as_of, as_of - 20)["unavailable"]
    assert "two-sided" in P.quote(_Raw([_candle(h, 0.0, 0.44)]), as_of, as_of - 20)["unavailable"]
    assert "hour boundary" in P.quote(_Raw([_candle(h, 0.4, 0.44)]), h + 5, h - 5)["unavailable"]
    assert "not selected" in P.quote(None, as_of, as_of)["unavailable"]


# ------------------------------------------------------------------ as-of view

def _mutate_future(comp, events, as_of):
    out = copy.deepcopy(events)
    n = 0
    for e in out:
        # earliest time this event's result could be used, had it completed
        r = S.release_time(comp, e) if S.kind_of(comp.source) == "kalshi_only" else \
            S.release_time(comp, {**e, "completed": True})
        if r is not None and r <= as_of:
            continue
        n += 1
        k = S.kind_of(comp.source)
        if k == "espn_team":
            e.update(completed=True, home_score=97, away_score=3, winner="home", home_periods=[50, 47],
                     away_periods=[1, 2], status="STATUS_FINAL")
        elif k == "espn_fight":
            e.update(completed=True, winner=e["fighters"][0], method="finish")
        elif k == "field":
            e["completed"] = True
            for i, x in enumerate(e["entrants"]):
                x.update(position=i + 1, finished=True, won=i == 0)
        else:
            e.update(completed=True, winner_id=e.get("home"), available_at=_iso_s(as_of + 60))
    return out, n


def _iso_s(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize("key", ["nba", "ufc", "pga", "k_kxatpmatch"])
def test_future_outcomes_cannot_change_predictions(key):
    comp, entry = COMPS[key], _proto1()["competitions"][key]
    events = S.frozen_events(comp)
    cs = [c for c in read_jsonl(NORMALIZED / key / "contracts.jsonl") if c["status"] == "ok" and M.in_test(c)]
    cl = sorted({c["cluster"] for c in cs})[len(cs) and 3]
    cc = [c for c in cs if c["cluster"] == cl]
    as_of = M.ts(cc[0]["start"]) - 3600 - 1800        # 30 min before the cutoff: results between are hidden
    if key == "k_kxatpmatch":
        # a settlement between as_of and the cutoff must not be used
        extra = copy.deepcopy(next(e for e in events if e.get("available_at")))
        extra.update(source_game_id="kalshi:test-late", start=_iso_s(as_of - 7200), available_at=_iso_s(as_of + 600),
                     home=cc[0]["home"], away=cc[0]["away"], winner_id=cc[0]["home"])
        events = events + [extra]
    base, _ = P.predict_frozen(comp, entry, events, cc, as_of)
    mutated, n = _mutate_future(comp, events, as_of)
    assert n > 0
    again, _ = P.predict_frozen(comp, entry, mutated, cc, as_of)
    assert base == again
    if key == "k_kxatpmatch":
        assert base != P.predict_frozen(comp, entry, events, cc, as_of + 1200)[0]


def test_hide_clears_outcomes_and_keeps_pregame_fields():
    nba = COMPS["nba"]
    e = next(x for x in S.frozen_events(nba) if x["completed"])
    h = S.hide(nba, e)
    assert h["completed"] is False and h["home_score"] is None and h["winner"] is None and h["home_periods"] == []
    assert h["status"] == S.HIDDEN_STATUS
    assert (h["home"], h["away"], h["start"], h["neutral"], h["season_type"]) == \
        (e["home"], e["away"], e["start"], e["neutral"], e["season_type"])
    ufc = COMPS["ufc"]
    f = next(x for x in S.frozen_events(ufc) if x["completed"] and x["winner"])
    hf = S.hide(ufc, f)
    assert hf["winner"] is None and hf["method"] is None and all(r["winner"] is None for r in hf["fighter_records"])
    assert hf["scheduled_rounds"] == f["scheduled_rounds"] and hf["fighters"] == f["fighters"]
    pga = COMPS["pga"]
    g = next(x for x in S.frozen_events(pga) if x["completed"])
    hg = S.hide(pga, g)
    assert all(x["position"] is None and x["won"] is None and x["finished"] is None for x in hg["entrants"])
    assert [x["id"] for x in hg["entrants"]] == [x["id"] for x in g["entrants"]]


# ------------------------------------------------------------------ pagination

class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"content-type": "application/json"}


class PagedSession:
    def __init__(self, pages, fail_at=None):
        self.pages, self.fail_at, self.calls = pages, fail_at, []

    def get(self, url, params=None, timeout=None, headers=None):
        i = len(self.calls)
        self.calls.append(dict(params or {}))
        if self.fail_at is not None and i == self.fail_at:
            return FakeResp(404, b"{}")
        cur = self.pages[i]
        return FakeResp(200, {"markets": cur, "cursor": f"c{i + 1}" if i + 1 < len(self.pages) else ""})


@pytest.fixture
def fast_net(monkeypatch):
    monkeypatch.setitem(C.NETWORK, "min_interval_s", 0.0)


def test_pagination_complete_and_incomplete(tmp_path, fast_net):
    now = lambda: datetime.now(timezone.utc)
    s = PagedSession([[{"ticker": "A"}], [{"ticker": "B"}], [{"ticker": "C"}]])
    ev = D.Evidence(tmp_path / "a", now, s)
    items, st = D.event_markets(ev.get, "EV-1")
    assert [m["ticker"] for m in items] == ["A", "B", "C"] and st["complete"] and st["pages"] == 3
    assert [c.get("cursor") for c in s.calls] == [None, "c1", "c2"]
    s2 = PagedSession([[{"ticker": "A"}], [{"ticker": "B"}], [{"ticker": "C"}]], fail_at=1)
    items, st = D.event_markets(D.Evidence(tmp_path / "b", now, s2).get, "EV-1")
    assert not st["complete"] and "HTTP 404" in st["error"]


def test_pagination_page_limit(tmp_path, fast_net, monkeypatch):
    monkeypatch.setattr(C, "MAX_PAGES", 2)
    s = PagedSession([[{"ticker": "A"}], [{"ticker": "B"}], [{"ticker": "C"}]])
    _, st = D.event_markets(D.Evidence(tmp_path, lambda: datetime.now(timezone.utc), s).get, "EV-1")
    assert not st["complete"] and "page limit" in st["error"]


# ------------------------------------------------------------------ synthetic live environment (Kalshi-only tennis)

class Clock:
    def __init__(self, t, step=0.01):
        self.t, self.step = t, step

    def __call__(self):
        self.t += self.step
        return datetime.fromtimestamp(self.t, timezone.utc)


class LiveSession:
    """Milestones + markets + settled markets + candles for one synthetic ATP match (and an F1 race)."""

    def __init__(self, start, a, b, candle_status=200, f1=False):
        self.start, self.a, self.b, self.candle_status, self.f1 = start, a, b, candle_status, f1
        self.et = "KXATPMATCH-26OCT10TSTAB"
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        p = dict(params or {})
        self.calls.append((url, p))
        if url.endswith("/milestones"):
            ms = [{"id": "11111111-2222-3333-4444-555555555555", "type": "tennis_match", "category": "Sports",
                   "start_date": _iso_s(self.start), "title": "A vs B",
                   "details": {"home_competitor_id": self.a, "away_competitor_id": self.b},
                   "primary_event_tickers": [self.et], "related_event_tickers": [self.et]}]
            if self.f1:
                ms.append({"id": "f1-ms", "type": "racing_tournament", "category": "Sports",
                           "start_date": _iso_s(self.start), "title": "GP", "details": {},
                           "primary_event_tickers": ["KXF1RACE-TESTGP"], "related_event_tickers": []})
            return FakeResp(200, {"milestones": ms, "cursor": ""})
        if url.endswith("/markets") and p.get("status") == "settled":
            return FakeResp(200, {"markets": [], "cursor": ""})
        if url.endswith("/markets"):
            if p.get("event_ticker") != self.et:
                return FakeResp(200, {"markets": [], "cursor": ""})
            ms = []
            for who, suf in ((self.a, "AAA"), (self.b, "BBB")):
                ms.append({"ticker": f"{self.et}-{suf}", "event_ticker": self.et, "custom_strike": {"tennis_competitor": who},
                           "strike_type": "structured", "status": "active", "result": "",
                           "rules_primary": f"If {suf} wins the match, then the market resolves to Yes.",
                           "created_time": _iso_s(self.start - 86400), "open_time": _iso_s(self.start - 86000),
                           "yes_bid_dollars": "0.4400", "yes_ask_dollars": "0.4600"})
            return FakeResp(200, {"markets": ms, "cursor": ""})
        if "/candlesticks" in url:
            if self.candle_status != 200:
                return FakeResp(self.candle_status, b"{}")
            end = int(p["end_ts"]) - int(p["end_ts"]) % 3600
            return FakeResp(200, {"candlesticks": [_candle(end, 0.41, 0.43)]})
        return FakeResp(404, b"{}")


@pytest.fixture
def live_env(tmp_path, monkeypatch, fast_net):
    monkeypatch.setattr(C, "CAPTURE_DIR", tmp_path / "capture")
    monkeypatch.setattr(C, "EVIDENCE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(PN, "protocol_path", lambda: tmp_path / "sports_phase2_capture_v2.json")
    cw = tmp_path / "crosswalk_dep.json"
    cw.write_text('{"mapping": {}}', encoding="utf-8")
    orig = PN.dependency_paths
    monkeypatch.setattr(PN, "dependency_paths", lambda comp: orig(comp) + [cw])
    monkeypatch.setattr(PN, "rel", lambda p: p.resolve().as_posix())
    PN._sha.cache_clear()
    comp = COMPS["k_kxatpmatch"]
    PN.freeze([comp, COMPS["f1"]], lambda m: None)
    last = [e for e in S.frozen_events(comp) if e.get("available_at")][-1]
    start = 1_791_640_800.0                  # 2026-10-10T14:00:00Z
    yield {"comp": comp, "a": last["home"], "b": last["away"], "start": start, "cutoff": start - 3600,
           "open": start - 4800, "crosswalk": cw, "tmp": tmp_path}
    PN._sha.cache_clear()


def _run(env, mode, t, session=None, step=0.01, comps="k_kxatpmatch", **kw):
    s = session or LiveSession(env["start"], env["a"], env["b"])
    logs = []
    code = CP.run(mode, comps, logs.append, now_fn=Clock(t, step), session=s, **kw)
    return code, logs


def _accepted(env, slot="checkpoint"):
    return sorted((C.CAPTURE_DIR / "accepted" / slot).glob("*/*.json"))


def _attempts():
    return sorted((C.CAPTURE_DIR / "attempts").glob("*/*.json"))


def test_window_capture_accepted_with_honest_timing(live_env):
    env = live_env
    code, _ = _run(env, "window", env["open"] + 300)
    assert code == 0
    [p] = _accepted(env)
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert rec["status"] == "WINDOW_CAPTURE" and "not an exact T-60" in rec["timing_label"]
    asof, fin = rec["prediction_as_of_ts"], CP._ts(rec["finalized_at"])
    assert env["open"] <= asof <= fin <= env["cutoff"]
    assert all(CP._ts(e["received_at"]) <= asof for e in rec["evidence"])
    assert rec["horizon_minutes"] == round((env["start"] - asof) / 60, 3)
    lr = rec["as_of_view"]["latest_release_used"]
    assert lr is None or CP._ts(lr) <= asof
    [gw] = [c for c in rec["contracts"] if c["market_pick"]]
    assert set(gw["candidates"]) == set(P.CANDIDATES)
    assert gw["candidates"]["market"]["p"] == pytest.approx(0.42)
    assert "p" in gw["candidates"]["frozen"] and "p" in gw["candidates"]["calibrated"]
    assert "unavailable" in gw["candidates"]["blend"]
    assert not _accepted(env, "early")
    assert RP.replay_path(p, lambda m: None)


def test_duplicate_run_never_replaces_accepted(live_env):
    env = live_env
    _run(env, "window", env["open"] + 60)
    [p] = _accepted(env)
    before = p.read_bytes()
    _run(env, "window", env["open"] + 600)
    assert p.read_bytes() == before
    dups = [json.loads(a.read_text(encoding="utf-8")) for a in _attempts()]
    assert [d["status"] for d in dups] == ["DUPLICATE_ATTEMPT"]
    assert dups[0]["accepted_record_sha256"] == json.loads(before)["record_sha256"]


def test_failed_attempt_does_not_occupy_slot_and_retry_is_accepted(live_env):
    env = live_env
    bad = LiveSession(env["start"], env["a"], env["b"], candle_status=404)
    _run(env, "window", env["open"] + 60, session=bad)
    assert not _accepted(env)
    [f] = _attempts()
    assert json.loads(f.read_text(encoding="utf-8"))["status"] == "FAILED_ATTEMPT"
    _run(env, "window", env["open"] + 400)
    [p] = _accepted(env)
    assert json.loads(p.read_text(encoding="utf-8"))["status"] == "WINDOW_CAPTURE"


def test_finalization_after_cutoff_is_a_failed_attempt_then_missed(live_env):
    env = live_env
    _run(env, "window", env["cutoff"] - 6, step=0.5)          # clock passes the cutoff during the attempt
    assert not _accepted(env)
    att = [json.loads(a.read_text(encoding="utf-8")) for a in _attempts()]
    assert att and att[0]["status"] == "FAILED_ATTEMPT" and any("timing" in r for r in att[0]["failure_reasons"])
    _run(env, "window", env["cutoff"] + 120)
    [p] = _accepted(env)
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert rec["status"] == "MISSED" and rec["prior_failed_attempts"]


def test_missed_only_after_window_and_never_over_accepted(live_env):
    env = live_env
    _run(env, "window", env["cutoff"] - 600)
    _run(env, "window", env["cutoff"] + 60)
    [p] = _accepted(env)
    assert json.loads(p.read_text(encoding="utf-8"))["status"] == "WINDOW_CAPTURE"


def test_missed_written_when_window_expired_without_capture(live_env):
    env = live_env
    _run(env, "window", env["cutoff"] + 60)
    [p] = _accepted(env)
    assert json.loads(p.read_text(encoding="utf-8"))["status"] == "MISSED"


def test_mode_enforcement(live_env):
    env = live_env
    _run(env, "window", env["open"] - 600)                    # before the window: nothing recorded
    assert not _accepted(env) and not _accepted(env, "early") and not _attempts()
    _run(env, "early", env["open"] + 60, lookahead_h=6)       # inside the window: early never records
    assert not _accepted(env) and not _accepted(env, "early")
    _run(env, "early", env["start"] - 3 * 3600, lookahead_h=6)
    [p] = _accepted(env, "early")
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert rec["status"] == "EARLY_SNAPSHOT" and CP._ts(rec["finalized_at"]) < env["open"]
    assert not _accepted(env)


def test_pin_failure_creates_diagnostic_not_accepted(live_env):
    env = live_env
    env["crosswalk"].write_text('{"mapping": {"x": "y"}}', encoding="utf-8")
    PN._sha.cache_clear()
    _run(env, "window", env["open"] + 60)
    assert not _accepted(env)
    att = [json.loads(a.read_text(encoding="utf-8")) for a in _attempts()]
    assert att and all(a["status"] == "FAILED_ATTEMPT" for a in att)
    assert any("dependency changed" in r for a in att for r in a["failure_reasons"])


def test_replay_refuses_corruption_edits_and_changed_dependencies(live_env):
    env = live_env
    _run(env, "window", env["open"] + 60)
    [p] = _accepted(env)
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert RP.replay_record(rec)["ok"]
    edited = {**rec, "horizon_minutes": rec["horizon_minutes"] + 1}
    with pytest.raises(RP.ReplayRefused, match="checksum"):
        RP.replay_record(edited)
    e = rec["evidence"][-1]
    body = C.EVIDENCE_ROOT / rec["run_id"] / e["source"] / e["key"][:2] / f"{e['key']}.body"
    orig = body.read_bytes()
    body.write_bytes(orig[:-1] + bytes([orig[-1] ^ 1]))
    with pytest.raises(RP.ReplayRefused, match="evidence bytes changed"):
        RP.replay_record(rec)
    body.write_bytes(orig)
    env["crosswalk"].write_text('{"mapping": {"changed": "espn:1"}}', encoding="utf-8")
    with pytest.raises(RP.ReplayRefused, match="dependencies changed"):
        RP.replay_record(rec)


def test_unsupported_adapter_listed_not_recorded(live_env):
    env = live_env
    s = LiveSession(env["start"], env["a"], env["b"], f1=True)
    code, _ = _run(env, "window", env["open"] + 60, session=s, comps="k_kxatpmatch,f1")
    assert code == 0
    run = json.loads(sorted((C.CAPTURE_DIR / "runs").glob("*.json"))[-1].read_text(encoding="utf-8"))
    f1 = [e for e in run["events"] if e["competition"] == "f1"]
    assert f1 and f1[0]["outcome"] == "unsupported_adapter" and "entrant" in f1[0]["reason"]
    assert all(p.parent.name != "f1" for p in _accepted(env))


# ------------------------------------------------------------------ matrix

def test_offline_matrix_covers_universe():
    comp_rows, series_rows = MX.build(lambda m: None)
    assert len({r["competition"] for r in comp_rows}) == 313
    assert len(series_rows) == 3871
    assert {r["live_status"] for r in series_rows} == {"supported", "unsupported", "unattempted"}
    f1 = [r for r in comp_rows if r["competition"] == "f1"]
    assert f1 and all(r["live_adapter"] == "unsupported" and r["frozen"] == "unavailable" for r in f1)
    assert all(r["comparability"] == "field_assumption_differs" for r in comp_rows if r["competition"] == "pga")
    cal = sorted(f"{r['competition']}|{r['family']}" for r in comp_rows if r["calibrated"] == "available")
    assert cal == ["k_kxatpmatch|game_winner", "k_kxwtamatch|game_winner", "ncaaf|game_winner", "ncaaf|spread",
                   "ncaaf|total"]


def test_dependencies_include_crosswalk_and_inventory():
    deps = PN.dependency_hashes(COMPS["nba"], fresh=True)
    assert any(p.endswith("crosswalk/nba_participants.json") for p in deps)
    assert any(p.endswith("sports_inventory_v1.csv") for p in deps)
    assert any(p.endswith("nba/events.jsonl") for p in deps)

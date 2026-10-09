"""Per-source parameter selection (training window only) and frozen test-period forecasting.

All forecasts are made at ``cutoff = event start - HORIZON`` inside :func:`engines.replay`.
Selection never reads test-period outcomes; forecasting uses only parameters frozen in the protocol.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .contracts import team_scores
from .engines import (DAY, Elo, CountScores, Empirical, FieldRatings, NormalScores, clip, field_probs,
                      joint, prob_from_joint, prob_from_normal, replay)

SPLITS = {"history_start": "2022-07-01T00:00:00Z", "train_start": "2023-07-01T00:00:00Z",
          "test_start": "2026-01-01T00:00:00Z", "test_end": "2026-10-06T00:00:00Z"}
HORIZON_MIN = 60
ELO_GRID = {"k": [10, 20, 30, 45], "home": [0, 35, 70, 100], "carry": [0.0, 0.33], "mov": [False, True]}
FIGHT_ELO_GRID = {"k": [16, 32, 48, 64], "carry": [0.0, 0.33]}
ALPHA_GRID = [0.03, 0.06, 0.1, 0.15, 0.22]
NEGBIN_R_GRID = [3.0, 6.0, 12.0, 25.0, 60.0]
FIELD_GRID = {"alpha": [0.05, 0.1, 0.2, 0.35], "beta": [2.0, 4.0, 6.0, 9.0, 13.0, 18.0]}
DIST_SHRINK_GRID = [2.0, 5.0, 10.0, 20.0, 40.0]
KONLY_DEFAULT = {"k": 24, "home": 0, "carry": 0.0, "mov": False}
KONLY_MIN_TRAIN = 300
FIELD_SIMS = 4000
COUNT_NMAX = {"poisson": 15, "negbin": 30}


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


T_TRAIN, T_TEST, T_END = ts(SPLITS["train_start"]), ts(SPLITS["test_start"]), ts(SPLITS["test_end"])
T_HIST = ts(SPLITS["history_start"])


def ll(p: float, y: int) -> float:
    p = clip(p)
    return -(math.log(p) if y else math.log(1 - p))


def in_test(c: dict) -> bool:
    return c.get("start") is not None and T_TEST <= ts(c["start"]) < T_END


def seed_of(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


# --------------------------------------------------------------------------------------------
# Team sports
# --------------------------------------------------------------------------------------------

def _scope_key(seg):
    return seg or "full"


def team_releases(comp, events):
    rel = []
    for e in events:
        if not e["completed"] or e["home_score"] is None or e["season_type"] in comp.exclude_season_types:
            continue
        if ts(e["start"]) < T_HIST:
            continue
        rel.append((ts(e["start"]) + comp.result_lag_hours * 3600, e))
    return rel


def _scope_pair(comp, e, seg):
    return team_scores(e, seg, comp.segments, comp.regulation_periods if seg is None else None)


def _train_games(comp, events):
    return [e for e in events if e["completed"] and e["home_score"] is not None
            and e["season_type"] not in comp.exclude_season_types and T_TRAIN <= ts(e["start"]) < T_TEST]


def _elo_run(comp, rel, queries, prm):
    elo = Elo(prm["k"], prm["home"], prm["carry"], prm["mov"])
    out = {}

    def upd(e):
        t = ts(e["start"])
        hs, as_ = e["home_score"], e["away_score"]
        s = 1.0 if e["winner"] == "home" else 0.0 if e["winner"] == "away" else 0.5
        elo.update(e["home"], e["away"], t, s, e["neutral"], hs - as_)

    def pred(q):
        key, home, away, t, neutral = q
        out[key] = elo.prob(home, away, t, neutral)

    replay(rel, [(q[3] - HORIZON_MIN * 60, q) for q in queries], upd, pred)
    return out


def _score_run(comp, rel, queries, seg, alpha):
    eng = (NormalScores if comp.score_model == "normal" else CountScores)(alpha)
    out = {}

    def upd(e):
        sc = _scope_pair(comp, e, seg)
        if sc is not None:
            eng.update(e["home"], e["away"], ts(e["start"]), sc[0], sc[1])

    def pred(q):
        key, home, away, t, _ = q
        out[key] = eng.raw(home, away, t)

    replay(rel, [(q[3] - HORIZON_MIN * 60, q) for q in queries], upd, pred)
    return out


def _ols(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    vx = sum((a - mx) ** 2 for a in x)
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / vx if vx > 0 else 0.0
    a0 = my - b * mx
    sd = math.sqrt(sum((c - a0 - b * a) ** 2 for a, c in zip(x, y)) / max(1, n - 2))
    return b, a0, max(sd, 0.5)


def select_team(comp, events, contracts) -> dict:
    rel = team_releases(comp, events)
    train = _train_games(comp, events)
    qs = [(e["source_game_id"], e["home"], e["away"], ts(e["start"]), e["neutral"]) for e in train]
    out = {"n_train_games": len(train), "scopes": {}}
    if len(train) < 100:
        out["blocked"] = f"only {len(train)} completed training-window source games"
        return out
    # Elo for full-game two-way winners
    if not comp.three_way:
        best = None
        for k in ELO_GRID["k"]:
            for h in ELO_GRID["home"]:
                for c in ELO_GRID["carry"]:
                    for mv in ELO_GRID["mov"]:
                        prm = {"k": k, "home": h, "carry": c, "mov": mv}
                        pr = _elo_run(comp, rel, qs, prm)
                        L = [ll(pr[e["source_game_id"]], int(e["winner"] == "home")) for e in train if e["winner"] in ("home", "away")]
                        score = sum(L) / len(L)
                        if best is None or score < best[0]:
                            best = (score, prm)
        out["elo"] = {**best[1], "train_logloss": round(best[0], 5)}
    segs = sorted({c["segment"] for c in contracts if c["status"] == "ok" and c["segment"]})
    for seg in [None] + segs:
        pairs = [p for p in (_scope_pair(comp, e, seg) for e in train) if p is not None]
        if len(pairs) < 100:
            out["scopes"][_scope_key(seg)] = {"blocked": f"only {len(pairs)} training games with {_scope_key(seg)} scores"}
            continue
        emp = Empirical(pairs)
        sc = {"n_train": len(pairs), "naive": {"n": emp.n}}
        best = None
        for alpha in ALPHA_GRID:
            raw = _score_run(comp, rel, qs, seg, alpha)
            rows = [(raw[e["source_game_id"]], p) for e, p in ((e, _scope_pair(comp, e, seg)) for e in train)
                    if p is not None and raw.get(e["source_game_id"]) is not None]
            if comp.score_model == "normal":
                bm, am, sdm = _ols([r[0][0] - r[0][1] for r in rows], [p[0] - p[1] for _, p in rows])
                bt, at, sdt = _ols([r[0][0] + r[0][1] for r in rows], [p[0] + p[1] for _, p in rows])
                crit = math.log(sdm) + math.log(sdt)
                prm = {"alpha": alpha, "a_m": bm, "b_m": am, "sd_m": sdm, "a_t": bt, "b_t": at, "sd_t": sdt}
                if best is None or crit < best[0]:
                    best = (crit, prm)
            else:
                rgrid = NEGBIN_R_GRID if comp.score_model == "negbin" else [None]
                for r in rgrid:
                    tot = 0.0
                    for (lh, la), (h, a) in rows:
                        tot -= _logpmf(h, lh, r) + _logpmf(a, la, r)
                    crit = tot / len(rows)
                    prm = {"alpha": alpha, "r": r}
                    if best is None or crit < best[0]:
                        best = (crit, prm)
        sc["model"] = {**best[1], "train_criterion": round(best[0], 5)}
        out["scopes"][_scope_key(seg)] = sc
    return out


def _logpmf(k, mu, r):
    if r is None:
        return -mu + k * math.log(mu) - math.lgamma(k + 1)
    p = r / (r + mu)
    return math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p)


def _contract_args(c):
    return c["kind"], c.get("side"), c.get("strike"), bool(c.get("tie"))


def predict_team(comp, events, contracts, params) -> list[dict]:
    rel = team_releases(comp, events)
    train = _train_games(comp, events)
    test = [c for c in contracts if c["status"] == "ok" and in_test(c)]
    games = {}
    for c in test:
        games[c["source_game_id"]] = (c["source_game_id"], c["home"], c["away"], ts(c["start"]), c.get("neutral", False))
    qs = list(games.values())
    elo_p = _elo_run(comp, rel, qs, params["elo"]) if params.get("elo") else {}
    preds = []
    by_seg = defaultdict(list)
    for c in test:
        by_seg[c["segment"]].append(c)
    for seg, cs in by_seg.items():
        sc = params["scopes"].get(_scope_key(seg)) or {}
        if "model" not in sc:
            for c in cs:
                preds.append({"ticker": c["ticker"], "model": "excluded", "p": None,
                              "reason": sc.get("blocked", "scope not fitted")})
            continue
        pairs = [p for p in (_scope_pair(comp, e, seg) for e in train) if p is not None]
        emp = Empirical(pairs)
        raw = _score_run(comp, rel, qs, seg, sc["model"]["alpha"])
        no_tie = seg is None and not comp.three_way
        for c in cs:
            kind, side, strike, tie = _contract_args(c)
            base = {"ticker": c["ticker"], "cluster": c["cluster"], "start": c["start"],
                    "cutoff": datetime.fromtimestamp(ts(c["start"]) - HORIZON_MIN * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            preds.append({**base, "model": "naive", "p": emp.prob(kind, side, strike, tie, no_tie)})
            r = raw.get(c["source_game_id"])
            if r is not None:
                if comp.score_model == "normal":
                    p = prob_from_normal(sc["model"], r[0], r[1], kind, side, strike, tie, no_tie)
                else:
                    J = joint(r[0], r[1], "poisson" if sc["model"]["r"] is None else "negbin", sc["model"]["r"],
                              COUNT_NMAX["poisson" if sc["model"]["r"] is None else "negbin"])
                    p = prob_from_joint(J, kind, side, strike, tie, no_tie)
                preds.append({**base, "model": "score", "p": p})
            if kind == "winner" and no_tie and c["source_game_id"] in elo_p and side:
                ph = elo_p[c["source_game_id"]]
                preds.append({**base, "model": "elo", "p": ph if side == "home" else 1 - ph})
    return preds


# --------------------------------------------------------------------------------------------
# Fights
# --------------------------------------------------------------------------------------------

def fight_releases(comp, events):
    return [(ts(e["start"]) + comp.result_lag_hours * 3600, e) for e in events
            if e["completed"] and ts(e["start"]) >= T_HIST and (e["winner"] or e["method"])]


class _FightState:
    def __init__(self, prm, shrink, base_rates):
        self.elo = Elo(prm["k"], 0, prm["carry"], False, gap_days=240)
        self.shrink, self.base = shrink, base_rates
        self.dist = defaultdict(lambda: [0, 0])

    def update(self, e):
        t = ts(e["start"])
        a, b = e["fighters"]
        if e["winner"] in (a, b):
            self.elo.update(a, b, t, 1.0 if e["winner"] == a else 0.0, True)
        if e["method"]:
            for f in (a, b):
                self.dist[f][0] += int(e["method"] == "distance")
                self.dist[f][1] += 1

    def p_distance(self, a, b, rounds):
        base = self.base.get(str(rounds), self.base.get("all", 0.5))
        k = self.shrink
        da, na = self.dist[a]
        db, nb = self.dist[b]
        # each fighter's tendency shrunk to the base rate, then averaged
        pa = (da + k * base) / (na + k)
        pb = (db + k * base) / (nb + k)
        return 0.5 * (pa + pb)


def _fight_base_rates(train):
    by = defaultdict(lambda: [0, 0])
    for e in train:
        if e["method"]:
            for key in (str(e["scheduled_rounds"]), "all"):
                by[key][0] += int(e["method"] == "distance")
                by[key][1] += 1
    return {k: (v[0] + 0.5) / (v[1] + 1) for k, v in by.items()}


def select_fight(comp, events, contracts) -> dict:
    rel = fight_releases(comp, events)
    train = [e for e in events if e["completed"] and T_TRAIN <= ts(e["start"]) < T_TEST]
    if len(train) < 100:
        return {"blocked": f"only {len(train)} training fights", "n_train": len(train)}
    base = _fight_base_rates(train)
    best = None
    for k in FIGHT_ELO_GRID["k"]:
        for c in FIGHT_ELO_GRID["carry"]:
            for sh in DIST_SHRINK_GRID:
                st = _FightState({"k": k, "carry": c}, sh, base)
                L_w, L_d = [], []

                def pred(e):
                    a, b = e["fighters"]
                    t = ts(e["start"])
                    if e["winner"] in (a, b):
                        L_w.append(ll(st.elo.prob(a, b, t, True), int(e["winner"] == a)))
                    if e["method"]:
                        L_d.append(ll(st.p_distance(a, b, e["scheduled_rounds"]), int(e["method"] == "distance")))

                replay(rel, [(ts(e["start"]) - HORIZON_MIN * 60, e) for e in train], st.update, pred)
                crit = (sum(L_w) / len(L_w), sum(L_d) / max(1, len(L_d)))
                if best is None or sum(crit) < sum(best[0]):
                    best = (crit, {"k": k, "carry": c, "shrink": sh})
    return {"n_train": len(train), "base_rates": base, "params": best[1],
            "train_logloss_winner": round(best[0][0], 5), "train_logloss_distance": round(best[0][1], 5)}


def predict_fight(comp, events, contracts, params) -> list[dict]:
    rel = fight_releases(comp, events)
    st = _FightState(params["params"], params["params"]["shrink"], params["base_rates"])
    test = [c for c in contracts if c["status"] == "ok" and in_test(c)]
    preds = []

    def pred(c):
        a, b = c["fighters"]
        t = ts(c["start"])
        base = {"ticker": c["ticker"], "cluster": c["cluster"], "start": c["start"],
                "cutoff": datetime.fromtimestamp(t - HORIZON_MIN * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if c["kind"] == "winner":
            me = c["participant_source"]
            other = b if me == a else a
            preds.append({**base, "model": "naive", "p": 0.5})
            preds.append({**base, "model": "elo", "p": st.elo.prob(me, other, t, True)})
        else:
            r = str(c.get("scheduled_rounds"))
            preds.append({**base, "model": "naive", "p": params["base_rates"].get(r, params["base_rates"].get("all"))})
            preds.append({**base, "model": "score", "p": st.p_distance(a, b, c.get("scheduled_rounds"))})

    replay(rel, [(ts(c["start"]) - HORIZON_MIN * 60, c) for c in test], st.update, pred)
    return preds


# --------------------------------------------------------------------------------------------
# Fields (golf, F1)
# --------------------------------------------------------------------------------------------

def _field_avail(comp, e):
    end = ts(e["end"]) + DAY if e.get("end") else ts(e["start"]) + (4 * DAY if comp.source == "espn_golf" else 0)
    return max(end, ts(e["start"])) + comp.result_lag_hours * 3600


def field_releases(comp, events):
    return [(_field_avail(comp, e), e) for e in events if e["completed"] and ts(e["start"]) >= T_HIST]


def select_field(comp, events, contracts) -> dict:
    rel = field_releases(comp, events)
    train = [e for e in events if e["completed"] and T_TRAIN <= ts(e["start"]) < T_TEST]
    if len(train) < 15:
        return {"blocked": f"only {len(train)} training events", "n_train": len(train)}
    best = None
    for alpha in FIELD_GRID["alpha"]:
        for beta in FIELD_GRID["beta"]:
            fr = FieldRatings(alpha)
            L = []

            def pred(e):
                winners = [x["id"] for x in e["entrants"] if x["won"]]
                if len(winners) != 1:
                    return
                s = {x["id"]: fr.strength(x["id"]) for x in e["entrants"]}
                w = {i: math.exp(beta * v) for i, v in s.items()}
                L.append(-math.log(clip(w[winners[0]] / sum(w.values()))))

            replay(rel, [(ts(e["start"]) - HORIZON_MIN * 60, e) for e in train], lambda e: fr.update(e["entrants"]), pred)
            crit = sum(L) / max(1, len(L))
            if best is None or crit < best[0]:
                best = (crit, {"alpha": alpha, "beta": beta})
    return {"n_train": len(train), "params": best[1], "train_winner_logloss": round(best[0], 5),
            "field_assumption": "field = entrants listed in the source result (published pre-event; late withdrawals included)"}


def predict_field(comp, events, contracts, params) -> list[dict]:
    rel = field_releases(comp, events)
    by_ev = {e["source_game_id"]: e for e in events}
    fr = FieldRatings(params["params"]["alpha"])
    test = [c for c in contracts if c["status"] == "ok" and in_test(c)]
    groups = defaultdict(list)
    for c in test:
        groups[c["source_game_id"]].append(c)
    preds = []

    def pred(gid):
        e = by_ev[gid]
        cs = groups[gid]
        s = {x["id"]: fr.strength(x["id"]) for x in e["entrants"]}
        ns = {c.get("top_n") or 1 for c in cs}
        probs = field_probs(s, params["params"]["beta"], ns, FIELD_SIMS, seed_of(comp.key, gid))
        N = len(e["entrants"])
        for c in cs:
            n = c.get("top_n") or 1
            base = {"ticker": c["ticker"], "cluster": c["cluster"], "start": c["start"],
                    "cutoff": datetime.fromtimestamp(ts(c["start"]) - HORIZON_MIN * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            preds.append({**base, "model": "naive", "p": min(1.0, n / N)})
            preds.append({**base, "model": "score", "p": probs[n][c["participant_source"]]})

    replay(rel, [(ts(by_ev[g]["start"]) - HORIZON_MIN * 60, g) for g in groups], lambda e: fr.update(e["entrants"]), pred)
    return preds


# --------------------------------------------------------------------------------------------
# Kalshi-settlement-only two-way competitions
# --------------------------------------------------------------------------------------------

def konly_releases(events):
    return [(ts(e["available_at"]), e) for e in events if e.get("available_at")]


def _konly_run(events, queries, prm):
    elo = Elo(prm["k"], 0, prm["carry"], False, gap_days=120)
    out = {}

    def upd(e):
        elo.update(e["home"], e["away"], ts(e["start"]), 1.0 if e["winner_id"] == e["home"] else 0.0, True)

    def pred(q):
        key, a, b, t = q
        out[key] = elo.prob(a, b, t, True)

    replay(konly_releases(events), [(q[3] - HORIZON_MIN * 60, q) for q in queries], upd, pred)
    return out


def select_konly(comp, events, contracts) -> dict:
    train = [e for e in events if e.get("start") and ts(e["start"]) < T_TEST]
    if len(train) < KONLY_MIN_TRAIN:
        return {"n_train": len(train), "params": KONLY_DEFAULT,
                "selection": f"default parameters (fewer than {KONLY_MIN_TRAIN} pre-test events)"}
    qs = [(e["source_game_id"], e["home"], e["away"], ts(e["start"])) for e in train]
    best = None
    for k in [12, 24, 36, 48]:
        for c in [0.0, 0.33]:
            prm = {"k": k, "home": 0, "carry": c, "mov": False}
            pr = _konly_run(events, qs, prm)
            L = [ll(pr[e["source_game_id"]], int(e["winner_id"] == e["home"])) for e in train]
            s = sum(L) / len(L)
            if best is None or s < best[0]:
                best = (s, prm)
    return {"n_train": len(train), "params": best[1], "train_logloss": round(best[0], 5), "selection": "grid"}


def predict_konly(comp, events, contracts, params) -> list[dict]:
    test = [c for c in contracts if c["status"] == "ok" and in_test(c) and c.get("start")]
    qs = {}
    for c in test:
        other = c["away"] if c["participant"] == c["home"] else c["home"]
        qs[c["ticker"]] = (c["ticker"], c["participant"], other, ts(c["start"]))
    pr = _konly_run(events, list(qs.values()), params["params"])
    preds = []
    for c in test:
        base = {"ticker": c["ticker"], "cluster": c["cluster"], "start": c["start"],
                "cutoff": datetime.fromtimestamp(ts(c["start"]) - HORIZON_MIN * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        preds.append({**base, "model": "naive", "p": 0.5})
        preds.append({**base, "model": "elo", "p": pr[c["ticker"]]})
    return preds


SELECT = {"espn_team": select_team, "espn_fight": select_fight, "espn_golf": select_field,
          "jolpica": select_field, "kalshi_only": select_konly}
PREDICT = {"espn_team": predict_team, "espn_fight": predict_fight, "espn_golf": predict_field,
           "jolpica": predict_field, "kalshi_only": predict_konly}
PRIMARY = {"game_winner": "elo", "kalshi_only": "elo"}

"""Baseline engines and the availability-gated replay they all run inside.

The replay interleaves two streams in time order:
  * result releases: a finished event becomes usable at ``available_at`` (start + a frozen,
    conservative per-competition lag, or Kalshi's settlement timestamp for Kalshi-only data);
  * forecast queries at ``cutoff`` (event start minus the horizon).
Releases with ``available_at <= cutoff`` are applied before a query; unfinished events never
release. Engines therefore cannot see any result that was not public at the forecast cutoff.
"""

from __future__ import annotations

import bisect
import math
import random
from collections import defaultdict
from typing import Callable, Iterable

DAY = 86400.0


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def clip(p: float, eps: float = 1e-4) -> float:
    return min(1.0 - eps, max(eps, p))


def replay(releases: list[tuple[float, dict]], queries: list[tuple[float, object]],
           update: Callable[[dict], None], predict: Callable[[object], None]) -> None:
    """Apply each release whose time <= the query cutoff before answering that query."""
    rel = sorted(releases, key=lambda x: (x[0], x[1].get("source_game_id", "")))
    qs = sorted(queries, key=lambda x: x[0])
    i = 0
    for t, q in qs:
        while i < len(rel) and rel[i][0] <= t:
            update(rel[i][1])
            i += 1
        predict(q)


# --------------------------------------------------------------------------------------------
# Elo (two-way winners; fights; Kalshi-only)
# --------------------------------------------------------------------------------------------

class Elo:
    def __init__(self, k: float = 20.0, home: float = 0.0, carry: float = 0.0, mov: bool = False,
                 gap_days: float = 60.0, base: float = 1500.0):
        self.k, self.home, self.carry, self.mov, self.gap = k, home, carry, mov, gap_days * DAY
        self.base = base
        self.r: dict[str, float] = {}
        self.last: dict[str, float] = {}
        self.n: dict[str, int] = defaultdict(int)

    def rating(self, team: str, t: float) -> float:
        r = self.r.get(team, self.base)
        if self.carry and team in self.last and t - self.last[team] > self.gap:
            r = self.base + (r - self.base) * (1.0 - self.carry)
        return r

    def prob(self, a: str, b: str, t: float, neutral: bool = False) -> float:
        d = self.rating(a, t) - self.rating(b, t) + (0.0 if neutral else self.home)
        return 1.0 / (1.0 + 10.0 ** (-d / 400.0))

    def update(self, a: str, b: str, t: float, score: float, neutral: bool = False, margin: float | None = None):
        ra, rb = self.rating(a, t), self.rating(b, t)
        d = ra - rb + (0.0 if neutral else self.home)
        p = 1.0 / (1.0 + 10.0 ** (-d / 400.0))
        mult = 1.0
        if self.mov and margin is not None and score != 0.5:
            winner_diff = d if score == 1.0 else -d
            mult = math.log(abs(margin) + 1.0) * 2.2 / (winner_diff * 0.001 + 2.2)
        delta = self.k * mult * (score - p)
        self.r[a], self.r[b] = ra + delta, rb - delta
        self.last[a] = self.last[b] = t
        self.n[a] += 1
        self.n[b] += 1


# --------------------------------------------------------------------------------------------
# Score engines
# --------------------------------------------------------------------------------------------

class NormalScores:
    """EWMA points scored/allowed per team for one scope (full game or a segment)."""

    def __init__(self, alpha: float, carry: float = 0.3, gap_days: float = 60.0):
        self.alpha, self.carry, self.gap = alpha, carry, gap_days * DAY
        self.S: dict[str, float] = {}
        self.A: dict[str, float] = {}
        self.last: dict[str, float] = {}
        self.lh = None
        self.la = None

    def _get(self, team, t):
        mean = 0.5 * ((self.lh or 0.0) + (self.la or 0.0))
        s, a = self.S.get(team, mean), self.A.get(team, mean)
        if team in self.last and t - self.last[team] > self.gap:
            s = mean + (s - mean) * (1 - self.carry)
            a = mean + (a - mean) * (1 - self.carry)
        return s, a

    def raw(self, home, away, t) -> tuple[float, float] | None:
        if self.lh is None:
            return None
        sh, ah = self._get(home, t)
        sa, aa = self._get(away, t)
        mean = 0.5 * (self.lh + self.la)
        return (sh + aa) / 2 + (self.lh - mean), (sa + ah) / 2 + (self.la - mean)

    def update(self, home, away, t, hs, as_):
        if self.lh is None:
            self.lh, self.la = float(hs), float(as_)
        mean = 0.5 * (self.lh + self.la)
        sh, ah = self._get(home, t)
        sa, aa = self._get(away, t)
        hadj, aadj = hs - (self.lh - mean), as_ - (self.la - mean)
        a = self.alpha
        self.S[home], self.A[home] = sh + a * (hadj - sh), ah + a * (aadj - ah)
        self.S[away], self.A[away] = sa + a * (aadj - sa), aa + a * (hadj - aa)
        self.last[home] = self.last[away] = t
        self.lh += 0.01 * (hs - self.lh)
        self.la += 0.01 * (as_ - self.la)


class CountScores:
    """EWMA attack/defence ratios per team (Poisson / negative-binomial goals or runs)."""

    def __init__(self, alpha: float, carry: float = 0.3, gap_days: float = 60.0):
        self.alpha, self.carry, self.gap = alpha, carry, gap_days * DAY
        self.att: dict[str, float] = {}
        self.dfn: dict[str, float] = {}
        self.last: dict[str, float] = {}
        self.lh = None
        self.la = None

    def _get(self, team, t):
        at, df = self.att.get(team, 1.0), self.dfn.get(team, 1.0)
        if team in self.last and t - self.last[team] > self.gap:
            at = 1 + (at - 1) * (1 - self.carry)
            df = 1 + (df - 1) * (1 - self.carry)
        return at, df

    def raw(self, home, away, t) -> tuple[float, float] | None:
        if self.lh is None:
            return None
        ah, dh = self._get(home, t)
        aa, da = self._get(away, t)
        return max(0.05, self.lh * ah * da), max(0.05, self.la * aa * dh)

    def update(self, home, away, t, hs, as_):
        if self.lh is None:
            self.lh, self.la = max(0.3, float(hs)), max(0.3, float(as_))
        ah, dh = self._get(home, t)
        aa, da = self._get(away, t)
        a = self.alpha
        eh = self.lh * ah * da
        ea = self.la * aa * dh
        # multiplicative EWMA on observed / expected, bounded for stability
        rh = min(3.0, max(0.33, (hs + 0.5) / (eh + 0.5)))
        ra = min(3.0, max(0.33, (as_ + 0.5) / (ea + 0.5)))
        self.att[home] = ah * (1 + a * (rh - 1))
        self.dfn[away] = da * (1 + a * (rh - 1))
        self.att[away] = aa * (1 + a * (ra - 1))
        self.dfn[home] = dh * (1 + a * (ra - 1))
        self.last[home] = self.last[away] = t
        self.lh += 0.01 * (hs - self.lh)
        self.la += 0.01 * (as_ - self.la)


def pois_pmf(lam: float, n: int) -> list[float]:
    out = [math.exp(-lam)]
    for k in range(1, n + 1):
        out.append(out[-1] * lam / k)
    return out


def negbin_pmf(mu: float, r: float, n: int) -> list[float]:
    p = r / (r + mu)
    out = []
    for k in range(n + 1):
        out.append(math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p)))
    return out


def joint(lh: float, la: float, dist: str, r: float | None, nmax: int) -> list[list[float]]:
    ph = pois_pmf(lh, nmax) if dist == "poisson" else negbin_pmf(lh, r, nmax)
    pa = pois_pmf(la, nmax) if dist == "poisson" else negbin_pmf(la, r, nmax)
    return [[x * y for y in pa] for x in ph]


def _thr(k: float) -> float:
    return math.floor(k) + 0.5


def prob_from_joint(J, kind: str, side: str | None, strike: float | None, tie: bool, no_tie: bool) -> float:
    n = len(J)
    ph = pa = pt = 0.0
    if kind == "winner":
        for i in range(n):
            for j in range(n):
                if i > j:
                    ph += J[i][j]
                elif j > i:
                    pa += J[i][j]
                else:
                    pt += J[i][j]
        if tie:
            return pt
        p = ph if side == "home" else pa
        if no_tie:
            p = p / max(1e-9, ph + pa)
        return p
    tot = 0.0
    for i in range(n):
        for j in range(n):
            v = J[i][j]
            if kind == "total":
                ok = i + j > strike
            elif kind == "btts":
                ok = i > 0 and j > 0
            elif kind == "spread":
                ok = ((i - j) if side == "home" else (j - i)) > strike
            elif kind == "team_total":
                ok = (i if side == "home" else j) > strike
            else:
                raise ValueError(kind)
            if ok:
                tot += v
    return tot


def prob_from_normal(cal: dict, mh: float, ma: float, kind: str, side: str | None, strike: float | None,
                     tie: bool, no_tie: bool) -> float:
    m = cal["a_m"] * (mh - ma) + cal["b_m"]
    t = cal["a_t"] * (mh + ma) + cal["b_t"]
    sm, st = cal["sd_m"], cal["sd_t"]
    if kind == "winner":
        if no_tie:
            p_home = phi(m / sm)
            return p_home if side == "home" else 1 - p_home
        p_tie = phi((0.5 - m) / sm) - phi((-0.5 - m) / sm)
        if tie:
            return p_tie
        p_home = 1 - phi((0.5 - m) / sm)
        return p_home if side == "home" else 1 - p_home - p_tie
    if kind == "total":
        return 1 - phi((_thr(strike) - t) / st)
    if kind == "spread":
        ms = m if side == "home" else -m
        return 1 - phi((_thr(strike) - ms) / sm)
    if kind == "team_total":
        mu = (t + m) / 2 if side == "home" else (t - m) / 2
        sd = math.sqrt(sm * sm + st * st) / 2
        return 1 - phi((_thr(strike) - mu) / sd)
    raise ValueError(kind)


# --------------------------------------------------------------------------------------------
# Empirical (naive) distributions
# --------------------------------------------------------------------------------------------

class Empirical:
    """League-wide train-period distribution of a scope's (home, away) scores; no team information."""

    def __init__(self, pairs: Iterable[tuple[int, int]]):
        pairs = list(pairs)
        self.n = len(pairs)
        self.margin = sorted(h - a for h, a in pairs)
        self.total = sorted(h + a for h, a in pairs)
        self.home = sorted(h for h, _ in pairs)
        self.away = sorted(a for _, a in pairs)
        self.btts = sum(1 for h, a in pairs if h > 0 and a > 0)
        self.hw = sum(1 for h, a in pairs if h > a)
        self.aw = sum(1 for h, a in pairs if a > h)

    @staticmethod
    def _gt(xs: list, k: float) -> float:
        return (len(xs) - bisect.bisect_right(xs, k) + 0.5) / (len(xs) + 1.0)

    def prob(self, kind: str, side: str | None, strike: float | None, tie: bool, no_tie: bool) -> float:
        n = self.n
        if kind == "winner":
            pt = (n - self.hw - self.aw + 0.5) / (n + 1.0)
            if tie:
                return pt
            if no_tie:
                ph = (self.hw + 0.5) / (self.hw + self.aw + 1.0)
                return ph if side == "home" else 1 - ph
            return ((self.hw if side == "home" else self.aw) + 0.5) / (n + 1.0)
        if kind == "total":
            return self._gt(self.total, strike)
        if kind == "btts":
            return (self.btts + 0.5) / (n + 1.0)
        if kind == "spread":
            if side == "home":
                return self._gt(self.margin, strike)
            neg = sorted(-x for x in self.margin)
            return self._gt(neg, strike)
        if kind == "team_total":
            return self._gt(self.home if side == "home" else self.away, strike)
        raise ValueError(kind)


# --------------------------------------------------------------------------------------------
# Field (golf / motorsport) engine
# --------------------------------------------------------------------------------------------

class FieldRatings:
    """EWMA of finishing percentile (0 = winner, 1 = last; non-finishers at 1)."""

    def __init__(self, alpha: float, prior: float = 0.5, prior_weight: float = 3.0):
        self.alpha, self.prior, self.pw = alpha, prior, prior_weight
        self.s: dict[str, float] = {}
        self.n: dict[str, int] = defaultdict(int)

    def strength(self, pid: str) -> float:
        v = self.s.get(pid, self.prior)
        n = self.n.get(pid, 0)
        w = n / (n + self.pw)
        return -(w * v + (1 - w) * self.prior)

    def update(self, entrants: list[dict]):
        N = len(entrants)
        fin = [e for e in entrants if e["position"] is not None]
        for e in entrants:
            pct = (e["position"] - 1) / max(1, N - 1) if e["position"] is not None else 1.0
            if e["position"] is None and not fin:
                continue
            prev = self.s.get(e["id"])
            self.s[e["id"]] = pct if prev is None else prev + self.alpha * (pct - prev)
            self.n[e["id"]] += 1


def field_probs(strengths: dict[str, float], beta: float, top_ns: Iterable[int], sims: int, seed: int
                ) -> dict[int, dict[str, float]]:
    ids = sorted(strengths)
    w = [math.exp(beta * strengths[i]) for i in ids]
    tot = sum(w)
    out = {1: {i: wi / tot for i, wi in zip(ids, w)}}
    ns = sorted({n for n in top_ns if n > 1})
    if ns:
        rng = random.Random(seed)
        cnt = {n: defaultdict(int) for n in ns}
        maxn = max(ns)
        for _ in range(sims):
            # Plackett-Luce via Gumbel-max: sort by log w + Gumbel noise
            keys = sorted(((math.log(wi) - math.log(-math.log(rng.random() or 1e-12)), i) for wi, i in zip(w, ids)),
                          reverse=True)
            for rank, (_, i) in enumerate(keys[:maxn], start=1):
                for n in ns:
                    if rank <= n:
                        cnt[n][i] += 1
        for n in ns:
            out[n] = {i: (cnt[n][i] + 0.5) / (sims + 1.0) for i in ids}
    return out

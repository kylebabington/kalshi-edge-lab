"""Calibration and model-market blend fitting with frozen, deterministic selection rules.

Rows are dicts with ``p`` (frozen-model probability), ``y`` (0/1), ``cluster`` (event) and, for
blends, ``p_mkt``. Fitting is event-equal: each row weighs 1 / (rows of its event).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from . import config as C

TIE = 1e-12
FORM_ORDER = {"identity": 0, "intercept": 1, "platt": 2}


def logit(p: float) -> float:
    p = min(1 - C.CLIP, max(C.CLIP, p))
    return math.log(p / (1 - p))


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def event_weights(rows) -> list[float]:
    n = Counter(r["cluster"] for r in rows)
    return [1.0 / n[r["cluster"]] for r in rows]


def event_ll(rows, probs) -> float:
    """Event-equal mean log loss (per-event mean, events averaged), clipped as in Phase 1."""
    per = defaultdict(lambda: [0.0, 0])
    for r, p in zip(rows, probs):
        p = min(1 - C.CLIP, max(C.CLIP, p))
        g = per[r["cluster"]]
        g[0] += -math.log(p) if r["y"] else -math.log(1 - p)
        g[1] += 1
    return sum(a / n for a, n in per.values()) / len(per)


def support(rows) -> dict:
    ev = defaultdict(set)
    for r in rows:
        ev[r["cluster"]].add(r["y"])
    return {"events": len(ev), "pos_events": sum(1 for v in ev.values() if 1 in v),
            "neg_events": sum(1 for v in ev.values() if 0 in v), "rows": len(rows)}


def meets(sup: dict, req: dict) -> bool:
    return sup["events"] >= req["events"] and sup["pos_events"] >= req["per_class_events"] and \
        sup["neg_events"] >= req["per_class_events"]


def newton(feats: list[list[float]], offs: list[float], ys: list[int], ws: list[float], theta0: list[float],
           lam: float) -> list[float] | None:
    """Minimise sum w*logloss(sigma(off + theta.f)) + lam*|theta - theta0|^2; None if not converged."""
    opt = C.CAL["optimizer"]
    k = len(theta0)
    th = list(theta0)

    def obj(t):
        s = 0.0
        for f, o, y, w in zip(feats, offs, ys, ws):
            z = o + sum(a * b for a, b in zip(t, f))
            # log(1 + e^z) - y z, computed stably
            s += w * ((max(z, 0) + math.log1p(math.exp(-abs(z)))) - y * z)
        return s + lam * sum((a - b) ** 2 for a, b in zip(t, theta0))

    cur = obj(th)
    for _ in range(opt["max_iter"]):
        g = [2 * lam * (th[j] - theta0[j]) for j in range(k)]
        H = [[2 * lam if i == j else 0.0 for j in range(k)] for i in range(k)]
        for f, o, y, w in zip(feats, offs, ys, ws):
            q = sigmoid(o + sum(a * b for a, b in zip(th, f)))
            for i in range(k):
                g[i] += w * (q - y) * f[i]
                for j in range(k):
                    H[i][j] += w * q * (1 - q) * f[i] * f[j]
        if max(abs(x) for x in g) < opt["tol_grad"]:
            return th
        step = _solve(H, g)
        if step is None:
            return None
        t = 1.0
        for _h in range(opt["max_halvings"] + 1):
            cand = [a - t * s for a, s in zip(th, step)]
            val = obj(cand)
            if val <= cur:
                break
            t /= 2
        else:
            return None
        moved = max(abs(t * s) for s in step)
        th, cur = cand, val
        if moved < opt["tol_step"]:
            return th
    return None


def _solve(H, g):
    if len(g) == 1:
        return [g[0] / H[0][0]] if H[0][0] > 0 else None
    (a, b), (c, d) = H
    det = a * d - b * c
    if det <= 0 or a <= 0:
        return None
    return [(d * g[0] - b * g[1]) / det, (-c * g[0] + a * g[1]) / det]


# ---------------------------------------------------------------- calibration

def fit_calibration(rows, form: str, lam: float) -> dict | None:
    xs = [logit(r["p"]) for r in rows]
    ys = [r["y"] for r in rows]
    ws = event_weights(rows)
    if form == "identity":
        return {"a": 0.0, "b": 1.0}
    if form == "intercept":
        th = newton([[1.0] for _ in xs], xs, ys, ws, [0.0], lam)
        return None if th is None else {"a": th[0], "b": 1.0}
    th = newton([[1.0, x] for x in xs], [0.0] * len(xs), ys, ws, [0.0, 1.0], lam)
    return None if th is None else {"a": th[0], "b": th[1]}


def apply_calibration(prm: dict, p: float) -> float:
    return sigmoid(prm["a"] + prm["b"] * logit(p))


def select_calibration(train, val, full) -> dict:
    """Frozen selection: thresholds, grid on train, choose on validation, refit on F1-F3."""
    st, sv = support(train), support(val)
    out = {"train_support": st, "validation_support": sv}
    if not meets(st, C.CAL["min_train"]):
        out["status"] = "unavailable: training support below threshold"
        return out
    if not meets(sv, C.CAL["min_validation"]):
        out["status"] = "unavailable: validation support below threshold"
        return out
    grid = [("identity", 0.0)] + [(f, lam) for f in ("intercept", "platt") for lam in C.CAL["lambda_grid"]]
    cands = []
    for form, lam in grid:
        prm = fit_calibration(train, form, lam)
        if prm is None:
            cands.append({"form": form, "lambda": lam, "status": "not converged"})
            continue
        v = event_ll(val, [apply_calibration(prm, r["p"]) for r in val])
        cands.append({"form": form, "lambda": lam, "status": "ok", "params_train": prm, "val_log_loss_event": v})
    best = None
    for c in cands:
        if c["status"] != "ok":
            continue
        if best is None or c["val_log_loss_event"] < best["val_log_loss_event"] - TIE:
            best = c
        elif abs(c["val_log_loss_event"] - best["val_log_loss_event"]) <= TIE:
            key_c = (FORM_ORDER[c["form"]], -c["lambda"])
            key_b = (FORM_ORDER[best["form"]], -best["lambda"])
            if key_c < key_b:
                best = c
    out["candidates"] = cands
    if best is None:
        out["status"] = "unavailable: no candidate converged"
        return out
    final = fit_calibration(full, best["form"], best["lambda"])
    if final is None:
        out["status"] = "unavailable: refit on F1-F3 did not converge"
        return out
    out.update({"status": "ok", "selected": {"form": best["form"], "lambda": best["lambda"]}, "params": final,
                "full_support": support(full)})
    return out


# ---------------------------------------------------------------- blend

def fit_blend(rows, w: float, lam: float | None) -> dict | None:
    offs = [w * logit(r["p"]) + (1 - w) * logit(r["p_mkt"]) for r in rows]
    if lam is None:
        return {"w": w, "c": 0.0, "lambda": None}
    th = newton([[1.0] for _ in rows], offs, [r["y"] for r in rows], event_weights(rows), [0.0], lam)
    return None if th is None else {"w": w, "c": th[0], "lambda": lam}


def apply_blend(prm: dict, p: float, p_mkt: float) -> float:
    return sigmoid(prm["c"] + prm["w"] * logit(p) + (1 - prm["w"]) * logit(p_mkt))


def _blend_key(c):
    return (0 if c["lambda"] is None else 1, -(c["lambda"] or 0.0), c["w"])


def select_blend(train, val, full) -> dict:
    st, sv = support(train), support(val)
    out = {"train_support": st, "validation_support": sv}
    if not meets(st, C.BLEND["min_train"]):
        out["status"] = "unavailable: matched training support below threshold"
        return out
    if not meets(sv, C.BLEND["min_validation"]):
        out["status"] = "unavailable: matched validation support below threshold"
        return out
    cands = []
    for lam in [None] + C.BLEND["intercept"]["ridge_lambda_grid"]:
        for w in C.BLEND["w_grid"]:
            prm = fit_blend(train, w, lam)
            if prm is None:
                cands.append({"w": w, "lambda": lam, "status": "not converged"})
                continue
            v = event_ll(val, [apply_blend(prm, r["p"], r["p_mkt"]) for r in val])
            cands.append({"w": w, "lambda": lam, "status": "ok", "c_train": prm["c"], "val_log_loss_event": v})
    best = None
    for c in cands:
        if c["status"] != "ok":
            continue
        if best is None or c["val_log_loss_event"] < best["val_log_loss_event"] - TIE or (
                abs(c["val_log_loss_event"] - best["val_log_loss_event"]) <= TIE and _blend_key(c) < _blend_key(best)):
            best = c
    out["candidates"] = cands
    if best is None:
        out["status"] = "unavailable: no candidate converged"
        return out
    final = fit_blend(full, best["w"], best["lambda"])
    if final is None:
        out["status"] = "unavailable: refit on F1-F3 did not converge"
        return out
    out.update({"status": "ok", "selected": {"w": best["w"], "lambda": best["lambda"]}, "params": final,
                "full_support": support(full)})
    return out

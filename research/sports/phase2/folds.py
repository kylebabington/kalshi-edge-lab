"""Walk-forward out-of-fold forecasts from the frozen Phase 1 models.

For fold F = [F_start, F_end) the Phase 1 selection is re-run with its training window ending at
the *fitting boundary* ``B = F_start - horizon - (result_lag + extra)``, so every label used to
choose parameters was available before the fold's first forecast cutoff. This is verified per
competition with the Phase 1 availability rules; a violating competition-fold is excluded.
Forecasts are then produced by the unmodified Phase 1 code (each one replays only results released
by its own cutoff) over the window [B, F_end), and contracts starting before F_start are dropped.

Phase 1 modules read their window from module globals; :func:`fold_window` sets them temporarily
and always restores them. No Phase 1 file is modified.
"""

from __future__ import annotations

from contextlib import contextmanager

from .. import models as M
from . import config as C


@contextmanager
def fold_window(test_start: float, test_end: float):
    saved = (M.T_TEST, M.T_END)
    M.T_TEST, M.T_END = test_start, test_end
    try:
        yield
    finally:
        M.T_TEST, M.T_END = saved


def boundary(comp, fold_start: float) -> float:
    lag_h = comp.result_lag_hours + C.BOUNDARY_EXTRA_LAG_H[comp.source]
    return fold_start - M.HORIZON_MIN * 60 - lag_h * 3600


def selection_label_times(comp, events) -> list[float]:
    """Availability times of the labels Phase 1 selection reads under the current window."""
    src = comp.source
    if src == "espn_team":
        return [M.ts(e["start"]) + comp.result_lag_hours * 3600 for e in M._train_games(comp, events)]
    if src == "espn_fight":
        return [M.ts(e["start"]) + comp.result_lag_hours * 3600 for e in events
                if e["completed"] and M.T_TRAIN <= M.ts(e["start"]) < M.T_TEST]
    if src in ("espn_golf", "jolpica"):
        return [M._field_avail(comp, e) for e in events if e["completed"] and M.T_TRAIN <= M.ts(e["start"]) < M.T_TEST]
    if src == "kalshi_only":
        out = []
        for e in events:
            if e.get("start") and M.ts(e["start"]) < M.T_TEST:
                out.append(M.ts(e["available_at"]) if e.get("available_at") else float("inf"))
        return out
    raise ValueError(src)


def primary_row(rows: list[dict], family: str) -> dict | None:
    """Phase 1 primary model for one contract (same rule as evaluate.primary_model)."""
    by = {r["model"]: r for r in rows if r.get("p") is not None}
    for m in (("elo", "score") if family == "game_winner" else ("score", "elo")):
        if m in by:
            return by[m]
    return None


def run_fold(comp, events, contracts, fold: str) -> tuple[dict, list[dict]]:
    """(status, out-of-fold primary rows) for one competition and fold."""
    f0, f1 = (M.ts(x) for x in C.PERIODS[fold])
    B = boundary(comp, f0)
    first_cutoff = f0 - M.HORIZON_MIN * 60
    st = {"fold": fold, "boundary": B, "fold_start": f0, "fold_end": f1}
    fold_contracts = [c for c in contracts if c["status"] == "ok" and c.get("start") and f0 <= M.ts(c["start"]) < f1]
    st["fold_contracts"] = len(fold_contracts)
    if not fold_contracts:
        st["status"] = "no contracts in fold"
        return st, []
    with fold_window(B, f1):
        times = selection_label_times(comp, events)
        st["selection_labels"] = len(times)
        late = [t for t in times if t > first_cutoff]
        if late:
            st["status"] = "excluded: selection label available after the fold's first cutoff"
            st["late_labels"] = len(late)
            return st, []
        params = M.SELECT[comp.source](comp, events, contracts)
        if params.get("blocked"):
            st["status"] = f"blocked: {params['blocked']}"
            return st, []
        st["params"] = params
        window = [c for c in contracts if c["status"] == "ok" and c.get("start") and B <= M.ts(c["start"]) < f1]
        preds = M.PREDICT[comp.source](comp, events, window, params)
    fam = {c["ticker"]: c["family"] for c in fold_contracts}
    by_t = {}
    for p in preds:
        if p["ticker"] in fam:
            by_t.setdefault(p["ticker"], []).append(p)
    out = []
    for t in sorted(by_t):
        r = primary_row(by_t[t], fam[t])
        if r is not None:
            out.append({"ticker": t, "fold": fold, "model": r["model"], "p": r["p"], "cluster": r["cluster"],
                        "start": r["start"], "cutoff": r["cutoff"], "family": fam[t]})
    st["status"] = "ok"
    st["oof_rows"] = len(out)
    return st, out

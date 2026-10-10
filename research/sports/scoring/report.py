"""Descriptive pilot report over finalized settlement scores (offline).

Metrics match the frozen Phase 1 evaluator: Brier on the original probability, log loss on
``clip(p, 1e-4)``. Events are ``competition|cluster``; every contract of one event is one cluster.
Paired differences use only contracts where both candidates have a probability (candidate-specific
common sets). Point estimates only: this is an operational pilot, not a representative sample.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict

from .. import evaluate as EV
from ..live import predict as LP
from . import config as C
from . import score as SC

CSV_COLUMNS = ["group_by", "group", "kind", "name", "contracts", "events", "brier", "brier_event",
               "log_loss", "log_loss_event"]


def load_scores() -> list[dict]:
    out = []
    for p in sorted((C.SCORE_DIR / "finalized").glob("*/*/*.json")):
        out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


def labelled_rows(scores: list[dict], cand: str) -> list[dict]:
    rows = []
    for s in scores:
        if s["y"] is None:
            continue
        c = s["candidates"][cand]
        if "p" not in c:
            continue
        if not SC.valid_p(c["p"]):
            raise ValueError(f"invalid {cand} probability {c['p']!r} in {s['ticker']}")
        rows.append({"p": c["p"], "y": s["y"], "cluster": s["event_key"], "ticker": s["ticker"]})
    return rows


def candidate_metrics(scores: list[dict], cand: str) -> dict:
    m = EV.metrics(labelled_rows(scores, cand))
    if not m["n"]:
        return {"contracts": 0, "events": 0}
    return {"contracts": m["n"], "events": m["clusters"], "brier": m["brier"], "brier_event": m["brier_event"],
            "log_loss": m["log_loss"], "log_loss_event": m["log_loss_event"]}


def paired(scores: list[dict], a: str, b: str) -> dict:
    """Mean loss difference a - b on contracts where both have a probability (contract and event views)."""
    ra = {r["ticker"]: r for r in labelled_rows(scores, a)}
    rb = {r["ticker"]: r for r in labelled_rows(scores, b)}
    common = sorted(set(ra) & set(rb))
    if not common:
        return {"contracts": 0, "events": 0}
    per = defaultdict(list)
    for t in common:
        la, lb = EV._losses(ra[t]), EV._losses(rb[t])
        per[ra[t]["cluster"]].append((la[0] - lb[0], la[1] - lb[1]))
    flat = [d for v in per.values() for d in v]
    ev = [(sum(d[0] for d in v) / len(v), sum(d[1] for d in v) / len(v)) for v in per.values()]
    return {"contracts": len(flat), "events": len(per),
            "brier": sum(d[0] for d in flat) / len(flat), "brier_event": sum(e[0] for e in ev) / len(ev),
            "log_loss": sum(d[1] for d in flat) / len(flat), "log_loss_event": sum(e[1] for e in ev) / len(ev)}


def groups(scores: list[dict]):
    yield "all", "all", scores
    for g in C.GROUP_KEYS:
        by = defaultdict(list)
        for s in scores:
            by[str(s.get(g))].append(s)
        for k in sorted(by):
            yield g, k, by[k]


def table(scores: list[dict]) -> list[dict]:
    rows = []
    for gb, gk, ss in groups(scores):
        for cand in LP.CANDIDATES:
            rows.append({"group_by": gb, "group": gk, "kind": "candidate", "name": cand, **candidate_metrics(ss, cand)})
        for a, b in C.PAIRS:
            rows.append({"group_by": gb, "group": gk, "kind": "paired_diff", "name": f"{a}-{b}", **paired(ss, a, b)})
    return rows


def latest_run() -> dict | None:
    runs = sorted((C.SCORE_DIR / "runs").glob("*.json"))
    return json.loads(runs[-1].read_text(encoding="utf-8")) if runs else None


def coverage(scores: list[dict]) -> dict:
    eligible, excluded = SC.scan()
    ok_contracts = sum(1 for _, r in eligible for c in r["contracts"] if c["status"] == "ok")
    horizons = [r["horizon_minutes"] for _, r in eligible if r.get("horizon_minutes") is not None]
    states = Counter(s["settlement"]["state"] for s in scores)
    available, missing, missing_lab = Counter(), defaultdict(Counter), defaultdict(Counter)
    for _, r in eligible:
        for c in r["contracts"]:
            if c["status"] != "ok":
                continue
            for k in LP.CANDIDATES:
                if "p" in c["candidates"][k]:
                    available[k] += 1
                else:
                    missing[k][c["candidates"][k].get("unavailable")] += 1
    for s in scores:
        if s["y"] is None:
            continue
        for k in LP.CANDIDATES:
            if "p" not in s["candidates"][k]:
                missing_lab[k][s["candidates"][k].get("unavailable")] += 1
    last = latest_run()
    pending = Counter(d["state"] for d in (last or {}).get("diagnostics", []))
    return {"eligible_records": len(eligible), "eligible_events": len({SC.event_key(r) for _, r in eligible}),
            "eligible_contracts": ok_contracts, "finalized_scores": len(scores),
            "finalized_states": dict(states), "labelled_contracts": sum(1 for s in scores if s["y"] is not None),
            "labelled_events": len({s["event_key"] for s in scores if s["y"] is not None}),
            "horizon_minutes": ({"min": min(horizons), "median": statistics.median(horizons), "max": max(horizons)}
                                if horizons else None),
            "candidate_available_eligible": dict(available),
            "missing_inputs_eligible": {k: dict(v) for k, v in missing.items()},
            "missing_inputs_labelled": {k: dict(v) for k, v in missing_lab.items()},
            "latest_run": (last or {}).get("run_id"), "not_final_in_latest_run": dict(pending),
            "excluded_records": dict(excluded)}


def write_csv(rows: list[dict]) -> None:
    C.REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(C.REPORT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{r[k]:.6f}" if isinstance(r.get(k), float) else r.get(k, "")) for k in CSV_COLUMNS})


def _fmt(v) -> str:
    return f"{v:.4f}" if isinstance(v, float) else "-"


def stage_report(log, write: bool = True) -> int:
    scores = load_scores()
    cov = coverage(scores)
    log(C.PILOT_NOTE)
    for k, v in cov.items():
        log(f"{k}: {v}")
    rows = table(scores)
    for r in rows:
        if r["group_by"] in ("all", "sport", "family", "cohort") and r.get("contracts"):
            log(f"{r['group_by']:<11} {r['group']:<24} {r['kind']:<11} {r['name']:<18} n={r['contracts']:>4} "
                f"events={r['events']:>3} brier={_fmt(r.get('brier'))} (event {_fmt(r.get('brier_event'))}) "
                f"logloss={_fmt(r.get('log_loss'))} (event {_fmt(r.get('log_loss_event'))})")
    if write:
        write_csv(rows)
        log(f"wrote {C.REPORT_CSV} ({len(rows)} rows)")
    return 0

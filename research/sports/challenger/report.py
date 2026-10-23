"""Read-only prospective reporting for isolated ITF challenger captures.

Only complete prediction records after the challenger freeze, inside the capture window,
with capture-time evidence that the outcome was not yet available, enter metrics.
Later settlement does not prove that unavailability. Missing timing evidence is
cannot_verify and is kept out of Brier. Unknown/void/nonbinary settlements are counted
separately and never enter binary Brier.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime  # timezone-aware ISO timestamps on records

from .. import models as M
from ..live import config as LC
from . import itf_singles_v1 as CH
from .constants import CAPTURE_DIR, COMPETITIONS, NOMINAL_HORIZON_MIN
from .records import inspect_record, list_complete


BINARY_Y = {"SETTLED_YES": 1, "SETTLED_NO": 0}
NON_BINARY = ("VOID_OR_CANCELLED", "NONBINARY", "INCONSISTENT", "LOOKUP_FAILED", "CONFLICTING_DUPLICATE")


def _ts(value) -> float | None:
    if not value:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load_challenger_protocol() -> dict:
    return CH.load_protocol()


def freeze_ts() -> float:
    return M.ts(load_challenger_protocol()["frozen_at"])


def prospective_eligibility(pred: dict, freeze: float | None = None) -> tuple[str, str | None]:
    """verified | excluded | cannot_verify. Settlement is not consulted."""
    freeze = freeze_ts() if freeze is None else freeze
    as_of = _ts(pred.get("prediction_as_of"))
    recorded = _ts(pred.get("recorded_at"))
    start = _ts(pred.get("start"))
    if as_of is None or recorded is None or start is None:
        return "cannot_verify", "missing_essential_timing"
    if as_of <= freeze or recorded <= freeze:
        return "excluded", "before_challenger_freeze"
    cutoff = start - NOMINAL_HORIZON_MIN * 60
    w_open = cutoff - LC.WINDOW_MIN * 60
    if not (w_open <= as_of <= recorded <= cutoff):
        return "excluded", "outside_capture_window"
    outcome = (pred.get("outcome_availability") or {}).get("capture_time_status")
    if outcome == "available":
        return "excluded", "outcome_available_at_capture"
    if outcome != "unavailable":
        return "cannot_verify", "missing_capture_time_outcome_evidence"
    evidence = pred.get("evidence") or []
    if not evidence:
        return "cannot_verify", "missing_evidence_receipts"
    late = [e for e in evidence if _ts(e.get("received_at")) is not None and _ts(e["received_at"]) > as_of]
    if late:
        return "cannot_verify", "evidence_received_after_prediction_as_of"
    return "verified", None


def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def event_equal_brier(rows: list[dict], p_key: str) -> dict:
    """Mean of per-event mean Brier. ``rows`` must already be binary-labelled."""
    per = defaultdict(list)
    for r in rows:
        per[r["cluster"]].append(_brier(r[p_key], r["y"]))
    if not per:
        return {"n_contracts": 0, "n_events": 0, "brier_event": None, "brier_contract": None}
    brier_event = sum(sum(v) / len(v) for v in per.values()) / len(per)
    all_b = [x for v in per.values() for x in v]
    return {
        "n_contracts": len(all_b),
        "n_events": len(per),
        "brier_event": brier_event,
        "brier_contract": sum(all_b) / len(all_b),
    }


def event_equal_delta(rows: list[dict], a_key: str, b_key: str) -> dict:
    """Event-equal mean of (Brier_a - Brier_b) on the common set ``rows``."""
    per = defaultdict(list)
    for r in rows:
        per[r["cluster"]].append(_brier(r[a_key], r["y"]) - _brier(r[b_key], r["y"]))
    if not per:
        return {"n_contracts": 0, "n_events": 0, "d_brier_event": None, "d_brier_contract": None}
    d_event = sum(sum(v) / len(v) for v in per.values()) / len(per)
    all_d = [x for v in per.values() for x in v]
    return {
        "n_contracts": len(all_d),
        "n_events": len(per),
        "d_brier_event": d_event,
        "d_brier_contract": sum(all_d) / len(all_d),
        "common_set": f"{a_key} and {b_key} both present with binary y",
    }


def summarize() -> dict:
    proto = load_challenger_protocol()
    freeze = M.ts(proto["frozen_at"])
    preds = []
    incomplete = []
    for p, rec in list_complete(CAPTURE_DIR / "predictions"):
        preds.append(rec)
    pred_root = CAPTURE_DIR / "predictions"
    if pred_root.is_dir():
        for p in sorted(pred_root.glob("**/*.json")):
            rec, status = inspect_record(p)
            if status != "ok":
                incomplete.append({"path": str(p), "status": status})
    exclusions = [r for _, r in list_complete(CAPTURE_DIR / "exclusions")]
    settlements = {f"{r['competition']}|{r['cluster']}": r
                   for _, r in list_complete(CAPTURE_DIR / "settlements")}

    recorded_events, recorded_contracts = 0, 0
    by_elig = defaultdict(lambda: {"events": 0, "contracts": 0, "reasons": defaultdict(int)})
    pending = {"events": 0, "contracts": 0}
    settled_bin = {"events": 0, "contracts": 0}
    nonbinary_counts = defaultdict(int)
    metric_rows = []

    for rec in preds:
        cs = rec.get("contracts") or []
        recorded_events += 1
        recorded_contracts += len(cs)
        elig, why = prospective_eligibility(rec, freeze)
        by_elig[elig]["events"] += 1
        by_elig[elig]["contracts"] += len(cs)
        if why:
            by_elig[elig]["reasons"][why] += 1
        key = f"{rec.get('competition')}|{rec.get('cluster')}"
        stl = settlements.get(key)
        if stl is None:
            pending["events"] += 1
            pending["contracts"] += len(cs)
            continue
        binary_here = 0
        for c in cs:
            row = next((x for x in (stl.get("contracts") or []) if x.get("ticker") == c.get("ticker")), None)
            state = (row or {}).get("state") or "LOOKUP_FAILED"
            if state in BINARY_Y:
                binary_here += 1
                if elig == "verified":
                    pch = (c.get("challenger") or {}).get("p")
                    pfz = (c.get("paired_frozen") or {}).get("p")
                    pmk = (c.get("market") or {}).get("p")
                    metric_rows.append({
                        "cluster": rec["cluster"],
                        "ticker": c["ticker"],
                        "y": BINARY_Y[state],
                        "p_challenger": pch,
                        "p_paired_frozen": pfz,
                        "p_market": pmk,
                    })
            else:
                nonbinary_counts[state] += 1
        if binary_here:
            settled_bin["events"] += 1
            settled_bin["contracts"] += binary_here

    ch_fz = [r for r in metric_rows
             if isinstance(r.get("p_challenger"), (int, float))
             and isinstance(r.get("p_paired_frozen"), (int, float))]
    ch_mk = [r for r in metric_rows
             if isinstance(r.get("p_challenger"), (int, float))
             and isinstance(r.get("p_market"), (int, float))]
    for r in ch_fz:
        r["p_a"], r["p_b"] = r["p_challenger"], r["p_paired_frozen"]
        r["y"] = r["y"]
    # event_equal_delta expects a_key/b_key on rows
    d_fz = event_equal_delta(
        [{"cluster": r["cluster"], "p_challenger": r["p_challenger"],
          "p_paired_frozen": r["p_paired_frozen"], "y": r["y"]} for r in ch_fz],
        "p_challenger", "p_paired_frozen")
    d_mk = event_equal_delta(
        [{"cluster": r["cluster"], "p_challenger": r["p_challenger"],
          "p_market": r["p_market"], "y": r["y"]} for r in ch_mk],
        "p_challenger", "p_market")

    return {
        "research_only": True,
        "no_bet": True,
        "protocol": proto.get("protocol"),
        "challenger_frozen_at": proto.get("frozen_at"),
        "candidate_id": proto.get("candidate_id"),
        "competitions": list(COMPETITIONS),
        "incomplete_files": incomplete,
        "recorded": {"events": recorded_events, "contracts": recorded_contracts},
        "excluded_records": {"events": len(exclusions),
                             "reasons": _count(exclusions, "reason")},
        "eligibility": {k: {"events": v["events"], "contracts": v["contracts"],
                            "reasons": dict(v["reasons"])} for k, v in by_elig.items()},
        "pending": pending,
        "settled_binary": settled_bin,
        "settled_non_binary": dict(nonbinary_counts),
        "comparisons": {
            "challenger_minus_paired_frozen": {
                **d_fz,
                "note": "paired k=24 reference computed on the same as-of history; not assumed equal to live capture_v2",
            },
            "challenger_minus_market": d_mk,
        },
        "promotion": "none",
        "profitability_claim": "none",
        "significance_gate": "none",
        "development_replay": (
            "Predictions recorded outside the window, after the outcome was available, or without "
            "capture-time unavailability evidence remain development replay and are not in the "
            "prospective metrics."
        ),
    }


def _count(rows, key):
    out = defaultdict(int)
    for r in rows:
        out[r.get(key) or "unspecified"] += 1
    return dict(out)


def render(summary: dict) -> str:
    elig = summary.get("eligibility") or {}
    cf = summary["comparisons"]["challenger_minus_paired_frozen"]
    cm = summary["comparisons"]["challenger_minus_market"]
    nb = summary.get("settled_non_binary") or {}
    return (
        f"ITF challenger prospective report (RESEARCH_ONLY / NO_BET)\n"
        f"challenger frozen_at {summary['challenger_frozen_at']}\n"
        f"recorded events={summary['recorded']['events']} contracts={summary['recorded']['contracts']}\n"
        f"exclusions events={summary['excluded_records']['events']}\n"
        f"eligibility { {k: v['events'] for k, v in elig.items()} }\n"
        f"pending events={summary['pending']['events']}  settled_binary events={summary['settled_binary']['events']}\n"
        f"non-binary settlement counts {nb or '{}'}\n"
        f"challenger-paired_frozen d_brier_event={cf.get('d_brier_event')!r} "
        f"n_events={cf.get('n_events')} n_contracts={cf.get('n_contracts')}\n"
        f"challenger-market d_brier_event={cm.get('d_brier_event')!r} "
        f"n_events={cm.get('n_events')} n_contracts={cm.get('n_contracts')}\n"
        f"no promotion, no profitability claim, no new significance gate\n"
    )

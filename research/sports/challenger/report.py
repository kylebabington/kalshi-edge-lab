"""Read-only prospective reporting for isolated ITF challenger captures.

Default path prints a summary and writes nothing. Settlement observations supply
outcomes; they do not establish capture-time eligibility. Each prediction is
checked against its own frozen wrapper protocol, never against today's modified
wrapper code. RESEARCH_ONLY / NO_BET.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .. import models as M
from ..live import config as LC
from . import itf_singles_v1 as CH
from . import outcomes as OC
from .constants import CAPTURE_DIR, CHALLENGER_CAPTURE_V1, CHALLENGER_CAPTURE_V2, COMPETITIONS, NOMINAL_HORIZON_MIN
from .records import inspect_record, list_complete


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
    pin_problems = OC.verify_prediction_against_own_protocol(pred)
    if pin_problems:
        return "cannot_verify", pin_problems[0]
    return "verified", None


def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def event_equal_brier(rows: list[dict], p_key: str) -> dict:
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


def _comparisons(metric_rows: list[dict]) -> dict:
    ch_fz = [r for r in metric_rows
             if isinstance(r.get("p_challenger"), (int, float))
             and isinstance(r.get("p_paired_frozen"), (int, float))]
    ch_mk = [r for r in metric_rows
             if isinstance(r.get("p_challenger"), (int, float))
             and isinstance(r.get("p_market"), (int, float))]
    return {
        "challenger_minus_paired_frozen": {
            **event_equal_delta(ch_fz, "p_challenger", "p_paired_frozen"),
            "note": "paired k=24 reference on the same as-of history; not assumed equal to live capture_v2",
        },
        "challenger_minus_market": event_equal_delta(ch_mk, "p_challenger", "p_market"),
    }


def _empty_block():
    return {
        "recorded": {"events": 0, "contracts": 0},
        "eligibility": {},
        "pending": {"events": 0, "contracts": 0},
        "settled_binary": {"events": 0, "contracts": 0},
        "settled_non_binary": {},
        "conflicting": {"events": 0, "contracts": 0},
        "quarantined_observations": 0,
        "comparisons": _comparisons([]),
    }


def _ingest(preds: list[dict]) -> tuple[dict, list[dict]]:
    block = _empty_block()
    by_elig = defaultdict(lambda: {"events": 0, "contracts": 0, "reasons": defaultdict(int)})
    pending = {"events": 0, "contracts": 0}
    settled_bin = {"events": 0, "contracts": 0}
    conflicting = {"events": 0, "contracts": 0}
    nonbinary_counts = defaultdict(int)
    metric_rows = []
    n_quarantined = 0
    freeze = freeze_ts()
    recorded_events = recorded_contracts = 0
    for rec in preds:
        cs = rec.get("contracts") or []
        recorded_events += 1
        recorded_contracts += len(cs)
        elig, why = prospective_eligibility(rec, freeze)
        by_elig[elig]["events"] += 1
        by_elig[elig]["contracts"] += len(cs)
        if why:
            by_elig[elig]["reasons"][why] += 1
        derived = OC.derive_event(rec)
        n_quarantined += derived["n_quarantined"]
        binary_here = 0
        conf_here = 0
        unresolved = derived["unresolved_tickers"]
        for c in cs:
            t = c.get("ticker")
            d = (derived["contracts"] or {}).get(t) or {"state": "MISSING", "y": None, "scoreable": False}
            state = d.get("state")
            if d.get("scoreable") and elig == "verified":
                binary_here += 1
                pch = (c.get("challenger") or {}).get("p")
                pfz = (c.get("paired_frozen") or {}).get("p")
                pmk = (c.get("market") or {}).get("p")
                metric_rows.append({
                    "cluster": rec["cluster"], "ticker": t, "y": d["y"],
                    "p_challenger": pch, "p_paired_frozen": pfz, "p_market": pmk,
                    "wrapper_protocol": rec.get("protocol"),
                })
            elif state == "CONFLICTING":
                conf_here += 1
            elif state in OC.TERMINAL_STATES and state not in OC.BINARY_Y:
                nonbinary_counts[state] += 1
            elif state in OC.REVIEW_STATES:
                nonbinary_counts[state] += 1
        if binary_here:
            settled_bin["events"] += 1
            settled_bin["contracts"] += binary_here
        if conf_here:
            conflicting["events"] += 1
            conflicting["contracts"] += conf_here
        if unresolved:
            pending["events"] += 1
            pending["contracts"] += len(unresolved)
    block["recorded"] = {"events": recorded_events, "contracts": recorded_contracts}
    block["eligibility"] = {k: {"events": v["events"], "contracts": v["contracts"],
                                "reasons": dict(v["reasons"])} for k, v in by_elig.items()}
    block["pending"] = pending
    block["settled_binary"] = settled_bin
    block["settled_non_binary"] = dict(nonbinary_counts)
    block["conflicting"] = conflicting
    block["quarantined_observations"] = n_quarantined
    block["comparisons"] = _comparisons(metric_rows)
    return block, metric_rows


def summarize() -> dict:
    proto = load_challenger_protocol()
    preds = [rec for _, rec in list_complete(CAPTURE_DIR / "predictions")]
    incomplete = []
    pred_root = CAPTURE_DIR / "predictions"
    if pred_root.is_dir():
        for p in sorted(pred_root.glob("**/*.json")):
            rec, status = inspect_record(p)
            if status != "ok":
                incomplete.append({"path": str(p), "status": status})
    exclusions = [r for _, r in list_complete(CAPTURE_DIR / "exclusions")]
    by_version = defaultdict(list)
    for rec in preds:
        by_version[rec.get("protocol") or "unknown"].append(rec)
    version_blocks = {}
    for pid in (CHALLENGER_CAPTURE_V1, CHALLENGER_CAPTURE_V2, *sorted(set(by_version) - {CHALLENGER_CAPTURE_V1, CHALLENGER_CAPTURE_V2})):
        if pid in by_version:
            version_blocks[pid], _ = _ingest(by_version[pid])
    combined, _ = _ingest(preds)
    return {
        "research_only": True,
        "no_bet": True,
        "challenger_frozen_at": proto.get("frozen_at"),
        "candidate_id": proto.get("candidate_id"),
        "competitions": list(COMPETITIONS),
        "incomplete_files": incomplete,
        "excluded_records": {"events": len(exclusions), "reasons": _count(exclusions, "reason")},
        "by_wrapper_version": version_blocks,
        "combined": combined,
        "combined_pools_wrapper_versions": True,
        "combined_versions": sorted(by_version),
        "combined_note": (
            "combined metrics pool every complete prediction regardless of wrapper version. "
            "Per-version blocks do not pool. Each comparison prints its own n_events and n_contracts."
        ),
        "promotion": "none",
        "profitability_claim": "none",
        "significance_gate": "none",
        "development_replay": (
            "Predictions recorded outside the window, after the outcome was available, or without "
            "capture-time unavailability evidence remain development replay and are not in the "
            "prospective metrics. Settlement observations never establish eligibility."
        ),
    }


def _count(rows, key):
    out = defaultdict(int)
    for r in rows:
        out[r.get(key) or "unspecified"] += 1
    return dict(out)


def render(summary: dict) -> str:
    combined = summary.get("combined") or _empty_block()
    elig = combined.get("eligibility") or {}
    cf = combined["comparisons"]["challenger_minus_paired_frozen"]
    cm = combined["comparisons"]["challenger_minus_market"]
    nb = combined.get("settled_non_binary") or {}
    versions = summary.get("combined_versions") or []
    by_v = summary.get("by_wrapper_version") or {}
    ver_lines = []
    for pid, blk in by_v.items():
        ver_lines.append(
            f"  {pid}: recorded_events={blk['recorded']['events']} "
            f"pending={blk['pending']['events']} binary={blk['settled_binary']['events']} "
            f"conflicting={blk['conflicting']['events']}"
        )
    return (
        f"ITF challenger prospective report (RESEARCH_ONLY / NO_BET)\n"
        f"challenger frozen_at {summary['challenger_frozen_at']}\n"
        f"wrapper versions present {versions or 'none'}\n"
        f"combined pools wrapper versions={summary.get('combined_pools_wrapper_versions')}\n"
        + ("\n".join(ver_lines) + "\n" if ver_lines else "")
        + f"recorded events={combined['recorded']['events']} contracts={combined['recorded']['contracts']}\n"
        f"exclusions events={summary['excluded_records']['events']}\n"
        f"eligibility { {k: v['events'] for k, v in elig.items()} }\n"
        f"pending events={combined['pending']['events']}  settled_binary events={combined['settled_binary']['events']}\n"
        f"conflicting events={combined['conflicting']['events']} contracts={combined['conflicting']['contracts']}\n"
        f"non-binary settlement counts {nb or '{}'}\n"
        f"quarantined observations {combined.get('quarantined_observations')}\n"
        f"challenger-paired_frozen d_brier_event={cf.get('d_brier_event')!r} "
        f"n_events={cf.get('n_events')} n_contracts={cf.get('n_contracts')}\n"
        f"challenger-market d_brier_event={cm.get('d_brier_event')!r} "
        f"n_events={cm.get('n_events')} n_contracts={cm.get('n_contracts')}\n"
        f"no promotion, no profitability claim, no new significance gate\n"
    )

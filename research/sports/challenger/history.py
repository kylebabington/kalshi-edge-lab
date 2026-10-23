"""As-of history for the frozen ITF helper.

``itf_singles_v1.predict(events, queries, params)`` applies Kalshi releases with
``available_at <= start - 60 minutes``. A capture at start-80 must not feed it results
released in the following 20 minutes. This module builds that filtered list and refuses
to call the helper when the remaining rows are not proven safe.

The helper is not modified. Men's and women's event lists stay separate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import models as M
from ..live import sources as S
from . import itf_singles_v1 as CH
from .constants import NOMINAL_HORIZON_MIN, PAIRED_FROZEN_PARAMS


INTERNAL = ("_receipt_at", "_receipt_source")


def _ts(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    return None


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def strip_internal(e: dict) -> dict:
    return {k: v for k, v in e.items() if k not in INTERNAL}


def annotate(events: list[dict], receipt_at: str, source: str) -> list[dict]:
    out = []
    for e in events:
        row = dict(e)
        row["_receipt_at"] = receipt_at
        row["_receipt_source"] = source
        out.append(row)
    return out


def release_ts(e: dict) -> float | None:
    if e.get("available_at") and e.get("winner_id"):
        return _ts(e["available_at"])
    return None


def receipt_ts(e: dict) -> float | None:
    return _ts(e.get("_receipt_at"))


def helper_cutoff(start_ts: float) -> float:
    return start_ts - NOMINAL_HORIZON_MIN * 60


def helper_would_apply(e: dict, start_ts: float) -> bool:
    r = release_ts(e)
    return r is not None and r <= helper_cutoff(start_ts)


def safety_problems(view: list[dict], as_of: float, start_ts: float) -> list[str]:
    """If the frozen helper ran on ``view``, would it use a post-as_of release?"""
    problems = []
    cutoff = helper_cutoff(start_ts)
    for e in view:
        r = release_ts(e)
        if r is None:
            if e.get("winner_id") and e.get("completed"):
                problems.append(
                    f"completed result {e.get('source_game_id')} has no available_at; helper safety unknown"
                )
            continue
        if r > as_of and r <= cutoff:
            problems.append(
                f"result {e.get('source_game_id')} released at {_iso(r)} is after prediction_as_of "
                f"and at or before the helper cutoff {_iso(cutoff)}"
            )
        elif r > as_of:
            problems.append(
                f"result {e.get('source_game_id')} released at {_iso(r)} is after prediction_as_of"
            )
        rec = receipt_ts(e)
        if rec is not None and rec > as_of and r <= cutoff:
            problems.append(
                f"result {e.get('source_game_id')} locally received at {_iso(rec)} after prediction_as_of"
            )
    return problems


def as_of_history(comp, events: list[dict], as_of: float, start_ts: float) -> tuple[list[dict] | None, dict]:
    """Keep only results released and locally received by ``as_of``; hide the rest.

    Returns (view, meta). view is None when the helper cannot be called safely.
    """
    used, hidden = 0, 0
    missing_receipt = []
    hide_failed = []
    view = []
    for e in events:
        rel = release_ts(e)
        rec = receipt_ts(e)
        if rel is not None and rec is None:
            missing_receipt.append(e.get("source_game_id"))
            continue
        keep = rel is not None and rel <= as_of and rec is not None and rec <= as_of
        if keep:
            view.append(strip_internal(e))
            used += 1
            continue
        hid = S.hide(comp, e)
        if hid.get("available_at") or hid.get("winner_id"):
            hide_failed.append(e.get("source_game_id"))
        view.append(strip_internal(hid))
        hidden += 1
    meta = {
        "results_used": used,
        "events_hidden": hidden,
        "n_input": len(events),
        "n_view": len(view),
        "prediction_as_of": _iso(as_of),
        "helper_cutoff": _iso(helper_cutoff(start_ts)),
        "pool": getattr(comp, "key", None),
    }
    if missing_receipt:
        meta["unavailable"] = (
            "missing_local_receipt: results lack a local receipt time at or before prediction_as_of: "
            + ",".join(str(x) for x in missing_receipt[:8])
        )
        return None, meta
    if hide_failed:
        meta["unavailable"] = (
            "helper_unsafe_hide_failed: outcome fields remained after hide for "
            + ",".join(str(x) for x in hide_failed[:8])
        )
        return None, meta
    problems = safety_problems(view, as_of, start_ts)
    if problems:
        meta["unavailable"] = "helper_unsafe: " + problems[0]
        meta["safety_problems"] = problems
        return None, meta
    meta["status"] = "ok"
    return view, meta


def queries_for_contracts(contracts: list[dict], start_ts: float) -> tuple[list[tuple], str | None]:
    """(key, participant_uuid, opponent_uuid, start_timestamp) per ok contract."""
    ok = [c for c in contracts if c.get("status") == "ok" and c.get("participant")]
    parts = sorted({c["participant"] for c in ok})
    if len(parts) != 2:
        return [], "not_two_participant_uuids"
    if not ok:
        return [], "no_ok_contracts"
    queries = []
    for c in ok:
        opp = parts[0] if c["participant"] == parts[1] else parts[1]
        queries.append((c["ticker"], c["participant"], opp, start_ts))
    return queries, None


def predict_paired(events: list[dict], queries: list[tuple], challenger_params: dict) -> tuple[dict, dict]:
    """k=48 and k=24 on the identical filtered event list and queries."""
    p_ch = CH.predict(events, queries, challenger_params)
    p_fz = CH.predict(events, queries, PAIRED_FROZEN_PARAMS)
    return p_ch, p_fz


def capture_time_outcome(contracts: list[dict], raw_by_ticker: dict, as_of_view: list[dict],
                         cluster: str, as_of: float) -> dict:
    """Whether the outcome was already available from capture-time evidence (not a later settlement)."""
    by_id = {e.get("source_game_id"): e for e in as_of_view}
    hist = by_id.get(cluster) or {}
    in_history = bool(hist.get("winner_id") and release_ts(hist) is not None and release_ts(hist) <= as_of)
    rows = []
    any_available = False
    any_unverified = False
    for c in contracts:
        raw = raw_by_ticker.get(c["ticker"]) or {}
        status = raw.get("status")
        result = raw.get("result")
        settlement_ts = raw.get("settlement_ts")
        kind, _ = None, None
        try:
            from ..adapters import kalshi_settlement_outcome
            kind, _ = kalshi_settlement_outcome(raw) if raw else ("unsettled", None)
        except Exception:
            kind = None
        if in_history or kind in ("yes", "no", "void") or status in ("finalized", "settled"):
            state = "available"
            any_available = True
        elif status in ("active", "open", "initialized", "unopened") and not result and kind in (None, "unsettled"):
            state = "unavailable"
        else:
            state = "cannot_verify"
            any_unverified = True
        rows.append({
            "ticker": c["ticker"],
            "market_status": status,
            "result": result,
            "settlement_ts": settlement_ts,
            "settlement_kind": kind,
            "in_as_of_history": in_history,
            "capture_time_status": state,
        })
    if any_available:
        event_status = "available"
    elif any_unverified or not rows:
        event_status = "cannot_verify"
    else:
        event_status = "unavailable"
    return {
        "capture_time_status": event_status,
        "in_as_of_history": in_history,
        "contracts": rows,
        "note": "Later settlement fetches do not update this block and do not prove outcome unavailability.",
    }

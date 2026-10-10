"""Kalshi settlement lookup and classification (labels only from confirmed final settlement)."""

from __future__ import annotations

from collections import defaultdict

from ..live import discover as D
from . import config as C

MARKET_FIELDS = ("status", "result", "settlement_value_dollars", "settlement_ts", "close_time")


def _value(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return "unparseable"


def classify(m: dict) -> tuple[str, str]:
    """(state, detail) for one market dict. Never infers a label from anything but the final settlement."""
    status = m.get("status")
    res = (m.get("result") or "").strip().lower()
    val = _value(m.get("settlement_value_dollars"))
    if status not in C.FINAL_MARKET_STATUSES:
        return "PENDING", f"status {status!r} is not a confirmed final settlement"
    if val == "unparseable" or (isinstance(val, float) and not 0.0 <= val <= 1.0):
        return "INCONSISTENT", f"settlement value {m.get('settlement_value_dollars')!r} is not a price in [0, 1]"
    if res == "":
        return "INCONSISTENT", f"final status {status!r} without a result"
    if res == "yes":
        if val is None:
            return "INCONSISTENT", "result yes without a settlement value"
        return ("SETTLED_YES", "result yes, value 1") if val == 1.0 else \
            ("INCONSISTENT", f"result yes with settlement value {val}")
    if res == "no":
        if val is None:
            return "INCONSISTENT", "result no without a settlement value"
        return ("SETTLED_NO", "result no, value 0") if val == 0.0 else \
            ("INCONSISTENT", f"result no with settlement value {val}")
    if res == "scalar":
        if val is None:
            return "INCONSISTENT", "scalar result without a settlement value"
        return "NONBINARY", f"scalar settlement at {val}"
    if res in C.VOID_RESULTS:
        return "VOID_OR_CANCELLED", f"result {res!r}"
    return "INCONSISTENT", f"unrecognized result {res!r}"


def snapshot(m: dict) -> dict:
    return {k: m.get(k) for k in MARKET_FIELDS}


def lookup(get, event_tickers: list[str], tickers: list[str]) -> tuple[dict, list[str]]:
    """Settlement state per requested ticker from the completely paginated event listings.

    Returns ({ticker: {state, detail, market}}, request keys used). A ticker seen more than once with
    different settlement fields is CONFLICTING_DUPLICATE; identical repeats are harmless.
    """
    tr = D.Tracker(get)
    seen = defaultdict(list)
    failed = []
    for ev in sorted(set(event_tickers)):
        try:
            items, st = D.event_markets(tr.get, ev)
        except Exception as exc:  # network errors after retries
            failed.append(f"{ev}: {type(exc).__name__}: {exc}")
            continue
        if not st["complete"]:
            failed.append(f"{ev}: {st['error']}")
            continue
        for m in items:
            seen[m.get("ticker")].append(snapshot(m))
    out = {}
    for t in tickers:
        snaps = seen.get(t)
        if not snaps:
            out[t] = ({"state": "LOOKUP_FAILED", "detail": "; ".join(failed)} if failed else
                      {"state": "NOT_FOUND", "detail": "ticker absent from the event listings"})
            continue
        distinct = {tuple(sorted(s.items(), key=lambda kv: kv[0])) for s in snaps}
        if len(distinct) > 1:
            out[t] = {"state": "CONFLICTING_DUPLICATE", "detail": f"{len(snaps)} rows, {len(distinct)} distinct",
                      "market": snaps}
            continue
        state, detail = classify(snaps[0])
        out[t] = {"state": state, "detail": detail, "market": snaps[0]}
    return out, tr.keys

"""Which competitions a collector cycle hands to the frozen capture.

This is a scheduling hint only: it never produces evidence, never decides acceptance and never writes
research records. The frozen capture repeats its own complete milestone discovery, applies the exact
window rule against the *source* start time and decides WINDOW_CAPTURE / FAILED_ATTEMPT / MISSED.

A competition is due when one of its supported, uncovered Kalshi milestones (no accepted checkpoint
record names any of its event tickers yet) is either

* ``window``: inside [window open - early slack, cutoff + late slack] by the Kalshi milestone start. The
  slack covers source start times that differ from Kalshi's; or
* ``reconcile``: past that range but still inside the capture's discovery range and not yet handed to
  one successful capture run after its cutoff, so the capture can write MISSED (never a forecast).

Milestones whose last capture outcome was structural (no linked contracts, unsupported adapter, no
frozen model) are retried at most every ``STRUCTURAL_RETRY_MIN`` minutes inside their window.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..live import config as LC

EARLY_SLACK_MIN = 15
LATE_SLACK_MIN = 30
STRUCTURAL_RETRY_MIN = 15
SOON_MIN = 10
STRUCTURAL_OUTCOMES = ("no_linked_contracts", "unsupported_adapter", "no_frozen_model")
STATE_RETENTION_S = 3 * 86400


@dataclass
class Plan:
    due: dict[str, list[dict]] = field(default_factory=dict)
    throttled: list[str] = field(default_factory=list)
    covered: int = 0
    considered: int = 0
    window_open_or_soon: bool = False

    def competitions(self) -> list[str]:
        return sorted(self.due)

    def reconcile_keys(self) -> dict[str, list[str]]:
        return {k: [x["key"] for x in v if x["reason"] == "reconcile"] for k, v in self.due.items()}

    def to_dict(self) -> dict:
        return {"competitions": self.competitions(),
                "due": {k: [{kk: x[kk] for kk in ("reason", "milestone_start", "title")} for x in v]
                        for k, v in sorted(self.due.items())},
                "throttled_structural": len(self.throttled), "covered_by_accepted_record": self.covered,
                "milestones_considered": self.considered, "window_open_or_soon": self.window_open_or_soon}


def milestone_key(comp: str, milestone_id: str) -> str:
    return f"{comp}|{milestone_id}"


def comp_tickers(m: dict, series_comp: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for et in m.get("event_tickers") or []:
        k = series_comp.get(et.split("-")[0])
        if k:
            out.setdefault(k, []).append(et)
    return out


def plan(milestones: list[dict], now: float, series_comp: dict, eligible: set[str], covered: dict[str, set],
         state: dict, ts) -> Plan:
    """``ts`` parses an ISO time to epoch seconds; ``covered`` maps competition -> accepted event tickers."""
    lo, hi = now + LC.WINDOW_DISCOVERY_H[0] * 3600, now + LC.WINDOW_DISCOVERY_H[1] * 3600
    out = Plan()
    structural = state.get("structural", {})
    reconciled = state.get("reconciled", {})
    for m in milestones:
        if not m.get("start"):
            continue
        s = ts(m["start"])
        if not lo <= s <= hi:
            continue
        cutoff = s - LC.HORIZON_MIN * 60
        w_open = cutoff - LC.WINDOW_MIN * 60
        for k, tickers in comp_tickers(m, series_comp).items():
            if k not in eligible:
                continue
            out.considered += 1
            if covered.get(k, set()) & set(tickers):
                out.covered += 1
                continue
            key = milestone_key(k, m["milestone_id"])
            item = {"key": key, "milestone_start": m["start"], "title": m.get("title")}
            if w_open - EARLY_SLACK_MIN * 60 <= now <= cutoff + LATE_SLACK_MIN * 60:
                out.window_open_or_soon = True
                last = structural.get(key)
                if last is not None and now - last < STRUCTURAL_RETRY_MIN * 60:
                    out.throttled.append(key)
                    continue
                out.due.setdefault(k, []).append({**item, "reason": "window"})
            elif now > cutoff + LATE_SLACK_MIN * 60:
                if key not in reconciled:
                    out.due.setdefault(k, []).append({**item, "reason": "reconcile"})
            elif w_open - (EARLY_SLACK_MIN + SOON_MIN) * 60 <= now:
                out.window_open_or_soon = True
    return out


def update_state(state: dict, run_summary: dict | None, milestones: list[dict], series_comp: dict, p: Plan,
                 now: float, capture_ok: bool) -> dict:
    """Remember structural outcomes and reconciled milestones after a capture run; drop old entries."""
    state = {"structural": dict(state.get("structural", {})), "reconciled": dict(state.get("reconciled", {}))}
    by_ticker = {}
    for m in milestones:
        for k, tickers in comp_tickers(m, series_comp).items():
            for t in tickers:
                by_ticker[(k, t)] = milestone_key(k, m["milestone_id"])
    for e in (run_summary or {}).get("events", []):
        if e.get("outcome") not in STRUCTURAL_OUTCOMES:
            continue
        for t in e.get("kalshi_event_tickers") or e.get("event_tickers") or []:
            key = by_ticker.get((e.get("competition"), t))
            if key:
                state["structural"][key] = now
    if capture_ok:
        for keys in p.reconcile_keys().values():
            for key in keys:
                state["reconciled"][key] = now
    for name in ("structural", "reconciled"):
        state[name] = {k: v for k, v in state[name].items() if now - v < STATE_RETENTION_S}
    return state

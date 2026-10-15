"""Lifecycle, retrieval freshness and quote parsing — kept as separate fields."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research.explore.paths import AGING_S, FRESH_S

# Market statuses that mean the contract is currently tradable on Kalshi.
ACTIVE_MARKET_STATUS = "active"
FINAL_MARKET_STATUS = "finalized"
CLOSED_LIKE = frozenset({"closed", "determined", "disputed", "amended", "finalized", "inactive"})


def parse_price(value: Any, *, size: Any = None) -> float | None:
    """Parse a dollar quote. Missing / empty / zero-with-zero-size → None (never 0 invent)."""
    if value is None or value == "":
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    size_n: float | None
    try:
        size_n = float(size) if size is not None and size != "" else None
    except (TypeError, ValueError):
        size_n = None
    # Empty book conventions: bid 0 with no size, ask 1 with no size.
    if size_n is not None and size_n <= 0:
        if price <= 0.0 or price >= 1.0:
            return None
    if price < 0.0 or price > 1.0:
        return None
    return price


def parse_count(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_seconds(iso: str | None, now: datetime | None = None) -> float | None:
    dt = parse_iso(iso)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds())


def retrieval_freshness(retrieved_at: str | None, *, now: datetime | None = None) -> dict[str, Any]:
    """Age-based freshness of the newest successful retrieval. Independent of lifecycle."""
    age = age_seconds(retrieved_at, now)
    if age is None:
        return {
            "state": "unknown",
            "age_seconds": None,
            "basis": "No successful retrieval timestamp on this entry",
            "reason": "No successful retrieval timestamp on this entry",
        }
    if age <= FRESH_S:
        state = "fresh"
        basis = f"Retrieved within {FRESH_S // 3600} h"
    elif age <= AGING_S:
        state = "aging"
        basis = f"Retrieved within {AGING_S // 3600} h but older than {FRESH_S // 3600} h"
    else:
        state = "stale"
        basis = f"Last successful retrieval older than {AGING_S // 3600} h"
    return {"state": state, "age_seconds": age, "basis": basis, "as_of": retrieved_at}


def market_is_open(status: str | None) -> bool:
    return (status or "").lower() == ACTIVE_MARKET_STATUS


def derive_lifecycle(markets: list[dict[str, Any]], *, markets_complete: bool) -> dict[str, Any]:
    """Event-level lifecycle from market statuses. Never infer final from an incomplete list."""
    if not markets:
        return {
            "state": "unknown",
            "basis": "No markets cached for this event",
            "reason": "No markets cached for this event",
            "markets_complete": markets_complete,
        }
    if not markets_complete:
        return {
            "state": "unknown",
            "basis": "Market list is incomplete; finality and closure are not inferred",
            "reason": "markets_complete is false",
            "markets_complete": False,
        }
    statuses = [(m.get("status") or "").lower() for m in markets]
    if all(s == FINAL_MARKET_STATUS for s in statuses):
        return {
            "state": "final",
            "basis": "All cached markets are finalized and the market list is complete",
            "markets_complete": True,
        }
    if any(s == ACTIVE_MARKET_STATUS for s in statuses):
        return {
            "state": "open",
            "basis": "At least one market has status active",
            "markets_complete": True,
        }
    if all(s in CLOSED_LIKE for s in statuses) and statuses:
        return {
            "state": "closed",
            "basis": "Every market is closed-like and none is active; list is complete",
            "markets_complete": True,
        }
    return {
        "state": "unknown",
        "basis": "Market statuses do not map to open, closed or final",
        "reason": "Unrecognized or mixed non-final statuses",
        "markets_complete": True,
    }


def contract_status_counts(markets: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in markets:
        key = (m.get("status") or "unknown").lower() or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def quote_summary(markets: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize quote sides without inventing zeros."""
    two = one = none = 0
    for m in markets:
        yes_bid = m.get("yes_bid")
        yes_ask = m.get("yes_ask")
        if yes_bid is not None and yes_ask is not None:
            two += 1
        elif yes_bid is not None or yes_ask is not None:
            one += 1
        else:
            none += 1
    return {"two_sided": two, "one_sided": one, "none": none}

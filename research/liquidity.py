"""Liquidity and spread tercile analysis."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def tercile_edges(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 3:
        return None
    ordered = sorted(values)
    n = len(ordered)
    t1 = ordered[max(0, n // 3 - 1)]
    t2 = ordered[max(0, (2 * n) // 3 - 1)]
    return t1, t2


def assign_tercile(value: float, edges: tuple[float, float]) -> str:
    lo, hi = edges
    if value <= lo:
        return "low"
    if value <= hi:
        return "medium"
    return "high"


def annotate_liquidity_groups(observations: list[dict]) -> dict:
    """
    Define documented terciles from the observation sample:

    - volume: market lifetime volume (contracts), low/medium/high by tercile
    - spread: yes_ask - yes_bid in dollars, tight/medium/wide by tercile
      (tight = lowest spread tercile)

    Returns the edge definitions used.
    """
    volumes = []
    spreads = []
    for obs in observations:
        v = _to_float(obs.get("volume"))
        s = _to_float(obs.get("spread"))
        if v is not None:
            volumes.append(v)
        if s is not None:
            spreads.append(s)

    vol_edges = tercile_edges(volumes)
    spread_edges = tercile_edges(spreads)

    for obs in observations:
        v = _to_float(obs.get("volume"))
        s = _to_float(obs.get("spread"))
        if vol_edges and v is not None:
            obs["volume_group"] = assign_tercile(v, vol_edges)
        else:
            obs["volume_group"] = "unknown"
        if spread_edges and s is not None:
            # invert label: low spread => tight
            raw = assign_tercile(s, spread_edges)
            obs["spread_group"] = {
                "low": "tight",
                "medium": "medium",
                "high": "wide",
            }[raw]
        else:
            obs["spread_group"] = "unknown"

    return {
        "volume_tercile_edges": vol_edges,
        "spread_tercile_edges": spread_edges,
        "volume_definition": (
            "Terciles of market lifetime volume (contracts) within the analysis sample. "
            f"Edges={vol_edges}"
        ),
        "spread_definition": (
            "Terciles of YES ask-bid spread (dollars) within the analysis sample. "
            "tight=lowest tercile, wide=highest tercile. "
            f"Edges={spread_edges}"
        ),
    }


def summarize_by_group(
    observations: Iterable[dict],
    group_key: str,
    *,
    side: str = "YES",
) -> list[dict]:
    groups: dict[str, dict] = {}
    profit_key = "yes_profit" if side == "YES" else "no_profit"
    cost_key = "yes_total_cost" if side == "YES" else "no_total_cost"
    won_key = "yes_won" if side == "YES" else "no_won"
    entry_key = "yes_entry" if side == "YES" else "no_entry"

    for obs in observations:
        g = obs.get(group_key) or "unknown"
        bucket = groups.setdefault(
            g,
            {
                "group": g,
                "n": 0,
                "wins": 0,
                "net_pnl": Decimal("0"),
                "capital": Decimal("0"),
                "sum_entry": Decimal("0"),
                "sum_spread": Decimal("0"),
            },
        )
        bucket["n"] += 1
        bucket["wins"] += int(bool(obs.get(won_key)))
        bucket["net_pnl"] += Decimal(str(obs.get(profit_key, 0)))
        bucket["capital"] += Decimal(str(obs.get(cost_key, 0)))
        bucket["sum_entry"] += Decimal(str(obs.get(entry_key, 0)))
        bucket["sum_spread"] += Decimal(str(obs.get("spread", 0)))

    rows = []
    for g, bucket in sorted(groups.items()):
        n = bucket["n"]
        capital = bucket["capital"]
        rows.append(
            {
                "group": g,
                "n": n,
                "win_rate": float(bucket["wins"] / n) if n else 0.0,
                "avg_entry": float(bucket["sum_entry"] / n) if n else 0.0,
                "avg_spread": float(bucket["sum_spread"] / n) if n else 0.0,
                "net_pnl": float(bucket["net_pnl"]),
                "roi": float(bucket["net_pnl"] / capital) if capital else 0.0,
            }
        )
    return rows

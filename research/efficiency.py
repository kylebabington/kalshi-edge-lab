"""Efficiency analyses: favorite/longshot, OOS splits, bootstrap, ranking."""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable


def sample_size_label(n: int) -> str:
    if n < 30:
        return "insufficient"
    if n < 100:
        return "weak evidence"
    if n < 500:
        return "preliminary"
    return "stronger sample"


def parse_close_year(obs: dict) -> int | None:
    value = obs.get("close_time")
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.year


def choose_period_bounds(observations: list[dict]) -> dict:
    """
    Chronological discovery / validation / OOS bounds.

    Requires >=3 distinct settlement years. Otherwise OOS is unavailable —
    do not label a single narrow sample as a discovery/OOS study.
    Prefer Discovery <=2024 / Validation 2025 / OOS >=2026 when present.
    """
    years = sorted({y for y in (parse_close_year(o) for o in observations) if y})
    base = {
        "discovery_max_year": None,
        "validation_year": None,
        "oos_min_year": None,
        "adapted": False,
        "years_present": years,
        "oos_available": False,
        "oos_unavailable_reason": None,
    }
    if len(years) < 3:
        base["oos_unavailable_reason"] = "insufficient temporal coverage"
        return base

    if min(years) <= 2024 and 2025 in years and max(years) >= 2026:
        return {
            **base,
            "discovery_max_year": 2024,
            "validation_year": 2025,
            "oos_min_year": 2026,
            "adapted": False,
            "oos_available": True,
        }

    return {
        **base,
        "discovery_max_year": years[-3],
        "validation_year": years[-2],
        "oos_min_year": years[-1],
        "adapted": True,
        "oos_available": True,
    }


def period_name(obs: dict, bounds: dict) -> str:
    if not bounds.get("oos_available"):
        return "unsplit"
    year = parse_close_year(obs)
    if year is None:
        return "unknown"
    if year <= bounds["discovery_max_year"]:
        return "discovery"
    if year == bounds["validation_year"]:
        return "validation"
    if year >= bounds["oos_min_year"]:
        return "out_of_sample"
    return "other"


def event_weighted_roi(observations: Iterable[dict], *, side: str) -> float:
    """
    Each event contributes equal total weight regardless of strike count.

    Observation weight w_i = 1 / n_event(i).
    ROI = sum(w_i * pnl_i) / sum(w_i * cost_i).
    """
    obs_list = list(observations)
    if not obs_list:
        return 0.0
    profit_key = "yes_profit" if side == "YES" else "no_profit"
    cost_key = "yes_total_cost" if side == "YES" else "no_total_cost"
    by_event: dict[str, list] = defaultdict(list)
    for obs in obs_list:
        key = str(obs.get("event_ticker") or obs.get("ticker") or id(obs))
        by_event[key].append(obs)
    weighted_pnl = Decimal("0")
    weighted_cost = Decimal("0")
    for rows in by_event.values():
        w = Decimal("1") / Decimal(len(rows))
        for obs in rows:
            weighted_pnl += w * Decimal(str(obs[profit_key]))
            weighted_cost += w * Decimal(str(obs[cost_key]))
    if weighted_cost == 0:
        return 0.0
    return float(weighted_pnl / weighted_cost)


def summarize_side(observations: Iterable[dict], *, side: str) -> dict:
    obs_list = list(observations)
    n = len(obs_list)
    if n == 0:
        return {
            "n": 0,
            "unique_events": 0,
            "avg_entry": 0.0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "roi": 0.0,
            "event_weighted_roi": 0.0,
            "sample_label": sample_size_label(0),
        }

    entry_key = "yes_entry" if side == "YES" else "no_entry"
    won_key = "yes_won" if side == "YES" else "no_won"
    profit_key = "yes_profit" if side == "YES" else "no_profit"
    cost_key = "yes_total_cost" if side == "YES" else "no_total_cost"

    sum_entry = Decimal("0")
    wins = 0
    net = Decimal("0")
    capital = Decimal("0")
    events = set()
    for obs in obs_list:
        sum_entry += Decimal(str(obs[entry_key]))
        wins += int(bool(obs[won_key]))
        net += Decimal(str(obs[profit_key]))
        capital += Decimal(str(obs[cost_key]))
        if obs.get("event_ticker"):
            events.add(obs["event_ticker"])

    avg_entry = sum_entry / Decimal(n)
    win_rate = Decimal(wins) / Decimal(n)
    roi = net / capital if capital else Decimal("0")
    return {
        "n": n,
        "unique_events": len(events),
        "avg_entry": float(avg_entry),
        "win_rate": float(win_rate),
        "calibration_gap": float(win_rate - avg_entry),
        "net_pnl": float(net),
        "roi": float(roi),
        "event_weighted_roi": event_weighted_roi(obs_list, side=side),
        "sample_label": sample_size_label(n),
    }


SPREAD_FILTERS = (None, Decimal("0.20"), Decimal("0.10"), Decimal("0.05"))


def spread_filtered_profitability(
    observations: list[dict],
    *,
    side: str = "YES",
    limits: tuple = SPREAD_FILTERS,
) -> list[dict]:
    """Pre-registered diagnostic spread filters (not optimized on ROI)."""
    rows = []
    for limit in limits:
        if limit is None:
            subset = observations
            label = "all"
        else:
            lim = Decimal(str(limit))
            subset = [
                o for o in observations if Decimal(str(o.get("spread", "1"))) <= lim
            ]
            label = f"spread_le_{limit}"
        summary = summarize_side(subset, side=side)
        rows.append({"spread_filter": label, **summary})
    return rows


def favorite_longshot_report(observations: list[dict]) -> dict:
    """Cheap YES (<=0.20) vs expensive YES (>=0.80), plus opposite-side NO."""
    cheap_yes = [o for o in observations if Decimal(str(o["yes_entry"])) <= Decimal("0.20")]
    expensive_yes = [o for o in observations if Decimal(str(o["yes_entry"])) >= Decimal("0.80")]
    # Opposite-side for cheap YES: buy NO where YES was cheap
    cheap_yes_no_side = cheap_yes
    expensive_yes_no_side = expensive_yes
    return {
        "cheap_yes": summarize_side(cheap_yes, side="YES"),
        "expensive_yes": summarize_side(expensive_yes, side="YES"),
        "opposite_no_when_yes_cheap": summarize_side(cheap_yes_no_side, side="NO"),
        "opposite_no_when_yes_expensive": summarize_side(expensive_yes_no_side, side="NO"),
    }


def category_summary(observations: list[dict], *, side: str = "YES") -> list[dict]:
    by_cat: dict[str, list] = defaultdict(list)
    for obs in observations:
        by_cat[obs.get("category") or "Unknown"].append(obs)
    rows = []
    for category, rows_obs in sorted(by_cat.items()):
        summary = summarize_side(rows_obs, side=side)
        spreads = [Decimal(str(o.get("spread", 0))) for o in rows_obs]
        avg_spread = float(sum(spreads) / len(spreads)) if spreads else 0.0
        rows.append(
            {
                "category": category,
                "markets": len({o["ticker"] for o in rows_obs}),
                "observations": summary["n"],
                "unique_events": summary["unique_events"],
                "win_rate": summary["win_rate"],
                "avg_entry": summary["avg_entry"],
                "net_pnl": summary["net_pnl"],
                "roi": summary["roi"],
                "event_weighted_roi": summary.get("event_weighted_roi", 0.0),
                "avg_spread": avg_spread,
                "sample_label": summary["sample_label"],
            }
        )
    return rows


def yearly_summary(observations: list[dict], *, side: str = "YES") -> list[dict]:
    by_year: dict[int, list] = defaultdict(list)
    for obs in observations:
        year = parse_close_year(obs)
        if year is None:
            continue
        by_year[year].append(obs)
    rows = []
    for year, rows_obs in sorted(by_year.items()):
        summary = summarize_side(rows_obs, side=side)
        rows.append({"year": year, **summary})
    return rows


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, center - margin), min(1.0, center + margin))


def bootstrap_roi_ci(
    observations: list[dict],
    *,
    side: str,
    n_boot: int = 1000,
    seed: int = 42,
    by_event: bool = True,
) -> tuple[float, float]:
    """
    Bootstrap ROI confidence interval.

    When by_event=True, resample unique events (with all their markets) to
    reduce overcounting correlated contracts within an event.
    """
    if not observations:
        return (0.0, 0.0)

    profit_key = "yes_profit" if side == "YES" else "no_profit"
    cost_key = "yes_total_cost" if side == "YES" else "no_total_cost"
    rng = random.Random(seed)

    if by_event:
        groups: dict[str, list] = defaultdict(list)
        for obs in observations:
            key = obs.get("event_ticker") or obs.get("ticker")
            groups[key].append(obs)
        keys = list(groups.keys())
        if not keys:
            return (0.0, 0.0)
        rois = []
        for _ in range(n_boot):
            sample_keys = [keys[rng.randrange(len(keys))] for _ in range(len(keys))]
            net = Decimal("0")
            capital = Decimal("0")
            for key in sample_keys:
                for obs in groups[key]:
                    net += Decimal(str(obs[profit_key]))
                    capital += Decimal(str(obs[cost_key]))
            rois.append(float(net / capital) if capital else 0.0)
    else:
        rois = []
        n = len(observations)
        for _ in range(n_boot):
            sample = [observations[rng.randrange(n)] for _ in range(n)]
            net = sum(Decimal(str(o[profit_key])) for o in sample)
            capital = sum(Decimal(str(o[cost_key])) for o in sample)
            rois.append(float(net / capital) if capital else 0.0)

    rois.sort()
    lo = rois[int(0.025 * (len(rois) - 1))]
    hi = rois[int(0.975 * (len(rois) - 1))]
    return lo, hi


def period_summaries(observations: list[dict], bounds: dict, *, side: str = "YES") -> list[dict]:
    if not bounds.get("oos_available"):
        return []
    by_period: dict[str, list] = defaultdict(list)
    for obs in observations:
        by_period[period_name(obs, bounds)].append(obs)
    rows = []
    for name in ("discovery", "validation", "out_of_sample", "other", "unknown"):
        if name not in by_period:
            continue
        summary = summarize_side(by_period[name], side=side)
        ci = bootstrap_roi_ci(by_period[name], side=side)
        wins = int(round(summary["win_rate"] * summary["n"])) if summary["n"] else 0
        w_lo, w_hi = wilson_interval(wins, summary["n"])
        rows.append(
            {
                "period": name,
                **summary,
                "roi_ci_low": ci[0],
                "roi_ci_high": ci[1],
                "win_rate_ci_low": w_lo,
                "win_rate_ci_high": w_hi,
            }
        )
    return rows


@dataclass
class RankedStrategy:
    name: str
    n: int
    roi: float
    sample_label: str
    unique_events: int


def rank_simple_strategies(
    observations: list[dict],
    *,
    min_observations: int = 100,
    side: str = "YES",
) -> list[dict]:
    """Rank a few pre-registered bucket strategies without in-sample optimization."""
    strategies = []

    def add(name: str, subset: list[dict]) -> None:
        summary = summarize_side(subset, side=side)
        if summary["n"] < min_observations:
            return
        strategies.append(
            {
                "strategy": name,
                "n": summary["n"],
                "unique_events": summary["unique_events"],
                "roi": summary["roi"],
                "win_rate": summary["win_rate"],
                "avg_entry": summary["avg_entry"],
                "sample_label": summary["sample_label"],
            }
        )

    add("all_markets", observations)
    add(
        "yes_entry_le_0.20",
        [o for o in observations if Decimal(str(o["yes_entry"])) <= Decimal("0.20")],
    )
    add(
        "yes_entry_ge_0.80",
        [o for o in observations if Decimal(str(o["yes_entry"])) >= Decimal("0.80")],
    )
    add(
        "spread_le_0.03",
        [o for o in observations if Decimal(str(o.get("spread", 1))) <= Decimal("0.03")],
    )

    strategies.sort(key=lambda r: r["roi"], reverse=True)
    return strategies

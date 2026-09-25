"""Deterministic stratified sampling from a cached market inventory."""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


SAMPLING_BALANCED = "balanced"
SAMPLING_UNIVERSE_WEIGHTED = "universe-weighted"
SAMPLING_MODES = (SAMPLING_BALANCED, SAMPLING_UNIVERSE_WEIGHTED)


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def close_year(market: dict) -> int | None:
    """Sampling stratum year from close_time only (no settlement_ts fallback)."""
    ts = _parse_ts(market.get("close_time"))
    return ts.year if ts else None


def close_date_key(market: dict) -> str:
    """UTC calendar date from close_time only."""
    ts = _parse_ts(market.get("close_time"))
    return ts.date().isoformat() if ts else "unknown"


# Back-compat aliases used by older call sites / reports.
settlement_year = close_year
settlement_date_key = close_date_key


def series_date_key(market: dict) -> str:
    series = market.get("series_ticker") or "unknown"
    return f"{series}|{close_date_key(market)}"


def seeded_rng(*parts: Any) -> random.Random:
    """Deterministic RNG from SHA-256 of seed material (not Python str hashing)."""
    material = ":".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


@dataclass
class SamplingDiagnostics:
    sampling_mode: str = SAMPLING_BALANCED
    inventory_markets: int = 0
    inventory_date_range: str = "n/a"
    inventory_years: list[int] = field(default_factory=list)
    inventory_categories: dict[str, int] = field(default_factory=dict)
    inventory_unique_series: int = 0
    inventory_unique_events: int = 0
    selected_markets: int = 0
    selected_unique_events: int = 0
    selected_unique_series: int = 0
    selected_unique_close_dates: int = 0
    selected_date_range: str = "n/a"
    selected_by_year: dict[int, int] = field(default_factory=dict)
    selected_by_category: dict[str, int] = field(default_factory=dict)
    selected_by_year_category: dict[str, int] = field(default_factory=dict)
    markets_per_event_median: float = 0.0
    markets_per_event_p95: float = 0.0
    markets_per_event_max: int = 0
    largest_event: str = "n/a"
    largest_event_market_count: int = 0
    largest_series: str = "n/a"
    largest_series_market_count: int = 0
    largest_calendar_day: str = "n/a"
    largest_single_day_market_count: int = 0
    category_inventory_share: dict[str, float] = field(default_factory=dict)
    category_sample_share: dict[str, float] = field(default_factory=dict)
    seed: int = 42
    max_markets_per_event: int = 5
    max_markets_per_series_date: int = 10

    def as_dict(self) -> dict:
        return {
            "sampling_mode": self.sampling_mode,
            "inventory_markets": self.inventory_markets,
            "inventory_date_range": self.inventory_date_range,
            "inventory_years": list(self.inventory_years),
            "inventory_categories": dict(self.inventory_categories),
            "inventory_unique_series": self.inventory_unique_series,
            "inventory_unique_events": self.inventory_unique_events,
            "selected_markets": self.selected_markets,
            "selected_unique_events": self.selected_unique_events,
            "selected_unique_series": self.selected_unique_series,
            "selected_unique_close_dates": self.selected_unique_close_dates,
            "selected_date_range": self.selected_date_range,
            "selected_by_year": dict(self.selected_by_year),
            "selected_by_category": dict(self.selected_by_category),
            "selected_by_year_category": dict(self.selected_by_year_category),
            "markets_per_event_median": self.markets_per_event_median,
            "markets_per_event_p95": self.markets_per_event_p95,
            "markets_per_event_max": self.markets_per_event_max,
            "largest_event": self.largest_event,
            "largest_event_market_count": self.largest_event_market_count,
            "largest_series": self.largest_series,
            "largest_series_market_count": self.largest_series_market_count,
            "largest_calendar_day": self.largest_calendar_day,
            "largest_single_day_market_count": self.largest_single_day_market_count,
            "category_inventory_share": dict(self.category_inventory_share),
            "category_sample_share": dict(self.category_sample_share),
            "seed": self.seed,
            "max_markets_per_event": self.max_markets_per_event,
            "max_markets_per_series_date": self.max_markets_per_series_date,
        }


def _date_range(markets: Iterable[dict]) -> str:
    dates = sorted(
        d
        for d in (_parse_ts(m.get("close_time")) for m in markets)
        if d is not None
    )
    if not dates:
        return "n/a"
    return f"{dates[0].date().isoformat()} -> {dates[-1].date().isoformat()}"


def inventory_diagnostics(markets: list[dict]) -> dict:
    eligible = [m for m in markets if close_year(m) is not None]
    years = sorted({y for y in (close_year(m) for m in eligible) if y})
    cats = Counter(m.get("category") or "Unknown" for m in eligible)
    return {
        "inventory_markets": len(eligible),
        "inventory_date_range": _date_range(eligible),
        "inventory_years": years,
        "inventory_categories": dict(cats.most_common()),
        "inventory_unique_series": len(
            {m.get("series_ticker") for m in eligible if m.get("series_ticker")}
        ),
        "inventory_unique_events": len(
            {m.get("event_ticker") for m in eligible if m.get("event_ticker")}
        ),
    }


def dominance_tops(
    markets: list[dict],
) -> tuple[tuple[str, int], tuple[str, int], tuple[str, int]]:
    by_event: Counter[str] = Counter()
    by_series: Counter[str] = Counter()
    by_day: Counter[str] = Counter()
    for m in markets:
        if m.get("event_ticker"):
            by_event[str(m["event_ticker"])] += 1
        if m.get("series_ticker"):
            by_series[str(m["series_ticker"])] += 1
        by_day[close_date_key(m)] += 1

    def _top(counter: Counter[str]) -> tuple[str, int]:
        if not counter:
            return ("n/a", 0)
        key, count = counter.most_common(1)[0]
        return (key, count)

    return _top(by_event), _top(by_series), _top(by_day)


def _dominance_counts(markets: list[dict]) -> tuple[int, int, int]:
    top_event, top_series, top_day = dominance_tops(markets)
    return top_event[1], top_series[1], top_day[1]


def _percentile(sorted_vals: list[int], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = min(len(sorted_vals) - 1, max(0, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return float(sorted_vals[idx])


def _markets_per_event_stats(markets: list[dict]) -> tuple[float, float, int]:
    counts = Counter(
        str(m.get("event_ticker") or m.get("ticker") or "")
        for m in markets
        if m.get("event_ticker") or m.get("ticker")
    )
    vals = sorted(counts.values())
    if not vals:
        return 0.0, 0.0, 0
    mid = vals[len(vals) // 2]
    if len(vals) % 2 == 0 and len(vals) >= 2:
        mid = (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2.0
    return float(mid), _percentile(vals, 95), max(vals)


def iter_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def stratified_sample(
    markets: list[dict],
    *,
    max_markets: int | None,
    seed: int = 42,
    max_markets_per_event: int = 5,
    max_markets_per_series_date: int = 10,
    sampling: str = SAMPLING_BALANCED,
) -> tuple[list[dict], SamplingDiagnostics]:
    """
    Deterministic stratified sample by (close_year, category).

    ``sampling`` is ``balanced`` (default) or ``universe-weighted``.
    Caps limit analysis-set concentration; inventory itself is unchanged.
    """
    if sampling not in SAMPLING_MODES:
        raise ValueError(f"Unknown sampling mode: {sampling}")

    usable = [
        m
        for m in markets
        if not m.get("skip_reason") and close_year(m) is not None and m.get("ticker")
    ]
    diag = SamplingDiagnostics(
        sampling_mode=sampling,
        seed=seed,
        max_markets_per_event=max_markets_per_event,
        max_markets_per_series_date=max_markets_per_series_date,
    )
    inv = inventory_diagnostics(usable)
    for key, value in inv.items():
        setattr(diag, key, value)

    if max_markets is None or max_markets >= len(usable):
        selected = list(usable)
    elif sampling == SAMPLING_BALANCED:
        selected = _select_balanced(
            usable,
            max_markets=max_markets,
            seed=seed,
            max_markets_per_event=max_markets_per_event,
            max_markets_per_series_date=max_markets_per_series_date,
        )
    else:
        selected = _select_universe_weighted(
            usable,
            max_markets=max_markets,
            seed=seed,
            max_markets_per_event=max_markets_per_event,
            max_markets_per_series_date=max_markets_per_series_date,
        )

    _fill_selection_diagnostics(diag, usable, selected)
    return selected, diag


def stratified_sample_from_jsonl(
    path: Path,
    *,
    max_markets: int | None,
    seed: int = 42,
    max_markets_per_event: int = 5,
    max_markets_per_series_date: int = 10,
    sampling: str = SAMPLING_BALANCED,
) -> tuple[list[dict], SamplingDiagnostics]:
    """
    Memory-conscious sample from a large usable_inventory.jsonl.

    Pass 1 builds a compact index; pass 2 loads only selected full rows.
    """
    # Compact rows: ticker, event, series, category, close_time
    compact: list[dict] = []
    for row in iter_jsonl(path):
        if row.get("skip_reason") or not row.get("ticker"):
            continue
        if close_year(row) is None:
            continue
        compact.append(
            {
                "ticker": row.get("ticker"),
                "event_ticker": row.get("event_ticker"),
                "series_ticker": row.get("series_ticker"),
                "category": row.get("category") or "Unknown",
                "close_time": row.get("close_time"),
            }
        )

    selected_compact, diag = stratified_sample(
        compact,
        max_markets=max_markets,
        seed=seed,
        max_markets_per_event=max_markets_per_event,
        max_markets_per_series_date=max_markets_per_series_date,
        sampling=sampling,
    )
    wanted = {str(m["ticker"]) for m in selected_compact}
    selected: list[dict] = []
    for row in iter_jsonl(path):
        ticker = row.get("ticker")
        if ticker and str(ticker) in wanted:
            selected.append(row)
            if len(selected) >= len(wanted):
                break
    # Preserve deterministic order from sampler.
    by_ticker = {str(r.get("ticker")): r for r in selected}
    ordered = [by_ticker[str(m["ticker"])] for m in selected_compact if str(m["ticker"]) in by_ticker]
    _fill_selection_diagnostics(diag, compact, ordered)
    return ordered, diag


def _fill_selection_diagnostics(
    diag: SamplingDiagnostics,
    inventory: list[dict],
    selected: list[dict],
) -> None:
    diag.selected_markets = len(selected)
    diag.selected_unique_events = len(
        {m.get("event_ticker") for m in selected if m.get("event_ticker")}
    )
    diag.selected_unique_series = len(
        {m.get("series_ticker") for m in selected if m.get("series_ticker")}
    )
    diag.selected_unique_close_dates = len(
        {close_date_key(m) for m in selected if close_date_key(m) != "unknown"}
    )
    diag.selected_date_range = _date_range(selected)
    diag.selected_by_year = dict(
        sorted(Counter(y for y in (close_year(m) for m in selected) if y).items())
    )
    diag.selected_by_category = dict(
        Counter(m.get("category") or "Unknown" for m in selected).most_common()
    )
    yc: Counter[str] = Counter()
    for m in selected:
        year = close_year(m) or "unknown"
        cat = m.get("category") or "Unknown"
        yc[f"{year}|{cat}"] += 1
    diag.selected_by_year_category = dict(sorted(yc.items()))

    median, p95, mx = _markets_per_event_stats(selected)
    diag.markets_per_event_median = median
    diag.markets_per_event_p95 = p95
    diag.markets_per_event_max = mx

    top_event, top_series, top_day = dominance_tops(selected)
    diag.largest_event, diag.largest_event_market_count = top_event
    diag.largest_series, diag.largest_series_market_count = top_series
    diag.largest_calendar_day, diag.largest_single_day_market_count = top_day

    inv_n = max(1, len(inventory))
    sel_n = max(1, len(selected))
    inv_cats = Counter(m.get("category") or "Unknown" for m in inventory)
    sel_cats = Counter(m.get("category") or "Unknown" for m in selected)
    all_cats = sorted(set(inv_cats) | set(sel_cats))
    diag.category_inventory_share = {
        c: inv_cats[c] / inv_n for c in all_cats
    }
    diag.category_sample_share = {c: sel_cats[c] / sel_n for c in all_cats}


def _try_add_factory(
    *,
    max_markets_per_event: int,
    max_markets_per_series_date: int,
):
    selected: list[dict] = []
    event_counts: Counter[str] = Counter()
    series_date_counts: Counter[str] = Counter()
    day_counts: Counter[str] = Counter()
    selected_tickers: set[str] = set()

    def try_add(market: dict) -> bool:
        ticker = market.get("ticker")
        if not ticker or ticker in selected_tickers:
            return False
        event = str(market.get("event_ticker") or ticker)
        sd = series_date_key(market)
        if event_counts[event] >= max_markets_per_event:
            return False
        if series_date_counts[sd] >= max_markets_per_series_date:
            return False
        selected.append(market)
        selected_tickers.add(str(ticker))
        event_counts[event] += 1
        series_date_counts[sd] += 1
        day_counts[close_date_key(market)] += 1
        return True

    return selected, selected_tickers, day_counts, try_add


def _select_universe_weighted(
    markets: list[dict],
    *,
    max_markets: int,
    seed: int,
    max_markets_per_event: int,
    max_markets_per_series_date: int,
) -> list[dict]:
    """Proportional-to-universe stratum allocation (legacy behavior)."""
    strata: dict[tuple[int | str, str], list[dict]] = defaultdict(list)
    for market in markets:
        year = close_year(market) or "unknown"
        category = market.get("category") or "Unknown"
        strata[(year, category)].append(market)

    stratum_keys = sorted(strata.keys(), key=lambda k: (str(k[0]), k[1]))
    sizes = {key: len(strata[key]) for key in stratum_keys}
    total = sum(sizes.values()) or 1

    quotas: dict[tuple[int | str, str], int] = {}
    if max_markets >= len(stratum_keys):
        remaining = max_markets
        for key in stratum_keys:
            quotas[key] = 1
            remaining -= 1
        for key in stratum_keys:
            extra = int(remaining * sizes[key] / total)
            quotas[key] = min(sizes[key], quotas[key] + extra)
        assigned = sum(quotas.values())
        if assigned < max_markets:
            for key in sorted(stratum_keys, key=lambda k: sizes[k], reverse=True):
                if assigned >= max_markets:
                    break
                if quotas[key] < sizes[key]:
                    quotas[key] += 1
                    assigned += 1
    else:
        ranked = sorted(stratum_keys, key=lambda k: (-sizes[k], str(k[0]), k[1]))
        for key in ranked[:max_markets]:
            quotas[key] = 1

    selected, selected_tickers, _day_counts, try_add = _try_add_factory(
        max_markets_per_event=max_markets_per_event,
        max_markets_per_series_date=max_markets_per_series_date,
    )

    for key in stratum_keys:
        pool = list(strata[key])
        pool.sort(key=lambda m: str(m.get("ticker") or ""))
        rng = seeded_rng(seed, "uw", key[0], key[1])
        rng.shuffle(pool)
        need = quotas.get(key, 0)
        for market in pool:
            if need <= 0 or len(selected) >= max_markets:
                break
            if try_add(market):
                need -= 1

    if len(selected) < max_markets:
        remainder = []
        for key in stratum_keys:
            for market in strata[key]:
                ticker = market.get("ticker")
                if ticker and str(ticker) not in selected_tickers:
                    remainder.append(market)
        remainder.sort(key=lambda m: str(m.get("ticker") or ""))
        rng = seeded_rng(seed, "uw", "remainder")
        rng.shuffle(remainder)
        for market in remainder:
            if len(selected) >= max_markets:
                break
            try_add(market)

    return selected


def _select_balanced(
    markets: list[dict],
    *,
    max_markets: int,
    seed: int,
    max_markets_per_event: int,
    max_markets_per_series_date: int,
) -> list[dict]:
    """
    Even year×category allocation with event-first round-robin.

    Sparse strata may contribute all available eligible observations.
    """
    strata: dict[tuple[int | str, str], list[dict]] = defaultdict(list)
    for market in markets:
        year = close_year(market)
        if year is None:
            continue
        category = market.get("category") or "Unknown"
        strata[(year, category)].append(market)

    stratum_keys = sorted(strata.keys(), key=lambda k: (str(k[0]), k[1]))
    if not stratum_keys:
        return []

    sizes = {key: len(strata[key]) for key in stratum_keys}
    n = len(stratum_keys)
    base = max_markets // n
    rem = max_markets % n
    quotas: dict[tuple[int | str, str], int] = {}
    for i, key in enumerate(stratum_keys):
        want = base + (1 if i < rem else 0)
        quotas[key] = min(sizes[key], want)

    # If some strata were clamped, redistribute remainder evenly.
    assigned = sum(quotas.values())
    if assigned < max_markets:
        for key in stratum_keys:
            if assigned >= max_markets:
                break
            if quotas[key] < sizes[key]:
                quotas[key] += 1
                assigned += 1

    selected, selected_tickers, day_counts, try_add = _try_add_factory(
        max_markets_per_event=max_markets_per_event,
        max_markets_per_series_date=max_markets_per_series_date,
    )

    for key in stratum_keys:
        year, category = key
        need = quotas.get(key, 0)
        if need <= 0:
            continue

        by_event: dict[str, list[dict]] = defaultdict(list)
        for market in strata[key]:
            event = str(market.get("event_ticker") or market.get("ticker"))
            by_event[event].append(market)

        event_keys = sorted(by_event.keys())
        evt_rng = seeded_rng(seed, "evt", year, category)
        evt_rng.shuffle(event_keys)

        event_queues: list[list[dict]] = []
        for event in event_keys:
            pool = list(by_event[event])
            pool.sort(key=lambda m: str(m.get("ticker") or ""))
            mkt_rng = seeded_rng(seed, "mkt", event)
            mkt_rng.shuffle(pool)
            event_queues.append(pool)

        # Round-robin across events; within each pick prefer underrepresented close dates.
        while need > 0 and len(selected) < max_markets and event_queues:
            progressed = False
            next_queues: list[list[dict]] = []
            for queue in event_queues:
                if need <= 0 or len(selected) >= max_markets:
                    next_queues.append(queue)
                    continue
                if not queue:
                    continue
                # Prefer markets on less-represented calendar days in this stratum fill.
                queue.sort(
                    key=lambda m: (
                        day_counts[close_date_key(m)],
                        str(m.get("ticker") or ""),
                    )
                )
                candidate = queue.pop(0)
                if try_add(candidate):
                    need -= 1
                    progressed = True
                if queue:
                    next_queues.append(queue)
            event_queues = next_queues
            if not progressed:
                break

    # Remainder pass across strata (no duplication).
    if len(selected) < max_markets:
        remainder = []
        for key in stratum_keys:
            for market in strata[key]:
                ticker = market.get("ticker")
                if ticker and str(ticker) not in selected_tickers:
                    remainder.append(market)
        remainder.sort(key=lambda m: str(m.get("ticker") or ""))
        rng = seeded_rng(seed, "balanced", "remainder")
        rng.shuffle(remainder)
        for market in remainder:
            if len(selected) >= max_markets:
                break
            try_add(market)

    return selected

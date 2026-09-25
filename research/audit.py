"""Audits: extreme quotes, market lifespan, horizon coverage reconciliation."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from research.prices import parse_ts


LIFESPAN_BUCKETS = (
    ("<1h", 0, 1),
    ("1–6h", 1, 6),
    ("6–24h", 6, 24),
    ("1–3d", 24, 72),
    ("3–7d", 72, 168),
    (">7d", 168, None),
)

HORIZON_REJECTION_KEYS = (
    "horizon_before_market_open",
    "candle_missing_despite_market_being_open",
    "stale_candle",
    "missing_non_executable_bid",
    "missing_non_executable_ask",
    "unresolved_fee",
    "unsupported_fee",
    "other_rejection",
)

EXTREME_QUOTE_FIELDS = (
    "ticker",
    "event_ticker",
    "series_ticker",
    "category",
    "result",
    "open_time",
    "close_time",
    "expected_expiration_time",
    "expiration_time",
    "latest_expiration_time",
    "occurrence_datetime",
    "settlement_ts",
    "can_close_early",
    "target_time",
    "candle_timestamp",
    "candle_lag",
    "yes_bid",
    "yes_ask",
    "spread",
    "midquote",
    "last_trade",
    "volume",
    "open_interest",
)


def market_duration_hours(market: dict) -> float | None:
    open_ts = parse_ts(market.get("open_time"))
    close_ts = parse_ts(market.get("close_time"))
    if open_ts is None or close_ts is None:
        return None
    return max(0.0, (close_ts - open_ts).total_seconds() / 3600.0)


def lifespan_bucket(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 1:
        return "<1h"
    if hours < 6:
        return "1–6h"
    if hours < 24:
        return "6–24h"
    if hours < 72:
        return "1–3d"
    if hours < 168:
        return "3–7d"
    return ">7d"


def lifespan_report(markets: Iterable[dict]) -> list[dict]:
    counts: Counter[str] = Counter()
    for market in markets:
        counts[lifespan_bucket(market_duration_hours(market))] += 1
    order = [label for label, _, _ in LIFESPAN_BUCKETS] + ["unknown"]
    return [{"bucket": label, "n": counts.get(label, 0)} for label in order if counts.get(label, 0) or label != "unknown"]


def empty_horizon_coverage(horizon: str) -> dict:
    row = {
        "horizon": horizon,
        "markets_attempted": 0,
        "usable_observations": 0,
        "unique_events": 0,
    }
    for key in HORIZON_REJECTION_KEYS:
        row[key] = 0
    return row


def reconcile_horizon_row(row: dict) -> bool:
    rejected = sum(int(row.get(k, 0)) for k in HORIZON_REJECTION_KEYS)
    return int(row["markets_attempted"]) == int(row["usable_observations"]) + rejected


def reconcile_sample_to_horizon(
    *,
    selected_sample: int,
    horizon_attempted: int,
    pre_horizon_excluded: int,
) -> bool:
    """selected_sample == horizon_attempted + pre_horizon_excluded."""
    return int(selected_sample) == int(horizon_attempted) + int(pre_horizon_excluded)


def pre_horizon_exclusion_summary(
    *,
    skip_reason: int = 0,
    nonstandard_settlement: int = 0,
    candle_fetch_failures: int = 0,
    other: int = 0,
) -> dict[str, int]:
    return {
        "skip_reason": int(skip_reason),
        "nonstandard_settlement": int(nonstandard_settlement),
        "candle_fetch_failures": int(candle_fetch_failures),
        "other": int(other),
        "pre_horizon_excluded": int(
            skip_reason + nonstandard_settlement + candle_fetch_failures + other
        ),
    }


class HorizonCoverageTracker:
    """Per-horizon attempt/rejection accounting; totals must reconcile."""

    def __init__(self, horizons: Iterable[str]) -> None:
        self.rows = {h: empty_horizon_coverage(h) for h in horizons}
        self._usable_events: dict[str, set[str]] = defaultdict(set)

    def attempt(self, horizon: str) -> None:
        self.rows[horizon]["markets_attempted"] += 1

    def usable(self, horizon: str, event_ticker: str | None) -> None:
        self.rows[horizon]["usable_observations"] += 1
        if event_ticker:
            self._usable_events[horizon].add(str(event_ticker))
            self.rows[horizon]["unique_events"] = len(self._usable_events[horizon])

    def reject(self, horizon: str, reason: str) -> None:
        key = reason if reason in HORIZON_REJECTION_KEYS else "other_rejection"
        self.rows[horizon][key] += 1

    def as_rows(self) -> list[dict]:
        return [self.rows[h] for h in self.rows]

    def all_reconcile(self) -> bool:
        return all(reconcile_horizon_row(row) for row in self.as_rows())


def is_extreme_quote(obs: dict) -> bool:
    try:
        ask = Decimal(str(obs.get("yes_ask", obs.get("yes_entry"))))
    except Exception:
        return False
    result = str(obs.get("result") or "").lower()
    if ask >= Decimal("0.95") and result == "no":
        return True
    if ask <= Decimal("0.05") and result == "yes":
        return True
    return False


def extreme_quote_rows(observations: Iterable[dict]) -> list[dict]:
    rows = []
    for obs in observations:
        if not is_extreme_quote(obs):
            continue
        rows.append(
            {
                "ticker": obs.get("ticker"),
                "event_ticker": obs.get("event_ticker"),
                "series_ticker": obs.get("series_ticker"),
                "category": obs.get("category"),
                "result": obs.get("result"),
                "open_time": obs.get("open_time"),
                "close_time": obs.get("close_time"),
                "expected_expiration_time": obs.get("expected_expiration_time"),
                "expiration_time": obs.get("expiration_time"),
                "latest_expiration_time": obs.get("latest_expiration_time"),
                "occurrence_datetime": obs.get("occurrence_datetime"),
                "settlement_ts": obs.get("settlement_ts"),
                "can_close_early": obs.get("can_close_early"),
                "target_time": obs.get("target_entry_ts") or obs.get("target_time"),
                "candle_timestamp": obs.get("candle_timestamp"),
                "candle_lag": obs.get("candle_lag_seconds") or obs.get("candle_lag"),
                "yes_bid": obs.get("yes_bid"),
                "yes_ask": obs.get("yes_ask"),
                "spread": obs.get("spread"),
                "midquote": obs.get("midquote"),
                "last_trade": obs.get("last_trade"),
                "volume": obs.get("volume"),
                "open_interest": obs.get("open_interest"),
            }
        )
    return rows

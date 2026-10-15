"""Offline taker-fee estimate from the cached per-series fee bundle.

Never fetches. A missing or unresolvable schedule yields ``None`` (fee unavailable), never zero.
Event-level fee overrides are not available offline, so every estimate notes that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from kalshi import cache
from kalshi.fees import FeeResolutionError, compute_taker_fee, resolve_fee_schedule

EVENT_OVERRIDE_NOTE = "series-level schedule from local cache; event-level fee overrides not checked"


@dataclass(frozen=True)
class FeeEstimate:
    fee: float | None
    source: str
    note: str


def estimate_taker_fee(series: str | None, price: float | None, at: datetime,
                       *, fee_dir: Path | None = None) -> FeeEstimate:
    if not series:
        return FeeEstimate(None, "unavailable", "series ticker unknown")
    if price is None or not (0 < price < 1):
        return FeeEstimate(None, "unavailable", "no executable price to apply a fee to")
    path = (fee_dir / f"{series.replace('/', '_')}.json") if fee_dir else cache.fee_series_path(series)
    try:
        bundle = cache.read_json(path, default=None)
    except (OSError, ValueError):
        bundle = None
    if not isinstance(bundle, dict):
        return FeeEstimate(None, "unavailable", f"no cached fee schedule for series {series}")
    try:
        schedule = resolve_fee_schedule(
            series_ticker=series, entry_ts=at, fee_changes=list(bundle.get("fee_changes") or []),
            series=bundle.get("series_metadata") or {},
        )
        fee = compute_taker_fee(price=Decimal(str(price)), contracts=1, schedule=schedule)
    except (FeeResolutionError, ValueError) as error:
        return FeeEstimate(None, "unavailable", f"fee schedule unresolved: {error}")
    return FeeEstimate(float(fee), schedule.source, EVENT_OVERRIDE_NOTE)

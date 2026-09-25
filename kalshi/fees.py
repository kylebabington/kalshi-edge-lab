"""Time-aware Kalshi fee calculations.

Primary taker model (quadratic schedules):

    fee = round_up(M * 0.07 * C * P * (1 - P))

where rounding brings fee + position_cost up to the next centicent ($0.0001).

Fee type/multiplier at a simulated entry timestamp comes from
GET /series/fee_changes?show_historical=true (and event override history),
not from today's series metadata alone. Event-level overrides take precedence
while active; a later null override clears them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_UP
from typing import Any


CENTICENT = Decimal("0.0001")
TAKER_COEFFICIENT = Decimal("0.07")
SUPPORTED_TAKER_FEE_TYPES = {
    "quadratic",
    "quadratic_with_maker_fees",
    "quadratic_with_combo_maker_fees",
}

# Provenance / rejection codes
SOURCE_EVENT_OVERRIDE = "event_override"
SOURCE_FEE_CHANGES = "fee_changes"
SOURCE_SERIES_NO_HISTORY = "series_metadata_no_fee_change_history"

CODE_UNRESOLVED_FEE_METADATA = "unresolved_fee_metadata"
CODE_UNSUPPORTED_FEE_TYPE = "unsupported_fee_type"
CODE_MISSING_HISTORICAL = "missing_historical_fee_info"
CODE_SERIES_FETCH_FAILURE = "series_metadata_fetch_failure"
CODE_SERIES_NOT_FOUND = "series_metadata_not_found"
CODE_SERIES_PARSE_FAILURE = "series_metadata_parse_failure"
CODE_FEE_CHANGE_FETCH_FAILURE = "fee_change_fetch_failure"
CODE_EVENT_FEE_CHANGE_FAILURE = "event_fee_change_failure"

UNRESOLVED_FEE_CODES = frozenset(
    {
        CODE_UNRESOLVED_FEE_METADATA,
        CODE_MISSING_HISTORICAL,
        CODE_SERIES_FETCH_FAILURE,
        CODE_SERIES_NOT_FOUND,
        CODE_SERIES_PARSE_FAILURE,
        CODE_FEE_CHANGE_FETCH_FAILURE,
        CODE_EVENT_FEE_CHANGE_FAILURE,
    }
)


class FeeResolutionError(ValueError):
    """Fee schedule cannot be determined confidently."""

    code: str = CODE_UNRESOLVED_FEE_METADATA

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class UnsupportedFeeError(FeeResolutionError):
    """Fee structure is understood but unsupported for taker simulation."""

    code = CODE_UNSUPPORTED_FEE_TYPE


@dataclass(frozen=True)
class FeeSchedule:
    fee_type: str
    fee_multiplier: Decimal
    source: str
    effective_ts: str | None = None
    historical_fee_changes_count: int | None = None


def parse_ts(value: str | datetime | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    # Numeric unix timestamps (seconds), including CSV string forms.
    if text.isdigit() or (
        text.startswith("-") and text[1:].isdigit()
    ) or _is_float_timestamp(text):
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
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


def _is_float_timestamp(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return "." in text and text.replace(".", "", 1).isdigit()


def to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise ValueError("Missing numeric value")
    return Decimal(str(value))


def round_fee_centicent(raw_fee: Decimal, position_cost: Decimal) -> Decimal:
    """Round up so fee + position_cost lands on a centicent boundary."""
    total = raw_fee + position_cost
    rounded_total = total.quantize(CENTICENT, rounding=ROUND_UP)
    fee = rounded_total - position_cost
    if fee < 0:
        fee = Decimal("0")
    return fee.quantize(CENTICENT)


def quadratic_taker_fee(
    *,
    price: Decimal,
    contracts: Decimal | int,
    multiplier: Decimal | int | float = 1,
) -> Decimal:
    """Kalshi-style quadratic taker fee."""
    p = to_decimal(price)
    c = to_decimal(contracts)
    m = to_decimal(multiplier)
    if p < 0 or p > 1:
        raise ValueError(f"Price out of range: {p}")
    if c <= 0:
        raise ValueError(f"Contracts must be positive: {c}")

    position_cost = (p * c).quantize(CENTICENT)
    raw = m * TAKER_COEFFICIENT * c * p * (Decimal("1") - p)
    return round_fee_centicent(raw, position_cost)


def _change_scheduled_ts(change: dict) -> datetime | None:
    return parse_ts(change.get("scheduled_ts") or change.get("effective_ts"))


def _latest_applicable(
    changes: list[dict],
    entry: datetime,
) -> tuple[datetime, dict] | None:
    applicable: list[tuple[datetime, dict]] = []
    for change in changes:
        scheduled = _change_scheduled_ts(change)
        if scheduled is None:
            continue
        if scheduled <= entry:
            applicable.append((scheduled, change))
    if not applicable:
        return None
    return max(applicable, key=lambda item: item[0])


def _series_fee_changes_for(
    fee_changes: list[dict],
    series_ticker: str,
) -> list[dict]:
    if not series_ticker:
        return list(fee_changes)
    return [
        change
        for change in fee_changes
        if change.get("series_ticker") == series_ticker
    ]


def _resolve_event_override(
    *,
    entry: datetime,
    event: dict,
    event_fee_changes: list[dict] | None,
) -> FeeSchedule | None:
    """Apply event override / clear timeline. Return schedule or None to fall through."""
    rows = list(event_fee_changes) if event_fee_changes is not None else []

    if rows:
        latest = _latest_applicable(rows, entry)
        if latest is None:
            # No event fee-change yet in force; fall through to series.
            return None
        scheduled, change = latest
        override_type = change.get("fee_type_override")
        override_mult = change.get("fee_multiplier_override")
        if override_type is None and override_mult is None:
            # Explicit clear — parent series fee applies.
            return None
        if override_type is None or override_mult is None:
            raise FeeResolutionError(
                "Incomplete event fee override change "
                f"(fee_type_override={override_type!r}, "
                f"fee_multiplier_override={override_mult!r})",
                code=CODE_EVENT_FEE_CHANGE_FAILURE,
            )
        return FeeSchedule(
            fee_type=str(override_type),
            fee_multiplier=to_decimal(override_mult),
            source=SOURCE_EVENT_OVERRIDE,
            effective_ts=scheduled.isoformat(),
        )

    # Snapshot event fields (no historical event fee-change rows).
    override_type = event.get("fee_type_override")
    override_mult = event.get("fee_multiplier_override")
    if override_type is None and override_mult is None:
        return None
    if override_type is None or override_mult is None:
        raise FeeResolutionError(
            "Incomplete event fee override on event snapshot "
            f"(fee_type_override={override_type!r}, "
            f"fee_multiplier_override={override_mult!r})",
            code=CODE_EVENT_FEE_CHANGE_FAILURE,
        )
    return FeeSchedule(
        fee_type=str(override_type),
        fee_multiplier=to_decimal(override_mult),
        source=SOURCE_EVENT_OVERRIDE,
        effective_ts=None,
    )


def resolve_fee_schedule(
    *,
    series_ticker: str,
    entry_ts: str | datetime | int | float,
    fee_changes: list[dict],
    event: dict | None = None,
    series: dict | None = None,
    event_fee_changes: list[dict] | None = None,
) -> FeeSchedule:
    """Resolve fee type/multiplier actually in effect at entry_ts.

    ``entry_ts`` must be the simulated execution time (selected candle
    ``end_period_ts``), not the nominal horizon target.

    Precedence:
      1. Event fee override timeline (active override, or clear → fall through)
      2. Latest series fee_change with scheduled_ts <= entry_ts
      3. If this series has *no* fee_change rows at all, fall back to current
         series metadata with source=series_metadata_no_fee_change_history
         (explicit continuity assumption: no recorded changes).
      4. Otherwise unresolved — do not apply today's metadata over a
         known later fee_change when entry precedes all changes.
    """
    event = event or {}
    series = series or {}

    entry = parse_ts(entry_ts)
    if entry is None:
        raise FeeResolutionError(
            "Missing/invalid entry timestamp for fee lookup",
            code=CODE_MISSING_HISTORICAL,
        )

    override = _resolve_event_override(
        entry=entry,
        event=event,
        event_fee_changes=event_fee_changes,
    )
    if override is not None:
        return override

    series_changes = _series_fee_changes_for(fee_changes, series_ticker)
    history_count = len(series_changes)

    applicable = _latest_applicable(series_changes, entry)
    if applicable is not None:
        scheduled, change = applicable
        fee_type = change.get("fee_type")
        fee_multiplier = change.get("fee_multiplier")
        if fee_type is None or fee_multiplier is None:
            raise FeeResolutionError(
                f"Incomplete fee_change for series {series_ticker}",
                code=CODE_MISSING_HISTORICAL,
            )
        return FeeSchedule(
            fee_type=str(fee_type),
            fee_multiplier=to_decimal(fee_multiplier),
            source=SOURCE_FEE_CHANGES,
            effective_ts=scheduled.isoformat(),
            historical_fee_changes_count=history_count,
        )

    if series_changes:
        raise FeeResolutionError(
            f"No fee_change covering entry_ts for series {series_ticker}",
            code=CODE_MISSING_HISTORICAL,
        )

    fee_type = series.get("fee_type")
    fee_multiplier = series.get("fee_multiplier")
    if fee_type is None or fee_multiplier is None:
        raise FeeResolutionError(
            f"No fee_changes and incomplete series fee metadata for {series_ticker}",
            code=CODE_UNRESOLVED_FEE_METADATA,
        )

    return FeeSchedule(
        fee_type=str(fee_type),
        fee_multiplier=to_decimal(fee_multiplier),
        source=SOURCE_SERIES_NO_HISTORY,
        effective_ts=None,
        historical_fee_changes_count=0,
    )


def compute_taker_fee(
    *,
    price: Decimal,
    contracts: Decimal | int,
    schedule: FeeSchedule,
) -> Decimal:
    if schedule.fee_type not in SUPPORTED_TAKER_FEE_TYPES:
        raise UnsupportedFeeError(
            f"Unsupported fee_type for taker simulation: {schedule.fee_type}",
            code=CODE_UNSUPPORTED_FEE_TYPE,
        )
    return quadratic_taker_fee(
        price=price,
        contracts=contracts,
        multiplier=schedule.fee_multiplier,
    )


def is_actually_unsupported(error: BaseException) -> bool:
    code = getattr(error, "code", None)
    if code == CODE_UNSUPPORTED_FEE_TYPE:
        return True
    text = str(error).lower()
    return "unsupported fee_type" in text

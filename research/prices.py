"""Executable prices and no-lookahead candle selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any


HORIZONS = {
    "7d": timedelta(days=7),
    "48h": timedelta(hours=48),
    "24h": timedelta(hours=24),
    "6h": timedelta(hours=6),
    "1h": timedelta(hours=1),
}


class PriceError(ValueError):
    pass


class CandleSchemaError(PriceError):
    """HTTP 200 candle payload could not be normalized to the canonical schema."""


def parse_ts(value: Any) -> datetime | None:
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


def dollars_to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _ohlc_field(block: Any, *keys: str) -> Any:
    if not isinstance(block, dict):
        return block
    for key in keys:
        if key in block and block.get(key) is not None:
            return block.get(key)
    return None


def extract_ohlc_close(block: Any) -> Decimal | None:
    """Extract close from a canonical or raw OHLC block."""
    if block is None:
        return None
    if isinstance(block, dict):
        value = _ohlc_field(block, "close", "close_dollars")
        return dollars_to_decimal(value)
    return dollars_to_decimal(block)


def _normalize_ohlc_block(block: Any) -> dict[str, Any] | None:
    """Normalize an OHLC quote block to canonical open/high/low/close strings."""
    if block is None:
        return None
    if not isinstance(block, dict):
        close = dollars_to_decimal(block)
        if close is None:
            return None
        text = str(close)
        return {"open": text, "high": text, "low": text, "close": text}

    open_v = dollars_to_decimal(_ohlc_field(block, "open", "open_dollars"))
    high_v = dollars_to_decimal(_ohlc_field(block, "high", "high_dollars"))
    low_v = dollars_to_decimal(_ohlc_field(block, "low", "low_dollars"))
    close_v = dollars_to_decimal(_ohlc_field(block, "close", "close_dollars"))
    if close_v is None and open_v is None and high_v is None and low_v is None:
        return None
    return {
        "open": str(open_v) if open_v is not None else None,
        "high": str(high_v) if high_v is not None else None,
        "low": str(low_v) if low_v is not None else None,
        "close": str(close_v) if close_v is not None else None,
    }


def normalize_candlestick(candle: Any) -> dict[str, Any]:
    """Normalize historical or live candlestick payloads to one canonical schema.

    Canonical fields:
      end_period_ts
      yes_bid.{open,high,low,close}
      yes_ask.{open,high,low,close}
      price.{open,high,low,close}   (optional)
      volume
      open_interest
    """
    if not isinstance(candle, dict):
        raise CandleSchemaError("candlestick is not an object")

    end_ts = candle.get("end_period_ts")
    try:
        end_period_ts = int(end_ts)
    except (TypeError, ValueError):
        raise CandleSchemaError("candlestick missing/invalid end_period_ts") from None

    yes_bid = _normalize_ohlc_block(candle.get("yes_bid"))
    yes_ask = _normalize_ohlc_block(candle.get("yes_ask"))
    if yes_bid is None and yes_ask is None:
        raise CandleSchemaError("candlestick missing yes_bid/yes_ask OHLC")

    price = _normalize_ohlc_block(candle.get("price"))
    volume = candle.get("volume")
    if volume is None:
        volume = candle.get("volume_fp")
    open_interest = candle.get("open_interest")
    if open_interest is None:
        open_interest = candle.get("open_interest_fp")

    return {
        "end_period_ts": end_period_ts,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "price": price,
        "volume": volume,
        "open_interest": open_interest,
    }


def normalize_candlesticks(candles: Any) -> list[dict[str, Any]]:
    """Normalize a list of candlesticks; raises CandleSchemaError on bad shapes."""
    if candles is None:
        return []
    if not isinstance(candles, list):
        raise CandleSchemaError("candlesticks payload is not a list")
    return [normalize_candlestick(c) for c in candles]


def candle_end_ts(candle: dict) -> int | None:
    value = candle.get("end_period_ts")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SelectedCandle:
    candle: dict
    end_period_ts: int
    target_ts: int
    lag_seconds: int
    yes_bid: Decimal
    yes_ask: Decimal
    spread: Decimal
    midquote: Decimal
    yes_entry: Decimal
    no_entry: Decimal
    last_trade: Decimal | None


def midquote_from_bid_ask(yes_bid: Decimal, yes_ask: Decimal) -> Decimal:
    """Belief proxy from quotes; not an executable taker price."""
    return ((yes_bid + yes_ask) / Decimal("2")).quantize(Decimal("0.0001"))


def horizon_target_ts(close_time: Any, horizon_key: str) -> int:
    if horizon_key not in HORIZONS:
        raise PriceError(f"Unknown horizon: {horizon_key}")
    close_dt = parse_ts(close_time)
    if close_dt is None:
        raise PriceError("Missing close_time")
    return int((close_dt - HORIZONS[horizon_key]).timestamp())


def classify_missing_candle(
    *,
    open_time: Any,
    target_ts: int,
) -> str:
    """
    Distinguish impossible horizons from true missing candles.

    horizon_before_market_open: target is before the market opened
    candle_missing_despite_market_being_open: market was open at target
    """
    open_dt = parse_ts(open_time)
    if open_dt is not None and target_ts < int(open_dt.timestamp()):
        return "horizon_before_market_open"
    return "candle_missing_despite_market_being_open"


def select_candle_for_horizon(
    candles: list[dict],
    *,
    close_time: Any,
    horizon_key: str,
    max_staleness_seconds: int,
    open_time: Any = None,
) -> SelectedCandle:
    """Pick latest candle with end_period_ts <= target_time. Never look ahead.

    Expects canonical candles from ``normalize_candlestick`` (or already-canonical
    fixtures). Raw live/historical schemas must be normalized first.
    """
    target_ts = horizon_target_ts(close_time, horizon_key)

    usable: list[tuple[int, dict]] = []
    for candle in candles:
        try:
            normalized = normalize_candlestick(candle)
        except CandleSchemaError:
            continue
        end_ts = candle_end_ts(normalized)
        if end_ts is None:
            continue
        if end_ts <= target_ts:
            usable.append((end_ts, normalized))

    if not usable:
        raise PriceError(classify_missing_candle(open_time=open_time, target_ts=target_ts))

    end_ts, candle = max(usable, key=lambda item: item[0])
    # Hard no-lookahead check
    if end_ts > target_ts:
        raise PriceError("lookahead_guard")

    lag = target_ts - end_ts
    if lag > max_staleness_seconds:
        raise PriceError("stale_candle")

    yes_bid = extract_ohlc_close(candle.get("yes_bid"))
    yes_ask = extract_ohlc_close(candle.get("yes_ask"))
    if yes_bid is None or yes_bid <= 0 or yes_bid >= 1:
        raise PriceError("missing_non_executable_bid")
    if yes_ask is None or yes_ask <= 0 or yes_ask >= 1:
        raise PriceError("missing_non_executable_ask")
    if yes_ask < yes_bid:
        raise PriceError("crossed_book")

    last_trade = None
    price_block = candle.get("price")
    if isinstance(price_block, dict):
        last_trade = extract_ohlc_close(price_block)

    yes_entry = yes_ask
    no_entry = (Decimal("1") - yes_bid).quantize(Decimal("0.0001"))
    spread = (yes_ask - yes_bid).quantize(Decimal("0.0001"))
    midquote = midquote_from_bid_ask(yes_bid, yes_ask)

    return SelectedCandle(
        candle=candle,
        end_period_ts=end_ts,
        target_ts=target_ts,
        lag_seconds=lag,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        spread=spread,
        midquote=midquote,
        yes_entry=yes_entry,
        no_entry=no_entry,
        last_trade=last_trade,
    )


def candle_window_for_close(close_time: Any) -> tuple[int, int]:
    """Unix start/end covering all horizons for one market."""
    close_dt = parse_ts(close_time)
    if close_dt is None:
        raise PriceError("Missing close_time")
    start = close_dt - timedelta(days=7, hours=2)
    end = close_dt + timedelta(minutes=1)
    return int(start.timestamp()), int(end.timestamp())

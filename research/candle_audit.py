"""Candlestick schema normalization and candle-fetch failure auditing."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import requests

from kalshi.client import KalshiAPIError, KalshiNotFoundError
from kalshi.historical import MissingSettlementTsError
from research.prices import (
    CandleSchemaError,
    PriceError,
    candle_window_for_close,
    parse_ts,
)
from research.sampling import close_year, seeded_rng


FAILURE_REASONS = (
    "http_400",
    "http_404",
    "http_429",
    "http_5xx",
    "timeout",
    "connection_error",
    "json_decode_error",
    "schema_parse_error",
    "missing_series_ticker",
    "missing_settlement_ts",
    "wrong_endpoint_partition",
    "empty_candles",
    "invalid_candle_window",
    "other",
)


@dataclass
class CandleFailureRecord:
    ticker: str
    event_ticker: str | None = None
    series_ticker: str | None = None
    category: str | None = None
    close_time: str | None = None
    settlement_ts: str | None = None
    data_source: str | None = None
    close_year: int | None = None
    market_settled_ts: str | None = None
    chosen_endpoint: str | None = None
    use_historical: bool | None = None
    start_ts: int | None = None
    end_ts: int | None = None
    period_interval: int | None = None
    http_status: int | None = None
    api_error_code: str | None = None
    api_error_message: str | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    response_shape: str | None = None
    failure_reason: str = "other"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def historical_candles_path(ticker: str) -> str:
    return f"/historical/markets/{ticker}/candlesticks"


def live_candles_path(series_ticker: str, ticker: str) -> str:
    return f"/series/{series_ticker}/markets/{ticker}/candlesticks"


def classify_kalshi_api_error(error: BaseException) -> tuple[str, int | None, str | None, str | None]:
    """Return (failure_reason, http_status, api_error_code, api_error_message)."""
    if isinstance(error, KalshiNotFoundError):
        return (
            "http_404",
            getattr(error, "status_code", 404) or 404,
            getattr(error, "api_error_code", None),
            getattr(error, "api_error_message", None) or str(error),
        )
    if isinstance(error, KalshiAPIError):
        status = getattr(error, "status_code", None)
        code = getattr(error, "api_error_code", None)
        msg = getattr(error, "api_error_message", None) or str(error)
        kind = getattr(error, "failure_kind", None)
        if kind in FAILURE_REASONS:
            return kind, status, code, msg
        if status == 400:
            return "http_400", status, code, msg
        if status == 404:
            return "http_404", status, code, msg
        if status == 429:
            return "http_429", status, code, msg
        if status is not None and status >= 500:
            return "http_5xx", status, code, msg
        text = str(error).lower()
        if "timeout" in text:
            return "timeout", status, code, msg
        if "connection" in text:
            return "connection_error", status, code, msg
        if "json" in text or "malformed" in text:
            return "json_decode_error", status, code, msg
        return "other", status, code, msg

    if isinstance(error, requests.Timeout):
        return "timeout", None, None, str(error)
    if isinstance(error, requests.ConnectionError):
        return "connection_error", None, None, str(error)
    if isinstance(error, (json.JSONDecodeError, ValueError)) and "json" in str(error).lower():
        return "json_decode_error", None, None, str(error)
    if isinstance(error, CandleSchemaError):
        return "schema_parse_error", None, None, str(error)
    if isinstance(error, MissingSettlementTsError):
        return "missing_settlement_ts", None, None, str(error)
    if isinstance(error, PriceError):
        text = str(error)
        if "series" in text.lower():
            return "missing_series_ticker", None, None, text
        if "close_time" in text.lower() or "window" in text.lower():
            return "invalid_candle_window", None, None, text
        return "other", None, None, text
    return "other", None, None, str(error)


def build_failure_record(
    market_row: dict,
    *,
    cutoff: dict | None = None,
    use_historical: bool | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    period_interval: int | None = 60,
    error: BaseException | None = None,
    failure_reason: str | None = None,
    response_shape: str | None = None,
) -> CandleFailureRecord:
    ticker = str(market_row.get("ticker") or "")
    series = market_row.get("series_ticker")
    reason = failure_reason
    http_status = None
    api_code = None
    api_msg = None
    exc_type = type(error).__name__ if error else None
    exc_msg = str(error) if error else None
    if error is not None and reason is None:
        reason, http_status, api_code, api_msg = classify_kalshi_api_error(error)
    reason = reason or "other"

    if use_historical is True:
        endpoint = historical_candles_path(ticker)
    elif use_historical is False and series:
        endpoint = live_candles_path(str(series), ticker)
    else:
        endpoint = None

    return CandleFailureRecord(
        ticker=ticker,
        event_ticker=market_row.get("event_ticker"),
        series_ticker=series,
        category=market_row.get("category"),
        close_time=str(market_row.get("close_time") or "") or None,
        settlement_ts=str(market_row.get("settlement_ts") or "") or None,
        data_source=market_row.get("data_source"),
        close_year=close_year(market_row),
        market_settled_ts=(cutoff or {}).get("market_settled_ts"),
        chosen_endpoint=endpoint,
        use_historical=use_historical,
        start_ts=start_ts,
        end_ts=end_ts,
        period_interval=period_interval,
        http_status=http_status,
        api_error_code=api_code,
        api_error_message=api_msg,
        exception_type=exc_type,
        exception_message=exc_msg,
        response_shape=response_shape,
        failure_reason=reason,
    )


def candle_failure_audit_path() -> Path:
    from kalshi import cache

    return cache.RESULTS_ROOT / "candle_failure_audit.csv"


FAILURE_CSV_FIELDS = [
    "ticker",
    "event_ticker",
    "series_ticker",
    "category",
    "close_time",
    "settlement_ts",
    "data_source",
    "close_year",
    "market_settled_ts",
    "chosen_endpoint",
    "use_historical",
    "start_ts",
    "end_ts",
    "period_interval",
    "http_status",
    "api_error_code",
    "api_error_message",
    "exception_type",
    "exception_message",
    "response_shape",
    "failure_reason",
]


def write_candle_failure_audit(rows: list[CandleFailureRecord], path: Path | None = None) -> Path:
    path = path or candle_failure_audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FAILURE_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())
    return path


def read_candle_failure_audit(path: Path | None = None) -> list[dict]:
    path = path or candle_failure_audit_path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def summarize_failures(rows: Iterable[dict | CandleFailureRecord]) -> dict[str, Counter]:
    reason: Counter = Counter()
    status: Counter = Counter()
    endpoint: Counter = Counter()
    data_source: Counter = Counter()
    year: Counter = Counter()
    category: Counter = Counter()
    for raw in rows:
        row = raw.as_dict() if isinstance(raw, CandleFailureRecord) else dict(raw)
        reason[str(row.get("failure_reason") or "other")] += 1
        st = row.get("http_status")
        status[str(st) if st not in (None, "") else "none"] += 1
        ep = row.get("chosen_endpoint") or "unknown"
        if isinstance(ep, str) and ep.startswith("/historical/"):
            endpoint["historical"] += 1
        elif isinstance(ep, str) and ep.startswith("/series/"):
            endpoint["live"] += 1
        else:
            endpoint[str(ep)] += 1
        data_source[str(row.get("data_source") or "unknown")] += 1
        year[str(row.get("close_year") or "unknown")] += 1
        category[str(row.get("category") or "Unknown")] += 1
    return {
        "failure_reason": reason,
        "http_status": status,
        "endpoint": endpoint,
        "data_source": data_source,
        "year": year,
        "category": category,
    }


def format_failure_summaries(summaries: dict[str, Counter]) -> str:
    lines: list[str] = ["CANDLE FAILURE SUMMARY"]
    for key in (
        "failure_reason",
        "http_status",
        "endpoint",
        "data_source",
        "year",
        "category",
    ):
        lines.append(f"\nby {key}:")
        for name, count in summaries[key].most_common():
            lines.append(f"  {name}: {count}")
    return "\n".join(lines)


def reconstruct_failures_from_log(
    log_rows: list[dict],
    markets_by_ticker: dict[str, dict],
    *,
    cutoff: dict | None = None,
) -> list[CandleFailureRecord]:
    """Build audit rows from failed_tickers.jsonl when CSV is absent."""
    out: list[CandleFailureRecord] = []
    for row in log_rows:
        if row.get("stage") and row.get("stage") != "candles":
            continue
        ticker = str(row.get("ticker") or "")
        market = markets_by_ticker.get(ticker) or {"ticker": ticker}
        reason_text = str(row.get("reason") or "")
        use_historical = None
        if "/historical/" in reason_text:
            use_historical = True
        elif "/series/" in reason_text:
            use_historical = False
        # Synthesize a Kalshi-like error for classification.
        error: BaseException
        if "Not found:" in reason_text:
            error = KalshiNotFoundError(reason_text, status_code=404, path=reason_text)
        elif reason_text.startswith("HTTP 400"):
            error = KalshiAPIError(reason_text, status_code=400)
        elif reason_text.startswith("HTTP 429"):
            error = KalshiAPIError(reason_text, status_code=429)
        else:
            error = KalshiAPIError(reason_text)
        try:
            start_ts, end_ts = candle_window_for_close(market.get("close_time"))
        except PriceError:
            start_ts, end_ts = None, None
        out.append(
            build_failure_record(
                market,
                cutoff=cutoff,
                use_historical=use_historical,
                start_ts=start_ts,
                end_ts=end_ts,
                error=error,
            )
        )
    return out


def select_retry_debug_mix(
    failures: list[dict],
    *,
    cutoff: dict,
    limit: int = 100,
    seed: int = 42,
    n_live404_should_historical: int = 80,
    n_genuine_live: int = 10,
    n_other: int = 10,
) -> list[dict]:
    """
    Deterministic mix validating the routing fix:
      - prior live-404 that should now route historical
      - genuinely-live markets (settlement >= cutoff)
      - other failure types
    """
    rng = seeded_rng(seed, "retry-candle-failures", limit)
    cutoff_ts = parse_ts((cutoff or {}).get("market_settled_ts"))

    live404_should_hist: list[dict] = []
    genuine_live: list[dict] = []
    other: list[dict] = []

    for row in failures:
        endpoint = str(row.get("chosen_endpoint") or "")
        reason = str(row.get("failure_reason") or "")
        is_live_404 = reason == "http_404" and (
            endpoint.startswith("/series/")
            or str(row.get("use_historical")).lower() in ("false", "0")
        )
        settled = parse_ts(row.get("settlement_ts"))
        should_hist = (
            settled is not None
            and cutoff_ts is not None
            and settled < cutoff_ts
        )
        should_live = (
            settled is not None
            and cutoff_ts is not None
            and settled >= cutoff_ts
        )
        if is_live_404 and should_hist:
            live404_should_hist.append(row)
        elif should_live:
            genuine_live.append(row)
        else:
            other.append(row)

    def take(pool: list[dict], n: int) -> list[dict]:
        if not pool or n <= 0:
            return []
        ordered = sorted(pool, key=lambda r: str(r.get("ticker") or ""))
        rng.shuffle(ordered)
        return ordered[:n]

    selected: list[dict] = []
    selected.extend(take(live404_should_hist, n_live404_should_historical))
    selected.extend(take(genuine_live, n_genuine_live))
    selected.extend(take(other, n_other))

    # Fill remainder from leftover pools if any bucket undersized.
    remaining = limit - len(selected)
    if remaining > 0:
        used = {str(r.get("ticker")) for r in selected}
        leftovers = [
            r
            for r in (live404_should_hist + genuine_live + other)
            if str(r.get("ticker")) not in used
        ]
        selected.extend(take(leftovers, remaining))

    return selected[:limit]


def fee_rejection_class(message: str, *, code: str | None = None) -> str:
    """Map a fee resolution failure to a diagnostic class.

    Metadata retrieval / wiring failures are unresolved — never
    ``unsupported_fee_type`` unless the fee structure itself is unsupported.
    """
    if code:
        return str(code)
    text = (message or "").lower()
    if "unsupported fee_type" in text:
        return "unsupported_fee_type"
    if "incomplete event fee" in text or "event fee override" in text:
        return "event_fee_change_failure"
    if "fee-change fetch failure" in text or (
        "fee_change" in text and ("api" in text or "request" in text or "http" in text)
    ):
        return "fee_change_fetch_failure"
    if "series metadata not found" in text:
        return "series_metadata_not_found"
    if "series metadata fetch failure" in text:
        return "series_metadata_fetch_failure"
    if "series metadata parse failure" in text:
        return "series_metadata_parse_failure"
    if "no fee_change covering" in text:
        return "missing_historical_fee_info"
    if "incomplete fee_change" in text:
        return "missing_historical_fee_info"
    if "incomplete series fee metadata" in text or "no fee_changes and incomplete" in text:
        return "unresolved_fee_metadata"
    if "missing/invalid entry" in text:
        return "missing_historical_fee_info"
    return "unresolved_fee_metadata"


UNSUPPORTED_FEE_CSV_FIELDS = [
    "ticker",
    "event_ticker",
    "series_ticker",
    "category",
    "close_year",
    "horizon",
    "entry_ts",
    "fee_type",
    "fee_multiplier",
    "fee_source",
    "rejection_class",
    "message",
]


def unsupported_fee_audit_path() -> Path:
    from kalshi import cache

    return cache.RESULTS_ROOT / "unsupported_fee_audit.csv"


def write_unsupported_fee_audit(rows: list[dict], path: Path | None = None) -> Path:
    path = path or unsupported_fee_audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=UNSUPPORTED_FEE_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in UNSUPPORTED_FEE_CSV_FIELDS})
    return path


def summarize_unsupported_fees(rows: list[dict]) -> dict[str, Counter]:
    out = {
        "fee_type": Counter(),
        "fee_multiplier": Counter(),
        "fee_source": Counter(),
        "series_ticker": Counter(),
        "category": Counter(),
        "close_year": Counter(),
        "rejection_class": Counter(),
    }
    for row in rows:
        for key in out:
            out[key][str(row.get(key) or "unknown")] += 1
    return out


def partition_fee_summaries(
    rows: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Split audit rows into (unresolved, actually_unsupported)."""
    unresolved: list[dict] = []
    unsupported: list[dict] = []
    for row in rows:
        cls = str(row.get("rejection_class") or "")
        if cls == "unsupported_fee_type":
            unsupported.append(row)
        else:
            unresolved.append(row)
    return unresolved, unsupported

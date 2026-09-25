"""Audit settled inventory rows whose close_time is after inventory build time."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from kalshi import cache
from research.sampling import _parse_ts, iter_jsonl


FUTURE_CLOSE_COLUMNS = (
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
    "settlement_ts",
    "occurrence_datetime",
    "can_close_early",
    "data_source",
    "settled_before_close",
    "seconds_between_close_and_settlement",
)


def resolve_inventory_build_time(snapshot: dict | None = None) -> datetime:
    snap = snapshot
    if snap is None:
        snap = cache.read_json(cache.inventory_snapshot_path(), default={}) or {}
    built = _parse_ts(snap.get("built_at"))
    if built is not None:
        return built
    return datetime.now(timezone.utc)


def future_close_row(
    row: dict,
    *,
    build_time: datetime,
) -> dict | None:
    close = _parse_ts(row.get("close_time"))
    if close is None or close <= build_time:
        return None
    if row.get("result") not in ("yes", "no"):
        return None

    settlement = _parse_ts(row.get("settlement_ts"))
    settled_before_close = None
    seconds_between = None
    if settlement is not None:
        seconds_between = int((close - settlement).total_seconds())
        settled_before_close = settlement < close

    return {
        "ticker": row.get("ticker"),
        "event_ticker": row.get("event_ticker"),
        "series_ticker": row.get("series_ticker"),
        "category": row.get("category"),
        "result": row.get("result"),
        "open_time": row.get("open_time"),
        "close_time": row.get("close_time"),
        "expected_expiration_time": row.get("expected_expiration_time"),
        "expiration_time": row.get("expiration_time"),
        "latest_expiration_time": row.get("latest_expiration_time"),
        "settlement_ts": row.get("settlement_ts"),
        "occurrence_datetime": row.get("occurrence_datetime"),
        "can_close_early": row.get("can_close_early"),
        "data_source": row.get("data_source"),
        "settled_before_close": settled_before_close,
        "seconds_between_close_and_settlement": seconds_between,
    }


def collect_future_close_rows(
    rows: Iterable[dict],
    *,
    build_time: datetime,
) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        audited = future_close_row(row, build_time=build_time)
        if audited is not None:
            out.append(audited)
    return out


def summarize_future_close(rows: list[dict]) -> dict[str, Any]:
    closes = [_parse_ts(r.get("close_time")) for r in rows]
    closes = [c for c in closes if c is not None]
    early_true = sum(1 for r in rows if r.get("can_close_early") is True)
    early_false = sum(1 for r in rows if r.get("can_close_early") is False)
    settled_before = [r for r in rows if r.get("settled_before_close") is True]
    deltas = [
        int(r["seconds_between_close_and_settlement"])
        for r in settled_before
        if r.get("seconds_between_close_and_settlement") is not None
    ]
    return {
        "future_close_rows": len(rows),
        "unique_events": len({r.get("event_ticker") for r in rows if r.get("event_ticker")}),
        "can_close_early_true": early_true,
        "can_close_early_false": early_false,
        "min_future_close": min(closes).isoformat() if closes else None,
        "max_future_close": max(closes).isoformat() if closes else None,
        "settled_before_close_count": len(settled_before),
        "settled_before_close_share": (
            len(settled_before) / len(rows) if rows else 0.0
        ),
        "median_seconds_settlement_before_close": (
            sorted(deltas)[len(deltas) // 2] if deltas else None
        ),
        "mean_seconds_settlement_before_close": (
            int(sum(deltas) / len(deltas)) if deltas else None
        ),
    }


def write_future_close_audit_csv(rows: list[dict], path: Path | None = None) -> Path:
    cache.ensure_dirs()
    out = path or (cache.RESULTS_ROOT / "future_close_audit.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FUTURE_CLOSE_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in FUTURE_CLOSE_COLUMNS})
    tmp.replace(out)
    return out


def run_future_close_audit(
    *,
    usable_path: Path | None = None,
    snapshot: dict | None = None,
    build_time: datetime | None = None,
) -> tuple[list[dict], dict[str, Any], Path]:
    path = usable_path or cache.usable_inventory_path()
    build = build_time or resolve_inventory_build_time(snapshot)
    rows = collect_future_close_rows(iter_jsonl(path), build_time=build)
    summary = summarize_future_close(rows)
    summary["inventory_build_time"] = build.isoformat()
    out_path = write_future_close_audit_csv(rows)
    return rows, summary, out_path

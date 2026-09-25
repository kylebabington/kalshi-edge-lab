"""Fee-resolution audit for the frozen sample (no candle/ROI rerun)."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from kalshi import cache
from kalshi.client import KalshiClient
from kalshi.fee_index import FeeIndex, classify_series_metadata_failure_cause
from kalshi.fees import (
    CODE_MISSING_HISTORICAL,
    CODE_UNSUPPORTED_FEE_TYPE,
    SOURCE_EVENT_OVERRIDE,
    SOURCE_FEE_CHANGES,
    SOURCE_SERIES_NO_HISTORY,
    FeeResolutionError,
    UnsupportedFeeError,
    is_actually_unsupported,
    parse_ts,
)
from research.audit import (
    pre_horizon_exclusion_summary,
    reconcile_sample_to_horizon,
)
from research.candle_audit import (
    fee_rejection_class,
    partition_fee_summaries,
    summarize_unsupported_fees,
    unsupported_fee_audit_path,
)


FORCE_INCLUDE_SERIES = (
    "APRPOTUS",
    "CASED",
    "FRM",
    "HIGHNY",
    "JOBLESS",
    "TSAW",
    "RAINSEA",
    "RAINNY",
    "HIGHCHI",
    "GAS",
)

FEE_RESOLUTION_DEBUG_FIELDS = (
    "series_ticker",
    "series_lookup_http_status",
    "series_lookup_response_or_error",
    "fee_change_lookup_http_status",
    "fee_change_row_count",
    "earliest_fee_change",
    "latest_fee_change",
    "current_fee_type",
    "current_fee_multiplier",
    "cached_metadata_available",
    "events_represented",
    "years_represented",
    "resolution_status",
)


def fee_resolution_debug_path() -> Path:
    return cache.RESULTS_ROOT / "fee_resolution_debug.csv"


def fee_coverage_funnel_path() -> Path:
    return cache.RESULTS_ROOT / "fee_coverage_funnel.csv"


def missing_historical_report_path() -> Path:
    return cache.RESULTS_ROOT / "missing_historical_fee_info_report.csv"


def load_fee_audit_rows(path: Path | None = None) -> list[dict]:
    path = path or unsupported_fee_audit_path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_observation_rows(path: Path | None = None) -> list[dict]:
    path = path or (cache.RESULTS_ROOT / "observations.csv")
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_candle_failure_rows(path: Path | None = None) -> list[dict]:
    path = path or (cache.RESULTS_ROOT / "candle_failure_audit.csv")
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_horizon_coverage_rows(path: Path | None = None) -> list[dict]:
    path = path or (cache.RESULTS_ROOT / "horizon_coverage.csv")
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def select_debug_series(
    audit_rows: list[dict],
    *,
    limit: int = 40,
    force_include: Iterable[str] = FORCE_INCLUDE_SERIES,
) -> list[str]:
    """Deterministic sample across years/categories plus force-include series."""
    force = [s for s in force_include]
    by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    counts = Counter(r.get("series_ticker") or "" for r in audit_rows)
    for row in audit_rows:
        series = str(row.get("series_ticker") or "")
        if not series:
            continue
        year = str(row.get("close_year") or "unknown")
        category = str(row.get("category") or "unknown")
        if series not in by_key[(year, category)]:
            by_key[(year, category)].append(series)

    selected: list[str] = []
    seen: set[str] = set()
    for series in force:
        if series not in seen:
            selected.append(series)
            seen.add(series)

    # Stable order by year then category.
    for year, category in sorted(by_key.keys()):
        for series in sorted(by_key[(year, category)], key=lambda s: (-counts[s], s)):
            if series in seen:
                continue
            selected.append(series)
            seen.add(series)
            if len(selected) >= limit:
                return selected
    return selected


def _earliest_latest_change(changes: list[dict]) -> tuple[str, str]:
    stamps: list[tuple[datetime, str]] = []
    for change in changes:
        ts = parse_ts(change.get("scheduled_ts") or change.get("effective_ts"))
        if ts is None:
            continue
        stamps.append((ts, ts.isoformat()))
    if not stamps:
        return "", ""
    stamps.sort(key=lambda item: item[0])
    return stamps[0][1], stamps[-1][1]


def build_fee_resolution_debug_rows(
    *,
    fee_index: FeeIndex,
    series_tickers: list[str],
    audit_rows: list[dict],
) -> list[dict]:
    events_by_series: dict[str, set[str]] = defaultdict(set)
    years_by_series: dict[str, set[str]] = defaultdict(set)
    for row in audit_rows:
        series = str(row.get("series_ticker") or "")
        if not series:
            continue
        if row.get("event_ticker"):
            events_by_series[series].add(str(row["event_ticker"]))
        if row.get("close_year"):
            years_by_series[series].add(str(row["close_year"]))

    rows: list[dict] = []
    for series in series_tickers:
        bundle = fee_index.get_series_bundle(series)
        meta = bundle.series_metadata or {}
        earliest, latest = _earliest_latest_change(bundle.fee_changes)
        cached_path = cache.series_path(series)
        cached_ok = False
        if cached_path.exists():
            cached = cache.read_json(cached_path, default=None)
            if isinstance(cached, dict) and not cached.get("_missing"):
                cached_ok = (
                    cached.get("fee_type") is not None
                    or (cached.get("series") or {}).get("fee_type") is not None
                    or meta.get("fee_type") is not None
                )
        series_resp = ""
        if bundle.series_error:
            series_resp = bundle.series_error
        elif meta:
            series_resp = (
                f"fee_type={meta.get('fee_type')};"
                f"fee_multiplier={meta.get('fee_multiplier')}"
            )
        rows.append(
            {
                "series_ticker": series,
                "series_lookup_http_status": bundle.series_http_status or "",
                "series_lookup_response_or_error": series_resp,
                "fee_change_lookup_http_status": bundle.fee_change_http_status or "",
                "fee_change_row_count": bundle.fee_change_row_count,
                "earliest_fee_change": earliest,
                "latest_fee_change": latest,
                "current_fee_type": meta.get("fee_type") or "",
                "current_fee_multiplier": meta.get("fee_multiplier")
                if meta.get("fee_multiplier") is not None
                else "",
                "cached_metadata_available": str(cached_ok).lower(),
                "events_represented": len(events_by_series.get(series, set())),
                "years_represented": ";".join(
                    sorted(years_by_series.get(series, set()))
                ),
                "resolution_status": bundle.resolution_status,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict], fieldnames: Iterable[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fieldnames)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})
    return path


def reclassify_series_metadata_failures(
    audit_rows: list[dict],
    fee_index: FeeIndex,
) -> Counter:
    """Break former series_metadata_failure / unresolved metadata into exact causes."""
    causes: Counter = Counter()
    relevant = [
        r
        for r in audit_rows
        if r.get("rejection_class")
        in ("series_metadata_failure", "unresolved_fee_metadata")
        or "incomplete series fee metadata" in str(r.get("message") or "").lower()
    ]
    # Unique by series for API/cache diagnosis, but count observation rows.
    series_cause: dict[str, str] = {}
    for row in relevant:
        series = str(row.get("series_ticker") or "")
        if series not in series_cause:
            bundle = fee_index.get_series_bundle(series)
            series_cause[series] = classify_series_metadata_failure_cause(
                series_ticker=series,
                market_row_had_series=False,  # lean inventory path
                bundle=bundle,
            )
        causes[series_cause[series]] += 1
    return causes


def provenance_bucket(source: str | None) -> str:
    if source == SOURCE_FEE_CHANGES:
        return "resolved_from_historical_fee_changes"
    if source == SOURCE_SERIES_NO_HISTORY:
        return "resolved_from_series_metadata_no_fee_change_history"
    if source == SOURCE_EVENT_OVERRIDE:
        return "resolved_from_event_override"
    if source == "documented_valid_fallback":
        return "resolved_from_documented_valid_fallback"
    return "unresolved"


def replay_fee_coverage(
    *,
    fee_index: FeeIndex,
    audit_rows: list[dict],
    observation_rows: list[dict],
) -> dict[str, Any]:
    """Replay fee resolution for prior fee-touching observations (no candles)."""
    funnel = Counter()
    by_year: dict[str, Counter] = defaultdict(Counter)
    by_category: dict[str, Counter] = defaultdict(Counter)
    by_series: dict[str, Counter] = defaultdict(Counter)
    recoverable = 0
    still_unresolved = 0
    still_unsupported = 0
    unique_markets_resolved: set[str] = set()
    unique_markets_unresolved: set[str] = set()

    # Prior successes keep their recorded source.
    for row in observation_rows:
        source = row.get("fee_source") or ""
        bucket = provenance_bucket(source)
        funnel[bucket] += 1
        year = str(row.get("close_year") or "unknown")
        category = str(row.get("category") or "unknown")
        series = str(row.get("series_ticker") or "unknown")
        by_year[year][bucket] += 1
        by_category[category][bucket] += 1
        by_series[series][bucket] += 1
        if row.get("ticker"):
            unique_markets_resolved.add(str(row["ticker"]))

    missing_hist_details: list[dict] = []
    for row in audit_rows:
        series = str(row.get("series_ticker") or "")
        entry_ts = row.get("entry_ts")
        ticker = str(row.get("ticker") or "")
        year = str(row.get("close_year") or "unknown")
        category = str(row.get("category") or "unknown")
        try:
            # Series-first resolve for coverage. Event overrides are audited
            # separately via fee_resolution_debug / event bundles for samples.
            # Passing every event_ticker here would re-fetch thousands of events.
            schedule = fee_index.resolve_for_market(
                series_ticker=series,
                entry_ts=entry_ts or "",
                event_ticker=None,
                event=None,
            )
            # Validate taker support.
            from decimal import Decimal

            from kalshi.fees import compute_taker_fee

            compute_taker_fee(
                price=Decimal("0.50"),
                contracts=1,
                schedule=schedule,
            )
            bucket = provenance_bucket(schedule.source)
            funnel[bucket] += 1
            by_year[year][bucket] += 1
            by_category[category][bucket] += 1
            by_series[series][bucket] += 1
            recoverable += 1
            if ticker:
                unique_markets_resolved.add(ticker)
        except UnsupportedFeeError as error:
            funnel["actually_unsupported_fee_type"] += 1
            by_year[year]["actually_unsupported_fee_type"] += 1
            by_category[category]["actually_unsupported_fee_type"] += 1
            by_series[series]["actually_unsupported_fee_type"] += 1
            still_unsupported += 1
            if ticker:
                unique_markets_unresolved.add(ticker)
            _ = error
        except FeeResolutionError as error:
            code = getattr(error, "code", None) or fee_rejection_class(str(error))
            if code == CODE_UNSUPPORTED_FEE_TYPE or is_actually_unsupported(error):
                funnel["actually_unsupported_fee_type"] += 1
                still_unsupported += 1
            else:
                funnel["unresolved"] += 1
                still_unresolved += 1
                by_year[year]["unresolved"] += 1
                by_category[category]["unresolved"] += 1
                by_series[series]["unresolved"] += 1
            if ticker:
                unique_markets_unresolved.add(ticker)
            if code == CODE_MISSING_HISTORICAL or "no fee_change covering" in str(
                error
            ).lower():
                bundle = fee_index.get_series_bundle(series)
                earliest, _latest = _earliest_latest_change(bundle.fee_changes)
                missing_hist_details.append(
                    {
                        "series": series,
                        "ticker": ticker,
                        "entry_ts": entry_ts,
                        "close_year": year,
                        "category": category,
                        "fee_change_history_available": bundle.fee_change_row_count > 0,
                        "fee_change_row_count": bundle.fee_change_row_count,
                        "earliest_known_fee_rule": earliest,
                        "why": str(error),
                    }
                )
        except Exception as error:  # noqa: BLE001
            funnel["unresolved"] += 1
            still_unresolved += 1
            by_year[year]["unresolved"] += 1
            by_category[category]["unresolved"] += 1
            by_series[series]["unresolved"] += 1
            if ticker:
                unique_markets_unresolved.add(ticker)
            _ = error

    return {
        "funnel": funnel,
        "by_year": {k: dict(v) for k, v in sorted(by_year.items())},
        "by_category": {k: dict(v) for k, v in sorted(by_category.items())},
        "by_series": {k: dict(v) for k, v in sorted(by_series.items())},
        "recoverable_observations": recoverable,
        "still_unresolved": still_unresolved,
        "still_unsupported": still_unsupported,
        "unique_markets_resolved": len(unique_markets_resolved),
        "unique_markets_unresolved": len(unique_markets_unresolved),
        "missing_historical_details": missing_hist_details,
    }


def summarize_missing_historical(details: list[dict]) -> list[dict]:
    """Collapse per-observation missing-historical rows to per-series summary."""
    by_series: dict[str, dict[str, Any]] = {}
    for row in details:
        series = str(row.get("series") or "")
        entry = parse_ts(row.get("entry_ts"))
        slot = by_series.setdefault(
            series,
            {
                "series": series,
                "n_observations": 0,
                "entry_ts_min": None,
                "entry_ts_max": None,
                "fee_change_history_available": row.get("fee_change_history_available"),
                "fee_change_row_count": row.get("fee_change_row_count"),
                "earliest_known_fee_rule": row.get("earliest_known_fee_rule"),
                "why": row.get("why"),
            },
        )
        slot["n_observations"] += 1
        if entry is not None:
            if slot["entry_ts_min"] is None or entry < parse_ts(slot["entry_ts_min"]):
                slot["entry_ts_min"] = entry.isoformat()
            if slot["entry_ts_max"] is None or entry > parse_ts(slot["entry_ts_max"]):
                slot["entry_ts_max"] = entry.isoformat()
    return [by_series[k] for k in sorted(by_series.keys())]


def reconcile_frozen_sample_horizons(
    *,
    selected_sample: int,
    candle_failures: list[dict] | None = None,
    horizon_rows: list[dict] | None = None,
) -> dict[str, Any]:
    candle_failures = candle_failures if candle_failures is not None else load_candle_failure_rows()
    horizon_rows = horizon_rows if horizon_rows is not None else load_horizon_coverage_rows()

    # Pre-horizon exclusions: candle fetch exceptions (not empty_candles).
    fetch_fails = [
        r
        for r in candle_failures
        if str(r.get("failure_reason") or "") not in ("", "empty_candles")
    ]
    empty = [
        r for r in candle_failures if str(r.get("failure_reason") or "") == "empty_candles"
    ]
    attempted = 0
    if horizon_rows:
        attempted = int(horizon_rows[0].get("markets_attempted") or 0)

    exclusions = pre_horizon_exclusion_summary(
        skip_reason=0,
        nonstandard_settlement=0,
        candle_fetch_failures=len(fetch_fails),
        other=0,
    )
    ok = reconcile_sample_to_horizon(
        selected_sample=selected_sample,
        horizon_attempted=attempted,
        pre_horizon_excluded=exclusions["pre_horizon_excluded"],
    )
    return {
        "selected_sample": selected_sample,
        "horizon_attempted": attempted,
        "pre_horizon_excluded": exclusions["pre_horizon_excluded"],
        "exclusion_breakdown": exclusions,
        "empty_candles_still_attempted": len(empty),
        "reconcile_ok": ok,
        "pre_horizon_markets": [
            {
                "ticker": r.get("ticker"),
                "failure_reason": r.get("failure_reason"),
                "http_status": r.get("http_status"),
                "exception_message": r.get("exception_message")
                or r.get("api_error_message"),
                "chosen_endpoint": r.get("chosen_endpoint"),
            }
            for r in fetch_fails
        ],
    }


def run_fee_resolution_audit(
    *,
    client: KalshiClient,
    markets: list[dict],
    refresh: bool = False,
    progress: Any = print,
) -> dict[str, Any]:
    """Fee-only audit against frozen sample artifacts (no candles/ROI)."""
    cache.ensure_dirs()
    fee_index = FeeIndex(client=client, refresh=refresh, progress=progress)
    audit_rows = load_fee_audit_rows()
    observation_rows = load_observation_rows()

    # Ensure fee cache for every series in the frozen sample + audit.
    series_needed: set[str] = set()
    for m in markets:
        if m.get("series_ticker"):
            series_needed.add(str(m["series_ticker"]))
    for row in audit_rows:
        if row.get("series_ticker"):
            series_needed.add(str(row["series_ticker"]))

    progress(f"Building per-series fee cache for {len(series_needed):,} series...")
    fee_change_success = 0
    for idx, series in enumerate(sorted(series_needed), start=1):
        bundle = fee_index.get_series_bundle(series)
        if bundle.fee_change_http_status == 200 or (
            bundle.fee_change_error is None and bundle.resolution_status == "ok"
        ):
            fee_change_success += 1
        if idx % 100 == 0:
            progress(f"  fee cache {idx}/{len(series_needed)}")

    debug_series = select_debug_series(audit_rows)
    debug_rows = build_fee_resolution_debug_rows(
        fee_index=fee_index,
        series_tickers=debug_series,
        audit_rows=audit_rows,
    )
    debug_path = write_csv(
        fee_resolution_debug_path(),
        debug_rows,
        FEE_RESOLUTION_DEBUG_FIELDS,
    )
    progress(f"Wrote fee resolution debug: {debug_path}")

    cause_counts = reclassify_series_metadata_failures(audit_rows, fee_index)
    coverage = replay_fee_coverage(
        fee_index=fee_index,
        audit_rows=audit_rows,
        observation_rows=observation_rows,
    )
    missing_summary = summarize_missing_historical(
        coverage["missing_historical_details"]
    )
    missing_path = write_csv(
        missing_historical_report_path(),
        missing_summary,
        (
            "series",
            "n_observations",
            "entry_ts_min",
            "entry_ts_max",
            "fee_change_history_available",
            "fee_change_row_count",
            "earliest_known_fee_rule",
            "why",
        ),
    )

    # Funnel flat CSV
    funnel_rows = [
        {"bucket": k, "observations": v} for k, v in coverage["funnel"].most_common()
    ]
    funnel_path = write_csv(
        fee_coverage_funnel_path(),
        funnel_rows,
        ("bucket", "observations"),
    )

    sample_reconcile = reconcile_frozen_sample_horizons(
        selected_sample=len(markets),
    )

    unresolved_rows, unsupported_rows = partition_fee_summaries(audit_rows)

    return {
        "series_needed": len(series_needed),
        "fee_changes_lookup_success": fee_change_success,
        "fee_changes_lookup_total": len(series_needed),
        "debug_path": str(debug_path),
        "debug_series_count": len(debug_series),
        "series_metadata_failure_causes": dict(cause_counts),
        "series_metadata_failure_total": sum(cause_counts.values()),
        "unique_series_in_metadata_failures": len(
            {
                r.get("series_ticker")
                for r in audit_rows
                if r.get("rejection_class")
                in ("series_metadata_failure", "unresolved_fee_metadata")
                or "incomplete series fee metadata"
                in str(r.get("message") or "").lower()
            }
        ),
        "coverage": coverage,
        "missing_historical_summary": missing_summary,
        "missing_historical_path": str(missing_path),
        "funnel_path": str(funnel_path),
        "sample_reconcile": sample_reconcile,
        "prior_unresolved_count": len(unresolved_rows),
        "prior_unsupported_count": len(unsupported_rows),
        "prior_rejection_class": dict(
            summarize_unsupported_fees(audit_rows)["rejection_class"]
        ),
    }

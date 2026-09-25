"""
Kalshi Edge Lab — Market Efficiency Backtest

Public market data only. No live trading. No credentials.

Primary execution model:
  1-contract taker at historical top-of-book bid/ask close.

Sensitivity sizes 10/100 are labeled:
  "top-of-book price assumption; historical depth unavailable"
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console

from kalshi import cache
from kalshi.client import KalshiAPIError, KalshiClient
from kalshi.fee_index import FeeIndex
from kalshi.fees import (
    FeeResolutionError,
    UnsupportedFeeError,
    compute_taker_fee,
    is_actually_unsupported,
)
from kalshi.historical import (
    MarketInventory,
    MissingSettlementTsError,
    build_inventory_snapshot,
    candle_endpoint_for_market,
    market_uses_historical_candles,
    write_usable_inventory,
)
from research.audit import (
    HorizonCoverageTracker,
    extreme_quote_rows,
    lifespan_report,
    pre_horizon_exclusion_summary,
    reconcile_sample_to_horizon,
)
from research.candle_audit import (
    build_failure_record,
    fee_rejection_class,
    format_failure_summaries,
    partition_fee_summaries,
    read_candle_failure_audit,
    reconstruct_failures_from_log,
    select_retry_debug_mix,
    summarize_failures,
    summarize_unsupported_fees,
    write_candle_failure_audit,
    write_unsupported_fee_audit,
)
from research.fee_audit import run_fee_resolution_audit
from research.efficiency import (
    category_summary,
    choose_period_bounds,
    favorite_longshot_report,
    period_name,
    period_summaries,
    rank_simple_strategies,
    spread_filtered_profitability,
    summarize_side,
    yearly_summary,
)
from research.h1 import summarize_h1
from research.liquidity import annotate_liquidity_groups
from research.price_buckets import (
    aggregate_midquote_calibration,
    aggregate_price_buckets,
    tight_spread_midquote_calibrations,
)
from research.prices import (
    HORIZONS,
    CandleSchemaError,
    PriceError,
    candle_window_for_close,
    normalize_candlesticks,
    select_candle_for_horizon,
)
from research.future_close import run_future_close_audit
from research.reporting import (
    print_efficiency_report,
    print_future_close_audit,
    print_inventory_audit,
    print_recent_settled_diagnostics,
    print_sampling_diagnostics,
    save_results,
)
from research.sample_identity import (
    SampleIdentityError,
    build_sample_identity_manifest,
    compute_sample_hash,
    format_frozen_sample_banner,
    freeze_from_preview,
    load_frozen_sample,
    load_preview_tickers,
    observation_population_diagnostics,
    sample_identity_path,
    sample_population_diagnostics,
)
from research.sampling import (
    SAMPLING_BALANCED,
    SAMPLING_MODES,
    SamplingDiagnostics,
    close_year,
    inventory_diagnostics,
    stratified_sample,
    stratified_sample_from_jsonl,
)
from research.settlement import simulate_taker_trade


console = Console(legacy_windows=False, force_terminal=True)

PRIMARY_CONTRACTS = 1
SENSITIVITY_CONTRACTS = (10, 100)
DEFAULT_STALENESS_SECONDS = 2 * 60 * 60  # 2 hours
DEPTH_DISCLAIMER = "top-of-book price assumption; historical depth unavailable"
DEFAULT_MAX_MARKETS_PER_EVENT = 5
DEFAULT_MAX_MARKETS_PER_SERIES_DATE = 10
SMOKE_INVENTORY_MAX_ITEMS = 5000
PERIOD_INTERVAL = 60


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kalshi whole-market efficiency backtest (research only).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--smoke",
        action="store_true",
        help="Small end-to-end run (~50 stratified markets).",
    )
    mode.add_argument(
        "--max-markets",
        type=int,
        help="Cap analysis sample size after stratified sampling.",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="Analyze the full usable inventory (large download).",
    )
    mode.add_argument(
        "--inventory-only",
        action="store_true",
        help="Build/resume full market inventory and exit (no candles/analysis).",
    )
    mode.add_argument(
        "--sample-preview",
        action="store_true",
        help="Dry sampling preview from cached usable inventory (no candles).",
    )
    mode.add_argument(
        "--retry-candle-failures",
        action="store_true",
        help="Retry a deterministic subset of prior candle failures (no full study).",
    )
    mode.add_argument(
        "--debug-candles",
        action="store_true",
        help="Alias for --retry-candle-failures.",
    )
    mode.add_argument(
        "--freeze-sample",
        action="store_true",
        help="Build sample identity manifest from sample_preview_*.json and exit.",
    )
    mode.add_argument(
        "--audit-fees",
        action="store_true",
        help=(
            "Fee-resolution audit for the frozen sample only "
            "(no candles / no ROI rerun)."
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore local cache and re-download (respects --inventory-stream).",
    )
    parser.add_argument(
        "--inventory-stream",
        choices=("all", "historical", "recent"),
        default="all",
        help="Which inventory stream(s) to fetch/refresh with --inventory-only.",
    )
    parser.add_argument(
        "--sampling",
        choices=list(SAMPLING_MODES),
        default=SAMPLING_BALANCED,
        help="Sample allocation mode (default: balanced).",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="Sample size for --sample-preview (required with that mode).",
    )
    parser.add_argument(
        "--frozen-sample",
        type=str,
        default=None,
        help="Path to sample identity manifest (default: data/results/sample_identity_<mode>.json).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Retry/debug subset size (default 100).",
    )
    parser.add_argument(
        "--horizon",
        default="24h",
        choices=list(HORIZONS.keys()),
        help="Primary horizon highlighted in the console report.",
    )
    parser.add_argument(
        "--staleness-seconds",
        type=int,
        default=DEFAULT_STALENESS_SECONDS,
        help="Reject candles older than this many seconds before target time.",
    )
    parser.add_argument(
        "--min-observations",
        type=int,
        default=100,
        help="Minimum N for performance inference headlines and ranked strategies.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic sampling / bootstrap seed.",
    )
    parser.add_argument(
        "--max-markets-per-event",
        type=int,
        default=DEFAULT_MAX_MARKETS_PER_EVENT,
        help="Analysis-sample cap per event_ticker.",
    )
    parser.add_argument(
        "--max-markets-per-series-date",
        type=int,
        default=DEFAULT_MAX_MARKETS_PER_SERIES_DATE,
        help="Analysis-sample cap per series_ticker + close date.",
    )
    parser.add_argument(
        "--inventory-max-items",
        type=int,
        default=None,
        help="Cap inventory download/resume depth (metadata only; default unlimited).",
    )
    parser.add_argument(
        "--include-sensitivity",
        action="store_true",
        help="Also compute 10/100 contract sensitivity columns (depth unavailable).",
    )
    args = parser.parse_args(argv)
    if args.debug_candles:
        args.retry_candle_failures = True
    if args.sample_preview and not args.sample_size:
        parser.error("--sample-preview requires --sample-size N")
    return args


def _inventory_integrity_ok(snapshot: dict | None) -> bool:
    return bool(snapshot and snapshot.get("inventory_integrity_valid") is True)


def _print_inventory_integrity_failure(snapshot: dict | None) -> None:
    errors = list((snapshot or {}).get("inventory_integrity_errors") or [])
    if not errors:
        errors = ["inventory_integrity_valid is not true"]
    console.print("[red]Inventory integrity check failed. Sampling/candles are blocked.[/red]")
    for error in errors:
        console.print(f"[red]  {error}[/red]")


def progress(message: str) -> None:
    console.print(f"[dim]{message}[/dim]")


def build_observation(
    *,
    market_row: dict,
    horizon: str,
    selected,
    result: str,
    schedule,
    contracts: int = PRIMARY_CONTRACTS,
) -> dict[str, Any]:
    yes_trade = simulate_taker_trade(
        side="YES",
        entry_price=selected.yes_entry,
        result=result,
        contracts=contracts,
        schedule=schedule,
    )
    no_trade = simulate_taker_trade(
        side="NO",
        entry_price=selected.no_entry,
        result=result,
        contracts=contracts,
        schedule=schedule,
    )

    obs = {
        "ticker": market_row["ticker"],
        "event_ticker": market_row.get("event_ticker"),
        "series_ticker": market_row.get("series_ticker"),
        "category": market_row.get("category"),
        "open_time": market_row.get("open_time"),
        "close_time": market_row.get("close_time"),
        "expected_expiration_time": market_row.get("expected_expiration_time"),
        "expiration_time": market_row.get("expiration_time"),
        "latest_expiration_time": market_row.get("latest_expiration_time"),
        "occurrence_datetime": market_row.get("occurrence_datetime"),
        "settlement_ts": market_row.get("settlement_ts"),
        "can_close_early": market_row.get("can_close_early"),
        "result": result,
        "horizon": horizon,
        "target_entry_ts": selected.target_ts,
        "candle_timestamp": selected.end_period_ts,
        "candle_lag_seconds": selected.lag_seconds,
        "yes_bid": str(selected.yes_bid),
        "yes_ask": str(selected.yes_ask),
        "spread": str(selected.spread),
        "midquote": str(selected.midquote),
        "yes_entry": str(selected.yes_entry),
        "no_entry": str(selected.no_entry),
        "last_trade": str(selected.last_trade) if selected.last_trade is not None else "",
        "volume": market_row.get("volume"),
        "open_interest": market_row.get("open_interest"),
        "contracts": contracts,
        "execution_note": (
            "primary_1_contract"
            if contracts == 1
            else DEPTH_DISCLAIMER
        ),
        "fee_type": schedule.fee_type,
        "fee_multiplier": str(schedule.fee_multiplier),
        "fee_source": schedule.source,
        "historical_fee_changes_count": schedule.historical_fee_changes_count,
        "yes_fee": str(yes_trade.fee),
        "no_fee": str(no_trade.fee),
        "yes_total_cost": str(yes_trade.total_cost),
        "no_total_cost": str(no_trade.total_cost),
        "yes_profit": str(yes_trade.net_profit),
        "no_profit": str(no_trade.net_profit),
        "yes_roi": str(yes_trade.roi),
        "no_roi": str(no_trade.roi),
        "yes_won": yes_trade.won,
        "no_won": no_trade.won,
        "data_source": market_row.get("data_source"),
    }
    return obs


def fetch_current_cutoff_or_abort(inventory: MarketInventory) -> dict:
    """Force-network cutoff fetch. Abort on failure; never use stale cache."""
    try:
        cutoff = inventory.fetch_current_cutoff_force_network()
    except Exception as error:  # noqa: BLE001 — must abort candle runs
        console.print(
            f"[red]FATAL: network GET /historical/cutoff failed: {error}[/red]"
        )
        console.print(
            "[red]Aborting candle run. Will not fall back to cached cutoff.[/red]"
        )
        raise SystemExit(2) from error
    progress(
        f"Current market_settled_ts (network): {cutoff.get('market_settled_ts')}"
    )
    return cutoff


def fetch_candles_for_market(
    client: KalshiClient,
    market_row: dict,
    cutoff: dict,
    *,
    refresh: bool,
) -> tuple[list[dict], dict[str, Any]]:
    """Fetch and normalize candles. Returns (candles, meta).

    meta includes endpoint routing fields for audits / retry transitions.
    Raises KalshiAPIError / PriceError / CandleSchemaError / MissingSettlementTsError.
    """
    ticker = market_row.get("ticker")
    series_ticker = market_row.get("series_ticker")
    if not ticker:
        raise PriceError("Missing ticker")
    if not series_ticker:
        raise PriceError("Missing series_ticker")

    start_ts, end_ts = candle_window_for_close(market_row.get("close_time"))
    if not (start_ts < end_ts):
        raise PriceError("invalid candle window: start_ts >= end_ts")
    if PERIOD_INTERVAL != 60:
        raise PriceError("period_interval must be 60 for this study")

    use_historical, endpoint = candle_endpoint_for_market(market_row, cutoff)
    meta: dict[str, Any] = {
        "ticker": ticker,
        "series_ticker": series_ticker,
        "use_historical": use_historical,
        "chosen_endpoint": endpoint,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "period_interval": PERIOD_INTERVAL,
        "market_settled_ts": cutoff.get("market_settled_ts"),
        "settlement_ts": market_row.get("settlement_ts"),
    }

    path = cache.candle_path(str(ticker), PERIOD_INTERVAL)
    if path.exists() and not refresh:
        payload = cache.read_json(path, default={}) or {}
        raw = payload.get("candlesticks") or []
        candles = normalize_candlesticks(raw)
        meta["from_cache"] = True
        meta["cached_use_historical"] = payload.get("use_historical")
        return candles, meta

    candles_raw = client.get_candlesticks_for_market(
        ticker=str(ticker),
        series_ticker=str(series_ticker),
        start_ts=start_ts,
        end_ts=end_ts,
        period_interval=PERIOD_INTERVAL,
        use_historical=use_historical,
    )
    try:
        candles = normalize_candlesticks(candles_raw)
    except CandleSchemaError:
        meta["response_shape"] = _response_shape_summary(candles_raw)
        raise

    cache.write_json(
        path,
        {
            "ticker": ticker,
            "series_ticker": series_ticker,
            "use_historical": use_historical,
            "chosen_endpoint": endpoint,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period_interval": PERIOD_INTERVAL,
            "market_settled_ts": cutoff.get("market_settled_ts"),
            "candlesticks": candles,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    meta["from_cache"] = False
    return candles, meta


def _response_shape_summary(payload: Any) -> str:
    if payload is None:
        return "null"
    if not isinstance(payload, list):
        return f"type={type(payload).__name__}"
    if not payload:
        return "list:len=0"
    first = payload[0]
    if not isinstance(first, dict):
        return f"list:len={len(payload)};elem={type(first).__name__}"
    keys = sorted(first.keys())
    bid = first.get("yes_bid")
    bid_keys = sorted(bid.keys()) if isinstance(bid, dict) else None
    return f"list:len={len(payload)};keys={keys};yes_bid_keys={bid_keys}"


def fetch_candles_cached(
    client: KalshiClient,
    market_row: dict,
    cutoff: dict,
    *,
    refresh: bool,
) -> list[dict]:
    """Back-compat wrapper used by the main analysis loop."""
    candles, _meta = fetch_candles_for_market(
        client, market_row, cutoff, refresh=refresh
    )
    return candles


def _write_identity_from_markets(
    markets: list[dict],
    *,
    sampling_mode: str,
    seed: int,
    sample_size: int,
    inventory_hash: str | None,
) -> Path:
    manifest = build_sample_identity_manifest(
        markets=markets,
        sampling_mode=sampling_mode,
        seed=seed,
        sample_size=sample_size,
        inventory_hash=inventory_hash,
    )
    path = sample_identity_path(sampling_mode)
    cache.write_json(path, manifest)
    return path


def run_freeze_sample(args: argparse.Namespace) -> int:
    """Persist sample identity from the balanced preview; verify hash."""
    cache.ensure_dirs()
    preview_path = cache.RESULTS_ROOT / f"sample_preview_{args.sampling}.json"
    snapshot = cache.read_json(cache.inventory_snapshot_path(), default={}) or {}
    inventory_hash = snapshot.get("inventory_hash")
    try:
        manifest = freeze_from_preview(
            preview_path=preview_path,
            inventory_hash=inventory_hash,
            sampling_mode=args.sampling,
        )
    except SampleIdentityError as error:
        console.print(f"[red]{error}[/red]")
        return 2

    # Resolve markets so year/category/events match inventory reality.
    if not cache.usable_inventory_path().exists():
        console.print("[red]usable_inventory.jsonl missing; cannot verify sample.[/red]")
        return 2
    identity_path = sample_identity_path(args.sampling)
    cache.write_json(identity_path, manifest)
    try:
        markets, verified = load_frozen_sample(
            identity_path,
            cache.usable_inventory_path(),
            expected_hash=manifest["sample_hash"],
        )
    except SampleIdentityError as error:
        console.print(f"[red]Frozen sample verification failed: {error}[/red]")
        return 2
    cache.write_json(identity_path, verified)
    console.print(format_frozen_sample_banner(verified))
    progress(f"Wrote frozen sample identity: {identity_path}")
    progress(f"Resolved markets: {len(markets):,}")
    return 0


def _ensure_failure_audit(
    *,
    markets_by_ticker: dict[str, dict],
    cutoff: dict,
) -> list[dict]:
    """Load or reconstruct candle_failure_audit.csv from the prior 5k run log."""
    existing = read_candle_failure_audit()
    if existing:
        return existing
    log_rows = cache.read_jsonl(cache.failed_tickers_path())
    records = reconstruct_failures_from_log(
        log_rows, markets_by_ticker, cutoff=cutoff
    )
    # Prefer latest failure per ticker.
    by_ticker: dict[str, Any] = {}
    for rec in records:
        by_ticker[rec.ticker] = rec
    deduped = list(by_ticker.values())
    write_candle_failure_audit(deduped)
    return [r.as_dict() for r in deduped]


def run_retry_candle_failures(args: argparse.Namespace) -> int:
    """Retry a deterministic 80/10/10 mix; validate routing fix. No full study."""
    cache.ensure_dirs()
    sampling_mode = getattr(args, "sampling", SAMPLING_BALANCED)
    identity_path = (
        Path(args.frozen_sample)
        if args.frozen_sample
        else sample_identity_path(sampling_mode)
    )
    preview_path = cache.RESULTS_ROOT / f"sample_preview_{sampling_mode}.json"

    if not identity_path.exists():
        rc = run_freeze_sample(args)
        if rc != 0:
            return rc

    preview_tickers, _ = load_preview_tickers(preview_path)
    expected_hash = compute_sample_hash(preview_tickers)
    try:
        markets, manifest = load_frozen_sample(
            identity_path,
            cache.usable_inventory_path(),
            expected_hash=expected_hash,
        )
    except SampleIdentityError as error:
        console.print(f"[red]{error}[/red]")
        return 2

    console.print(format_frozen_sample_banner(manifest))

    client = KalshiClient(progress=progress)
    inventory = MarketInventory(client, refresh=False, progress=progress)
    cutoff = fetch_current_cutoff_or_abort(inventory)

    markets_by_ticker = {str(m.get("ticker")): m for m in markets}
    failures = _ensure_failure_audit(
        markets_by_ticker=markets_by_ticker, cutoff=cutoff
    )
    # Annotate old failures with whether they should now be historical under
    # the *current* cutoff (for wrong_endpoint_partition labeling).
    annotated: list[dict] = []
    for row in failures:
        ticker = str(row.get("ticker") or "")
        market = markets_by_ticker.get(ticker) or {
            "ticker": ticker,
            "settlement_ts": row.get("settlement_ts"),
            "series_ticker": row.get("series_ticker"),
            "close_time": row.get("close_time"),
            "category": row.get("category"),
            "data_source": row.get("data_source"),
            "event_ticker": row.get("event_ticker"),
        }
        # Fill missing market fields from inventory when available.
        for key in (
            "settlement_ts",
            "series_ticker",
            "close_time",
            "category",
            "data_source",
            "event_ticker",
        ):
            if not row.get(key) and market.get(key):
                row[key] = market.get(key)
        old_endpoint = row.get("chosen_endpoint") or ""
        try:
            new_hist, new_endpoint = candle_endpoint_for_market(market, cutoff)
            row["_new_use_historical"] = new_hist
            row["_new_endpoint"] = new_endpoint
            if (
                str(row.get("failure_reason")) == "http_404"
                and str(old_endpoint).startswith("/series/")
                and new_hist
            ):
                row["failure_reason_note"] = "wrong_endpoint_partition"
        except MissingSettlementTsError:
            row["_new_use_historical"] = None
            row["_new_endpoint"] = None
        annotated.append(row)

    summaries = summarize_failures(annotated)
    console.print(format_failure_summaries(summaries))

    selected = select_retry_debug_mix(
        annotated,
        cutoff=cutoff,
        limit=args.limit,
        seed=args.seed,
    )
    progress(
        f"Retrying {len(selected)} markets "
        f"(target mix 80 live404->hist / 10 live / 10 other)..."
    )

    transitions: list[dict] = []
    reason_counts: Counter = Counter()
    for idx, fail_row in enumerate(selected, start=1):
        ticker = str(fail_row.get("ticker") or "")
        market = markets_by_ticker.get(ticker)
        if market is None:
            progress(f"[{idx}/{len(selected)}] {ticker} MISSING from frozen sample")
            transitions.append(
                {
                    "ticker": ticker,
                    "old_endpoint": fail_row.get("chosen_endpoint"),
                    "new_endpoint": None,
                    "retry_result": "missing_from_sample",
                }
            )
            continue

        old_endpoint = fail_row.get("chosen_endpoint")
        try:
            candles, meta = fetch_candles_for_market(
                client, market, cutoff, refresh=True
            )
            new_endpoint = meta.get("chosen_endpoint")
            if not candles:
                result = "empty_candles"
                reason_counts["empty_candles"] += 1
            else:
                result = f"success_n={len(candles)}"
                reason_counts["success"] += 1
            progress(
                f"[{idx}/{len(selected)}] {ticker}\n"
                f"  old={old_endpoint}\n"
                f"  new={new_endpoint}\n"
                f"  result={result}"
            )
            transitions.append(
                {
                    "ticker": ticker,
                    "old_endpoint": old_endpoint,
                    "new_endpoint": new_endpoint,
                    "use_historical": meta.get("use_historical"),
                    "retry_result": result,
                    "n_candles": len(candles),
                }
            )
        except Exception as error:  # noqa: BLE001
            try:
                use_historical, new_endpoint = candle_endpoint_for_market(
                    market, cutoff
                )
            except MissingSettlementTsError as miss:
                use_historical, new_endpoint = None, None
                error = miss
            record = build_failure_record(
                market,
                cutoff=cutoff,
                use_historical=use_historical,
                error=error,
            )
            reason_counts[record.failure_reason] += 1
            progress(
                f"[{idx}/{len(selected)}] {ticker}\n"
                f"  old={old_endpoint}\n"
                f"  new={new_endpoint}\n"
                f"  result=FAIL {record.failure_reason}: {error}"
            )
            transitions.append(
                {
                    "ticker": ticker,
                    "old_endpoint": old_endpoint,
                    "new_endpoint": new_endpoint,
                    "use_historical": use_historical,
                    "retry_result": f"fail:{record.failure_reason}",
                    "error": str(error),
                }
            )

    console.print()
    console.print("ROUTE TRANSITION SUMMARY")
    live_to_hist_ok = 0
    live_to_hist_fail = 0
    for row in transitions:
        old_e = str(row.get("old_endpoint") or "")
        new_e = str(row.get("new_endpoint") or "")
        result = str(row.get("retry_result") or "")
        console.print(
            f"  {row.get('ticker')}: {old_e} -> {new_e} | {result}"
        )
        if old_e.startswith("/series/") and new_e.startswith("/historical/"):
            if result.startswith("success"):
                live_to_hist_ok += 1
            else:
                live_to_hist_fail += 1

    console.print()
    console.print(
        f"Prior live->historical successes: {live_to_hist_ok}  "
        f"failures: {live_to_hist_fail}"
    )
    console.print("Retry result counts:")
    for name, count in reason_counts.most_common():
        console.print(f"  {name}: {count}")

    out_path = cache.RESULTS_ROOT / "candle_retry_debug.csv"
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "ticker",
            "old_endpoint",
            "new_endpoint",
            "use_historical",
            "retry_result",
            "n_candles",
            "error",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in transitions:
            writer.writerow(row)
    progress(f"Wrote retry debug CSV: {out_path}")
    return 0


def run_audit_fees(args: argparse.Namespace) -> int:
    """Fee-resolution audit for the frozen sample (no candles / no ROI)."""
    cache.ensure_dirs()
    client = KalshiClient(progress=progress)
    sampling_mode = args.sampling
    identity_path = (
        Path(args.frozen_sample)
        if args.frozen_sample
        else sample_identity_path(sampling_mode)
    )
    if not identity_path.exists():
        console.print(
            "[red]Frozen sample identity not found. "
            "Run --sample-preview / --freeze-sample first.[/red]"
        )
        return 2
    if not cache.usable_inventory_path().exists():
        console.print("[red]usable_inventory.jsonl missing.[/red]")
        return 2

    try:
        markets, manifest = load_frozen_sample(
            identity_path,
            cache.usable_inventory_path(),
        )
    except SampleIdentityError as error:
        console.print(f"[red]{error}[/red]")
        return 2

    console.print(format_frozen_sample_banner(manifest))
    progress(f"Fee audit against frozen sample: {len(markets):,} markets")

    report = run_fee_resolution_audit(
        client=client,
        markets=markets,
        refresh=bool(args.refresh),
        progress=progress,
    )

    console.print()
    console.rule("FEE RESOLUTION AUDIT")
    console.print(f"Series in sample/audit:     {report['series_needed']:,}")
    console.print(
        "Fee-changes lookup success: "
        f"{report['fee_changes_lookup_success']:,} / "
        f"{report['fee_changes_lookup_total']:,}"
    )
    console.print(
        f"Prior audit unresolved:     {report['prior_unresolved_count']:,}"
    )
    console.print(
        f"Prior audit unsupported:    {report['prior_unsupported_count']:,}"
    )
    console.print(f"Prior rejection_class:      {report['prior_rejection_class']}")

    console.print()
    console.rule("SERIES METADATA FAILURE CAUSES (exact)")
    console.print(
        f"Total reclassified rows: {report['series_metadata_failure_total']:,} "
        f"across {report['unique_series_in_metadata_failures']:,} series"
    )
    for name, count in sorted(
        report["series_metadata_failure_causes"].items(),
        key=lambda item: (-item[1], item[0]),
    ):
        console.print(f"  {name}: {count:,}")

    coverage = report["coverage"]
    console.print()
    console.rule("FEE COVERAGE FUNNEL")
    for bucket, count in coverage["funnel"].most_common():
        console.print(f"  {bucket}: {count:,}")
    console.print(
        f"Recoverable from prior fee failures: {coverage['recoverable_observations']:,}"
    )
    console.print(f"Still unresolved: {coverage['still_unresolved']:,}")
    console.print(f"Still unsupported: {coverage['still_unsupported']:,}")
    console.print(
        f"Unique markets resolved (incl. prior obs): "
        f"{coverage['unique_markets_resolved']:,}"
    )
    console.print(
        f"Unique markets still unresolved: "
        f"{coverage['unique_markets_unresolved']:,}"
    )

    console.print()
    console.rule("MISSING HISTORICAL FEE INFO (147-class)")
    for row in report["missing_historical_summary"]:
        console.print(
            f"  {row['series']}: n={row['n_observations']} "
            f"entry=[{row.get('entry_ts_min')} .. {row.get('entry_ts_max')}] "
            f"fee_changes={row.get('fee_change_row_count')} "
            f"earliest={row.get('earliest_known_fee_rule')} "
            f"| {row.get('why')}"
        )
    console.print(f"Wrote: {report['missing_historical_path']}")

    recon = report["sample_reconcile"]
    console.print()
    console.rule("SAMPLE ↔ HORIZON RECONCILIATION")
    console.print(f"selected_sample:        {recon['selected_sample']:,}")
    console.print(f"horizon_attempted:      {recon['horizon_attempted']:,}")
    console.print(f"pre_horizon_excluded:   {recon['pre_horizon_excluded']:,}")
    console.print(f"reconcile_ok:           {recon['reconcile_ok']}")
    console.print(f"exclusion_breakdown:    {recon['exclusion_breakdown']}")
    for m in recon.get("pre_horizon_markets") or []:
        console.print(
            f"  excluded: {m.get('ticker')} reason={m.get('failure_reason')} "
            f"http={m.get('http_status')} endpoint={m.get('chosen_endpoint')}"
        )
        if m.get("exception_message"):
            console.print(f"    {m.get('exception_message')}")

    console.print()
    console.print(f"Debug CSV:  {report['debug_path']}")
    console.print(f"Funnel CSV: {report['funnel_path']}")
    return 0


def run(args: argparse.Namespace) -> int:
    cache.ensure_dirs()

    if getattr(args, "freeze_sample", False):
        return run_freeze_sample(args)

    if getattr(args, "audit_fees", False):
        return run_audit_fees(args)

    if getattr(args, "retry_candle_failures", False):
        return run_retry_candle_failures(args)

    if args.sample_preview:
        snapshot = cache.read_json(cache.inventory_snapshot_path(), default={}) or {}
        if not _inventory_integrity_ok(snapshot):
            _print_inventory_integrity_failure(snapshot)
            return 2
        if not cache.usable_inventory_path().exists():
            console.print(
                "[red]No usable_inventory.jsonl found. "
                "Run --inventory-only first.[/red]"
            )
            return 2
        progress(
            f"Sample preview: mode={args.sampling} size={args.sample_size:,} "
            f"(loading compact index from usable inventory; zero candle calls)"
        )
        markets, sampling_diag = stratified_sample_from_jsonl(
            cache.usable_inventory_path(),
            max_markets=args.sample_size,
            seed=args.seed,
            max_markets_per_event=args.max_markets_per_event,
            max_markets_per_series_date=args.max_markets_per_series_date,
            sampling=args.sampling,
        )
        print_sampling_diagnostics(sampling_diag.as_dict())
        preview_path = cache.RESULTS_ROOT / f"sample_preview_{args.sampling}.json"
        tickers = [m.get("ticker") for m in markets]
        cache.write_json(
            preview_path,
            {
                "sampling": sampling_diag.as_dict(),
                "tickers": tickers,
            },
        )
        identity = build_sample_identity_manifest(
            markets=markets,
            sampling_mode=args.sampling,
            seed=args.seed,
            sample_size=args.sample_size,
            inventory_hash=snapshot.get("inventory_hash"),
        )
        identity_path = sample_identity_path(args.sampling)
        cache.write_json(identity_path, identity)
        progress(f"Wrote sample preview manifest: {preview_path}")
        progress(f"Wrote sample identity: {identity_path}")
        console.print(format_frozen_sample_banner(identity))
        progress("Sample-preview complete; skipping candlestick downloads.")
        return 0

    if args.smoke:
        max_markets = 50
        inventory_max_items = (
            args.inventory_max_items
            if args.inventory_max_items is not None
            else SMOKE_INVENTORY_MAX_ITEMS
        )
    elif args.inventory_only:
        max_markets = None
        inventory_max_items = args.inventory_max_items
    elif args.full:
        max_markets = None
        inventory_max_items = args.inventory_max_items
    else:
        max_markets = args.max_markets
        inventory_max_items = args.inventory_max_items

    stream_map = {
        "all": ("historical", "recent_settled"),
        "historical": ("historical",),
        "recent": ("recent_settled",),
    }
    streams = stream_map[getattr(args, "inventory_stream", "all")]
    refresh_streams = set(streams) if args.refresh else None

    client = KalshiClient(progress=progress)
    inventory = MarketInventory(
        client,
        refresh=args.refresh,
        refresh_streams=refresh_streams,
        progress=progress,
    )

    usable_inventory, audit = inventory.build(
        inventory_max_items=inventory_max_items,
        enrich_limit=None,
        streams=streams,
    )

    # Real build() already wrote usable_inventory.jsonl. Tests/fakes may not.
    if usable_inventory:
        write_usable_inventory(usable_inventory)

    historical_status = inventory.get_stream_status("historical")
    recent_status = inventory.get_stream_status("recent_settled")
    stream_diag = getattr(audit, "_stream_diagnostics", None) or {}
    diag = dict(stream_diag)
    if not stream_diag and usable_inventory:
        diag = inventory_diagnostics(usable_inventory)
    else:
        diag.setdefault("inventory_markets", audit.usable)
        diag.setdefault("inventory_date_range", "n/a")
        diag.setdefault("inventory_years", [])
        diag.setdefault(
            "inventory_categories", dict(audit.categories.most_common())
        )
        diag.setdefault("inventory_unique_series", 0)
        diag.setdefault("inventory_unique_events", 0)

    snapshot = build_inventory_snapshot(
        audit=audit,
        usable=usable_inventory,
        historical=historical_status,
        recent=recent_status,
        inventory_max_items=inventory_max_items,
        api_base_url=client.base_url,
        diagnostics=diag,
        inventory_hash=stream_diag.get("inventory_hash"),
    )
    print_inventory_audit(audit, usable_inventory, stream_diagnostics=stream_diag)
    print_recent_settled_diagnostics(inventory.recent_stream_diagnostics())
    progress(
        f"Inventory snapshot written "
        f"(complete={snapshot.get('complete_inventory')}, "
        f"usable={audit.usable:,}, "
        f"integrity={snapshot.get('inventory_integrity_valid')})"
    )

    if args.inventory_only:
        rows, future_summary, future_path = run_future_close_audit(snapshot=snapshot)
        print_future_close_audit(future_summary, rows[:20], path=future_path)
        progress("Inventory-only mode complete; skipping analysis and candlesticks.")
        if not _inventory_integrity_ok(snapshot):
            _print_inventory_integrity_failure(snapshot)
            return 2
        return 0

    if not _inventory_integrity_ok(snapshot):
        _print_inventory_integrity_failure(snapshot)
        return 2

    sampling_mode = getattr(args, "sampling", SAMPLING_BALANCED)
    identity_path = (
        Path(args.frozen_sample)
        if args.frozen_sample
        else sample_identity_path(sampling_mode)
    )
    preview_path = cache.RESULTS_ROOT / f"sample_preview_{sampling_mode}.json"
    sampling_diag = None
    used_frozen = False

    # Prefer frozen sample for non-smoke analysis when identity exists.
    if (
        not args.smoke
        and not args.full
        and identity_path.exists()
        and preview_path.exists()
        and max_markets is not None
    ):
        preview_tickers, _ = load_preview_tickers(preview_path)
        expected_hash = compute_sample_hash(preview_tickers)
        try:
            markets, manifest = load_frozen_sample(
                identity_path,
                cache.usable_inventory_path(),
                expected_hash=expected_hash,
            )
            if max_markets is not None and len(markets) != max_markets:
                raise SampleIdentityError(
                    f"Frozen sample size {len(markets)} != --max-markets {max_markets}"
                )
            console.print(format_frozen_sample_banner(manifest))
            used_frozen = True
            sampling_diag = SamplingDiagnostics(
                sampling_mode=sampling_mode,
                seed=args.seed,
                selected_markets=len(markets),
            )
        except SampleIdentityError as error:
            console.print(f"[red]Frozen sample rejected: {error}[/red]")
            return 2

    if not used_frozen:
        if not usable_inventory:
            markets, sampling_diag = stratified_sample_from_jsonl(
                cache.usable_inventory_path(),
                max_markets=max_markets,
                seed=args.seed,
                max_markets_per_event=args.max_markets_per_event,
                max_markets_per_series_date=args.max_markets_per_series_date,
                sampling=sampling_mode,
            )
        else:
            markets, sampling_diag = stratified_sample(
                usable_inventory,
                max_markets=max_markets,
                seed=args.seed,
                max_markets_per_event=args.max_markets_per_event,
                max_markets_per_series_date=args.max_markets_per_series_date,
                sampling=sampling_mode,
            )
        print_sampling_diagnostics(sampling_diag.as_dict())
        if not args.smoke:
            _write_identity_from_markets(
                markets,
                sampling_mode=sampling_mode,
                seed=args.seed,
                sample_size=len(markets),
                inventory_hash=snapshot.get("inventory_hash"),
            )

    progress(
        f"Analysis sample locked ({len(markets):,} markets). "
        "Fetching current historical cutoff..."
    )

    cutoff = fetch_current_cutoff_or_abort(inventory)

    fee_index = FeeIndex(client=client, refresh=bool(args.refresh), progress=progress)
    # Prefetch fee bundles for series in the locked sample (once per series).
    sample_series = sorted(
        {str(m.get("series_ticker") or "") for m in markets if m.get("series_ticker")}
    )
    progress(f"Prefetching fee metadata for {len(sample_series):,} series...")
    for series in sample_series:
        fee_index.get_series_bundle(series)

    observations: list[dict] = []
    run_stats: Counter = Counter()
    run_stats["successful_markets"] = 0
    run_stats["failed_markets"] = 0
    run_stats["skipped_markets"] = 0
    horizon_tracker = HorizonCoverageTracker(HORIZONS.keys())
    candle_failures: list = []
    fee_audit_rows: list[dict] = []
    pre_horizon = Counter()

    for idx, market_row in enumerate(markets, start=1):
        ticker = market_row.get("ticker")
        progress(f"[{idx}/{len(markets)}] {ticker}")

        if market_row.get("skip_reason"):
            run_stats["skipped_markets"] += 1
            run_stats[f"skip:{market_row['skip_reason']}"] += 1
            pre_horizon["skip_reason"] += 1
            continue

        result = market_row.get("result")
        if result not in ("yes", "no"):
            run_stats["skipped_markets"] += 1
            run_stats["skip:nonstandard_settlement"] += 1
            pre_horizon["nonstandard_settlement"] += 1
            continue

        use_historical = None
        start_ts = end_ts = None
        try:
            start_ts, end_ts = candle_window_for_close(market_row.get("close_time"))
            use_historical, _endpoint = candle_endpoint_for_market(market_row, cutoff)
            candles, _meta = fetch_candles_for_market(
                client,
                market_row,
                cutoff,
                refresh=args.refresh,
            )
        except (
            KalshiAPIError,
            PriceError,
            CandleSchemaError,
            MissingSettlementTsError,
        ) as error:
            record = build_failure_record(
                market_row,
                cutoff=cutoff,
                use_historical=use_historical,
                start_ts=start_ts,
                end_ts=end_ts,
                error=error,
                response_shape=getattr(error, "response_shape", None)
                if False
                else None,
            )
            if isinstance(error, CandleSchemaError):
                record.failure_reason = "schema_parse_error"
                record.response_shape = str(error)
            candle_failures.append(record)
            run_stats["failed_markets"] += 1
            run_stats[f"fail:{record.failure_reason}"] += 1
            pre_horizon["candle_fetch_failures"] += 1
            cache.log_failed_ticker(
                str(ticker),
                str(error),
                {"stage": "candles", "failure_reason": record.failure_reason},
            )
            progress(f"  candle fail [{record.failure_reason}]: {error}")
            continue

        market_ok = False
        empty = not candles
        if empty:
            candle_failures.append(
                build_failure_record(
                    market_row,
                    cutoff=cutoff,
                    use_historical=use_historical,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    failure_reason="empty_candles",
                )
            )

        for horizon in HORIZONS:
            horizon_tracker.attempt(horizon)
            if empty:
                try:
                    select_candle_for_horizon(
                        [],
                        close_time=market_row.get("close_time"),
                        horizon_key=horizon,
                        max_staleness_seconds=args.staleness_seconds,
                        open_time=market_row.get("open_time"),
                    )
                except PriceError as error:
                    horizon_tracker.reject(horizon, str(error))
                continue

            try:
                selected = select_candle_for_horizon(
                    candles,
                    close_time=market_row.get("close_time"),
                    horizon_key=horizon,
                    max_staleness_seconds=args.staleness_seconds,
                    open_time=market_row.get("open_time"),
                )
            except PriceError as error:
                reason = str(error)
                horizon_tracker.reject(horizon, reason)
                run_stats[f"skip:{reason}"] += 1
                continue

            # Actual simulated execution time = selected candle end, not nominal target.
            entry_ts = selected.end_period_ts
            try:
                schedule = fee_index.resolve_for_market(
                    series_ticker=market_row.get("series_ticker") or "",
                    entry_ts=entry_ts,
                    event_ticker=market_row.get("event_ticker"),
                    event=market_row.get("event") or None,
                )
                compute_taker_fee(
                    price=selected.yes_entry,
                    contracts=PRIMARY_CONTRACTS,
                    schedule=schedule,
                )
            except (FeeResolutionError, UnsupportedFeeError) as error:
                if is_actually_unsupported(error):
                    reject_key = "unsupported_fee"
                    run_stats["skip:unsupported_fee"] += 1
                else:
                    reject_key = "unresolved_fee"
                    run_stats["skip:unresolved_fee"] += 1
                horizon_tracker.reject(horizon, reject_key)
                fee_audit_rows.append(
                    {
                        "ticker": market_row.get("ticker"),
                        "event_ticker": market_row.get("event_ticker"),
                        "series_ticker": market_row.get("series_ticker"),
                        "category": market_row.get("category"),
                        "close_year": close_year(market_row),
                        "horizon": horizon,
                        "entry_ts": entry_ts,
                        "fee_type": None,
                        "fee_multiplier": None,
                        "fee_source": None,
                        "rejection_class": fee_rejection_class(
                            str(error),
                            code=getattr(error, "code", None),
                        ),
                        "message": str(error),
                    }
                )
                progress(f"  fee skip: {error}")
                continue

            try:
                obs = build_observation(
                    market_row=market_row,
                    horizon=horizon,
                    selected=selected,
                    result=result,
                    schedule=schedule,
                    contracts=PRIMARY_CONTRACTS,
                )
                observations.append(obs)
                horizon_tracker.usable(horizon, market_row.get("event_ticker"))

                if args.include_sensitivity:
                    for size in SENSITIVITY_CONTRACTS:
                        sens = build_observation(
                            market_row=market_row,
                            horizon=horizon,
                            selected=selected,
                            result=result,
                            schedule=schedule,
                            contracts=size,
                        )
                        observations.append(sens)
                market_ok = True
            except Exception as error:  # noqa: BLE001 — keep long runs alive
                horizon_tracker.reject(horizon, "other_rejection")
                run_stats["failed_markets"] += 1
                cache.log_failed_ticker(str(ticker), str(error), {"stage": "simulate"})
                progress(f"  sim fail: {error}")

        if empty:
            run_stats["skipped_markets"] += 1
            run_stats["skip:empty_candles"] += 1

        if market_ok:
            run_stats["successful_markets"] += 1

    exclusion_summary = pre_horizon_exclusion_summary(
        skip_reason=int(pre_horizon.get("skip_reason", 0)),
        nonstandard_settlement=int(pre_horizon.get("nonstandard_settlement", 0)),
        candle_fetch_failures=int(pre_horizon.get("candle_fetch_failures", 0)),
    )
    horizon_rows_preview = horizon_tracker.as_rows()
    attempted = (
        int(horizon_rows_preview[0]["markets_attempted"]) if horizon_rows_preview else 0
    )
    sample_ok = reconcile_sample_to_horizon(
        selected_sample=len(markets),
        horizon_attempted=attempted,
        pre_horizon_excluded=exclusion_summary["pre_horizon_excluded"],
    )
    console.print()
    console.rule("SAMPLE ↔ HORIZON RECONCILIATION")
    console.print(f"selected_sample:      {len(markets):,}")
    console.print(f"horizon_attempted:    {attempted:,}")
    console.print(
        f"pre_horizon_excluded: {exclusion_summary['pre_horizon_excluded']:,} "
        f"({exclusion_summary})"
    )
    console.print(f"reconcile_ok:         {sample_ok}")

    if candle_failures:
        path = write_candle_failure_audit(candle_failures)
        console.print(format_failure_summaries(summarize_failures(candle_failures)))
        progress(f"Wrote candle failure audit: {path}")

    if fee_audit_rows:
        fee_path = write_unsupported_fee_audit(fee_audit_rows)
        unresolved_rows, unsupported_rows = partition_fee_summaries(fee_audit_rows)
        console.print()
        console.rule("UNRESOLVED FEE SUMMARY")
        fee_sum = summarize_unsupported_fees(unresolved_rows)
        for key, counter in fee_sum.items():
            console.print(f"by {key}:")
            for name, count in counter.most_common(12):
                console.print(f"  {name}: {count}")
        console.print()
        console.rule("ACTUALLY UNSUPPORTED FEE SUMMARY")
        unsup_sum = summarize_unsupported_fees(unsupported_rows)
        for key, counter in unsup_sum.items():
            console.print(f"by {key}:")
            for name, count in counter.most_common(12):
                console.print(f"  {name}: {count}")
        progress(f"Wrote unsupported fee audit: {fee_path}")

    primary_obs = [o for o in observations if int(o.get("contracts", 1)) == 1]
    horizon_obs = [o for o in primary_obs if o.get("horizon") == args.horizon]
    report_horizon = args.horizon
    if not horizon_obs and primary_obs:
        counts = Counter(o.get("horizon") for o in primary_obs)
        report_horizon = counts.most_common(1)[0][0]
        horizon_obs = [o for o in primary_obs if o.get("horizon") == report_horizon]
        progress(
            f"No observations for --horizon {args.horizon}; "
            f"reporting {report_horizon} instead ({len(horizon_obs)} obs)"
        )

    liquidity_meta = annotate_liquidity_groups(horizon_obs)
    executable_buckets = aggregate_price_buckets(horizon_obs, side="YES")
    midquote_buckets = aggregate_midquote_calibration(horizon_obs, side="YES")
    tight_cals = tight_spread_midquote_calibrations(horizon_obs, side="YES")
    fl = favorite_longshot_report(horizon_obs)
    h1_report = summarize_h1(horizon_obs)
    cat_rows = category_summary(horizon_obs, side="YES")
    bounds = choose_period_bounds(horizon_obs)
    for obs in primary_obs:
        obs["period"] = period_name(obs, bounds)

    period_rows = period_summaries(horizon_obs, bounds, side="YES")
    year_rows = yearly_summary(horizon_obs, side="YES")
    min_obs = 1 if args.smoke else args.min_observations
    strategy_rows = rank_simple_strategies(
        horizon_obs,
        min_observations=min_obs,
        side="YES",
    )
    spread_rows = spread_filtered_profitability(horizon_obs, side="YES")
    overall = summarize_side(horizon_obs, side="YES")
    extreme_rows = extreme_quote_rows(horizon_obs)
    horizon_rows = horizon_tracker.as_rows()
    lifespan_rows = lifespan_report(markets)

    selected_diag = sample_population_diagnostics(markets)
    usable_diag = observation_population_diagnostics(horizon_obs)

    dataset = {
        "settled_found": audit.combined_unique,
        "usable_markets": audit.usable,
        "sample_markets": len(markets),
        "selected_sample": selected_diag,
        "final_usable_observations": usable_diag,
        "unique_events": usable_diag["events"],
        "n_categories": usable_diag["categories"],
        "observations": len(horizon_obs),
        "date_range": usable_diag["date_range"],
        "period_bounds": bounds,
        "report_horizon": report_horizon,
        "all_horizon_observations": len(primary_obs),
        "overall_summary": overall,
        "sampling": sampling_diag.as_dict() if sampling_diag else {},
        "min_observations": min_obs,
    }

    out_dir = save_results(
        observations=observations,
        price_bucket_summary=executable_buckets,
        midquote_calibration=midquote_buckets,
        category_summary_rows=cat_rows,
        yearly_summary_rows=year_rows,
        strategy_summary_rows=strategy_rows,
        extreme_quote_rows=extreme_rows,
        horizon_coverage_rows=horizon_rows,
    )

    print_efficiency_report(
        dataset=dataset,
        executable_bucket_rows=executable_buckets,
        midquote_bucket_rows=midquote_buckets,
        tight_spread_calibrations=tight_cals,
        favorite_longshot=fl,
        h1_report=h1_report,
        category_rows=cat_rows,
        period_rows=period_rows,
        period_bounds=bounds,
        strategy_rows=strategy_rows,
        spread_profit_rows=spread_rows,
        liquidity_meta=liquidity_meta,
        run_stats=dict(run_stats),
        horizon_coverage=horizon_rows,
        lifespan_rows=lifespan_rows,
        extreme_rows=extreme_rows,
    )
    console.print(f"\nWrote results to {out_dir}")
    console.print(
        "\n[bold]Historical profitability does not prove future profitability.[/bold]"
    )
    console.print(
        "A price-calibration bias is not necessarily tradable after spread and fees."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        console.print("\nInterrupted.")
        return 130
    except SystemExit as exit_exc:
        code = exit_exc.code
        return int(code) if isinstance(code, int) else 1


if __name__ == "__main__":
    sys.exit(main())

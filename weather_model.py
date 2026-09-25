"""
External weather probability model CLI for KXHIGHNY.

Separates weather forecasting from trading evaluation.

Commands:
  python weather_model.py --build-calibration
  python weather_model.py --backtest
  python weather_model.py --live
  python weather_model.py --phase2-clinyc

Phase 1 does NOT place orders and does NOT optimize against trading ROI.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from rich.console import Console

from kalshi.client import (
    KalshiClient,
    get_historical_markets_for_series,
)
from research.weather.calibration import (
    build_calibration_rows,
    ensure_weather_cache_dirs,
    load_calibration_csv,
    write_calibration_csv,
)
from research.weather.models import (
    DECISION_NO_BET,
    DECISION_RESEARCH_ONLY,
    MIN_REQUIRED_EDGE,
    SERIES_TICKER,
    WeatherTradeEvaluation,
)
from research.weather.replay import example_historical_prediction, run_full_backtest
from research.weather.reporting import print_backtest_report, print_live_report

console = Console()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KXHIGHNY external weather probability research CLI",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--build-calibration",
        action="store_true",
        help="Build auditable GFS residual calibration CSV",
    )
    group.add_argument(
        "--backtest",
        action="store_true",
        help="Walk-forward forecast-quality evaluation (no trading ROI)",
    )
    group.add_argument(
        "--live",
        action="store_true",
        help="Live external evidence report for open KXHIGHNY events",
    )
    group.add_argument(
        "--phase2-clinyc",
        action="store_true",
        help=(
            "Phase 2: recover CLINYC settlement temps, regime catalog, "
            "and KNYC↔CLINYC pairs (no trading ROI)"
        ),
    )
    group.add_argument(
        "--phase3-transfer",
        action="store_true",
        help=(
            "Phase 3: provenance audit, identity-transfer assessment, "
            "direct CLINYC eligibility / walk-forward (no trading ROI)"
        ),
    )
    parser.add_argument(
        "--example-json",
        action="store_true",
        help="With --backtest, also print an example WeatherPrediction JSON",
    )
    return parser.parse_args(argv)


def cmd_build_calibration() -> int:
    ensure_weather_cache_dirs()
    console.print("[bold]Building GFS residual calibration dataset[/bold]")
    console.print("residual_f = actual_high_f - forecast_high_f")

    client = KalshiClient(
        progress=lambda message: console.print(message, style="dim"),
    )
    markets = get_historical_markets_for_series(SERIES_TICKER, client=client)
    console.print(f"Historical markets fetched: {len(markets)}")

    rows = build_calibration_rows(
        markets,
        progress=lambda m: console.print(m, style="dim"),
    )
    path = write_calibration_csv(rows)
    usable = sum(1 for r in rows if r.residual_f is not None)
    console.print(f"Wrote {len(rows)} rows ({usable} with residuals) -> {path}")
    regimes = {}
    for row in rows:
        regimes[row.settlement_source_regime] = regimes.get(row.settlement_source_regime, 0) + 1
    console.print(f"Settlement regimes in rows: {regimes}")
    return 0


def cmd_backtest(*, example_json: bool = False) -> int:
    ensure_weather_cache_dirs()
    rows = load_calibration_csv()
    if not rows:
        console.print(
            "[yellow]No calibration CSV found. Building it first...[/yellow]"
        )
        cmd_build_calibration()
        rows = load_calibration_csv()

    client = KalshiClient(
        progress=lambda message: console.print(message, style="dim"),
    )
    markets = get_historical_markets_for_series(SERIES_TICKER, client=client)
    report = run_full_backtest(
        markets,
        rows,
        progress=lambda m: console.print(m, style="dim"),
    )
    results_path = ensure_write_report(report)
    print_backtest_report(report, console=console)

    if example_json:
        example = example_historical_prediction(markets, rows)
        console.print("\n[bold]Example historical WeatherPrediction JSON[/bold]")
        console.print(json.dumps(example, indent=2, default=str))

    console.print(f"\nWrote report JSON: {results_path}")
    return 0


def ensure_write_report(report: dict[str, Any]):
    from kalshi import cache

    cache.ensure_dirs()
    path = cache.RESULTS_ROOT / "weather_model_backtest.json"
    cache.write_json(path, report)
    return path


def cmd_live() -> int:
    ensure_weather_cache_dirs()
    from research.weather.service import get_live_weather_events, serialize_event_bundle

    rows = load_calibration_csv()
    if not rows:
        console.print(
            "[yellow]Calibration CSV missing — live calibrated probs need "
            "--build-calibration first. Showing uncalibrated live evidence.[/yellow]"
        )

    payload = get_live_weather_events()
    events = payload.get("events") or []
    if not events:
        console.print("[yellow]No open range-bucket KXHIGHNY events.[/yellow]")
        return 0

    bundle = serialize_event_bundle(events[0])
    resolution = events[0]["resolution"]
    prediction = events[0]["prediction"]
    knyc = (events[0].get("evidence") or {}).get("observations", {}).get("knyc")
    markets_quotes = events[0].get("kalshi_markets") or []

    report_markets = [
        {
            "yes_sub_title": m.get("label"),
            "yes_bid_dollars": m.get("yes_bid_dollars"),
            "yes_ask_dollars": m.get("yes_ask_dollars"),
        }
        for m in markets_quotes
    ]

    print_live_report(
        resolution=resolution,
        prediction=prediction,
        knyc_summary=knyc,
        gfs_point=prediction.point_forecast_high,
        markets=report_markets,
        console=console,
    )

    evaluation = WeatherTradeEvaluation(
        prediction=prediction,
        decision=DECISION_RESEARCH_ONLY,
        notes=[
            "Phase 1: RESEARCH_ONLY — no bet sizing / ROI optimization",
            f"Equivalent decision under research gate: {DECISION_NO_BET}",
            f"MIN_REQUIRED_EDGE={MIN_REQUIRED_EDGE} (unvalidated)",
        ],
    )
    console.print(
        f"\n[dim]WeatherTradeEvaluation.decision = {evaluation.decision}[/dim]"
    )
    console.print(
        f"[dim]prediction_status = {prediction.prediction_status}[/dim]"
    )
    console.print("\n[bold]Example live WeatherPrediction JSON[/bold]")
    console.print(json.dumps(prediction.to_dict(), indent=2, default=str)[:4000])
    _ = bundle
    return 0


def cmd_phase2_clinyc() -> int:
    from research.weather.phase2 import run_phase2_research

    console.print("[bold]Phase 2 CLINYC settlement research[/bold]")
    console.print("Does NOT refresh full Kalshi inventory; series-scoped only.")
    report = run_phase2_research(
        progress=lambda m: console.print(m, style="dim"),
    )
    pairs = report.get("pairs") or {}
    regimes = report.get("regimes") or {}
    audit = report.get("clinyc_payload_audit") or {}
    console.print(
        f"exact_numeric_outcome_available_via_api="
        f"{audit.get('exact_numeric_outcome_available_via_api')}"
    )
    console.print(
        f"earliest TWC={regimes.get('earliest_verified_twc_event')}  "
        f"latest NWS={regimes.get('latest_verified_nws_event')}"
    )
    console.print(
        f"paired N={pairs.get('N')}  mean_diff={pairs.get('mean_difference')}  "
        f"same_bucket_pct={pairs.get('same_kalshi_bucket_pct')}  "
        f"dataset_exists={pairs.get('direct_clinyc_dataset_exists')}  "
        f"operationally_eligible={pairs.get('direct_clinyc_operationally_eligible')}"
    )
    return 0


def cmd_phase3_transfer() -> int:
    from research.weather.phase3 import run_phase3_research

    console.print("[bold]Phase 3 settlement target-transfer research[/bold]")
    console.print(
        "Does NOT set settlement_source_transfer_validated from development pairs."
    )
    console.print("Does NOT lower Phase 1 thresholds. Does NOT add ROI.")
    report = run_phase3_research(
        progress=lambda m: console.print(m, style="dim"),
    )
    if report.get("status") == "STOP":
        console.print(f"[red]STOP: {report.get('reason')}[/red]")
        return 2
    transfer = report.get("settlement_transfer") or {}
    eligibility = report.get("direct_clinyc_eligibility") or {}
    oos = report.get("direct_clinyc_oos") or {}
    console.print(f"transfer_status={transfer.get('transfer_status')}")
    console.print(
        f"transfer_validated={transfer.get('transfer_validated')} "
        f"(must remain false until prospective rule satisfied)"
    )
    console.print(
        f"development_n={transfer.get('development_n')}  "
        f"prospective_n={transfer.get('prospective_n')}/"
        f"{transfer.get('prospective_target_n')}"
    )
    console.print(
        f"mismatch observed={transfer.get('integer_mismatch_rate_observed')}  "
        f"95% upper={transfer.get('integer_mismatch_95_upper')}"
    )
    console.print(
        f"direct_clinyc dataset_exists="
        f"{eligibility.get('direct_clinyc_dataset_exists')}  "
        f"operationally_eligible="
        f"{eligibility.get('direct_clinyc_operationally_eligible')}"
    )
    console.print(
        f"direct CLINYC OOS N={oos.get('N')}  reason={oos.get('reason')}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.build_calibration:
        return cmd_build_calibration()
    if args.backtest:
        return cmd_backtest(example_json=args.example_json)
    if args.live:
        return cmd_live()
    if args.phase2_clinyc:
        return cmd_phase2_clinyc()
    if args.phase3_transfer:
        return cmd_phase3_transfer()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

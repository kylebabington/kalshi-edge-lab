"""Console reporting for the external weather model (no trading ROI)."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from research.weather.models import (
    GFS_PUBLICATION_LATENCY,
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_RUN_HISTORY,
    WeatherPrediction,
)


def print_backtest_report(report: dict[str, Any], console: Console | None = None) -> None:
    console = console or Console(force_terminal=False)
    ds = report.get("dataset", {})
    meth = report.get("methodology", {})

    console.print(
        Panel.fit(
            "[bold]KALSHI EDGE LAB - EXTERNAL WEATHER MODEL[/bold]\n"
            "Forecast quality only - no trading ROI",
            border_style="cyan",
        )
    )

    console.print("\n[bold]DATASET[/bold]")
    console.print(f"  historical markets: {ds.get('historical_markets')}")
    console.print(f"  usable range-bucket events: {ds.get('usable_range_bucket_events')}")
    console.print(f"  NWS CLI calibration events: {ds.get('nws_cli_calibration_events')}")
    console.print(f"  date range: {ds.get('date_min')} -> {ds.get('date_max')}")
    console.print(f"  residual convention: {report.get('residual_convention')}")
    console.print(f"  calibration_target_regime: {report.get('calibration_target_regime')}")
    console.print(f"  periods: {ds.get('periods')}")

    console.print("\n[bold]METHODOLOGY CONSTANTS[/bold]")
    console.print(f"  GFS_PUBLICATION_LATENCY: {GFS_PUBLICATION_LATENCY}")
    console.print(f"  MIN_RUN_HISTORY (burn-in): {MIN_RUN_HISTORY}")
    console.print(f"  MIN_N_MONTH: {MIN_N_MONTH}")
    console.print(f"  MIN_N_SEASON: {MIN_N_SEASON}")
    console.print(f"  MIN_N_RUN: {MIN_N_RUN}")
    console.print(f"  hierarchy: {meth.get('hierarchy')}")
    console.print(f"  run selection policy: {meth.get('run_selection_policy')}")
    console.print(f"  scoring: {meth.get('scoring')}")

    console.print("\n[bold]FORECAST ERROR / CALIBRATION QUALITY BY RUN[/bold]")
    console.print("  (Independent reports - do NOT pick the best run as OOS.)")

    for run_id, block in (report.get("by_run") or {}).items():
        console.print(f"\n  [cyan]{block.get('run_label')}[/cyan] ({run_id})")
        err = block.get("error_metrics") or {}
        console.print(
            f"    residuals N={err.get('N')}  MAE={_fmt(err.get('MAE'))}  "
            f"RMSE={_fmt(err.get('RMSE'))}  bias={_fmt(err.get('bias'))}"
        )
        console.print(
            f"    burn-in={block.get('burn_in_count')}  "
            f"OOS calibrated={block.get('oos_calibrated_count')}  "
            f"insufficient={block.get('insufficient_history')}"
        )
        console.print(
            f"    mean event Brier={_fmt(block.get('mean_event_brier'))}  "
            f"mean log loss={_fmt(block.get('mean_event_log_loss'))}"
        )
        console.print(
            f"    top-bucket accuracy={_fmt(block.get('top_bucket_accuracy'))}  "
            f"mean P(winner)={_fmt(block.get('mean_probability_on_winner'))}"
        )
        console.print(
            f"    raw GFS top-bucket accuracy={_fmt(block.get('raw_gfs_top_bucket_accuracy'))}"
        )
        console.print(
            f"    OOS-period N={block.get('oos_period_n')}  "
            f"Brier={_fmt(block.get('oos_period_mean_brier'))}  "
            f"log loss={_fmt(block.get('oos_period_mean_log_loss'))}"
        )

        bins = block.get("reliability_bins") or []
        if bins:
            table = Table(
                title=f"Calibration bins - {block.get('run_label')}",
                show_header=True,
            )
            table.add_column("Predicted")
            table.add_column("N", justify="right")
            table.add_column("Avg p", justify="right")
            table.add_column("Observed", justify="right")
            for row in bins:
                table.add_row(
                    str(row.get("bin")),
                    str(row.get("n")),
                    _fmt(row.get("avg_predicted")),
                    _fmt(row.get("observed_frequency")),
                )
            console.print(table)

    console.print("\n[bold]WARNINGS[/bold]")
    for w in report.get("warnings") or []:
        console.print(f"  [yellow]- {w}[/yellow]")


def print_live_report(
    *,
    resolution: Any,
    prediction: WeatherPrediction,
    knyc_summary: dict | None,
    gfs_point: float | None,
    markets: list[dict],
    console: Console | None = None,
) -> None:
    console = console or Console()

    mismatch = (
        prediction.calibration_target_regime != prediction.live_target_regime
        and not prediction.settlement_source_transfer_validated
    )

    console.print(
        Panel.fit(
            "[bold]KALSHI EDGE LAB - LIVE EXTERNAL WEATHER EVIDENCE[/bold]",
            border_style="cyan",
        )
    )

    if mismatch:
        console.print(
            Panel(
                "[bold red]EXPERIMENTAL - SETTLEMENT SOURCE MISMATCH[/bold red]\n"
                f"calibration_target_regime={prediction.calibration_target_regime}\n"
                f"live_target_regime={prediction.live_target_regime}\n"
                "NWS/KNYC residuals are NOT validated for Weather Company/CLINYC settlement.\n"
                "Displayed probabilities are meteorological estimates only.\n"
                "WeatherTradeEvaluation: RESEARCH_ONLY / NO_BET",
                border_style="red",
            )
        )

    console.print("\n[bold]EVENT[/bold]")
    console.print(f"  event: {prediction.event_ticker}")
    console.print(f"  date: {prediction.target_date}")
    console.print(f"  resolution: {resolution.resolution_source}")
    console.print(f"  station: {resolution.station_id}")
    console.print(f"  regime: {resolution.settlement_source_regime}")
    console.print(f"  certainty: {resolution.resolution_certainty}")

    console.print("\n[bold]EXTERNAL WEATHER EVIDENCE[/bold]")
    console.print(f"  GFS point forecast high: {_fmt(gfs_point)}°F")
    console.print(
        f"  GEFS: n={prediction.gefs_member_count}  "
        f"mean={_fmt(prediction.gefs_mean)}  median={_fmt(prediction.gefs_median)}  "
        f"std={_fmt(prediction.gefs_std)}  "
        f"min={_fmt(prediction.gefs_min)}  max={_fmt(prediction.gefs_max)}"
    )

    if knyc_summary:
        console.print("\n  [bold]KNYC OBSERVATIONS[/bold] (live only; not in historical replay)")
        console.print(
            f"    latest temp: {knyc_summary.get('latest_temperature')}"
        )
        console.print(
            f"    high so far: {knyc_summary.get('high_temperature')}"
        )
        console.print(
            f"    latest observation time: {knyc_summary.get('latest_time')}"
        )

    console.print("\n[bold]CALIBRATED FORECAST[/bold]")
    console.print(f"  status: {prediction.prediction_status}")
    console.print(f"  run: {prediction.forecast_run}")
    console.print(f"  expected final high: {_fmt(prediction.expected_high)}")
    console.print(
        f"  interval p10-p90: {_fmt(prediction.p10_high)} - {_fmt(prediction.p90_high)}"
    )
    console.print(f"  residual sample size: {prediction.residual_sample_size}")

    console.print("\n[bold]KALSHI BUCKET PROBABILITIES[/bold] (external model)")
    for label, p in prediction.bucket_probabilities.items():
        console.print(f"  {label:20s} {p * 100:5.1f}%")

    console.print(f"\n[bold]CONFIDENCE[/bold]: {prediction.confidence_label} "
                  f"({prediction.confidence_score:.2f})")
    console.print(f"  model agreement: {prediction.model_agreement}")
    console.print(f"  calibration quality: {prediction.calibration_quality}")
    for reason in (prediction.provenance or {}).get("confidence_reasons") or []:
        console.print(f"  - {reason}")

    if prediction.warnings:
        console.print("\n[bold]WARNINGS[/bold]")
        for w in prediction.warnings:
            console.print(f"  [yellow]- {w}[/yellow]")

    console.print("\n" + "=" * 60)
    console.print("[bold dim]SEPARATE SECTION - KALSHI MARKET PRICES[/bold dim]")
    console.print("[dim](Not used as weather-model features)[/dim]")
    console.print("=" * 60)
    for market in markets:
        label = market.get("yes_sub_title") or market.get("title")
        bid = market.get("yes_bid_dollars") or market.get("yes_bid")
        ask = market.get("yes_ask_dollars") or market.get("yes_ask")
        console.print(f"  {label}: bid={bid} ask={ask}")


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)

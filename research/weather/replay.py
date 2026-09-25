"""Walk-forward weather-model evaluation (forecast quality only — no trading ROI).

Scoring:
  MULTICLASS BRIER PER EVENT:
      sum((p_bucket - actual_indicator)^2 for all event buckets)
  LOG LOSS:
      -log(probability assigned to winning bucket)
  Reliability:
      pooled predicted bucket probabilities vs observed bucket frequency

Report each standardized GFS run independently.
Do NOT select the best run from full-sample results and call that OOS.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from research.weather.calibration import (
    lead_hours,
    metrics_by_run,
    model_run_init_utc,
    nws_cli_rows,
    parse_float,
    select_usable_events,
)
from research.weather.models import (
    MIN_RUN_HISTORY,
    PREDICTION_STATUS_OK,
    REGIME_NWS_CLI_KNYC,
    STANDARDIZED_RUNS,
)
from research.weather.probability import predict_from_calibration
from research.weather.resolution import (
    get_outcome_label,
    parse_temperature_bucket,
)


def multiclass_brier(probs: dict[str, float], winner_label: str) -> float:
    total = 0.0
    for label, p in probs.items():
        y = 1.0 if label == winner_label else 0.0
        total += (p - y) ** 2
    return total


def log_loss(probs: dict[str, float], winner_label: str, *, eps: float = 1e-15) -> float:
    p = max(eps, min(1.0 - eps, float(probs.get(winner_label, 0.0))))
    return -math.log(p)


def reliability_bins(
    predictions: Sequence[tuple[float, int]],
    *,
    n_bins: int = 10,
) -> list[dict[str, Any]]:
    """Pooled (predicted_prob, outcome_indicator) pairs into decile bins."""
    bins: list[dict[str, Any]] = []
    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        bucket = [
            (p, y)
            for p, y in predictions
            if (p >= lo if i == 0 else p > lo) and p <= hi + 1e-12
        ]
        # Fix boundary: use [lo, hi) except last bin inclusive.
        if i < n_bins - 1:
            bucket = [(p, y) for p, y in predictions if lo <= p < hi]
        else:
            bucket = [(p, y) for p, y in predictions if lo <= p <= 1.0]

        if not bucket:
            bins.append(
                {
                    "bin": f"{int(lo*100)}-{int(hi*100)}%",
                    "n": 0,
                    "avg_predicted": None,
                    "observed_frequency": None,
                }
            )
            continue
        avg_p = sum(p for p, _ in bucket) / len(bucket)
        freq = sum(y for _, y in bucket) / len(bucket)
        bins.append(
            {
                "bin": f"{int(lo*100)}-{int(hi*100)}%",
                "n": len(bucket),
                "avg_predicted": avg_p,
                "observed_frequency": freq,
            }
        )
    return bins


def chronological_periods(dates: list[str]) -> dict[str, tuple[str, str] | None]:
    """Split unique sorted dates into train / validation / OOS (~40/30/30)."""
    unique = sorted(set(dates))
    if len(unique) < 6:
        return {
            "train": (unique[0], unique[-1]) if unique else None,
            "validation": None,
            "oos": None,
            "note": "insufficient dates for 3-way split; walk-forward is primary",
        }  # type: ignore[dict-item]

    n = len(unique)
    i1 = max(1, int(n * 0.40))
    i2 = max(i1 + 1, int(n * 0.70))
    return {
        "train": (unique[0], unique[i1 - 1]),
        "validation": (unique[i1], unique[i2 - 1]),
        "oos": (unique[i2], unique[-1]),
    }


def raw_gfs_bucket_label(forecast: float, markets: list[dict]) -> str | None:
    for market in markets:
        bucket = parse_temperature_bucket(market)
        if bucket and bucket.contains(forecast):
            return bucket.label
    return None


def evaluate_run_walk_forward(
    *,
    run_id: str,
    run_label: str,
    usable_events: list[dict[str, Any]],
    calibration_rows: list[dict[str, Any]],
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Walk-forward evaluation for ONE standardized run (independent report)."""
    log = progress or (lambda _m: None)
    rows_for_regime = nws_cli_rows(calibration_rows)
    run_rows = [r for r in rows_for_regime if r.get("run_id") == run_id]

    event_scores: list[dict[str, Any]] = []
    reliability_pairs: list[tuple[float, int]] = []
    burn_in_count = 0
    missing_forecast = 0
    insufficient = 0
    oos_count = 0

    top_correct = 0
    top_total = 0
    winner_prob_sum = 0.0
    raw_correct = 0
    raw_total = 0

    for event in usable_events:
        if event["resolution"].settlement_source_regime != REGIME_NWS_CLI_KNYC:
            continue

        target_date = event["target_date"]
        event_ticker = event["event_ticker"]
        markets = event["markets"]
        winner = event["winner"]
        winner_label = get_outcome_label(winner)

        # Forecast for this event/run from calibration table.
        match = next(
            (
                r
                for r in run_rows
                if r.get("event_ticker") == event_ticker and r.get("target_date") == target_date
            ),
            None,
        )
        forecast = parse_float(match.get("forecast_high_f")) if match else None
        if forecast is None:
            missing_forecast += 1
            continue

        run_cfg = next(r for r in STANDARDIZED_RUNS if r["run_id"] == run_id)
        init = model_run_init_utc(
            target_date, int(run_cfg["run_date_offset"]), int(run_cfg["run_hour"])
        )
        as_of = (init.replace(tzinfo=timezone.utc)).isoformat()

        prediction = predict_from_calibration(
            event_ticker=event_ticker,
            target_date=target_date,
            as_of=as_of,
            run_id=run_id,
            run_label=run_label,
            forecast_high=forecast,
            markets=markets,
            calibration_rows=calibration_rows,
            calibration_target_regime=REGIME_NWS_CLI_KNYC,
            live_target_regime=REGIME_NWS_CLI_KNYC,
            settlement_source_transfer_validated=True,
            resolution_certainty=event["resolution"].resolution_certainty,
            lead_hours=lead_hours(target_date, init),
        )

        # Raw point GFS baseline (always, including burn-in).
        raw_label = raw_gfs_bucket_label(forecast, markets)
        if raw_label is not None:
            raw_total += 1
            if raw_label == winner_label:
                raw_correct += 1

        if prediction.prediction_status == "BURN_IN":
            burn_in_count += 1
            continue
        if prediction.prediction_status == "INSUFFICIENT_HISTORY":
            insufficient += 1
            continue
        if prediction.prediction_status != PREDICTION_STATUS_OK:
            continue

        oos_count += 1
        probs = prediction.bucket_probabilities
        brier = multiclass_brier(probs, winner_label)
        ll = log_loss(probs, winner_label)
        top_label = max(probs, key=probs.get) if probs else None
        top_total += 1
        if top_label == winner_label:
            top_correct += 1
        winner_prob_sum += float(probs.get(winner_label, 0.0))

        for label, p in probs.items():
            reliability_pairs.append((p, 1 if label == winner_label else 0))

        event_scores.append(
            {
                "event_ticker": event_ticker,
                "target_date": target_date,
                "brier": brier,
                "log_loss": ll,
                "winner_label": winner_label,
                "p_winner": probs.get(winner_label, 0.0),
                "top_label": top_label,
                "residual_sample_size": prediction.residual_sample_size,
                "calibration_quality": prediction.calibration_quality,
            }
        )

    mean_brier = (
        sum(e["brier"] for e in event_scores) / len(event_scores) if event_scores else None
    )
    mean_ll = (
        sum(e["log_loss"] for e in event_scores) / len(event_scores) if event_scores else None
    )

    dates = [e["target_date"] for e in usable_events]
    periods = chronological_periods(dates)

    # OOS-period subset scores (still walk-forward calibrated; period is reporting only).
    oos_bounds = periods.get("oos")
    oos_scores = event_scores
    if isinstance(oos_bounds, tuple):
        oos_scores = [
            e
            for e in event_scores
            if oos_bounds[0] <= e["target_date"] <= oos_bounds[1]
        ]

    oos_mean_brier = (
        sum(e["brier"] for e in oos_scores) / len(oos_scores) if oos_scores else None
    )
    oos_mean_ll = (
        sum(e["log_loss"] for e in oos_scores) / len(oos_scores) if oos_scores else None
    )

    log(f"{run_label}: OOS calibrated events={oos_count}, burn-in={burn_in_count}")

    return {
        "run_id": run_id,
        "run_label": run_label,
        "usable_events": len(usable_events),
        "burn_in_count": burn_in_count,
        "missing_forecast": missing_forecast,
        "insufficient_history": insufficient,
        "oos_calibrated_count": oos_count,
        "min_run_history": MIN_RUN_HISTORY,
        "mean_event_brier": mean_brier,
        "mean_event_log_loss": mean_ll,
        "top_bucket_accuracy": (top_correct / top_total) if top_total else None,
        "mean_probability_on_winner": (winner_prob_sum / top_total) if top_total else None,
        "raw_gfs_top_bucket_accuracy": (raw_correct / raw_total) if raw_total else None,
        "reliability_bins": reliability_bins(reliability_pairs),
        "periods": periods,
        "oos_period_mean_brier": oos_mean_brier,
        "oos_period_mean_log_loss": oos_mean_ll,
        "oos_period_n": len(oos_scores),
        "example_prediction": event_scores[0] if event_scores else None,
        "error_metrics": metrics_by_run(run_rows).get(run_id),
    }


def run_full_backtest(
    markets: list[dict],
    calibration_rows: list[dict[str, Any]],
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    usable = select_usable_events(markets)
    nws_events = [
        e for e in usable if e["resolution"].settlement_source_regime == REGIME_NWS_CLI_KNYC
    ]
    dates = [e["target_date"] for e in nws_events]
    per_run = {}
    for run in STANDARDIZED_RUNS:
        per_run[str(run["run_id"])] = evaluate_run_walk_forward(
            run_id=str(run["run_id"]),
            run_label=str(run["label"]),
            usable_events=nws_events,
            calibration_rows=calibration_rows,
            progress=progress,
        )

    return {
        "series_ticker": "KXHIGHNY",
        "residual_convention": "residual_f = actual_high_f - forecast_high_f",
        "calibration_target_regime": REGIME_NWS_CLI_KNYC,
        "dataset": {
            "historical_markets": len(markets),
            "usable_range_bucket_events": len(usable),
            "nws_cli_calibration_events": len(nws_events),
            "date_min": min(dates) if dates else None,
            "date_max": max(dates) if dates else None,
            "periods": chronological_periods(dates),
        },
        "methodology": {
            "walk_forward": True,
            "MIN_RUN_HISTORY": MIN_RUN_HISTORY,
            "hierarchy": [
                "month + same run",
                "season + same run",
                "same-run global",
                "insufficient_history (no cross-run fallback)",
            ],
            "run_selection_policy": (
                "operational: latest exact GFS run with "
                "run_init + GFS_PUBLICATION_LATENCY <= prediction_as_of; "
                "evaluation reports each standardized run independently"
            ),
            "scoring": {
                "brier": "sum((p_b - y_b)^2) over event buckets; report mean event Brier",
                "log_loss": "-log(p_winner); report mean event log loss",
                "reliability": "pooled predicted bucket probs vs observed frequency",
            },
        },
        "by_run": per_run,
        "warnings": [
            "Do not select best GFS run from full-sample scores for an OOS claim.",
            "GEFS historical ensemble generally unavailable; raw GEFS baseline omitted in historical replay.",
            "Calibration residuals are NWS CLI KNYC only.",
        ],
    }


def example_historical_prediction(
    markets: list[dict],
    calibration_rows: list[dict[str, Any]],
    *,
    run_id: str = "day_00z",
) -> dict[str, Any] | None:
    usable = [
        e
        for e in select_usable_events(markets)
        if e["resolution"].settlement_source_regime == REGIME_NWS_CLI_KNYC
    ]
    if not usable:
        return None
    # Prefer a mid/late event so burn-in has elapsed.
    event = usable[min(len(usable) - 1, max(MIN_RUN_HISTORY, len(usable) // 2))]
    run = next(r for r in STANDARDIZED_RUNS if r["run_id"] == run_id)
    match = next(
        (
            r
            for r in calibration_rows
            if r.get("event_ticker") == event["event_ticker"] and r.get("run_id") == run_id
        ),
        None,
    )
    forecast = parse_float(match.get("forecast_high_f")) if match else None
    init = model_run_init_utc(
        event["target_date"], int(run["run_date_offset"]), int(run["run_hour"])
    )
    pred = predict_from_calibration(
        event_ticker=event["event_ticker"],
        target_date=event["target_date"],
        as_of=datetime.now(timezone.utc).isoformat(),
        run_id=run_id,
        run_label=str(run["label"]),
        forecast_high=forecast,
        markets=event["markets"],
        calibration_rows=calibration_rows,
        calibration_target_regime=REGIME_NWS_CLI_KNYC,
        live_target_regime=REGIME_NWS_CLI_KNYC,
        settlement_source_transfer_validated=True,
        resolution_certainty=event["resolution"].resolution_certainty,
        lead_hours=lead_hours(event["target_date"], init),
    )
    return pred.to_dict()

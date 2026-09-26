"""Phase 5: operational GFS/HRRR replay, shadow eval, prospective automation helpers.

Incumbent WeatherPrediction remains calibrated GFS (MODEL_COMBINATION_POLICY=none).
Shadow challengers are research-only.
"""

from __future__ import annotations

import csv
import math
import random
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from historical_weather import get_gfs_run_high
from kalshi import cache
from research.weather.calibration import (
    CALIBRATION_DIR,
    MIN_SINGLE_RUN_DATE,
    compute_residual,
    error_metrics,
    lead_hours,
    load_calibration_csv,
    model_available_as_of,
    model_run_init_utc,
    parse_float,
    select_usable_events,
)
from research.weather.checkpoints import (
    CHECKPOINT_IDS,
    CHECKPOINT_SPECS,
    checkpoint_as_of_utc,
    is_intraday_checkpoint,
    methodology_checkpoint_block,
)
from research.weather.models import (
    ACTUAL_SOURCE_IEM_NWS_CLI,
    ACTUAL_SOURCE_KALSHI_EXPIRATION,
    GFS_PUBLICATION_LATENCY,
    HRRR_MODEL,
    HRRR_PUBLICATION_LATENCY,
    HRRR_STREAM_EXACT,
    MIN_RUN_HISTORY,
    MODEL_COMBINATION_POLICY,
    MODEL_GFS_OPERATIONAL_LATEST,
    MODEL_HRRR_OPERATIONAL_LATEST,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    REPLAY_MODE_FULL_OPERATIONAL,
    REPLAY_MODE_MODEL_ONLY,
    RESIDUAL_CONVENTION,
    SHADOW_CANDIDATE_ID,
    SHADOW_STATUS_AVAILABLE,
    SNAPSHOT_SCHEMA_VERSION,
    STANDARDIZED_RUNS,
)
from research.weather.probability import (
    bucket_probabilities_from_residuals,
    choose_operational_run,
    select_residual_pool,
)
from research.weather.replay import log_loss, multiclass_brier
from research.weather.resolution import (
    get_event_date,
    get_outcome_label,
    get_winning_market,
    group_markets_by_event,
    parse_temperature_bucket,
    season_for_month,
)
from research.weather.shadow import (
    combine_equal_weight,
    load_or_register_shadow_hypothesis,
    shadow_transfer_metadata,
)
from research.weather.sources.hrrr import (
    get_hrrr_run_high,
    hrrr_available_as_of,
    is_hrrr_run_available,
)

GFS_OPERATIONAL_CSV = CALIBRATION_DIR / "gfs_operational_replay.csv"
HRRR_OPERATIONAL_CSV = CALIBRATION_DIR / "hrrr_operational_replay.csv"

RESULTS_DIR = cache.REPO_ROOT / "data" / "results"
OPERATIONAL_COMPARISON_PATH = RESULTS_DIR / "operational_model_comparison.json"
ERROR_CORRELATION_PATH = RESULTS_DIR / "model_error_correlation.json"
SHADOW_EVAL_PATH = RESULTS_DIR / "shadow_model_evaluation.json"
PROSPECTIVE_SUMMARY_PATH = RESULTS_DIR / "prospective_validation_summary.json"
PHASE5_METHODOLOGY_PATH = RESULTS_DIR / "phase5_operational_methodology.json"

# Historical as-of observation trajectories are not yet reconstructable.
HISTORICAL_ASOF_OBS_AVAILABLE = False
HRRR_OPERATIONAL_LOOKBACK_HOURS = 48
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42

GFS_OPERATIONAL_CSV_FIELDS = [
    "event_ticker",
    "target_date",
    "checkpoint_id",
    "checkpoint_as_of",
    "model",
    "selected_run_init",
    "available_at",
    "run_id",
    "forecast_high_f",
    "model_remaining_day_high_f",
    "observed_high_so_far_f",
    "projected_final_high_f",
    "actual_high_f",
    "residual_f",
    "target_regime",
    "replay_mode",
    "actual_source",
    "winning_bucket_label",
    "month",
    "season",
    "lead_hours",
    "source",
    "provenance",
]

HRRR_OPERATIONAL_CSV_FIELDS = list(GFS_OPERATIONAL_CSV_FIELDS) + [
    "forecast_stream",
    "benchmark_kind",
]


def _ensure_dirs() -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def replay_mode_for_checkpoint(checkpoint_id: str) -> str:
    if is_intraday_checkpoint(checkpoint_id) and not HISTORICAL_ASOF_OBS_AVAILABLE:
        return REPLAY_MODE_MODEL_ONLY
    return REPLAY_MODE_FULL_OPERATIONAL


def projected_final_high(
    *,
    model_remaining_day_high_f: float | None,
    observed_high_so_far_f: float | None,
) -> float | None:
    vals = [v for v in (model_remaining_day_high_f, observed_high_so_far_f) if v is not None]
    if not vals:
        return None
    if observed_high_so_far_f is None:
        return model_remaining_day_high_f
    if model_remaining_day_high_f is None:
        return observed_high_so_far_f
    return max(float(observed_high_so_far_f), float(model_remaining_day_high_f))


def choose_operational_hrrr_run(
    *,
    target_date: str,
    checkpoint_as_of: datetime,
    lookback_hours: int = HRRR_OPERATIONAL_LOOKBACK_HOURS,
) -> dict[str, Any] | None:
    """Newest HRRR exact run with run_init + latency <= checkpoint_as_of."""
    if checkpoint_as_of.tzinfo is None:
        checkpoint_as_of = checkpoint_as_of.replace(tzinfo=timezone.utc)
    cursor = checkpoint_as_of.replace(minute=0, second=0, microsecond=0)
    best: dict[str, Any] | None = None
    for hours_ago in range(0, lookback_hours + 1):
        init = cursor - timedelta(hours=hours_ago)
        if init > checkpoint_as_of:
            continue
        if not is_hrrr_run_available(run_init=init, as_of=checkpoint_as_of):
            continue
        high = get_hrrr_run_high(target_date, init, as_of=checkpoint_as_of)
        if high is None:
            continue
        available = hrrr_available_as_of(init)
        # Prefer newest available_at (and newest init as tie-break).
        if best is None or available > best["available_at"] or (
            available == best["available_at"] and init > best["run_init"]
        ):
            best = {
                "run_init": init,
                "available_at": available,
                "forecast_high_f": float(high),
                "run_id": f"hrrr_ops_{init.strftime('%Y%m%dT%HZ')}",
            }
    return best


def _actuals_from_gfs_calibration() -> dict[str, dict[str, Any]]:
    """Map target_date -> actual/winner/regime from Phase 1 CSV (both regimes)."""
    out: dict[str, dict[str, Any]] = {}
    for row in load_calibration_csv():
        date = str(row.get("target_date") or "")
        actual = parse_float(row.get("actual_high_f"))
        if not date or actual is None:
            continue
        # Prefer first non-null; all runs share actual for a date.
        if date not in out:
            out[date] = {
                "actual_high_f": actual,
                "actual_source": row.get("actual_source") or ACTUAL_SOURCE_IEM_NWS_CLI,
                "target_regime": row.get("target_regime")
                or row.get("settlement_source_regime")
                or REGIME_NWS_CLI_KNYC,
                "winning_bucket_label": row.get("winning_bucket_label") or "",
                "event_ticker": row.get("event_ticker") or "",
            }
    return out


def _gfs_forecasts_for_event(target_date: str) -> dict[str, float | None]:
    forecasts: dict[str, float | None] = {}
    for run in STANDARDIZED_RUNS:
        run_id = str(run["run_id"])
        forecasts[run_id] = get_gfs_run_high(
            target_date,
            int(run["run_date_offset"]),
            int(run["run_hour"]),
        )
    return forecasts


def build_gfs_operational_replay_rows(
    markets: list[dict[str, Any]],
    *,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """One GFS operational-latest forecast per event × checkpoint."""
    log = progress or (lambda _m: None)
    usable = select_usable_events(markets)
    actuals = _actuals_from_gfs_calibration()
    rows: list[dict[str, Any]] = []

    for event in usable:
        target_date = event["target_date"]
        event_ticker = event["event_ticker"]
        meta = actuals.get(target_date)
        if meta is None:
            continue
        actual = float(meta["actual_high_f"])
        forecasts = _gfs_forecasts_for_event(target_date)

        for checkpoint_id in CHECKPOINT_IDS:
            as_of = checkpoint_as_of_utc(target_date, checkpoint_id)
            run_id = choose_operational_run(
                target_date=target_date,
                prediction_as_of=as_of,
                forecasts=forecasts,
            )
            if run_id is None:
                log(f"GFS ops miss {event_ticker} {checkpoint_id}")
                continue
            run = next(r for r in STANDARDIZED_RUNS if r["run_id"] == run_id)
            init = model_run_init_utc(
                target_date, int(run["run_date_offset"]), int(run["run_hour"])
            )
            available = model_available_as_of(init)
            # Reject future / unavailable (choose_operational_run already gates).
            if available > as_of:
                continue
            forecast = forecasts.get(run_id)
            if forecast is None:
                continue

            mode = replay_mode_for_checkpoint(checkpoint_id)
            # Historical: never use final CLI as high_so_far.
            observed_so_far = None
            model_remaining = float(forecast)
            projected = projected_final_high(
                model_remaining_day_high_f=model_remaining,
                observed_high_so_far_f=observed_so_far,
            )
            residual = compute_residual(
                actual_high_f=actual, forecast_high_f=float(projected)
            )
            month = int(target_date[5:7])
            rows.append(
                {
                    "event_ticker": event_ticker,
                    "target_date": target_date,
                    "checkpoint_id": checkpoint_id,
                    "checkpoint_as_of": as_of.isoformat(),
                    "model": MODEL_GFS_OPERATIONAL_LATEST,
                    "selected_run_init": init.isoformat(),
                    "available_at": available.isoformat(),
                    "run_id": run_id,
                    "forecast_high_f": float(projected) if projected is not None else None,
                    "model_remaining_day_high_f": model_remaining,
                    "observed_high_so_far_f": observed_so_far,
                    "projected_final_high_f": projected,
                    "actual_high_f": actual,
                    "residual_f": residual,
                    "target_regime": meta["target_regime"],
                    "replay_mode": mode,
                    "actual_source": meta["actual_source"],
                    "winning_bucket_label": meta["winning_bucket_label"]
                    or get_outcome_label(event["winner"])
                    or "",
                    "month": month,
                    "season": season_for_month(month),
                    "lead_hours": lead_hours(target_date, init),
                    "source": "open-meteo:single-runs:ncep_gfs_global:operational_latest",
                    "provenance": (
                        f"policy=latest_available_by_latency;"
                        f"latency_h={GFS_PUBLICATION_LATENCY.total_seconds() / 3600:.0f};"
                        f"replay_mode={mode};"
                        f"residual_convention={RESIDUAL_CONVENTION}"
                    ),
                }
            )
            log(f"GFS ops {event_ticker} {checkpoint_id} run={run_id}")

    return rows


def build_hrrr_operational_replay_rows(
    markets: list[dict[str, Any]],
    *,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """One HRRR operational-latest exact run per event × checkpoint."""
    log = progress or (lambda _m: None)
    usable = select_usable_events(markets)
    actuals = _actuals_from_gfs_calibration()
    rows: list[dict[str, Any]] = []

    for event in usable:
        target_date = event["target_date"]
        event_ticker = event["event_ticker"]
        meta = actuals.get(target_date)
        if meta is None:
            continue
        actual = float(meta["actual_high_f"])

        for checkpoint_id in CHECKPOINT_IDS:
            as_of = checkpoint_as_of_utc(target_date, checkpoint_id)
            selected = choose_operational_hrrr_run(
                target_date=target_date, checkpoint_as_of=as_of
            )
            if selected is None:
                log(f"HRRR ops miss {event_ticker} {checkpoint_id}")
                continue
            init = selected["run_init"]
            available = selected["available_at"]
            if available > as_of:
                continue
            forecast = float(selected["forecast_high_f"])
            mode = replay_mode_for_checkpoint(checkpoint_id)
            observed_so_far = None
            model_remaining = forecast
            projected = projected_final_high(
                model_remaining_day_high_f=model_remaining,
                observed_high_so_far_f=observed_so_far,
            )
            residual = compute_residual(
                actual_high_f=actual, forecast_high_f=float(projected)
            )
            month = int(target_date[5:7])
            rows.append(
                {
                    "event_ticker": event_ticker,
                    "target_date": target_date,
                    "checkpoint_id": checkpoint_id,
                    "checkpoint_as_of": as_of.isoformat(),
                    "model": MODEL_HRRR_OPERATIONAL_LATEST,
                    "selected_run_init": init.isoformat(),
                    "available_at": available.isoformat(),
                    "run_id": selected["run_id"],
                    "forecast_high_f": float(projected) if projected is not None else None,
                    "model_remaining_day_high_f": model_remaining,
                    "observed_high_so_far_f": observed_so_far,
                    "projected_final_high_f": projected,
                    "actual_high_f": actual,
                    "residual_f": residual,
                    "target_regime": meta["target_regime"],
                    "replay_mode": mode,
                    "actual_source": meta["actual_source"],
                    "winning_bucket_label": meta["winning_bucket_label"]
                    or get_outcome_label(event["winner"])
                    or "",
                    "month": month,
                    "season": season_for_month(month),
                    "lead_hours": lead_hours(target_date, init),
                    "source": "open-meteo:single-runs:ncep_hrrr_conus:operational_latest",
                    "provenance": (
                        f"policy=latest_available_by_latency;"
                        f"latency_h={HRRR_PUBLICATION_LATENCY.total_seconds() / 3600:.0f};"
                        f"replay_mode={mode};"
                        f"benchmark_kind=operational_replay;"
                        f"residual_convention={RESIDUAL_CONVENTION}"
                    ),
                    "forecast_stream": HRRR_STREAM_EXACT,
                    "benchmark_kind": "operational_replay",
                }
            )
            log(f"HRRR ops {event_ticker} {checkpoint_id}")

    return rows


def write_operational_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> Path:
    _ensure_dirs()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})
    return path


def load_operational_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def filter_operational_checkpoint(
    rows: Sequence[dict[str, Any]],
    *,
    model: str,
    checkpoint_id: str,
    regime: str = REGIME_NWS_CLI_KNYC,
    before_date: str | None = None,
) -> list[dict[str, Any]]:
    """Same model + checkpoint_id (+ regime); never pool other checkpoints."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if (row.get("model") or "") != model:
            continue
        if (row.get("checkpoint_id") or "") != checkpoint_id:
            continue
        if (row.get("target_regime") or regime) != regime:
            continue
        if before_date is not None and str(row.get("target_date") or "") >= before_date:
            continue
        if parse_float(row.get("residual_f")) is None:
            continue
        out.append(row)
    out.sort(key=lambda r: str(r.get("target_date")))
    return out


def predict_operational_calibrated(
    *,
    forecast_high: float,
    prior_rows: Sequence[dict[str, Any]],
    target_date: str,
    buckets: Sequence[Any],
) -> dict[str, Any]:
    """Walk-forward calibrated probs from checkpoint-specific prior residuals."""
    if len(prior_rows) < MIN_RUN_HISTORY:
        return {
            "status": "BURN_IN",
            "probabilities": None,
            "residual_n": len(prior_rows),
            "pool_level": "burn_in",
        }
    residuals, pool_level, meta = select_residual_pool(prior_rows, target_date=target_date)
    if pool_level == "insufficient_history" or len(residuals) < MIN_RUN_HISTORY:
        return {
            "status": "INSUFFICIENT_HISTORY",
            "probabilities": None,
            "residual_n": len(residuals),
            "pool_level": pool_level,
            "pool_meta": meta,
        }
    probs = bucket_probabilities_from_residuals(
        forecast_high=forecast_high,
        residuals=residuals,
        buckets=buckets,
    )
    return {
        "status": "OK",
        "probabilities": probs,
        "residual_n": len(residuals),
        "pool_level": pool_level,
        "pool_meta": meta,
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    deny = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denx == 0 or deny == 0:
        return None
    return num / (denx * deny)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None

    def _ranks(vals: list[float]) -> list[float]:
        ordered = sorted((v, i) for i, v in enumerate(vals))
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(ordered):
            j = i
            while j + 1 < len(ordered) and ordered[j + 1][0] == ordered[i][0]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[ordered[k][1]] = avg
            i = j + 1
        return ranks

    return _pearson(_ranks(xs), _ranks(ys))


def bootstrap_mean_ci(
    values: list[float],
    *,
    n_boot: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "ci95": None, "bootstrap_unit": "observation"}
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.fmean(sample))
    means.sort()
    lo = means[int(0.025 * (len(means) - 1))]
    hi = means[int(0.975 * (len(means) - 1))]
    return {
        "n": n,
        "mean": statistics.fmean(values),
        "ci95": [lo, hi],
        "bootstrap_unit": "observation",
        "n_boot": n_boot,
        "seed": seed,
    }


def bootstrap_clustered_event_date(
    observations: list[dict[str, Any]],
    *,
    value_key: str = "delta_brier",
    n_boot: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Cluster bootstrap by event/target_date — keep all checkpoints together."""
    if not observations:
        return {
            "n_obs": 0,
            "n_clusters": 0,
            "mean": None,
            "ci95": None,
            "bootstrap_unit": "event_date",
        }
    groups: dict[str, list[float]] = {}
    for obs in observations:
        key = str(obs.get("target_date") or obs.get("event_ticker") or "")
        val = obs.get(value_key)
        if val is None:
            continue
        groups.setdefault(key, []).append(float(val))
    keys = list(groups.keys())
    if not keys:
        return {
            "n_obs": 0,
            "n_clusters": 0,
            "mean": None,
            "ci95": None,
            "bootstrap_unit": "event_date",
        }
    flat = [v for vals in groups.values() for v in vals]
    point = statistics.fmean(flat)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(n_boot):
        sample_keys = [keys[rng.randrange(len(keys))] for _ in range(len(keys))]
        sample_vals = [v for k in sample_keys for v in groups[k]]
        if sample_vals:
            means.append(statistics.fmean(sample_vals))
    means.sort()
    lo = means[int(0.025 * (len(means) - 1))] if means else None
    hi = means[int(0.975 * (len(means) - 1))] if means else None
    return {
        "n_obs": len(flat),
        "n_clusters": len(keys),
        "mean": point,
        "ci95": [lo, hi] if lo is not None else None,
        "bootstrap_unit": "event_date",
        "n_boot": n_boot,
        "seed": seed,
    }


def _continuous_metrics(residuals: list[float]) -> dict[str, Any]:
    m = error_metrics(residuals)
    return {
        "n": m["N"],
        "bias": m["bias"],
        "MAE": m["MAE"],
        "RMSE": m["RMSE"],
    }


def _prob_metrics(
    rows: list[dict[str, Any]],
    *,
    probs_key: str,
) -> dict[str, Any]:
    briers: list[float] = []
    lls: list[float] = []
    tops: list[float] = []
    pws: list[float] = []
    for row in rows:
        probs = row.get(probs_key)
        winner = row.get("winning_bucket_label")
        if not probs or not winner:
            continue
        briers.append(multiclass_brier(probs, winner))
        lls.append(log_loss(probs, winner))
        top = max(probs, key=probs.get)
        tops.append(1.0 if top == winner else 0.0)
        pws.append(float(probs.get(winner, 0.0)))
    if not briers:
        return {"n": 0}
    return {
        "n": len(briers),
        "brier": statistics.fmean(briers),
        "log_loss": statistics.fmean(lls),
        "top_bucket_accuracy": statistics.fmean(tops),
        "mean_p_winner": statistics.fmean(pws),
    }


DISAGREEMENT_BINS = (
    ("lt_1", 0.0, 1.0),
    ("1_to_2", 1.0, 2.0),
    ("2_to_4", 2.0, 4.0),
    ("gt_4", 4.0, float("inf")),
)


def evaluate_shared_operational(
    *,
    gfs_rows: list[dict[str, Any]],
    hrrr_rows: list[dict[str, Any]],
    markets_by_event: dict[str, list[dict[str, Any]]],
    checkpoint_id: str,
    regime: str = REGIME_NWS_CLI_KNYC,
) -> dict[str, Any]:
    """Apples-to-apples comparison on shared dates for one checkpoint."""
    gfs_cp = filter_operational_checkpoint(
        gfs_rows, model=MODEL_GFS_OPERATIONAL_LATEST, checkpoint_id=checkpoint_id, regime=regime
    )
    hrrr_cp = filter_operational_checkpoint(
        hrrr_rows,
        model=MODEL_HRRR_OPERATIONAL_LATEST,
        checkpoint_id=checkpoint_id,
        regime=regime,
    )
    gfs_by_date = {str(r["target_date"]): r for r in gfs_cp}
    hrrr_by_date = {str(r["target_date"]): r for r in hrrr_cp}
    shared_dates = sorted(set(gfs_by_date) & set(hrrr_by_date))

    paired: list[dict[str, Any]] = []
    alignment_failures = 0
    shadow_unavailable: dict[str, int] = {}

    for date in shared_dates:
        g = gfs_by_date[date]
        h = hrrr_by_date[date]
        event_ticker = str(g.get("event_ticker") or h.get("event_ticker") or "")
        event_markets = markets_by_event.get(event_ticker) or []
        buckets = []
        for market in event_markets:
            parsed = parse_temperature_bucket(market)
            if parsed is not None:
                buckets.append(parsed)
        if not buckets:
            continue

        gfs_prior = filter_operational_checkpoint(
            gfs_rows,
            model=MODEL_GFS_OPERATIONAL_LATEST,
            checkpoint_id=checkpoint_id,
            regime=regime,
            before_date=date,
        )
        hrrr_prior = filter_operational_checkpoint(
            hrrr_rows,
            model=MODEL_HRRR_OPERATIONAL_LATEST,
            checkpoint_id=checkpoint_id,
            regime=regime,
            before_date=date,
        )
        gfs_fc = parse_float(g.get("forecast_high_f"))
        hrrr_fc = parse_float(h.get("forecast_high_f"))
        if gfs_fc is None or hrrr_fc is None:
            continue

        gfs_pred = predict_operational_calibrated(
            forecast_high=gfs_fc,
            prior_rows=gfs_prior,
            target_date=date,
            buckets=buckets,
        )
        hrrr_pred = predict_operational_calibrated(
            forecast_high=hrrr_fc,
            prior_rows=hrrr_prior,
            target_date=date,
            buckets=buckets,
        )
        if gfs_pred["status"] != "OK" or hrrr_pred["status"] != "OK":
            continue

        shadow = combine_equal_weight(
            gfs_probs=gfs_pred["probabilities"],
            hrrr_probs=hrrr_pred["probabilities"],
            gfs_buckets=buckets,
            hrrr_buckets=buckets,
            event_ticker=event_ticker,
        )
        if shadow["status"] == "BUCKET_ALIGNMENT_ERROR":
            alignment_failures += 1
        if shadow["status"] != SHADOW_STATUS_AVAILABLE:
            reason = str(shadow.get("unavailable_reason") or shadow["status"])
            shadow_unavailable[reason] = shadow_unavailable.get(reason, 0) + 1

        winner = str(g.get("winning_bucket_label") or "")
        gfs_err = float(g["actual_high_f"]) - gfs_fc
        hrrr_err = float(h["actual_high_f"]) - hrrr_fc
        gfs_brier = multiclass_brier(gfs_pred["probabilities"], winner)
        hrrr_brier = multiclass_brier(hrrr_pred["probabilities"], winner)
        shadow_brier = (
            multiclass_brier(shadow["probabilities"], winner)
            if shadow.get("probabilities")
            else None
        )

        paired.append(
            {
                "target_date": date,
                "event_ticker": event_ticker,
                "checkpoint_id": checkpoint_id,
                "replay_mode": g.get("replay_mode"),
                "gfs_forecast_high_f": gfs_fc,
                "hrrr_forecast_high_f": hrrr_fc,
                "actual_high_f": float(g["actual_high_f"]),
                "gfs_error": gfs_err,
                "hrrr_error": hrrr_err,
                "disagreement_abs_f": abs(gfs_fc - hrrr_fc),
                "winning_bucket_label": winner,
                "gfs_probs": gfs_pred["probabilities"],
                "hrrr_probs": hrrr_pred["probabilities"],
                "shadow_probs": shadow.get("probabilities"),
                "shadow_status": shadow["status"],
                "gfs_brier": gfs_brier,
                "hrrr_brier": hrrr_brier,
                "shadow_brier": shadow_brier,
                "delta_brier_hrrr": hrrr_brier - gfs_brier,
                "delta_brier_shadow": (
                    shadow_brier - gfs_brier if shadow_brier is not None else None
                ),
            }
        )

    gfs_resid = [float(p["actual_high_f"]) - float(p["gfs_forecast_high_f"]) for p in paired]
    hrrr_resid = [float(p["actual_high_f"]) - float(p["hrrr_forecast_high_f"]) for p in paired]
    gfs_err = [p["gfs_error"] for p in paired]
    hrrr_err = [p["hrrr_error"] for p in paired]

    disagreement_bins: dict[str, Any] = {}
    for name, lo, hi in DISAGREEMENT_BINS:
        subset = [
            p
            for p in paired
            if lo <= float(p["disagreement_abs_f"]) < hi
        ]
        disagreement_bins[name] = {
            "n": len(subset),
            "gfs_MAE": (
                statistics.fmean([abs(p["gfs_error"]) for p in subset]) if subset else None
            ),
            "hrrr_MAE": (
                statistics.fmean([abs(p["hrrr_error"]) for p in subset]) if subset else None
            ),
            "gfs_brier": (
                statistics.fmean([p["gfs_brier"] for p in subset]) if subset else None
            ),
            "hrrr_brier": (
                statistics.fmean([p["hrrr_brier"] for p in subset]) if subset else None
            ),
        }

    cov = None
    if len(gfs_err) >= 2:
        mx = statistics.fmean(gfs_err)
        my = statistics.fmean(hrrr_err)
        cov = sum((x - mx) * (y - my) for x, y in zip(gfs_err, hrrr_err)) / len(gfs_err)

    return {
        "checkpoint_id": checkpoint_id,
        "regime": regime,
        "shared_n": len(paired),
        "replay_modes": sorted({str(p.get("replay_mode")) for p in paired}),
        "continuous": {
            "gfs": _continuous_metrics(gfs_resid),
            "hrrr": _continuous_metrics(hrrr_resid),
        },
        "probabilistic": {
            "gfs": _prob_metrics(paired, probs_key="gfs_probs"),
            "hrrr": _prob_metrics(paired, probs_key="hrrr_probs"),
            "shadow": _prob_metrics(
                [p for p in paired if p.get("shadow_probs")],
                probs_key="shadow_probs",
            ),
        },
        "error_correlation": {
            "pearson": _pearson(gfs_err, hrrr_err),
            "spearman": _spearman(gfs_err, hrrr_err),
            "covariance": cov,
            "mean_abs_gfs_hrrr_disagreement_f": (
                statistics.fmean([p["disagreement_abs_f"] for p in paired]) if paired else None
            ),
        },
        "disagreement_bins": disagreement_bins,
        "paired_delta_brier": {
            "hrrr_minus_gfs": bootstrap_mean_ci(
                [p["delta_brier_hrrr"] for p in paired]
            ),
            "shadow_minus_gfs": bootstrap_mean_ci(
                [
                    p["delta_brier_shadow"]
                    for p in paired
                    if p.get("delta_brier_shadow") is not None
                ]
            ),
        },
        "bucket_alignment_failures": alignment_failures,
        "shadow_unavailable_reasons": shadow_unavailable,
        "paired_rows": paired,
    }


def write_phase5_methodology() -> Path:
    _ensure_dirs()
    hyp = load_or_register_shadow_hypothesis()
    payload = {
        "schema": "phase5_operational_methodology",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_combination_policy_live": MODEL_COMBINATION_POLICY,
        "snapshot_schema_version_new": SNAPSHOT_SCHEMA_VERSION,
        "incumbent": "calibrated_gfs",
        "shadow_candidate": hyp,
        "checkpoints": methodology_checkpoint_block(),
        "operational_models": {
            "gfs_operational_latest": {
                "selection": "latest STANDARDIZED_RUN with run_init + GFS_PUBLICATION_LATENCY <= as_of",
                "latency_hours": GFS_PUBLICATION_LATENCY.total_seconds() / 3600.0,
            },
            "hrrr_operational_latest": {
                "selection": "latest exact HRRR hour with run_init + HRRR_PUBLICATION_LATENCY <= as_of",
                "latency_hours": HRRR_PUBLICATION_LATENCY.total_seconds() / 3600.0,
                "benchmark_kind": "operational_replay",
                "not_used": ["previous_day1", "fixed-cycle skill pick"],
            },
        },
        "intraday_daily_high_semantics": {
            "projected_final_high_f": "max(observed_high_so_far_f, model_remaining_day_high_f)",
            "historical_asof_observations_available": HISTORICAL_ASOF_OBS_AVAILABLE,
            "dminus1_1800": REPLAY_MODE_FULL_OPERATIONAL,
            "intraday_checkpoints": REPLAY_MODE_MODEL_ONLY,
            "limitation": (
                "Intraday historical replay is MODEL_ONLY_REPLAY until as-of observation "
                "trajectories can be reconstructed honestly. Never use final CLI daily "
                "high as historical high_so_far. Do not claim intraday replay reproduces "
                "the live information state when observations available at that time "
                "are omitted."
            ),
        },
        "residual_pools": {
            "rule": "same model + same checkpoint_id + target_date < T + same regime",
            "never_pool": [
                "other checkpoints",
                "fixed-cycle exact-run",
                "previous_day1",
                "Phase 1/4 residual CSVs",
            ],
            "thresholds_unchanged": True,
        },
        "bootstrap": {
            "per_checkpoint_unit": "target_date",
            "pooled_unit": "event_date",
            "note": "Cluster same-day checkpoints together for pooled analyses",
        },
        "nbm": {"status": "PHASE_6_CANDIDATE"},
        "clinyc_transfer_v1_untouched": True,
    }
    cache.write_json(PHASE5_METHODOLOGY_PATH, payload)
    return PHASE5_METHODOLOGY_PATH


def run_phase5_operational_evaluation(
    markets: list[dict[str, Any]],
    *,
    progress: Callable[[str], None] | None = None,
    rebuild_replay: bool = True,
) -> dict[str, Any]:
    """Build operational replays (optional) and write comparison artifacts."""
    log = progress or (lambda _m: None)
    _ensure_dirs()
    hyp = load_or_register_shadow_hypothesis()
    write_phase5_methodology()

    if rebuild_replay:
        log("Building GFS operational replay…")
        gfs_rows = build_gfs_operational_replay_rows(markets, progress=progress)
        write_operational_csv(GFS_OPERATIONAL_CSV, gfs_rows, GFS_OPERATIONAL_CSV_FIELDS)
        log(f"Wrote {len(gfs_rows)} GFS operational rows → {GFS_OPERATIONAL_CSV}")

        log("Building HRRR operational replay…")
        hrrr_rows = build_hrrr_operational_replay_rows(markets, progress=progress)
        write_operational_csv(HRRR_OPERATIONAL_CSV, hrrr_rows, HRRR_OPERATIONAL_CSV_FIELDS)
        log(f"Wrote {len(hrrr_rows)} HRRR operational rows → {HRRR_OPERATIONAL_CSV}")
    else:
        gfs_rows = load_operational_csv(GFS_OPERATIONAL_CSV)
        hrrr_rows = load_operational_csv(HRRR_OPERATIONAL_CSV)

    markets_by_event = group_markets_by_event(markets)
    per_checkpoint: dict[str, Any] = {}
    all_paired: list[dict[str, Any]] = []

    for checkpoint_id in CHECKPOINT_IDS:
        log(f"Evaluating shared operational {checkpoint_id}…")
        result = evaluate_shared_operational(
            gfs_rows=gfs_rows,
            hrrr_rows=hrrr_rows,
            markets_by_event=markets_by_event,
            checkpoint_id=checkpoint_id,
            regime=REGIME_NWS_CLI_KNYC,
        )
        paired = result.pop("paired_rows")
        per_checkpoint[checkpoint_id] = result
        all_paired.extend(paired)

    pooled_delta = bootstrap_clustered_event_date(all_paired, value_key="delta_brier_hrrr")
    pooled_shadow = bootstrap_clustered_event_date(
        [p for p in all_paired if p.get("delta_brier_shadow") is not None],
        value_key="delta_brier_shadow",
    )

    coverage = {
        cid: {
            "gfs_n": len(
                filter_operational_checkpoint(
                    gfs_rows,
                    model=MODEL_GFS_OPERATIONAL_LATEST,
                    checkpoint_id=cid,
                    regime=REGIME_NWS_CLI_KNYC,
                )
            ),
            "hrrr_n": len(
                filter_operational_checkpoint(
                    hrrr_rows,
                    model=MODEL_HRRR_OPERATIONAL_LATEST,
                    checkpoint_id=cid,
                    regime=REGIME_NWS_CLI_KNYC,
                )
            ),
            "shared_n": per_checkpoint[cid]["shared_n"],
            "replay_mode": replay_mode_for_checkpoint(cid),
        }
        for cid in CHECKPOINT_IDS
    }

    comparison = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shadow_candidate_id": SHADOW_CANDIDATE_ID,
        "shadow_registered_at": hyp.get("registered_at"),
        "historical_asof_observation_coverage": {
            "available": HISTORICAL_ASOF_OBS_AVAILABLE,
            "intraday": "none — MODEL_ONLY_REPLAY",
            "dminus1_1800": "full operational (no intraday obs required)",
        },
        "coverage_per_checkpoint": coverage,
        "per_checkpoint": per_checkpoint,
        "pooled_across_checkpoints": {
            "n_obs": len(all_paired),
            "delta_brier_hrrr_minus_gfs": pooled_delta,
            "delta_brier_shadow_minus_gfs": pooled_shadow,
        },
        "limitations": [
            "Intraday checkpoints are MODEL_ONLY_REPLAY until historical as-of observations exist.",
            "Do not claim intraday replay equals live information state.",
            "Shadow V1 uses experimental KNYC→CLINYC transfer for CLINYC events; not validated.",
        ],
    }
    cache.write_json(OPERATIONAL_COMPARISON_PATH, comparison)

    correlation = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "per_checkpoint": {
            cid: per_checkpoint[cid]["error_correlation"]
            | {"disagreement_bins": per_checkpoint[cid]["disagreement_bins"]}
            for cid in CHECKPOINT_IDS
        },
        "note": "Descriptive only — not a switching rule",
    }
    cache.write_json(ERROR_CORRELATION_PATH, correlation)

    shadow_eval = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate": hyp,
        "status": "SHADOW ONLY",
        "experimental_transfer_status": "experimental",
        "per_checkpoint": {
            cid: {
                "probabilistic": per_checkpoint[cid]["probabilistic"],
                "paired_delta_brier": per_checkpoint[cid]["paired_delta_brier"],
                "bucket_alignment_failures": per_checkpoint[cid][
                    "bucket_alignment_failures"
                ],
                "shadow_unavailable_reasons": per_checkpoint[cid][
                    "shadow_unavailable_reasons"
                ],
            }
            for cid in CHECKPOINT_IDS
        },
        "pooled_delta_brier_shadow_minus_gfs": pooled_shadow,
        "promotion": False,
    }
    cache.write_json(SHADOW_EVAL_PATH, shadow_eval)

    return {
        "gfs_rows": len(gfs_rows),
        "hrrr_rows": len(hrrr_rows),
        "comparison_path": str(OPERATIONAL_COMPARISON_PATH),
        "correlation_path": str(ERROR_CORRELATION_PATH),
        "shadow_eval_path": str(SHADOW_EVAL_PATH),
        "methodology_path": str(PHASE5_METHODOLOGY_PATH),
        "hypothesis": hyp,
        "coverage": coverage,
    }

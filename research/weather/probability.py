"""Convert GFS point forecasts + historical residuals into bucket probabilities.

Hierarchy (SAME RUN ONLY — never pool different GFS runs):
    month + same run
    season + same run
    same-run global
    insufficient_history
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Sequence

from research.weather.calibration import (
    error_metrics,
    parse_float,
)
from research.weather.models import (
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_RUN_HISTORY,
    PREDICTION_STATUS_BURN_IN,
    PREDICTION_STATUS_INSUFFICIENT_HISTORY,
    PREDICTION_STATUS_MISSING_FORECAST,
    PREDICTION_STATUS_OK,
    PREDICTION_STATUS_REGIME_MISMATCH,
    REGIME_NWS_CLI_KNYC,
    TRANSFER_STATUS_EXPERIMENTAL,
    TRANSFER_STATUS_NONE,
    TRANSFER_STATUS_VALIDATED,
    WeatherPrediction,
)
from research.weather.resolution import (
    TemperatureBucket,
    parse_temperature_bucket,
    season_for_month,
)


def _prior_same_run_rows(
    rows: Sequence[dict[str, Any]],
    *,
    run_id: str,
    before_date: str,
    regime: str = REGIME_NWS_CLI_KNYC,
) -> list[dict[str, Any]]:
    prior = []
    for row in rows:
        if row.get("run_id") != run_id:
            continue
        if (row.get("settlement_source_regime") or regime) != regime:
            continue
        if str(row.get("target_date") or "") >= before_date:
            continue
        if parse_float(row.get("residual_f")) is None:
            continue
        prior.append(row)
    prior.sort(key=lambda r: str(r.get("target_date")))
    return prior


def select_residual_pool(
    prior_rows: Sequence[dict[str, Any]],
    *,
    target_date: str,
) -> tuple[list[float], str, dict[str, Any]]:
    """Hierarchical same-run residual selection.

    Returns (residuals, pool_level, meta).
    Never mixes other run_ids — caller must already filter to one run.
    """
    month = int(target_date[5:7])
    season = season_for_month(month)

    def _residuals(rows: Sequence[dict[str, Any]]) -> list[float]:
        out: list[float] = []
        for row in rows:
            value = parse_float(row.get("residual_f"))
            if value is not None:
                out.append(float(value))
        return out

    month_rows = [
        r
        for r in prior_rows
        if int(str(r.get("month") or r["target_date"][5:7])) == month
    ]
    month_residuals = _residuals(month_rows)
    if len(month_residuals) >= MIN_N_MONTH:
        return month_residuals, "month_same_run", {
            "N": len(month_residuals),
            "threshold": MIN_N_MONTH,
            "month": month,
        }

    season_rows = [
        r
        for r in prior_rows
        if (r.get("season") or season_for_month(int(r["target_date"][5:7]))) == season
    ]
    season_residuals = _residuals(season_rows)
    if len(season_residuals) >= MIN_N_SEASON:
        return season_residuals, "season_same_run", {
            "N": len(season_residuals),
            "threshold": MIN_N_SEASON,
            "season": season,
        }

    run_residuals = _residuals(prior_rows)
    if len(run_residuals) >= MIN_N_RUN:
        return run_residuals, "same_run_global", {
            "N": len(run_residuals),
            "threshold": MIN_N_RUN,
        }

    return run_residuals, "insufficient_history", {
        "N": len(run_residuals),
        "threshold": MIN_N_RUN,
        "min_run_history": MIN_RUN_HISTORY,
    }


def empirical_predictive_highs(
    forecast_high: float,
    residuals: Sequence[float],
) -> list[int]:
    """possible_actual = forecast + residual, rounded to integer °F."""
    return [int(round(forecast_high + r)) for r in residuals]


def bucket_probabilities_from_residuals(
    *,
    forecast_high: float,
    residuals: Sequence[float],
    buckets: Sequence[TemperatureBucket],
) -> dict[str, float]:
    samples = empirical_predictive_highs(forecast_high, residuals)
    if not samples or not buckets:
        return {b.label: 0.0 for b in buckets}

    counts = {b.label: 0 for b in buckets}
    assigned = 0
    for high in samples:
        matched = False
        for bucket in buckets:
            if bucket.contains(high):
                counts[bucket.label] += 1
                matched = True
                break
        if matched:
            assigned += 1

    if assigned == 0:
        return {b.label: 0.0 for b in buckets}

    probs = {label: count / assigned for label, count in counts.items()}
    # Normalize tiny float drift.
    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}
    return probs


def assert_probs_sum_to_one(probs: dict[str, float], *, tol: float = 1e-6) -> None:
    total = sum(probs.values())
    if probs and abs(total - 1.0) > tol:
        raise AssertionError(f"bucket probabilities sum to {total}, expected ~1")


def confidence_assessment(
    *,
    residual_n: int,
    residuals: Sequence[float],
    gefs_std: float | None,
    model_agreement: str,
    lead_hours: float | None,
    resolution_certainty: str,
    prediction_status: str,
    transfer_validated: bool,
) -> tuple[float, str, list[str]]:
    """Confidence in the probability estimate — NOT equal to P(bucket)."""
    reasons: list[str] = []
    score = 0.5

    if prediction_status == PREDICTION_STATUS_BURN_IN:
        return 0.15, "LOW", ["burn-in: insufficient prior same-run history"]
    if prediction_status == PREDICTION_STATUS_INSUFFICIENT_HISTORY:
        return 0.1, "LOW", ["insufficient same-run residual history"]
    if prediction_status == PREDICTION_STATUS_MISSING_FORECAST:
        return 0.0, "LOW", ["missing forecast"]

    if residual_n >= MIN_N_RUN:
        score += 0.2
        reasons.append(f"calibration N={residual_n} (>= run threshold {MIN_N_RUN})")
    elif residual_n >= MIN_RUN_HISTORY:
        score += 0.05
        reasons.append(f"calibration N={residual_n} (above burn-in only)")
    else:
        score -= 0.2
        reasons.append(f"small calibration sample N={residual_n}")

    if residuals:
        metrics = error_metrics(list(residuals))
        rmse = metrics["RMSE"] or 0.0
        reasons.append(f"historical RMSE={rmse:.2f}°F")
        if rmse <= 2.0:
            score += 0.1
        elif rmse >= 4.0:
            score -= 0.15
        spread = metrics["std"] or 0.0
        reasons.append(f"residual std={spread:.2f}°F")
        if spread >= 4.0:
            score -= 0.1

    if gefs_std is not None:
        reasons.append(f"GEFS std={gefs_std:.2f}°F")
        if gefs_std >= 4.0:
            score -= 0.1
        elif gefs_std <= 1.5:
            score += 0.05

    reasons.append(f"model_agreement={model_agreement}")
    if model_agreement == "HIGH":
        score += 0.1
    elif model_agreement == "LOW":
        score -= 0.1

    if lead_hours is not None:
        reasons.append(f"lead_hours={lead_hours:.1f}")
        if lead_hours > 36:
            score -= 0.05

    reasons.append(f"resolution_certainty={resolution_certainty}")
    if resolution_certainty.startswith("verified"):
        score += 0.05
    elif resolution_certainty == "assumed":
        score -= 0.1

    if not transfer_validated:
        score -= 0.25
        reasons.append("settlement_source_transfer_validated=false")

    score = max(0.0, min(1.0, score))
    if score >= 0.7:
        label = "HIGH"
    elif score >= 0.4:
        label = "MODERATE"
    else:
        label = "LOW"
    return score, label, reasons


def model_agreement_label(
    calibrated: dict[str, float],
    gefs_raw: dict[str, float] | None,
) -> str:
    """Fixed thresholds — not ROI-tuned."""
    if not gefs_raw or not calibrated:
        return "UNKNOWN"
    labels = set(calibrated) | set(gefs_raw)
    # Total variation distance / 2
    tv = 0.5 * sum(abs(calibrated.get(l, 0.0) - gefs_raw.get(l, 0.0)) for l in labels)
    if tv <= 0.15:
        return "HIGH"
    if tv <= 0.30:
        return "MODERATE"
    return "LOW"


def gefs_bucket_probabilities(
    member_temps: Sequence[float],
    buckets: Sequence[TemperatureBucket],
) -> dict[str, float]:
    if not member_temps or not buckets:
        return {b.label: 0.0 for b in buckets}
    counts = {b.label: 0 for b in buckets}
    for temp in member_temps:
        for bucket in buckets:
            if bucket.contains(temp):
                counts[bucket.label] += 1
                break
    n = len(member_temps)
    return {label: count / n for label, count in counts.items()}


def distribution_quantiles(samples: Sequence[float]) -> dict[str, float | None]:
    if not samples:
        return {
            "expected": None,
            "median": None,
            "p10": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p90": None,
        }
    ordered = sorted(float(s) for s in samples)

    def q(p: float) -> float:
        k = (len(ordered) - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return ordered[int(k)]
        return ordered[f] * (c - k) + ordered[c] * (k - f)

    return {
        "expected": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p10": q(0.10),
        "p25": q(0.25),
        "p50": q(0.50),
        "p75": q(0.75),
        "p90": q(0.90),
    }


def predict_from_calibration(
    *,
    event_ticker: str,
    target_date: str,
    as_of: str,
    run_id: str,
    run_label: str,
    forecast_high: float | None,
    markets: list[dict],
    calibration_rows: Sequence[dict[str, Any]],
    calibration_target_regime: str,
    live_target_regime: str,
    settlement_source_transfer_validated: bool,
    resolution_certainty: str = "verified_nws_cli",
    lead_hours: float | None = None,
    gefs_members: Sequence[float] | None = None,
    provenance: dict[str, Any] | None = None,
    calibration_method: str | None = None,
    transfer_status: str | None = None,
) -> WeatherPrediction:
    buckets = [
        b
        for m in markets
        if (b := parse_temperature_bucket(m)) is not None
    ]
    warnings: list[str] = []
    prov_in = dict(provenance or {})
    method = calibration_method or prov_in.get("calibration_method")
    t_status = transfer_status or prov_in.get("transfer_status")

    # Infer transfer_status when not provided.
    if t_status is None:
        if settlement_source_transfer_validated:
            t_status = TRANSFER_STATUS_VALIDATED
        elif calibration_target_regime != live_target_regime:
            t_status = TRANSFER_STATUS_EXPERIMENTAL
        else:
            t_status = TRANSFER_STATUS_NONE

    if calibration_target_regime != live_target_regime and not settlement_source_transfer_validated:
        warnings.append(
            "EXPERIMENTAL - SETTLEMENT SOURCE MISMATCH: "
            f"calibration_target_regime={calibration_target_regime} "
            f"live_target_regime={live_target_regime}"
        )
        if method and "identity_transfer" in str(method):
            warnings.append(
                "knyc_to_clinyc_identity_transfer: experimental research display only; "
                "settlement_source_transfer_validated=false"
            )

    if forecast_high is None:
        return WeatherPrediction(
            event_ticker=event_ticker,
            target_date=target_date,
            as_of=as_of,
            forecast_run=run_label,
            run_id=run_id,
            point_forecast_high=None,
            residual_sample_size=0,
            expected_high=None,
            median_high=None,
            p10_high=None,
            p25_high=None,
            p50_high=None,
            p75_high=None,
            p90_high=None,
            bucket_probabilities={b.label: 0.0 for b in buckets},
            confidence_score=0.0,
            confidence_label="LOW",
            model_agreement="UNKNOWN",
            calibration_quality="missing_forecast",
            prediction_status=PREDICTION_STATUS_MISSING_FORECAST,
            calibration_target_regime=calibration_target_regime,
            live_target_regime=live_target_regime,
            settlement_source_transfer_validated=settlement_source_transfer_validated,
            calibration_method=str(method) if method else None,
            transfer_status=str(t_status) if t_status else None,
            warnings=warnings + ["missing GFS forecast"],
            provenance=prov_in,
        )

    prior = _prior_same_run_rows(
        calibration_rows,
        run_id=run_id,
        before_date=target_date,
        regime=calibration_target_regime,
    )
    prior_n = len(prior)

    if prior_n < MIN_RUN_HISTORY:
        status = PREDICTION_STATUS_BURN_IN
        residuals = []
        for row in prior:
            value = parse_float(row.get("residual_f"))
            if value is not None:
                residuals.append(float(value))
        pool_level = "burn_in"
        pool_meta = {"N": prior_n, "threshold": MIN_RUN_HISTORY}
        probs = {b.label: 0.0 for b in buckets}
        samples = []
        warnings.append(
            f"BURN_IN: only {prior_n} prior same-run residuals "
            f"(need {MIN_RUN_HISTORY}); not counted as OOS calibrated prediction"
        )
    else:
        residuals, pool_level, pool_meta = select_residual_pool(prior, target_date=target_date)
        if pool_level == "insufficient_history" or len(residuals) < MIN_RUN_HISTORY:
            status = PREDICTION_STATUS_INSUFFICIENT_HISTORY
            probs = {b.label: 0.0 for b in buckets}
            samples = []
            warnings.append(
                "insufficient_history: same-run residual pool below methodology thresholds; "
                "refusing to fall back to other GFS runs"
            )
        else:
            status = PREDICTION_STATUS_OK
            probs = bucket_probabilities_from_residuals(
                forecast_high=forecast_high,
                residuals=residuals,
                buckets=buckets,
            )
            assert_probs_sum_to_one(probs)
            samples = empirical_predictive_highs(forecast_high, residuals)

    if (
        calibration_target_regime != live_target_regime
        and not settlement_source_transfer_validated
        and status == PREDICTION_STATUS_OK
    ):
        # Keep meteorological probabilities but flag regime mismatch.
        status_for_conf = PREDICTION_STATUS_REGIME_MISMATCH
    else:
        status_for_conf = status

    quant = distribution_quantiles(samples)

    gefs_probs = None
    gefs_stats: dict[str, Any] = {
        "gefs_mean": None,
        "gefs_median": None,
        "gefs_std": None,
        "gefs_min": None,
        "gefs_max": None,
        "gefs_member_count": None,
    }
    if gefs_members:
        gefs_probs = gefs_bucket_probabilities(gefs_members, buckets)
        gefs_stats = {
            "gefs_mean": statistics.fmean(gefs_members),
            "gefs_median": statistics.median(gefs_members),
            "gefs_std": statistics.pstdev(gefs_members) if len(gefs_members) > 1 else 0.0,
            "gefs_min": min(gefs_members),
            "gefs_max": max(gefs_members),
            "gefs_member_count": len(gefs_members),
        }

    agreement = model_agreement_label(probs, gefs_probs)
    conf_score, conf_label, conf_reasons = confidence_assessment(
        residual_n=len(residuals),
        residuals=residuals,
        gefs_std=gefs_stats["gefs_std"],
        model_agreement=agreement,
        lead_hours=lead_hours,
        resolution_certainty=resolution_certainty,
        prediction_status=status_for_conf if status_for_conf != PREDICTION_STATUS_REGIME_MISMATCH else status,
        transfer_validated=settlement_source_transfer_validated
        or calibration_target_regime == live_target_regime,
    )
    if status_for_conf == PREDICTION_STATUS_REGIME_MISMATCH:
        conf_score = min(conf_score, 0.35)
        conf_label = "LOW"
        conf_reasons.append("settlement source mismatch — experimental display only")

    cal_quality = pool_level
    if status == PREDICTION_STATUS_OK and len(residuals) >= MIN_N_RUN:
        cal_quality = f"{pool_level}:adequate"
    elif status == PREDICTION_STATUS_OK:
        cal_quality = f"{pool_level}:thin"

    prov = dict(prov_in)
    prov.update(
        {
            "residual_pool_level": pool_level,
            "residual_pool_meta": pool_meta,
            "confidence_reasons": conf_reasons,
            "methodology_thresholds": {
                "MIN_N_MONTH": MIN_N_MONTH,
                "MIN_N_SEASON": MIN_N_SEASON,
                "MIN_N_RUN": MIN_N_RUN,
                "MIN_RUN_HISTORY": MIN_RUN_HISTORY,
            },
            "residual_convention": "residual_f = actual_high_f - forecast_high_f",
            "calibration_method": method,
            "transfer_status": t_status,
        }
    )

    return WeatherPrediction(
        event_ticker=event_ticker,
        target_date=target_date,
        as_of=as_of,
        forecast_run=run_label,
        run_id=run_id,
        point_forecast_high=forecast_high,
        residual_sample_size=len(residuals),
        expected_high=quant["expected"],
        median_high=quant["median"],
        p10_high=quant["p10"],
        p25_high=quant["p25"],
        p50_high=quant["p50"],
        p75_high=quant["p75"],
        p90_high=quant["p90"],
        bucket_probabilities=probs,
        confidence_score=conf_score,
        confidence_label=conf_label,
        model_agreement=agreement,
        calibration_quality=cal_quality,
        prediction_status=status,
        calibration_target_regime=calibration_target_regime,
        live_target_regime=live_target_regime,
        settlement_source_transfer_validated=settlement_source_transfer_validated,
        calibration_method=str(method) if method else None,
        transfer_status=str(t_status) if t_status else None,
        gefs_raw_probabilities=gefs_probs,
        gefs_mean=gefs_stats["gefs_mean"],
        gefs_median=gefs_stats["gefs_median"],
        gefs_std=gefs_stats["gefs_std"],
        gefs_min=gefs_stats["gefs_min"],
        gefs_max=gefs_stats["gefs_max"],
        gefs_member_count=gefs_stats["gefs_member_count"],
        warnings=warnings,
        provenance=prov,
    )


def choose_operational_run(
    *,
    target_date: str,
    prediction_as_of: datetime,
    forecasts: dict[str, float | None],
) -> str | None:
    """Latest exact GFS run with run_init + latency <= prediction_as_of.

    Availability policy — NOT selected by historical Brier/accuracy.
    """
    from research.weather.calibration import model_available_as_of, model_run_init_utc
    from research.weather.models import STANDARDIZED_RUNS

    if prediction_as_of.tzinfo is None:
        prediction_as_of = prediction_as_of.replace(tzinfo=timezone.utc)

    candidates: list[tuple[datetime, str]] = []
    for run in STANDARDIZED_RUNS:
        run_id = str(run["run_id"])
        if forecasts.get(run_id) is None:
            continue
        init = model_run_init_utc(
            target_date, int(run["run_date_offset"]), int(run["run_hour"])
        )
        available = model_available_as_of(init)
        if available <= prediction_as_of:
            candidates.append((available, run_id))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]

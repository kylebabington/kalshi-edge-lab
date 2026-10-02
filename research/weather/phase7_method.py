"""Phase 7 prediction method — pinned by source hash in the frozen protocol.

Pure functions only. Given saved evidence (raw IEM text, raw Single Runs
payloads for every attempted candidate), canonical bucket definitions and the
frozen residual pools, these reproduce every Phase 7 research probability
offline. Fetching, logging, receipts and scheduling live in phase7.py and are
deliberately outside the method fingerprint.

Rules are Phase 6 v2.1 version C:
  * run selection = Phase 5 predicate (newest run with init + publication
    latency <= cutoff whose response yields a target-date high); an incomplete
    selected run is never replaced by an older complete one
  * remaining window = [cutoff, end of NWS CLI climate day), complete hours only
  * intraday observations: KNYC since climate-day start, available
    (obs_time + 20 min) by the cutoff, frozen staleness/gap trust rules
  * dminus1_1800: observations not applicable; complete climate-day forecast
  * calibration: same model + checkpoint + nws_cli_knyc frozen pool rows with
    target_date before the prediction target date; month/season/global
    hierarchy evaluated on the prediction date
"""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from research.weather import asof_observations as _asof
from research.weather import phase5 as _phase5
from research.weather import probability as _probability
from research.weather import replay as _replay
from research.weather import resolution as _resolution
from research.weather import shadow as _shadow
from research.weather.asof_observations import (
    OBS_AVAILABILITY_LAG,
    OBS_MAX_GAP_MIN,
    OBS_MAX_NEWEST_AGE_MIN,
    OBS_STATUS_OK,
    group_by_climate_date,
    observed_high_asof,
    validate_observations,
)
from research.weather.calibration import model_available_as_of, model_run_init_utc
from research.weather.checkpoints import CHECKPOINT_SPECS, is_intraday_checkpoint
from research.weather.models import (
    BUCKET_BOUNDARY_ASSUMPTION,
    CHECKPOINT_CAPTURE_WINDOW_MINUTES,
    GFS_PUBLICATION_LATENCY,
    HRRR_PUBLICATION_LATENCY,
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_RUN_HISTORY,
    MODEL_GFS_OPERATIONAL_LATEST,
    MODEL_HRRR_OPERATIONAL_LATEST,
    REGIME_NWS_CLI_KNYC,
    SHADOW_STATUS_AVAILABLE,
    STANDARDIZED_RUNS,
)
from research.weather.phase5 import (
    HRRR_OPERATIONAL_LOOKBACK_HOURS,
    filter_operational_checkpoint,
    predict_operational_calibrated,
    projected_final_high,
)
from research.weather.probability import select_residual_pool
from research.weather.replay import log_loss, multiclass_brier
from research.weather.resolution import TemperatureBucket
from research.weather.shadow import combine_equal_weight
from research.weather.sources import hrrr as _hrrr
from research.weather.sources import knyc_history as _knyc
from research.weather.sources import single_run_hourly as _srh
from research.weather.sources.hrrr import hrrr_available_as_of, is_hrrr_run_available
from research.weather.sources.knyc_history import parse_iem_asos_csv
from research.weather.sources.single_run_hourly import (
    MODEL_WINDOW_BASIS,
    WINDOW_STATUS_OK,
    parse_hourly_series,
    remaining_day_high,
)

METHOD_VERSION = "phase7_method_v1"
NYC_TZ = ZoneInfo("America/New_York")

OBS_STATUS_NOT_APPLICABLE = "NOT_APPLICABLE_PRE_TARGET_DAY"
COMPONENT_OK = "OK"
COMPONENT_UNAVAILABLE = "UNAVAILABLE"
REASON_NO_ELIGIBLE_RUN = "no_selection_eligible_run"
REASON_SEARCH_INCOMPLETE = "candidate_search_incomplete"
CALIBRATION_INSUFFICIENT_DETERMINISTIC = "CALIBRATION_INSUFFICIENT_DETERMINISTIC"
CALIBRATION_FEASIBLE = "FEASIBLE"

COMPONENT_MODELS = (MODEL_GFS_OPERATIONAL_LATEST, MODEL_HRRR_OPERATIONAL_LATEST)


def _utc(value: datetime | str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Run selection (Phase 5 predicate)
# ---------------------------------------------------------------------------


def ny_date_high(series: Sequence[tuple[datetime, float | None]] | None, target_date: str) -> float | None:
    """Max non-null value whose America/New_York valid date is the target date.

    This is the value Phase 5 obtains from timezone=America/New_York responses
    (get_gfs_run_high / get_hrrr_run_high); a run is selection-eligible iff it
    is not None.
    """
    values = [
        float(v)
        for t, v in (series or [])
        if v is not None and t.astimezone(NYC_TZ).date().isoformat() == target_date
    ]
    return max(values) if values else None


def gfs_candidates(target_date: str, cutoff: datetime) -> list[dict[str, Any]]:
    """Standardized GFS runs, newest availability first, with the latency gate."""
    cutoff = _utc(cutoff)
    out: list[dict[str, Any]] = []
    for run in STANDARDIZED_RUNS:
        init = model_run_init_utc(target_date, int(run["run_date_offset"]), int(run["run_hour"]))
        available = model_available_as_of(init)
        out.append(
            {
                "run_id": str(run["run_id"]),
                "run_init": init.isoformat(),
                "available_at": available.isoformat(),
                "latency_eligible": available <= cutoff,
            }
        )
    out.sort(key=lambda c: (c["available_at"], c["run_init"]), reverse=True)
    return out


def hrrr_candidates(cutoff: datetime, lookback_hours: int = HRRR_OPERATIONAL_LOOKBACK_HOURS) -> list[dict[str, Any]]:
    """Hourly HRRR inits within the lookback, newest first, with the latency gate."""
    cutoff = _utc(cutoff)
    cursor = cutoff.replace(minute=0, second=0, microsecond=0)
    out: list[dict[str, Any]] = []
    for hours_ago in range(0, lookback_hours + 1):
        init = cursor - timedelta(hours=hours_ago)
        if init > cutoff:
            continue
        out.append(
            {
                "run_id": f"hrrr_ops_{init.strftime('%Y%m%dT%HZ')}",
                "run_init": init.isoformat(),
                "available_at": hrrr_available_as_of(init).isoformat(),
                "latency_eligible": is_hrrr_run_available(run_init=init, as_of=cutoff),
            }
        )
    return out


def candidates_for(model: str, target_date: str, cutoff: datetime) -> list[dict[str, Any]]:
    if model == MODEL_GFS_OPERATIONAL_LATEST:
        return gfs_candidates(target_date, cutoff)
    if model == MODEL_HRRR_OPERATIONAL_LATEST:
        return hrrr_candidates(cutoff)
    raise ValueError(f"unknown model {model}")


def attempt_series(attempt: dict[str, Any] | None) -> list[tuple[datetime, float | None]] | None:
    if not attempt or attempt.get("outcome") != "ok":
        return None
    return parse_hourly_series(attempt.get("payload"))


def select_run(
    candidates: Sequence[dict[str, Any]],
    attempts: dict[str, dict[str, Any]],
    *,
    target_date: str,
) -> dict[str, Any]:
    """Newest latency-eligible candidate whose response yields a target-date high.

    ``attempts`` maps run_init ISO -> {"outcome", "payload"}. Failed or unusable
    fetches are not eligible, exactly as a None high in Phase 5. The search must
    have attempted every eligible candidate newer than the selection.
    """
    examined: list[dict[str, Any]] = []
    for cand in candidates:
        if not cand["latency_eligible"]:
            continue
        attempt = attempts.get(cand["run_init"])
        if attempt is None:
            return {
                "status": REASON_SEARCH_INCOMPLETE,
                "selected": None,
                "examined": examined,
                "first_unattempted_run_init": cand["run_init"],
            }
        high = ny_date_high(attempt_series(attempt), target_date)
        examined.append(
            {
                "run_init": cand["run_init"],
                "fetch_outcome": attempt.get("outcome"),
                "target_date_high_f": high,
                "selection_eligible": high is not None,
            }
        )
        if high is not None:
            return {
                "status": "SELECTED",
                "selected": {**cand, "target_date_high_f": high},
                "examined": examined,
            }
    return {"status": REASON_NO_ELIGIBLE_RUN, "selected": None, "examined": examined}


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


def observation_summary(
    raw_iem_text: str | None,
    *,
    target_date: str,
    checkpoint_id: str,
    cutoff: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    """(obs_summary, accepted_records, rejection_counts) at the evidence cutoff."""
    if not is_intraday_checkpoint(checkpoint_id):
        return (
            {
                "status": OBS_STATUS_NOT_APPLICABLE,
                "reason": "dminus1_checkpoint_precedes_target_climate_day",
                "observed_high_so_far_f": None,
            },
            [],
            {},
        )
    if raw_iem_text is None:
        return (
            {
                "status": _asof.OBS_STATUS_MISSING,
                "reason": "observation_fetch_failed",
                "observed_high_so_far_f": None,
            },
            [],
            {},
        )
    valid, rejections = validate_observations(parse_iem_asos_csv(raw_iem_text))
    day_records = group_by_climate_date(valid).get(target_date, [])
    summary = observed_high_asof(day_records, target_date=target_date, checkpoint_as_of=_utc(cutoff))
    cutoff_utc = _utc(cutoff)
    accepted = [
        {
            "observation_time_utc": r["obs_time"].isoformat(),
            "available_at_utc": r["available_at"].isoformat(),
            "temperature_f": float(r["temperature_f"]),
            "raw_metar": r.get("raw_metar"),
        }
        for r in day_records
        if r["obs_time"] <= cutoff_utc and r["available_at"] <= cutoff_utc
    ]
    return summary, accepted, dict(rejections)


# ---------------------------------------------------------------------------
# Components, calibration and shadow
# ---------------------------------------------------------------------------


def calibration_feasibility(
    pool_rows: Sequence[dict[str, Any]],
    *,
    model: str,
    checkpoint_id: str,
    target_date: str,
) -> dict[str, Any]:
    """Forecast-independent pool level for (model, checkpoint, prediction date)."""
    prior = filter_operational_checkpoint(
        pool_rows,
        model=model,
        checkpoint_id=checkpoint_id,
        regime=REGIME_NWS_CLI_KNYC,
        before_date=target_date,
    )
    residuals, level, meta = select_residual_pool(prior, target_date=target_date)
    insufficient = len(prior) < MIN_RUN_HISTORY or level == "insufficient_history" or len(residuals) < MIN_RUN_HISTORY
    return {
        "status": CALIBRATION_INSUFFICIENT_DETERMINISTIC if insufficient else CALIBRATION_FEASIBLE,
        "prior_rows_n": len(prior),
        "pool_level": level if len(prior) >= MIN_RUN_HISTORY else "burn_in",
        "pool_n": len(residuals),
        "pool_meta": meta,
    }


def _unavailable(base: dict[str, Any], reason: str) -> dict[str, Any]:
    return {**base, "status": COMPONENT_UNAVAILABLE, "reason": reason, "probabilities": None}


def component_prediction(
    *,
    model: str,
    target_date: str,
    checkpoint_id: str,
    cutoff: datetime,
    selection: dict[str, Any],
    series: Sequence[tuple[datetime, float | None]] | None,
    obs: dict[str, Any],
    pool_rows: Sequence[dict[str, Any]],
    buckets: Sequence[TemperatureBucket],
) -> dict[str, Any]:
    intraday = is_intraday_checkpoint(checkpoint_id)
    base: dict[str, Any] = {
        "model": model,
        "selection_status": selection.get("status"),
        "selected_run": selection.get("selected"),
        "candidates_examined": selection.get("examined"),
        "calibration_feasibility": calibration_feasibility(
            pool_rows, model=model, checkpoint_id=checkpoint_id, target_date=target_date
        ),
        "window": None,
        "model_remaining_day_high_f": None,
        "observed_high_so_far_f": None,
        "projected_final_high_f": None,
        "calibration": None,
        "residual_pool": None,
    }
    if selection.get("status") != "SELECTED":
        return _unavailable(base, str(selection.get("status")))

    window = remaining_day_high(list(series or []), target_date=target_date, checkpoint_as_of=_utc(cutoff))
    base["window"] = window
    if window.get("status") != WINDOW_STATUS_OK:
        return _unavailable(base, f"model_window_unavailable:{window.get('reason')}")
    remaining = float(window["model_remaining_day_high_f"])
    base["model_remaining_day_high_f"] = remaining

    observed: float | None = None
    if intraday:
        if obs.get("status") != OBS_STATUS_OK:
            return _unavailable(base, f"obs_{obs.get('status')}:{obs.get('reason')}")
        observed = float(obs["observed_high_so_far_f"])
    base["observed_high_so_far_f"] = observed

    projected = projected_final_high(model_remaining_day_high_f=remaining, observed_high_so_far_f=observed)
    base["projected_final_high_f"] = projected

    prior = filter_operational_checkpoint(
        pool_rows,
        model=model,
        checkpoint_id=checkpoint_id,
        regime=REGIME_NWS_CLI_KNYC,
        before_date=target_date,
    )
    cal = predict_operational_calibrated(
        forecast_high=float(projected), prior_rows=prior, target_date=target_date, buckets=buckets
    )
    residuals, level, _meta = select_residual_pool(prior, target_date=target_date)
    base["calibration"] = {k: v for k, v in cal.items() if k != "probabilities"}
    base["residual_pool"] = {
        "prior_target_dates": [str(r["target_date"]) for r in prior],
        "pool_level": level,
        "residuals_used": residuals,
    }
    if cal["status"] != "OK":
        return _unavailable(base, f"calibration_{cal['status']}")
    return {**base, "status": COMPONENT_OK, "reason": None, "probabilities": cal["probabilities"]}


def shadow_prediction(
    gfs: dict[str, Any],
    hrrr: dict[str, Any],
    *,
    buckets: Sequence[TemperatureBucket],
    event_ticker: str,
) -> dict[str, Any]:
    if gfs.get("status") != COMPONENT_OK or hrrr.get("status") != COMPONENT_OK:
        return {
            "status": COMPONENT_UNAVAILABLE,
            "reason": f"component_unavailable:gfs={gfs.get('status')};hrrr={hrrr.get('status')}",
            "probabilities": None,
        }
    block = combine_equal_weight(
        gfs_probs=gfs["probabilities"],
        hrrr_probs=hrrr["probabilities"],
        gfs_buckets=buckets,
        hrrr_buckets=buckets,
        event_ticker=event_ticker,
    )
    if block["status"] != SHADOW_STATUS_AVAILABLE:
        return {"status": COMPONENT_UNAVAILABLE, "reason": block.get("unavailable_reason"), "probabilities": None}
    return {"status": COMPONENT_OK, "reason": None, "probabilities": block["probabilities"], "definition": block["definition"]}


def buckets_from_dicts(rows: Sequence[dict[str, Any]]) -> list[TemperatureBucket]:
    return [
        TemperatureBucket(
            label=str(r["label"]),
            lower_bound=r.get("lower_bound"),
            upper_bound=r.get("upper_bound"),
            lower_inclusive=bool(r.get("lower_inclusive")),
            upper_inclusive=bool(r.get("upper_inclusive")),
            ticker=r.get("ticker"),
            bucket_boundary_assumption=str(r.get("bucket_boundary_assumption") or BUCKET_BOUNDARY_ASSUMPTION),
        )
        for r in rows
    ]


def build_prediction(
    *,
    target_date: str,
    checkpoint_id: str,
    event_ticker: str,
    cutoff: datetime,
    bucket_dicts: Sequence[dict[str, Any]],
    raw_iem_text: str | None,
    attempts_by_model: dict[str, dict[str, dict[str, Any]]],
    pool_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Every research probability for one checkpoint, from saved evidence only."""
    buckets = buckets_from_dicts(bucket_dicts)
    obs, accepted, rejections = observation_summary(
        raw_iem_text, target_date=target_date, checkpoint_id=checkpoint_id, cutoff=cutoff
    )
    components: dict[str, dict[str, Any]] = {}
    for model in COMPONENT_MODELS:
        attempts = attempts_by_model.get(model) or {}
        selection = select_run(candidates_for(model, target_date, cutoff), attempts, target_date=target_date)
        selected = selection.get("selected")
        series = attempt_series(attempts.get(selected["run_init"])) if selected else None
        components[model] = component_prediction(
            model=model,
            target_date=target_date,
            checkpoint_id=checkpoint_id,
            cutoff=cutoff,
            selection=selection,
            series=series,
            obs=obs,
            pool_rows=pool_rows,
            buckets=buckets,
        )
    gfs = components[MODEL_GFS_OPERATIONAL_LATEST]
    hrrr = components[MODEL_HRRR_OPERATIONAL_LATEST]
    shadow = shadow_prediction(gfs, hrrr, buckets=buckets, event_ticker=event_ticker)
    return {
        "method_version": METHOD_VERSION,
        "evidence_cutoff_utc": _utc(cutoff).isoformat(),
        "observations": {
            "summary": obs,
            "accepted": accepted,
            "rejection_counts": rejections,
        },
        "research_gfs": gfs,
        "research_hrrr": hrrr,
        "research_shadow": shadow,
        "paired_valid": all(c.get("status") == COMPONENT_OK for c in (gfs, hrrr, shadow)),
    }


def score_distribution(probs: dict[str, float] | None, winner: str) -> dict[str, Any] | None:
    if not probs:
        return None
    top = max(probs, key=probs.get)
    return {
        "brier": multiclass_brier(probs, winner),
        "log_loss": log_loss(probs, winner),
        "p_winner": float(probs.get(winner, 0.0)),
        "top_bucket_correct": top == winner,
    }


# ---------------------------------------------------------------------------
# Method fingerprint
# ---------------------------------------------------------------------------

METHOD_GROUPS: dict[str, tuple[Any, ...]] = {
    "forecast_window": (
        _srh.remaining_day_high,
        _srh.expected_window_hours,
        _srh._incomplete_reason,
        _srh._ceil_hour,
        _srh.parse_hourly_series,
        _asof.climate_day_bounds_utc,
    ),
    "observation": (
        _knyc.parse_iem_asos_csv,
        _knyc._normalize_station,
        _asof._parse_utc,
        _asof.record_available_at,
        _asof._station_ok,
        _asof.validate_observations,
        _asof.group_by_climate_date,
        _asof.observed_high_asof,
        observation_summary,
    ),
    "run_selection": (
        ny_date_high,
        gfs_candidates,
        hrrr_candidates,
        candidates_for,
        attempt_series,
        select_run,
        _hrrr.is_hrrr_run_available,
        _hrrr.hrrr_available_as_of,
        model_available_as_of,
        model_run_init_utc,
        _probability.choose_operational_run,
        _phase5.choose_operational_hrrr_run,
    ),
    "probability": (
        _resolution.TemperatureBucket,
        _resolution.parse_temperature_bucket,
        _probability.empirical_predictive_highs,
        _probability.bucket_probabilities_from_residuals,
        _probability.assert_probs_sum_to_one,
        buckets_from_dicts,
        component_prediction,
        build_prediction,
        _phase5.projected_final_high,
    ),
    "calibration": (
        _phase5.filter_operational_checkpoint,
        _phase5.predict_operational_calibrated,
        _probability.select_residual_pool,
        _resolution.season_for_month,
        calibration_feasibility,
    ),
    "shadow": (
        _shadow._boundary_key,
        _shadow.canonical_bucket_identity,
        _shadow._probs_by_canonical,
        _shadow._label_by_canonical,
        _shadow.bucket_universes_align,
        _shadow.combine_equal_weight,
        shadow_prediction,
    ),
    "scoring": (
        _replay.multiclass_brier,
        _replay.log_loss,
        score_distribution,
    ),
}


def method_constants() -> dict[str, Any]:
    return {
        "method_version": METHOD_VERSION,
        "gfs_publication_latency_h": GFS_PUBLICATION_LATENCY.total_seconds() / 3600,
        "hrrr_publication_latency_h": HRRR_PUBLICATION_LATENCY.total_seconds() / 3600,
        "hrrr_lookback_hours": HRRR_OPERATIONAL_LOOKBACK_HOURS,
        "standardized_gfs_runs": [dict(r) for r in STANDARDIZED_RUNS],
        "obs_availability_lag_min": OBS_AVAILABILITY_LAG.total_seconds() / 60,
        "obs_max_newest_age_min": OBS_MAX_NEWEST_AGE_MIN,
        "obs_max_gap_min": OBS_MAX_GAP_MIN,
        "obs_temp_range_f": [_asof.TEMP_MIN_F, _asof.TEMP_MAX_F],
        "obs_expected_station": _asof.EXPECTED_STATION_ID,
        "climate_day_utc_offset_h": _asof.CLIMATE_DAY_TZ.utcoffset(None).total_seconds() / 3600,
        "model_window_basis": MODEL_WINDOW_BASIS,
        "min_run_history": MIN_RUN_HISTORY,
        "min_n_month": MIN_N_MONTH,
        "min_n_season": MIN_N_SEASON,
        "min_n_run": MIN_N_RUN,
        "calibration_regime": REGIME_NWS_CLI_KNYC,
        "selection_hierarchy": ["month_same_run", "season_same_run", "same_run_global", "insufficient_history"],
        "capture_window_minutes": CHECKPOINT_CAPTURE_WINDOW_MINUTES,
        "checkpoint_specs": [dict(s) for s in CHECKPOINT_SPECS],
    }


def _source_hash(obj: Any) -> str:
    return hashlib.sha256(inspect.getsource(obj).encode("utf-8")).hexdigest()


def method_source_hashes() -> dict[str, dict[str, str]]:
    return {
        group: {f"{obj.__module__}:{obj.__qualname__}": _source_hash(obj) for obj in objs}
        for group, objs in METHOD_GROUPS.items()
    }


def method_fingerprint() -> dict[str, Any]:
    sources = method_source_hashes()
    constants = method_constants()
    group_hashes = {
        group: hashlib.sha256(json.dumps(h, sort_keys=True).encode("utf-8")).hexdigest()
        for group, h in sources.items()
    }
    constants_hash = hashlib.sha256(json.dumps(constants, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    combined = hashlib.sha256(
        json.dumps({"groups": group_hashes, "constants": constants_hash}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "method_version": METHOD_VERSION,
        "function_hashes": sources,
        "group_hashes": group_hashes,
        "constants": constants,
        "constants_hash": constants_hash,
        "method_fingerprint_sha256": combined,
    }


def method_drift(pinned: dict[str, Any]) -> list[str]:
    """Names of pinned method groups / constants whose current hash differs."""
    current = method_fingerprint()
    drift = [
        group
        for group, digest in (pinned.get("group_hashes") or {}).items()
        if current["group_hashes"].get(group) != digest
    ]
    drift += [g for g in current["group_hashes"] if g not in (pinned.get("group_hashes") or {})]
    if current["constants_hash"] != pinned.get("constants_hash"):
        drift.append("constants")
    return sorted(set(drift))

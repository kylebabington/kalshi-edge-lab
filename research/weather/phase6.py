"""Phase 6: historical KNYC as-of observations + full operational replay.

Three replay versions, each recalibrated walk-forward on its own residuals:

    A  original Phase 5 rows (model value = Phase 5 full-run target-date max,
       no observations; intraday = MODEL_ONLY_REPLAY)
    B  diagnostic bridge: Phase 5 model value + as-of KNYC observed high
       (OBS_BRIDGE_DIAGNOSTIC — the model value may include past hours)
    C  Phase 6 primary: remaining-day model max over [checkpoint, end of the
       NWS CLI climate day = next midnight EST) from the SAME selected run +
       as-of observed high (same climate-day bounds as the observations)
       (FULL_OPERATIONAL_REPLAY per eligible row)

projected_final_high_f = max(observed_high_so_far_f, model_remaining_day_high_f)

A→B isolates adding observations under the old window; B→C isolates the
window correction. All three are scored on identical eligible
(target_date, checkpoint) pairs. Run selection and publication latency are
inherited unchanged from the Phase 5 CSVs, which are read-only here.

RESEARCH_ONLY / NO_BET. Incumbent remains calibrated GFS; the shadow stays
shadow-only; CLINYC transfer status is untouched.
"""

from __future__ import annotations

import csv
import hashlib
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from historical_weather import GFS_MODEL
from kalshi import cache
from research.weather.asof_observations import (
    AVAILABILITY_BASIS,
    OBS_AVAILABILITY_LAG,
    OBS_MAX_GAP_MIN,
    OBS_MAX_NEWEST_AGE_MIN,
    OBS_STATUS_OK,
    OBS_WINDOW_BASIS,
    group_by_climate_date,
    observed_high_asof,
    summarize_ages,
    validate_observations,
)
from research.weather.calibration import CALIBRATION_DIR, compute_residual, parse_float
from research.weather.checkpoints import (
    CHECKPOINT_IDS,
    checkpoint_as_of_utc,
    is_intraday_checkpoint,
)
from research.weather.models import (
    DECISION_NO_BET,
    DECISION_RESEARCH_ONLY,
    GFS_PUBLICATION_LATENCY,
    HRRR_MODEL,
    HRRR_PUBLICATION_LATENCY,
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_RUN_HISTORY,
    MODEL_COMBINATION_POLICY,
    MODEL_GFS_OPERATIONAL_LATEST,
    MODEL_HRRR_OPERATIONAL_LATEST,
    REGIME_NWS_CLI_KNYC,
    REPLAY_MODE_FULL_OPERATIONAL,
    REPLAY_MODE_MODEL_ONLY,
    REPLAY_MODE_OBS_BRIDGE_DIAGNOSTIC,
    REPLAY_MODE_UNAVAILABLE,
    RESIDUAL_CONVENTION,
    SHADOW_CANDIDATE_ID,
    SHADOW_STATUS_AVAILABLE,
)
from research.weather.phase5 import (
    BOOTSTRAP_N,
    BOOTSTRAP_SEED,
    GFS_OPERATIONAL_CSV,
    GFS_OPERATIONAL_CSV_FIELDS,
    HISTORICAL_ASOF_OBS_AVAILABLE,
    HRRR_OPERATIONAL_CSV,
    HRRR_OPERATIONAL_CSV_FIELDS,
    OPERATIONAL_COMPARISON_PATH,
    RESULTS_DIR,
    bootstrap_clustered_event_date,
    filter_operational_checkpoint,
    load_operational_csv,
    predict_operational_calibrated,
    projected_final_high,
    _continuous_metrics,
    _prob_metrics,
)
from research.weather.replay import log_loss, multiclass_brier
from research.weather.resolution import group_markets_by_event, parse_temperature_bucket
from research.weather.shadow import (
    SHADOW_HYPOTHESIS_PATH,
    combine_equal_weight,
    load_or_register_shadow_hypothesis,
)
from research.weather.sources.knyc_history import (
    IEM_ASOS_URL,
    IEM_STATION,
    SOURCE_ID as OBS_SOURCE_ID,
    load_knyc_observations,
)
from research.weather.sources.single_run_hourly import (
    MODEL_WINDOW_BASIS,
    WINDOW_REASON_SERIES_UNAVAILABLE,
    WINDOW_STATUS_OK,
    WINDOW_STATUS_UNAVAILABLE,
    fetch_run_hourly,
    parse_hourly_series,
    remaining_day_high,
)

# v1 ended the version-C model window at America/New_York midnight; v2 ends it
# at the CLI climate-day end (midnight EST). v1 artifacts are kept as-is.
PHASE6_VERSION = "v2"
PHASE6_PREVIOUS_VERSION = "v1"

VERSION_A = "A_phase5_model_only"
VERSION_B = "B_phase5_model_max_plus_obs"
VERSION_C = "C_remaining_day_max_plus_obs"
VERSIONS = (VERSION_A, VERSION_B, VERSION_C)


def calibration_paths(version: str) -> dict[str, Path]:
    return {
        "gfs_obs_replay_csv": CALIBRATION_DIR / f"gfs_operational_replay_obs_{version}.csv",
        "hrrr_obs_replay_csv": CALIBRATION_DIR / f"hrrr_operational_replay_obs_{version}.csv",
        "gfs_obs_bridge_csv": CALIBRATION_DIR / f"gfs_operational_replay_obs_bridge_{version}.csv",
        "hrrr_obs_bridge_csv": CALIBRATION_DIR / f"hrrr_operational_replay_obs_bridge_{version}.csv",
        "asof_obs_csv": CALIBRATION_DIR / f"knyc_asof_observations_{version}.csv",
        "obs_normalized_csv": CALIBRATION_DIR / f"knyc_iem_observations_{version}.csv",
    }


def result_paths(version: str) -> dict[str, Path]:
    return {
        "coverage": RESULTS_DIR / f"phase6_obs_coverage_{version}.json",
        "comparison": RESULTS_DIR / f"phase6_operational_comparison_{version}.json",
        "shadow_eval": RESULTS_DIR / f"phase6_shadow_evaluation_{version}.json",
        "methodology": RESULTS_DIR / f"phase6_methodology_{version}.json",
    }


_CAL = calibration_paths(PHASE6_VERSION)
_RES = result_paths(PHASE6_VERSION)
GFS_OBS_REPLAY_CSV = _CAL["gfs_obs_replay_csv"]
HRRR_OBS_REPLAY_CSV = _CAL["hrrr_obs_replay_csv"]
GFS_OBS_BRIDGE_CSV = _CAL["gfs_obs_bridge_csv"]
HRRR_OBS_BRIDGE_CSV = _CAL["hrrr_obs_bridge_csv"]
ASOF_OBS_CSV = _CAL["asof_obs_csv"]
OBS_NORMALIZED_CSV = _CAL["obs_normalized_csv"]

PHASE6_COVERAGE_PATH = _RES["coverage"]
PHASE6_COMPARISON_PATH = _RES["comparison"]
PHASE6_SHADOW_EVAL_PATH = _RES["shadow_eval"]
PHASE6_METHODOLOGY_PATH = _RES["methodology"]

INTRADAY_CHECKPOINTS = tuple(c for c in CHECKPOINT_IDS if is_intraday_checkpoint(c))
OBS_EXCEEDS_FINAL_QA_F = 1.0

PHASE6_EXTRA_FIELDS = [
    "phase6_version",
    "replay_version",
    "replay_reason",
    "phase5_model_high_f",
    "remaining_window_status",
    "remaining_window_reason",
    "remaining_window_start_utc",
    "remaining_window_end_utc_exclusive",
    "remaining_window_basis",
    "remaining_window_expected_hours",
    "remaining_window_covered_hours",
    "remaining_day_model_high_f",
    "obs_status",
    "obs_reason",
    "obs_n_usable",
    "obs_n_delayed_at_checkpoint",
    "obs_newest_time_utc",
    "obs_newest_age_min",
    "obs_max_gap_min",
    "obs_binding",
    "availability_basis",
]

ASOF_CSV_FIELDS = [
    "target_date",
    "checkpoint_id",
    "checkpoint_as_of",
    "status",
    "reason",
    "observed_high_so_far_f",
    "time_of_high_utc",
    "n_usable",
    "n_delayed_at_checkpoint",
    "n_after_checkpoint",
    "newest_obs_time_utc",
    "newest_obs_available_at_utc",
    "newest_age_min",
    "max_gap_min",
    "window_start_utc",
    "window_basis",
    "availability_basis",
    "actual_high_f_qa_only",
    "obs_exceeds_final_by_gt_1f",
]

OBS_CSV_FIELDS = [
    "station_id",
    "station_raw",
    "observation_time_utc",
    "available_at_assumed_utc",
    "availability_time_utc",
    "availability_basis",
    "temperature_f",
    "units",
    "report_kind",
    "raw_metar",
    "source",
    "retrieved_at",
    "source_url",
    "cache_file",
    "response_sha256",
]

MODEL_API_NAME = {
    MODEL_GFS_OPERATIONAL_LATEST: GFS_MODEL,
    MODEL_HRRR_OPERATIONAL_LATEST: HRRR_MODEL,
}


def _ensure_dirs() -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("target_date") or ""), str(row.get("checkpoint_id") or "")


# ---------------------------------------------------------------------------
# As-of observations and remaining-day windows
# ---------------------------------------------------------------------------


def build_asof_lookup(
    valid_records: Sequence[dict[str, Any]],
    target_dates: Sequence[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    by_date = group_by_climate_date(valid_records)
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for target_date in target_dates:
        day_records = by_date.get(target_date, [])
        for checkpoint_id in INTRADAY_CHECKPOINTS:
            lookup[(target_date, checkpoint_id)] = observed_high_asof(
                day_records,
                target_date=target_date,
                checkpoint_as_of=checkpoint_as_of_utc(target_date, checkpoint_id),
            ) | {"checkpoint_id": checkpoint_id}
    return lookup


def build_window_lookup(
    phase5_rows: Sequence[dict[str, Any]],
    *,
    refresh: bool = False,
    fetcher: Callable[..., dict[str, Any]] = fetch_run_hourly,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[str, Any]]:
    """Remaining-day window per (model, target_date, checkpoint) from the selected run."""
    log = progress or (lambda _m: None)
    runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in phase5_rows:
        model = str(row.get("model") or "")
        init = str(row.get("selected_run_init") or "")
        if model and init:
            runs.setdefault((model, init), []).append(row)

    lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    summary: dict[str, Any] = {"runs_total": len(runs), "by_model": {}}
    for i, ((model, init_iso), rows) in enumerate(sorted(runs.items())):
        stats = summary["by_model"].setdefault(
            model, {"runs": 0, "ok": 0, "from_cache": 0, "errors": 0, "error_samples": []}
        )
        stats["runs"] += 1
        run_init = datetime.fromisoformat(init_iso)
        api_model = MODEL_API_NAME.get(model, model)
        result = fetcher(api_model, run_init, refresh=refresh)
        series = None
        if result.get("status") == "ok":
            stats["ok"] += 1
            if (result.get("meta") or {}).get("from_cache"):
                stats["from_cache"] += 1
            series = parse_hourly_series(result.get("payload"))
        else:
            stats["errors"] += 1
            if len(stats["error_samples"]) < 5:
                stats["error_samples"].append(
                    {"run_init": init_iso, "error": (result.get("meta") or {}).get("error")}
                )
        for row in rows:
            target_date, checkpoint_id = _key(row)
            window = remaining_day_high(
                series,
                target_date=target_date,
                checkpoint_as_of=checkpoint_as_of_utc(target_date, checkpoint_id),
            )
            if series is None:
                window["reason"] = WINDOW_REASON_SERIES_UNAVAILABLE
            lookup[(model, target_date, checkpoint_id)] = window
        if (i + 1) % 50 == 0:
            log(f"hourly series {i + 1}/{len(runs)} runs")
    return lookup, summary


# ---------------------------------------------------------------------------
# Version rows
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> Any:
    return "" if value is None else value


def _obs_fields(obs: dict[str, Any] | None) -> dict[str, Any]:
    if obs is None:
        return {
            "obs_status": "",
            "obs_reason": "",
            "obs_n_usable": "",
            "obs_n_delayed_at_checkpoint": "",
            "obs_newest_time_utc": "",
            "obs_newest_age_min": "",
            "obs_max_gap_min": "",
            "availability_basis": "",
        }
    return {
        "obs_status": obs.get("status"),
        "obs_reason": _fmt(obs.get("reason")),
        "obs_n_usable": obs.get("n_usable"),
        "obs_n_delayed_at_checkpoint": obs.get("n_delayed_at_checkpoint"),
        "obs_newest_time_utc": _fmt(obs.get("newest_obs_time_utc")),
        "obs_newest_age_min": _fmt(obs.get("newest_age_min")),
        "obs_max_gap_min": _fmt(obs.get("max_gap_min")),
        "availability_basis": AVAILABILITY_BASIS,
    }


def _finish_row(
    base: dict[str, Any],
    *,
    version: str,
    mode: str,
    reason: str | None,
    model_value: float | None,
    observed: float | None,
    actual: float | None,
) -> dict[str, Any]:
    projected = (
        projected_final_high(
            model_remaining_day_high_f=model_value, observed_high_so_far_f=observed
        )
        if model_value is not None
        else None
    )
    residual = (
        compute_residual(actual_high_f=actual, forecast_high_f=projected)
        if projected is not None and actual is not None
        else None
    )
    row = dict(base)
    row.update(
        {
            "forecast_high_f": _fmt(projected),
            "model_remaining_day_high_f": _fmt(model_value),
            "observed_high_so_far_f": _fmt(observed),
            "projected_final_high_f": _fmt(projected),
            "residual_f": _fmt(residual),
            "replay_mode": mode,
            "replay_version": version,
            "replay_reason": _fmt(reason),
            "phase6_version": PHASE6_VERSION,
            "obs_binding": (
                ""
                if observed is None or model_value is None
                else bool(observed > model_value)
            ),
            "provenance": (
                f"{base.get('provenance') or ''};phase6={PHASE6_VERSION};"
                f"replay_version={version};replay_mode={mode};"
                f"obs_availability={AVAILABILITY_BASIS}"
            ),
        }
    )
    return row


def build_version_rows(
    phase5_rows: Sequence[dict[str, Any]],
    asof_lookup: dict[tuple[str, str], dict[str, Any]],
    window_lookup: dict[tuple[str, str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (version_B_rows, version_C_rows). Phase 5 rows are not mutated.

    The final observed daily maximum is never used as observed_high_so_far.
    """
    rows_b: list[dict[str, Any]] = []
    rows_c: list[dict[str, Any]] = []
    for row in phase5_rows:
        target_date, checkpoint_id = _key(row)
        model = str(row.get("model") or "")
        intraday = is_intraday_checkpoint(checkpoint_id)
        actual = parse_float(row.get("actual_high_f"))
        p5_model = parse_float(row.get("model_remaining_day_high_f"))
        if p5_model is None:
            p5_model = parse_float(row.get("forecast_high_f"))
        obs = asof_lookup.get((target_date, checkpoint_id)) if intraday else None
        obs_ok = obs is not None and obs.get("status") == OBS_STATUS_OK
        observed = float(obs["observed_high_so_far_f"]) if obs_ok else None
        window = window_lookup.get((model, target_date, checkpoint_id)) or {
            "status": WINDOW_STATUS_UNAVAILABLE,
            "reason": WINDOW_REASON_SERIES_UNAVAILABLE,
        }
        window_ok = window.get("status") == WINDOW_STATUS_OK

        base = dict(row)
        base.update(_obs_fields(obs))
        base.update(
            {
                "phase5_model_high_f": _fmt(p5_model),
                "remaining_window_status": window.get("status"),
                "remaining_window_reason": _fmt(window.get("reason")),
                "remaining_window_start_utc": _fmt(window.get("window_start_utc")),
                "remaining_window_end_utc_exclusive": _fmt(window.get("window_end_utc_exclusive")),
                "remaining_window_basis": _fmt(window.get("window_basis")),
                "remaining_window_expected_hours": _fmt(window.get("expected_hours")),
                "remaining_window_covered_hours": _fmt(window.get("covered_hours")),
                "remaining_day_model_high_f": _fmt(window.get("model_remaining_day_high_f")),
            }
        )
        if intraday and obs is None:
            obs_reason = "obs_MISSING:no_asof_lookup"
        elif intraday and not obs_ok:
            obs_reason = f"obs_{obs.get('status')}:{obs.get('reason')}"
        else:
            obs_reason = None

        # Version B — diagnostic bridge (Phase 5 model value may include past hours).
        if not intraday:
            rows_b.append(
                _finish_row(
                    base,
                    version=VERSION_B,
                    mode=REPLAY_MODE_OBS_BRIDGE_DIAGNOSTIC,
                    reason="dminus1_checkpoint_precedes_target_day_observations",
                    model_value=p5_model,
                    observed=None,
                    actual=actual,
                )
            )
        elif obs_ok:
            rows_b.append(
                _finish_row(
                    base,
                    version=VERSION_B,
                    mode=REPLAY_MODE_OBS_BRIDGE_DIAGNOSTIC,
                    reason="phase5_full_run_target_date_max_may_include_past_hours",
                    model_value=p5_model,
                    observed=observed,
                    actual=actual,
                )
            )
        else:
            rows_b.append(
                _finish_row(
                    base,
                    version=VERSION_B,
                    mode=REPLAY_MODE_MODEL_ONLY,
                    reason=obs_reason,
                    model_value=p5_model,
                    observed=None,
                    actual=actual,
                )
            )

        # Version C — Phase 6 primary.
        if not window_ok:
            rows_c.append(
                _finish_row(
                    base,
                    version=VERSION_C,
                    mode=REPLAY_MODE_UNAVAILABLE,
                    reason=f"model_window_unavailable:{window.get('reason')}"
                    + (f";{obs_reason}" if obs_reason else ""),
                    model_value=None,
                    observed=observed,
                    actual=actual,
                )
            )
            continue
        remaining = float(window["model_remaining_day_high_f"])
        if not intraday:
            rows_c.append(
                _finish_row(
                    base,
                    version=VERSION_C,
                    mode=REPLAY_MODE_FULL_OPERATIONAL,
                    reason=None,
                    model_value=remaining,
                    observed=None,
                    actual=actual,
                )
            )
        elif obs_ok:
            rows_c.append(
                _finish_row(
                    base,
                    version=VERSION_C,
                    mode=REPLAY_MODE_FULL_OPERATIONAL,
                    reason=None,
                    model_value=remaining,
                    observed=observed,
                    actual=actual,
                )
            )
        else:
            rows_c.append(
                _finish_row(
                    base,
                    version=VERSION_C,
                    mode=REPLAY_MODE_MODEL_ONLY,
                    reason=obs_reason,
                    model_value=remaining,
                    observed=None,
                    actual=actual,
                )
            )
    return rows_b, rows_c


def eligible_keys(rows_c: Sequence[dict[str, Any]]) -> set[tuple[str, str]]:
    """(target_date, checkpoint) where BOTH GFS and HRRR version-C rows are FULL."""
    full: dict[tuple[str, str], set[str]] = {}
    for row in rows_c:
        if row.get("replay_mode") != REPLAY_MODE_FULL_OPERATIONAL:
            continue
        if parse_float(row.get("residual_f")) is None:
            continue
        full.setdefault(_key(row), set()).add(str(row.get("model")))
    need = {MODEL_GFS_OPERATIONAL_LATEST, MODEL_HRRR_OPERATIONAL_LATEST}
    return {k for k, models in full.items() if need <= models}


def restrict_rows(
    rows: Sequence[dict[str, Any]], keys: set[tuple[str, str]]
) -> list[dict[str, Any]]:
    return [r for r in rows if _key(r) in keys]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _buckets_for_event(
    markets_by_event: dict[str, list[dict[str, Any]]], event_ticker: str
) -> list[Any]:
    return [
        b
        for m in markets_by_event.get(event_ticker) or []
        if (b := parse_temperature_bucket(m)) is not None
    ]


def evaluate_version_checkpoint(
    *,
    gfs_rows: Sequence[dict[str, Any]],
    hrrr_rows: Sequence[dict[str, Any]],
    markets_by_event: dict[str, list[dict[str, Any]]],
    checkpoint_id: str,
    regime: str = REGIME_NWS_CLI_KNYC,
) -> tuple[list[dict[str, Any]], Counter]:
    """Walk-forward GFS / HRRR / shadow on shared dates; returns (paired, drop_reasons).

    Residual pools: same model + checkpoint + regime, target_date < T, drawn only
    from the rows passed in (callers pass one version's eligible rows).
    """
    drops: Counter = Counter()
    gfs_cp = filter_operational_checkpoint(
        gfs_rows, model=MODEL_GFS_OPERATIONAL_LATEST, checkpoint_id=checkpoint_id, regime=regime
    )
    hrrr_cp = filter_operational_checkpoint(
        hrrr_rows, model=MODEL_HRRR_OPERATIONAL_LATEST, checkpoint_id=checkpoint_id, regime=regime
    )
    gfs_by_date = {str(r["target_date"]): r for r in gfs_cp}
    hrrr_by_date = {str(r["target_date"]): r for r in hrrr_cp}
    drops["missing_hrrr_row"] += len(set(gfs_by_date) - set(hrrr_by_date))
    drops["missing_gfs_row"] += len(set(hrrr_by_date) - set(gfs_by_date))

    paired: list[dict[str, Any]] = []
    for date in sorted(set(gfs_by_date) & set(hrrr_by_date)):
        g = gfs_by_date[date]
        h = hrrr_by_date[date]
        event_ticker = str(g.get("event_ticker") or h.get("event_ticker") or "")
        buckets = _buckets_for_event(markets_by_event, event_ticker)
        if not buckets:
            drops["no_parseable_buckets"] += 1
            continue
        gfs_fc = parse_float(g.get("forecast_high_f"))
        hrrr_fc = parse_float(h.get("forecast_high_f"))
        if gfs_fc is None or hrrr_fc is None:
            drops["missing_forecast"] += 1
            continue
        gfs_prior = [r for r in gfs_cp if str(r["target_date"]) < date]
        hrrr_prior = [r for r in hrrr_cp if str(r["target_date"]) < date]
        gfs_pred = predict_operational_calibrated(
            forecast_high=gfs_fc, prior_rows=gfs_prior, target_date=date, buckets=buckets
        )
        hrrr_pred = predict_operational_calibrated(
            forecast_high=hrrr_fc, prior_rows=hrrr_prior, target_date=date, buckets=buckets
        )
        if gfs_pred["status"] != "OK" or hrrr_pred["status"] != "OK":
            if gfs_pred["status"] != "OK":
                drops[f"gfs_{gfs_pred['status']}"] += 1
            if hrrr_pred["status"] != "OK":
                drops[f"hrrr_{hrrr_pred['status']}"] += 1
            continue
        shadow = combine_equal_weight(
            gfs_probs=gfs_pred["probabilities"],
            hrrr_probs=hrrr_pred["probabilities"],
            gfs_buckets=buckets,
            hrrr_buckets=buckets,
            event_ticker=event_ticker,
        )
        if shadow["status"] != SHADOW_STATUS_AVAILABLE:
            drops[f"shadow_{shadow.get('unavailable_reason') or shadow['status']}"] += 1

        winner = str(g.get("winning_bucket_label") or "")
        actual = float(g["actual_high_f"])
        shadow_probs = shadow.get("probabilities")
        rec: dict[str, Any] = {
            "target_date": date,
            "event_ticker": event_ticker,
            "checkpoint_id": checkpoint_id,
            "gfs_replay_mode": g.get("replay_mode"),
            "hrrr_replay_mode": h.get("replay_mode"),
            "gfs_forecast_high_f": gfs_fc,
            "hrrr_forecast_high_f": hrrr_fc,
            "actual_high_f": actual,
            "gfs_error": actual - gfs_fc,
            "hrrr_error": actual - hrrr_fc,
            "winning_bucket_label": winner,
            "gfs_residual_n": gfs_pred["residual_n"],
            "hrrr_residual_n": hrrr_pred["residual_n"],
            "gfs_probs": gfs_pred["probabilities"],
            "hrrr_probs": hrrr_pred["probabilities"],
            "shadow_probs": shadow_probs,
            "shadow_status": shadow["status"],
            "gfs_brier": multiclass_brier(gfs_pred["probabilities"], winner),
            "hrrr_brier": multiclass_brier(hrrr_pred["probabilities"], winner),
            "gfs_log_loss": log_loss(gfs_pred["probabilities"], winner),
            "hrrr_log_loss": log_loss(hrrr_pred["probabilities"], winner),
            "shadow_brier": multiclass_brier(shadow_probs, winner) if shadow_probs else None,
            "shadow_log_loss": log_loss(shadow_probs, winner) if shadow_probs else None,
        }
        rec["delta_brier_hrrr"] = rec["hrrr_brier"] - rec["gfs_brier"]
        rec["delta_brier_shadow"] = (
            rec["shadow_brier"] - rec["gfs_brier"] if rec["shadow_brier"] is not None else None
        )
        rec["delta_log_loss_shadow"] = (
            rec["shadow_log_loss"] - rec["gfs_log_loss"]
            if rec["shadow_log_loss"] is not None
            else None
        )
        paired.append(rec)
    return paired, drops


def evaluate_version(
    *,
    gfs_rows: Sequence[dict[str, Any]],
    hrrr_rows: Sequence[dict[str, Any]],
    markets_by_event: dict[str, list[dict[str, Any]]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, int]]]:
    paired_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    drops_by_cp: dict[str, dict[str, int]] = {}
    for checkpoint_id in CHECKPOINT_IDS:
        paired, drops = evaluate_version_checkpoint(
            gfs_rows=gfs_rows,
            hrrr_rows=hrrr_rows,
            markets_by_event=markets_by_event,
            checkpoint_id=checkpoint_id,
        )
        drops_by_cp[checkpoint_id] = dict(drops)
        for rec in paired:
            paired_by_key[_key(rec)] = rec
    return paired_by_key, drops_by_cp


def metrics_block(paired: Sequence[dict[str, Any]]) -> dict[str, Any]:
    paired = list(paired)
    with_shadow = [p for p in paired if p.get("shadow_probs")]
    return {
        "n": len(paired),
        "n_dates": len({p["target_date"] for p in paired}),
        "probabilistic": {
            "gfs": _prob_metrics(paired, probs_key="gfs_probs"),
            "hrrr": _prob_metrics(paired, probs_key="hrrr_probs"),
            "shadow": _prob_metrics(with_shadow, probs_key="shadow_probs"),
        },
        "point": {
            "gfs": _continuous_metrics([p["gfs_error"] for p in paired]),
            "hrrr": _continuous_metrics([p["hrrr_error"] for p in paired]),
            "shadow": "not_applicable: preregistered shadow is a bucket distribution only",
        },
        "paired_delta_brier": {
            "hrrr_minus_gfs": bootstrap_clustered_event_date(
                paired, value_key="delta_brier_hrrr"
            ),
            "shadow_minus_gfs": bootstrap_clustered_event_date(
                with_shadow, value_key="delta_brier_shadow"
            ),
        },
        "paired_delta_log_loss": {
            "shadow_minus_gfs": bootstrap_clustered_event_date(
                with_shadow, value_key="delta_log_loss_shadow"
            ),
        },
    }


def _scopes(keys: Sequence[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    scopes: dict[str, list[tuple[str, str]]] = {
        cid: [k for k in keys if k[1] == cid] for cid in CHECKPOINT_IDS
    }
    scopes["pooled_intraday"] = [k for k in keys if k[1] in INTRADAY_CHECKPOINTS]
    scopes["pooled_all"] = list(keys)
    return scopes


def common_cohort_keys(
    paired_by_version: dict[str, dict[tuple[str, str], dict[str, Any]]],
) -> list[tuple[str, str]]:
    """Pairs scored OK for GFS, HRRR, and shadow in every version."""
    sets = [
        {k for k, rec in paired.items() if rec.get("shadow_probs")}
        for paired in paired_by_version.values()
    ]
    if not sets:
        return []
    return sorted(set.intersection(*sets))


def transition_deltas(
    paired_by_version: dict[str, dict[tuple[str, str], dict[str, Any]]],
    keys: Sequence[tuple[str, str]],
    *,
    src: str,
    dst: str,
) -> dict[str, Any]:
    """Paired Brier change dst − src per model on identical keys (forecast change only)."""
    out: dict[str, Any] = {}
    for model in ("gfs", "hrrr", "shadow"):
        obs = []
        for k in keys:
            a = paired_by_version[src][k].get(f"{model}_brier")
            b = paired_by_version[dst][k].get(f"{model}_brier")
            if a is None or b is None:
                continue
            obs.append({"target_date": k[0], "delta": float(b) - float(a)})
        out[model] = bootstrap_clustered_event_date(obs, value_key="delta")
    return out


def _reason_category(reason: Any) -> str:
    """Stable reason label without per-row numeric detail."""
    text = str(reason or "")
    if not text:
        return "none"
    parts = []
    for part in text.split(";"):
        parts.append(part if part.startswith("model_window") else part.split(":")[0])
    return ";".join(parts)


def _count_modes(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for model in (MODEL_GFS_OPERATIONAL_LATEST, MODEL_HRRR_OPERATIONAL_LATEST):
        per_cp: dict[str, Any] = {}
        for cid in CHECKPOINT_IDS:
            subset = [r for r in rows if r.get("model") == model and r.get("checkpoint_id") == cid]
            per_cp[cid] = {
                "n": len(subset),
                "replay_modes": dict(Counter(str(r.get("replay_mode")) for r in subset)),
                "reasons": dict(Counter(_reason_category(r.get("replay_reason")) for r in subset)),
            }
        out[model] = per_cp
    return out


def _strip_probs(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if not k.endswith("_probs")}


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> Path:
    _ensure_dirs()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})
    return path


def _write_obs_csv(valid: Sequence[dict[str, Any]]) -> Path:
    rows = []
    for r in valid:
        rows.append(
            {
                **{k: r.get(k) for k in OBS_CSV_FIELDS},
                "observation_time_utc": r["obs_time"].isoformat(),
                "available_at_assumed_utc": r["available_at"].isoformat(),
                "availability_basis": AVAILABILITY_BASIS,
            }
        )
    return _write_csv(OBS_NORMALIZED_CSV, rows, OBS_CSV_FIELDS)


def methodology_payload(hyp: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "phase6_methodology",
        "phase6_version": PHASE6_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision_status": [DECISION_RESEARCH_ONLY, DECISION_NO_BET],
        "incumbent": "calibrated_gfs",
        "model_combination_policy_live": MODEL_COMBINATION_POLICY,
        "shadow_candidate_id": SHADOW_CANDIDATE_ID,
        "shadow_registered_at": hyp.get("registered_at"),
        "shadow_unchanged": "0.5 * P_gfs + 0.5 * P_hrrr per canonical bucket (frozen V1)",
        "clinyc_transfer_status": "unchanged (experimental; not validated)",
        "not_added": ["kalshi_prices", "trading_signals", "bet_sizing", "learned_weights", "nbm", "new_models"],
        "observation_source": {
            "source": OBS_SOURCE_ID,
            "url": IEM_ASOS_URL,
            "station_query": IEM_STATION,
            "station_id": "KNYC",
            "report_types": ["routine METAR (3)", "SPECI (4)"],
            "temperature_field": "tmpf (degF, as published by IEM from the METAR)",
            "never_derived_from_cli_daily_high": True,
            "six_hour_max_groups_used": False,
            "note": (
                "Instantaneous METAR temperatures are a lower bound on the true running "
                "maximum (CLI uses continuous data); this is conservative."
            ),
        },
        "availability_assumption": {
            "rule": "available_at = observation_time + 20 min (later supplied availability wins)",
            "lag_minutes": OBS_AVAILABILITY_LAG.total_seconds() / 60.0,
            "basis": AVAILABILITY_BASIS,
            "reason": "IEM archive supplies observation time only, not publication time",
            "frozen_before_results": True,
            "consequence": "the :51 routine report is never usable at the following :00 checkpoint",
        },
        "observation_trust_rules": {
            "window": (
                "NWS CLI climate day (00:00 Local Standard Time, UTC-5, all year) of the "
                "target date through the checkpoint; during EDT 00:00-00:59 EDT reports "
                "belong to the previous climate day"
            ),
            "window_basis": OBS_WINDOW_BASIS,
            "window_rationale": (
                "nws_cli_knyc settles on the CLI daily maximum, which is computed over the "
                "LST climate day; an America/New_York-midnight window admitted 00:xx EDT "
                "reports above the final CLI high on 2 dates in the first run"
            ),
            "max_newest_age_min": OBS_MAX_NEWEST_AGE_MIN,
            "max_gap_min_including_midnight_to_first": OBS_MAX_GAP_MIN,
            "rejections": [
                "wrong station",
                "invalid timestamp",
                "missing/invalid temperature",
                "out of range (-40..130 F)",
                "non-F units",
                "conflicting duplicates (all dropped)",
                "exact duplicates (collapsed)",
                "not yet available at checkpoint (future)",
            ],
        },
        "model_window": {
            "version_A_B": "Phase 5 value: selected run's max over the whole NY target date (GFS) "
            "or over target-date hours from run init (same-day HRRR); may include past hours",
            "version_C": "max hourly forecast at valid times in [checkpoint, next midnight EST "
            "after the target climate date) from the same selected run; start and end come from "
            "climate_day_bounds_utc, end exclusive; every hour must be present with a value or "
            "the row is REPLAY_UNAVAILABLE (never a partial max, never a Phase 5 fallback)",
            "window_basis": MODEL_WINDOW_BASIS,
            "shared_with_observation_window": (
                "observation and model windows both use the NWS CLI climate day "
                "(midnight EST to midnight EST all year); during EDT this includes 00:00 EDT "
                "of the next calendar date and excludes 00:00 EDT of the target date"
            ),
            "completeness_reasons": {
                "final_expected_timestamp_absent": "run_horizon_ends_before_end_of_target_date",
                "timestamp_present_with_null_temperature": "missing_or_null_hours_in_remaining_window",
            },
            "supersedes": {
                "version": PHASE6_PREVIOUS_VERSION,
                "defect": (
                    "v1 ended the model window at the next America/New_York calendar midnight, "
                    "which during EDT omitted the final 00:00-00:59 EDT hour of the CLI climate "
                    "day (and for pre-day checkpoints included the target date's 00:00 EDT hour, "
                    "which belongs to the previous climate day)"
                ),
                "v1_artifacts_retained": True,
            },
            "run_selection_and_latency": {
                "inherited_from": "Phase 5 CSVs (unchanged)",
                "gfs_latency_hours": GFS_PUBLICATION_LATENCY.total_seconds() / 3600.0,
                "hrrr_latency_hours": HRRR_PUBLICATION_LATENCY.total_seconds() / 3600.0,
            },
            "hourly_source": "open-meteo single-runs API, timezone=GMT, cached raw per run",
        },
        "projected_final_high": "max(observed_high_so_far_f, model_remaining_day_high_f)",
        "replay_labels": {
            REPLAY_MODE_FULL_OPERATIONAL: "version C row with trusted as-of obs (intraday) and full remaining-day window",
            REPLAY_MODE_OBS_BRIDGE_DIAGNOSTIC: "version B row (never labeled full operational)",
            REPLAY_MODE_MODEL_ONLY: "no trustworthy as-of observations",
            REPLAY_MODE_UNAVAILABLE: "selected run lacks remaining-day coverage (version C)",
            "phase5_HISTORICAL_ASOF_OBS_AVAILABLE_flag": HISTORICAL_ASOF_OBS_AVAILABLE,
            "assignment": "per row; the Phase 5 global flag is not flipped",
        },
        "calibration": {
            "residual_convention": RESIDUAL_CONVENTION,
            "pool": "same model + same checkpoint + same regime + target_date < T, within one version",
            "thresholds": {
                "MIN_RUN_HISTORY": MIN_RUN_HISTORY,
                "MIN_N_MONTH": MIN_N_MONTH,
                "MIN_N_SEASON": MIN_N_SEASON,
                "MIN_N_RUN": MIN_N_RUN,
            },
            "versions_recalibrated_separately": True,
        },
        "cohorts": {
            "eligible_set": "(target_date, checkpoint) where GFS and HRRR version-C rows are both FULL_OPERATIONAL_REPLAY",
            "version_pools": "A, B, C each restricted to the eligible set, so pool membership is identical",
            "common_cohort": "eligible pairs scored OK for GFS, HRRR and shadow in A, B and C",
            "own_cohorts": "A on all Phase 5 rows; C on its own FULL rows — reported for cohort-change context",
        },
        "bootstrap": {
            "unit": "target_date cluster (all checkpoints of a date resampled together)",
            "n_boot": BOOTSTRAP_N,
            "seed": BOOTSTRAP_SEED,
            "interval": "95% percentile",
        },
        "interpretation_rules": [
            "A→B = effect of adding as-of observations under the old model window",
            "B→C = effect of correcting the model window to remaining-day hours",
            "own-cohort vs common-cohort differences are cohort changes, not forecast changes",
            "an interval excluding zero is not validation; the shadow remains shadow-only",
        ],
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_phase6_obs_replay(
    markets: list[dict[str, Any]],
    *,
    refresh: bool = False,
    progress: Callable[[str], None] | None = None,
    obs_loader: Callable[..., tuple[list[dict[str, Any]], list[dict[str, Any]]]] = load_knyc_observations,
    window_fetcher: Callable[..., dict[str, Any]] = fetch_run_hourly,
    gfs_phase5_rows: list[dict[str, Any]] | None = None,
    hrrr_phase5_rows: list[dict[str, Any]] | None = None,
    write_outputs: bool = True,
) -> dict[str, Any]:
    log = progress or (lambda _m: None)
    _ensure_dirs()
    hyp_sha_before = _sha256(SHADOW_HYPOTHESIS_PATH)
    hyp = load_or_register_shadow_hypothesis()

    gfs_a = gfs_phase5_rows if gfs_phase5_rows is not None else load_operational_csv(GFS_OPERATIONAL_CSV)
    hrrr_a = hrrr_phase5_rows if hrrr_phase5_rows is not None else load_operational_csv(HRRR_OPERATIONAL_CSV)
    if not gfs_a or not hrrr_a:
        raise RuntimeError("Phase 5 operational CSVs missing; run --phase5-operational first")
    phase5_rows = list(gfs_a) + list(hrrr_a)
    target_dates = sorted({str(r["target_date"]) for r in phase5_rows})

    # Observations
    start = datetime.strptime(target_dates[0], "%Y-%m-%d").date() - timedelta(days=1)
    end = datetime.strptime(target_dates[-1], "%Y-%m-%d").date() + timedelta(days=1)
    log(f"Loading KNYC observations {start} to {end}...")
    raw_records, fetch_log = obs_loader(start, end, refresh=refresh, progress=progress)
    valid, rejections = validate_observations(raw_records)
    asof_lookup = build_asof_lookup(valid, target_dates)

    # Remaining-day windows from the already-selected runs
    log("Loading hourly series for selected runs...")
    window_lookup, window_fetch = build_window_lookup(
        phase5_rows, refresh=refresh, fetcher=window_fetcher, progress=progress
    )

    rows_b, rows_c = build_version_rows(phase5_rows, asof_lookup, window_lookup)
    keys_e = eligible_keys(rows_c)

    def _split(rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            [r for r in rows if r.get("model") == MODEL_GFS_OPERATIONAL_LATEST],
            [r for r in rows if r.get("model") == MODEL_HRRR_OPERATIONAL_LATEST],
        )

    gfs_b, hrrr_b = _split(rows_b)
    gfs_c, hrrr_c = _split(rows_c)

    markets_by_event = group_markets_by_event(markets)

    # Versions on the identical eligible set (pools restricted identically).
    log("Evaluating versions A/B/C on the eligible set...")
    paired_by_version: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    drops_by_version: dict[str, dict[str, dict[str, int]]] = {}
    for version, (g_rows, h_rows) in {
        VERSION_A: (gfs_a, hrrr_a),
        VERSION_B: (gfs_b, hrrr_b),
        VERSION_C: (gfs_c, hrrr_c),
    }.items():
        paired, drops = evaluate_version(
            gfs_rows=restrict_rows(g_rows, keys_e),
            hrrr_rows=restrict_rows(h_rows, keys_e),
            markets_by_event=markets_by_event,
        )
        paired_by_version[version] = paired
        drops_by_version[version] = drops

    cohort = common_cohort_keys(paired_by_version)
    scopes = _scopes(cohort)

    # Own cohorts for cohort-change context.
    log("Evaluating own cohorts (A all Phase 5 rows; C own FULL rows)...")
    paired_a_own, drops_a_own = evaluate_version(
        gfs_rows=gfs_a, hrrr_rows=hrrr_a, markets_by_event=markets_by_event
    )
    full_c = [r for r in rows_c if r.get("replay_mode") == REPLAY_MODE_FULL_OPERATIONAL]
    g_cf, h_cf = _split(full_c)
    paired_c_own, drops_c_own = evaluate_version(
        gfs_rows=g_cf, hrrr_rows=h_cf, markets_by_event=markets_by_event
    )

    def _own_block(paired: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
        keys = sorted(k for k, rec in paired.items() if rec.get("shadow_probs"))
        return {
            scope: metrics_block([paired[k] for k in ks])
            for scope, ks in _scopes(keys).items()
        }

    common_results = {
        version: {
            scope: metrics_block([paired_by_version[version][k] for k in ks])
            for scope, ks in scopes.items()
        }
        for version in VERSIONS
    }
    transitions = {
        scope: {
            "A_to_B_obs_effect_old_window": transition_deltas(
                paired_by_version, ks, src=VERSION_A, dst=VERSION_B
            ),
            "B_to_C_window_correction": transition_deltas(
                paired_by_version, ks, src=VERSION_B, dst=VERSION_C
            ),
            "A_to_C_total": transition_deltas(
                paired_by_version, ks, src=VERSION_A, dst=VERSION_C
            ),
        }
        for scope, ks in scopes.items()
    }

    phase5_artifact = cache.read_json(OPERATIONAL_COMPARISON_PATH, default={}) or {}
    phase5_shared_n = {
        cid: ((phase5_artifact.get("per_checkpoint") or {}).get(cid) or {}).get("shared_n")
        for cid in CHECKPOINT_IDS
    }
    a_own_block = _own_block(paired_a_own)
    c_own_block = _own_block(paired_c_own)
    cohort_context = {
        "A_own_cohort_all_phase5_rows": a_own_block,
        "C_own_cohort_full_rows": c_own_block,
        "A_own_scored_n_incl_shadow_unavailable": {
            cid: sum(1 for k in paired_a_own if k[1] == cid) for cid in CHECKPOINT_IDS
        },
        "phase5_artifact_shared_n_crosscheck": phase5_shared_n,
        "drop_reasons": {"A_own": drops_a_own, "C_own": drops_c_own},
        "note": (
            "A own-cohort vs A common-cohort differences are cohort changes. "
            "A→B→C on the common cohort are forecast changes."
        ),
    }

    # Coverage / QA
    actual_by_date = {
        str(r["target_date"]): parse_float(r.get("actual_high_f")) for r in phase5_rows
    }
    asof_rows = []
    for (d, cid), res in sorted(asof_lookup.items()):
        actual = actual_by_date.get(d)
        obs_high = res.get("observed_high_so_far_f")
        exceeds = (
            obs_high is not None and actual is not None and obs_high > actual + OBS_EXCEEDS_FINAL_QA_F
        )
        asof_rows.append(
            {
                **res,
                "actual_high_f_qa_only": actual,
                "obs_exceeds_final_by_gt_1f": exceeds,
            }
        )

    per_cp_obs: dict[str, Any] = {}
    for cid in INTRADAY_CHECKPOINTS:
        subset = [r for r in asof_rows if r["checkpoint_id"] == cid]
        per_cp_obs[cid] = {
            "n_dates": len(subset),
            "status_counts": dict(Counter(r["status"] for r in subset)),
            "newest_usable_age_min": summarize_ages(
                [r["newest_age_min"] for r in subset if r.get("newest_age_min") is not None]
            ),
            "n_usable_obs": summarize_ages([r["n_usable"] for r in subset]),
            "delayed_reports_excluded_total": sum(r["n_delayed_at_checkpoint"] for r in subset),
            "dates_with_delayed_report_excluded": sum(1 for r in subset if r["n_delayed_at_checkpoint"]),
            "qa_obs_exceeds_final_by_gt_1f": sum(1 for r in subset if r["obs_exceeds_final_by_gt_1f"]),
            "obs_binding_rate_version_C": _binding_rate(rows_c, cid),
            "obs_binding_rate_version_B": _binding_rate(rows_b, cid),
        }
    obs_dates_with_any = sorted(group_by_climate_date(valid).keys())
    window_cov: dict[str, Any] = {}
    for model in (MODEL_GFS_OPERATIONAL_LATEST, MODEL_HRRR_OPERATIONAL_LATEST):
        window_cov[model] = {
            cid: {
                "status_counts": dict(
                    Counter(
                        str(r.get("remaining_window_status"))
                        for r in rows_c
                        if r.get("model") == model and r.get("checkpoint_id") == cid
                    )
                ),
                "unavailable_reasons": dict(
                    Counter(
                        str(r.get("remaining_window_reason"))
                        for r in rows_c
                        if r.get("model") == model
                        and r.get("checkpoint_id") == cid
                        and r.get("remaining_window_status") != WINDOW_STATUS_OK
                    )
                ),
            }
            for cid in CHECKPOINT_IDS
        }

    eligible_by_cp = {cid: sum(1 for k in keys_e if k[1] == cid) for cid in CHECKPOINT_IDS}
    cohort_by_cp = {cid: len(scopes[cid]) for cid in CHECKPOINT_IDS}
    intraday_computable = len(scopes["pooled_intraday"]) > 0

    coverage = {
        "schema": "phase6_obs_coverage",
        "phase6_version": PHASE6_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "observation_source": {
            "source": OBS_SOURCE_ID,
            "url": IEM_ASOS_URL,
            "station_query": IEM_STATION,
            "requested_range": [start.isoformat(), end.isoformat()],
            "fetch_log": fetch_log,
            "raw_records": len(raw_records),
            "valid_records": len(valid),
            "rejections": dict(rejections),
            "climate_dates_with_valid_obs": len(obs_dates_with_any),
            "window_basis": OBS_WINDOW_BASIS,
            "target_dates": len(target_dates),
            "target_dates_without_any_obs": [d for d in target_dates if d not in set(obs_dates_with_any)],
            "availability_basis": AVAILABILITY_BASIS,
        },
        "per_checkpoint_observations": per_cp_obs,
        "model_window_fetch": window_fetch,
        "model_window_coverage_version_C": window_cov,
        "replay_labels": {
            VERSION_A: _count_modes(phase5_rows),
            VERSION_B: _count_modes(rows_b),
            VERSION_C: _count_modes(rows_c),
        },
        "eligible_pairs_by_checkpoint": eligible_by_cp,
        "common_cohort_by_checkpoint": cohort_by_cp,
        "eligible_set_drop_reasons_by_version": drops_by_version,
        "intraday_result_computable": intraday_computable,
    }

    comparison = {
        "schema": "phase6_operational_comparison",
        "phase6_version": PHASE6_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision_status": [DECISION_RESEARCH_ONLY, DECISION_NO_BET],
        "versions": {
            VERSION_A: "original Phase 5 (model-only intraday), recalibrated on eligible set",
            VERSION_B: "Phase 5 model max + as-of observations (diagnostic bridge)",
            VERSION_C: "remaining-day model max + as-of observations (Phase 6 primary)",
        },
        "common_cohort": {
            "n_pairs": len(cohort),
            "n_dates": len({k[0] for k in cohort}),
            "by_checkpoint": cohort_by_cp,
            "results": common_results,
            "transitions": transitions,
        },
        "cohort_context": cohort_context,
        "intraday_result_computable": intraday_computable,
        "limitations": [
            "Observation availability is an assumed 20-minute lag; IEM supplies no publication time.",
            "METAR instantaneous temperatures can understate the true running maximum.",
            "Version B's model value may include hours already past; it is diagnostic only.",
            "Results are retrospective replays; the shadow is not validated by this analysis.",
        ],
    }

    shadow_eval = {
        "schema": "phase6_shadow_evaluation",
        "phase6_version": PHASE6_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate": hyp,
        "status": "SHADOW ONLY",
        "promotion": False,
        "hypothesis_file_sha256_before": hyp_sha_before,
        "hypothesis_file_sha256_after": _sha256(SHADOW_HYPOTHESIS_PATH),
        "per_version_common_cohort": {
            version: {
                scope: {
                    "n": block["n"],
                    "shadow": block["probabilistic"]["shadow"],
                    "gfs": block["probabilistic"]["gfs"],
                    "shadow_minus_gfs_brier": block["paired_delta_brier"]["shadow_minus_gfs"],
                    "shadow_minus_gfs_log_loss": block["paired_delta_log_loss"]["shadow_minus_gfs"],
                }
                for scope, block in common_results[version].items()
            }
            for version in VERSIONS
        },
    }

    methodology = methodology_payload(hyp)

    paths: dict[str, str] = {}
    if write_outputs:
        extra = PHASE6_EXTRA_FIELDS
        _write_csv(GFS_OBS_BRIDGE_CSV, gfs_b, GFS_OPERATIONAL_CSV_FIELDS + extra)
        _write_csv(HRRR_OBS_BRIDGE_CSV, hrrr_b, HRRR_OPERATIONAL_CSV_FIELDS + extra)
        _write_csv(GFS_OBS_REPLAY_CSV, gfs_c, GFS_OPERATIONAL_CSV_FIELDS + extra)
        _write_csv(HRRR_OBS_REPLAY_CSV, hrrr_c, HRRR_OPERATIONAL_CSV_FIELDS + extra)
        _write_csv(ASOF_OBS_CSV, asof_rows, ASOF_CSV_FIELDS)
        _write_obs_csv(valid)
        comparison["common_cohort"]["paired_rows"] = {
            version: [_strip_probs(paired_by_version[version][k]) for k in cohort]
            for version in VERSIONS
        }
        cache.write_json(PHASE6_COVERAGE_PATH, coverage)
        cache.write_json(PHASE6_COMPARISON_PATH, comparison)
        cache.write_json(PHASE6_SHADOW_EVAL_PATH, shadow_eval)
        cache.write_json(PHASE6_METHODOLOGY_PATH, methodology)
        paths = {
            "coverage": str(PHASE6_COVERAGE_PATH),
            "comparison": str(PHASE6_COMPARISON_PATH),
            "shadow_eval": str(PHASE6_SHADOW_EVAL_PATH),
            "methodology": str(PHASE6_METHODOLOGY_PATH),
            "gfs_obs_replay_csv": str(GFS_OBS_REPLAY_CSV),
            "hrrr_obs_replay_csv": str(HRRR_OBS_REPLAY_CSV),
            "gfs_obs_bridge_csv": str(GFS_OBS_BRIDGE_CSV),
            "hrrr_obs_bridge_csv": str(HRRR_OBS_BRIDGE_CSV),
            "asof_obs_csv": str(ASOF_OBS_CSV),
            "obs_normalized_csv": str(OBS_NORMALIZED_CSV),
        }

    return {
        "paths": paths,
        "coverage": coverage,
        "comparison": comparison,
        "shadow_eval": shadow_eval,
        "rows_b": rows_b,
        "rows_c": rows_c,
        "eligible_keys": keys_e,
        "cohort": cohort,
        "paired_by_version": paired_by_version,
    }


def _binding_rate(rows: Sequence[dict[str, Any]], checkpoint_id: str) -> dict[str, Any]:
    flags = [
        r.get("obs_binding")
        for r in rows
        if r.get("checkpoint_id") == checkpoint_id and r.get("obs_binding") not in ("", None)
    ]
    if not flags:
        return {"n": 0, "rate": None}
    return {"n": len(flags), "rate": statistics.fmean(1.0 if f else 0.0 for f in flags)}

"""Phase 4: independent HRRR evaluation + evidence methodology (no blending).

HRRR streams are NEVER pooled:
  hrrr_exact_run       — exact Single Runs initializations (fixed-cycle benchmark)
  hrrr_previous_day1   — Previous Runs API fixed ~24h lead series

Operational live policy: latest_available_by_latency (not skill-tuned).
"""

from __future__ import annotations

import csv
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from kalshi import cache
from research.weather.calibration import (
    compute_residual,
    lead_hours,
    load_calibration_csv,
    model_run_init_utc,
    nws_cli_rows,
    parse_float,
    season_for_month,
)
from research.weather.models import (
    FRESHNESS_AGING_SECONDS,
    FRESHNESS_FRESH_SECONDS,
    GFS_PUBLICATION_LATENCY,
    HRRR_FIXED_CYCLE_BENCHMARK_LABEL,
    HRRR_MODEL,
    HRRR_OPERATIONAL_SELECTION_POLICY,
    HRRR_PUBLICATION_LATENCY,
    HRRR_STREAM_EXACT,
    HRRR_STREAM_PREVIOUS_DAY1,
    MIN_RUN_HISTORY,
    MODEL_COMBINATION_POLICY,
    REGIME_NWS_CLI_KNYC,
    SNAPSHOT_SCHEMA_VERSION,
    STANDARDIZED_RUNS,
)
from research.weather.probability import (
    bucket_probabilities_from_residuals,
    select_residual_pool,
)
from research.weather.replay import log_loss, multiclass_brier
from research.weather.resolution import (
    get_event_date,
    get_outcome_label,
    group_markets_by_event,
    is_range_bucket_event,
    parse_temperature_bucket,
)
from research.weather.sources.hrrr import (
    get_hrrr_previous_day_high,
    get_hrrr_run_high,
    hrrr_available_as_of,
    is_hrrr_run_available,
)

HRRR_CALIBRATION_CSV = (
    cache.REPO_ROOT / "data" / "weather" / "calibration" / "hrrr_errors.csv"
)
HRRR_EVAL_REPORT = cache.RESULTS_ROOT / "hrrr_independent_evaluation.json"
METHODOLOGY_PATH = cache.RESULTS_ROOT / "weather_evidence_methodology.json"

# Fixed-cycle exact-run mapping (historical benchmark only — not operational policy).
# STANDARDIZED_RUNS hours → run_id label under hrrr_exact_run stream.
_EXACT_CYCLE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "run_id": "hrrr_exact_12z",
        "label": "HRRR exact previous-day 12Z (fixed-cycle benchmark)",
        "run_date_offset": -1,
        "run_hour": 12,
    },
    {
        "run_id": "hrrr_exact_18z",
        "label": "HRRR exact previous-day 18Z (fixed-cycle benchmark)",
        "run_date_offset": -1,
        "run_hour": 18,
    },
    {
        "run_id": "hrrr_exact_00z",
        "label": "HRRR exact event-day 00Z (fixed-cycle benchmark)",
        "run_date_offset": 0,
        "run_hour": 0,
    },
    {
        "run_id": "hrrr_exact_06z",
        "label": "HRRR exact event-day 06Z (fixed-cycle benchmark)",
        "run_date_offset": 0,
        "run_hour": 6,
    },
)

HRRR_CSV_FIELDS = [
    "event_ticker",
    "target_date",
    "model",
    "forecast_stream",
    "run_id",
    "run_label",
    "benchmark_kind",
    "model_run_time_utc",
    "model_available_as_of",
    "forecast_high_f",
    "actual_high_f",
    "residual_f",
    "forecast_error_f",
    "month",
    "season",
    "lead_hours",
    "winning_bucket_label",
    "settlement_source_regime",
    "source",
]


def write_methodology_report() -> Path:
    cache.ensure_dirs()
    payload = {
        "schema": "weather_evidence_methodology",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_combination_policy": MODEL_COMBINATION_POLICY,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_cadence_recommended": [
            "~24h before target day",
            "~12h before",
            "morning of event",
            "midday",
            "afternoon",
        ],
        "snapshot_cadence_note": (
            "Manual prospective checkpoints via --snapshot-live only. "
            "Never invent fake historical snapshots."
        ),
        "sources": [
            {
                "source_id": "gfs",
                "model": "ncep_gfs_global",
                "status": "ACTIVE",
                "publication_latency_hours": GFS_PUBLICATION_LATENCY.total_seconds()
                / 3600.0,
            },
            {
                "source_id": "gefs",
                "model": "ncep_gefs_seamless",
                "status": "ACTIVE",
            },
            {
                "source_id": "hrrr",
                "model": HRRR_MODEL,
                "status": "ACTIVE",
                "api": "open-meteo forecast + previous-runs + single-runs",
                "publication_latency_hours": HRRR_PUBLICATION_LATENCY.total_seconds()
                / 3600.0,
                "operational_selection_policy": HRRR_OPERATIONAL_SELECTION_POLICY,
                "forecast_streams": [
                    {
                        "forecast_stream": HRRR_STREAM_EXACT,
                        "kind": HRRR_FIXED_CYCLE_BENCHMARK_LABEL,
                        "note": (
                            "Exact Single Runs at selected 00/06/12/18Z cycles. "
                            "Historical benchmark only — residuals never pool with "
                            "previous_day1."
                        ),
                    },
                    {
                        "forecast_stream": HRRR_STREAM_PREVIOUS_DAY1,
                        "kind": "fixed_lead_previous_runs",
                        "note": (
                            "temperature_2m_previous_day1 ≈ value predicted ~24h "
                            "before each valid time. Separate residual pool."
                        ),
                    },
                ],
                "latency_rationale": (
                    "NCEP HRRR production typically completes ~60–100 min after "
                    "initialization; Open-Meteo rechunking can push public "
                    "availability toward ~2.5–3 h. Conservative research constant "
                    "of 3 h — not tuned on forecast skill."
                ),
                "latency_uncertainty": (
                    "Exact Open-Meteo operational availability varies by cycle; "
                    "3 h is intentionally conservative."
                ),
            },
            {
                "source_id": "nws_forecast",
                "model": "nws_api",
                "status": "ACTIVE",
                "note": "Live only — no dishonest historical backfill",
            },
            {
                "source_id": "nws_discussion",
                "model": "nws_afd",
                "status": "ACTIVE",
                "note": "Supporting prose evidence only — no probability adjustment",
            },
            {
                "source_id": "observations",
                "station": "KNYC",
                "status": "ACTIVE",
            },
            {
                "source_id": "nbm",
                "status": "PHASE_5_CANDIDATE",
                "note": "Not implemented in Phase 4",
            },
        ],
        "freshness_thresholds_seconds": {
            "fresh": FRESHNESS_FRESH_SECONDS,
            "aging": FRESHNESS_AGING_SECONDS,
            "stale_above": FRESHNESS_AGING_SECONDS,
        },
        "availability_assumptions": {
            "gfs": "run_init + GFS_PUBLICATION_LATENCY <= prediction_as_of",
            "hrrr": "run_init + HRRR_PUBLICATION_LATENCY <= prediction_as_of",
            "nws_forecast": "issued_at treated as available_at (live products)",
            "observations": "only timestamps <= prediction_as_of",
            "no_lookahead_gate": "available_at <= prediction_as_of (NOT valid_to)",
        },
        "research_gates": {
            "no_kalshi_prices_as_features": True,
            "no_future_weather": True,
            "no_blended_probability": True,
            "clinyc_transfer_v1_untouched": True,
            "hrrr_streams_never_pooled": True,
            "trading": "RESEARCH_ONLY / NO_BET",
        },
    }
    cache.write_json(METHODOLOGY_PATH, payload)
    return METHODOLOGY_PATH


def build_hrrr_calibration_rows(
    markets: list[dict[str, Any]],
    *,
    progress: Callable[[str], None] | None = None,
    include_previous_day1: bool = True,
    include_exact_cycles: bool = True,
) -> list[dict[str, Any]]:
    """Independent HRRR residual rows. Streams never mixed; never mix with GFS."""
    grouped = group_markets_by_event(markets)
    gfs_rows = load_calibration_csv()
    actuals: dict[str, float] = {}
    winners: dict[str, str] = {}
    regimes: dict[str, str] = {}
    for row in gfs_rows:
        if (row.get("settlement_source_regime") or "") != REGIME_NWS_CLI_KNYC:
            continue
        date = str(row.get("target_date") or "")
        actual = parse_float(row.get("actual_high_f"))
        if date and actual is not None:
            actuals[date] = actual
            winners[date] = str(row.get("winning_bucket_label") or "")
            regimes[date] = REGIME_NWS_CLI_KNYC

    rows: list[dict[str, Any]] = []
    for event_ticker, event_markets in sorted(
        grouped.items(), key=lambda item: get_event_date(item[0]) or ""
    ):
        if not is_range_bucket_event(event_markets):
            continue
        target_date = get_event_date(event_ticker)
        if not target_date:
            continue
        actual = actuals.get(target_date)
        if actual is None:
            continue
        winner = winners.get(target_date) or get_outcome_label(event_markets) or ""

        run_specs: list[dict[str, Any]] = []
        if include_previous_day1:
            run_specs.append(
                {
                    "run_id": "hrrr_previous_day1",
                    "label": "HRRR previous_day1 (~24h fixed lead)",
                    "forecast_stream": HRRR_STREAM_PREVIOUS_DAY1,
                    "benchmark_kind": "fixed_lead_previous_runs",
                    "kind": "previous_day1",
                }
            )
        if include_exact_cycles:
            for spec in _EXACT_CYCLE_SPECS:
                run_specs.append(
                    {
                        **spec,
                        "forecast_stream": HRRR_STREAM_EXACT,
                        "benchmark_kind": HRRR_FIXED_CYCLE_BENCHMARK_LABEL,
                        "kind": "exact",
                    }
                )

        for spec in run_specs:
            forecast = None
            init = None
            available = None
            source = ""
            if spec["kind"] == "previous_day1":
                forecast = get_hrrr_previous_day_high(target_date)
                # Approximate init for lead reporting only — not an exact run identity.
                init = model_run_init_utc(target_date, -1, 0)
                available = hrrr_available_as_of(init)
                source = "open-meteo:previous-runs:ncep_hrrr_conus:previous_day1"
            else:
                init = model_run_init_utc(
                    target_date, int(spec["run_date_offset"]), int(spec["run_hour"])
                )
                as_of = hrrr_available_as_of(init)
                if not is_hrrr_run_available(run_init=init, as_of=as_of):
                    continue
                forecast = get_hrrr_run_high(target_date, init, as_of=as_of)
                available = as_of
                source = "open-meteo:single-runs:ncep_hrrr_conus"

            if forecast is None or init is None:
                if progress:
                    progress(f"HRRR miss {event_ticker} {spec['run_id']}")
                continue

            residual = compute_residual(actual_high_f=actual, forecast_high_f=forecast)
            month = int(target_date[5:7])
            rows.append(
                {
                    "event_ticker": event_ticker,
                    "target_date": target_date,
                    "model": HRRR_MODEL,
                    "forecast_stream": spec["forecast_stream"],
                    "run_id": spec["run_id"],
                    "run_label": spec["label"],
                    "benchmark_kind": spec["benchmark_kind"],
                    "model_run_time_utc": init.isoformat(),
                    "model_available_as_of": available.isoformat() if available else "",
                    "forecast_high_f": forecast,
                    "actual_high_f": actual,
                    "residual_f": residual,
                    "forecast_error_f": forecast - actual,
                    "month": month,
                    "season": season_for_month(month),
                    "lead_hours": lead_hours(target_date, init),
                    "winning_bucket_label": winner,
                    "settlement_source_regime": regimes.get(
                        target_date, REGIME_NWS_CLI_KNYC
                    ),
                    "source": source,
                }
            )
            if progress:
                progress(
                    f"HRRR row {event_ticker} {spec['forecast_stream']}/"
                    f"{spec['run_id']} high={forecast}"
                )

    return rows


def write_hrrr_calibration_csv(rows: list[dict[str, Any]]) -> Path:
    HRRR_CALIBRATION_CSV.parent.mkdir(parents=True, exist_ok=True)
    with HRRR_CALIBRATION_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HRRR_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in HRRR_CSV_FIELDS})
    return HRRR_CALIBRATION_CSV


def load_hrrr_calibration_csv(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or HRRR_CALIBRATION_CSV
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def filter_hrrr_stream(
    rows: list[dict[str, Any]],
    *,
    forecast_stream: str,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Residual-pool filter: same forecast_stream (+ optional run_id) only."""
    out = [r for r in rows if (r.get("forecast_stream") or "") == forecast_stream]
    if run_id is not None:
        out = [r for r in out if r.get("run_id") == run_id]
    return out


def _error_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    residuals = [parse_float(r.get("residual_f")) for r in rows]
    residuals = [float(x) for x in residuals if x is not None]
    if not residuals:
        return {"N": 0}
    errors = [-r for r in residuals]  # forecast - actual
    return {
        "N": len(residuals),
        "bias": statistics.fmean(errors),
        "MAE": statistics.fmean(abs(e) for e in errors),
        "RMSE": math.sqrt(statistics.fmean(e * e for e in errors)),
        "residual_mean": statistics.fmean(residuals),
    }


def walkforward_hrrr_scores(
    rows: list[dict[str, Any]],
    *,
    markets_by_event: dict[str, list[dict[str, Any]]],
    forecast_stream: str,
    run_id: str,
) -> dict[str, Any]:
    """Walk-forward calibrated HRRR vs deterministic one-hot baseline.

    Residuals may only come from the SAME forecast_stream + run_id.
    """
    run_rows = filter_hrrr_stream(rows, forecast_stream=forecast_stream, run_id=run_id)
    run_rows = [
        r for r in run_rows if parse_float(r.get("residual_f")) is not None
    ]
    run_rows.sort(key=lambda r: str(r.get("target_date")))

    raw_briers: list[float] = []
    cal_briers: list[float] = []
    raw_ll: list[float] = []
    cal_ll: list[float] = []
    raw_top: list[int] = []
    cal_top: list[int] = []
    raw_pw: list[float] = []
    cal_pw: list[float] = []
    oos_dates: list[str] = []

    for idx, row in enumerate(run_rows):
        target_date = str(row["target_date"])
        forecast = parse_float(row.get("forecast_high_f"))
        winner = str(row.get("winning_bucket_label") or "")
        event_ticker = str(row.get("event_ticker") or "")
        event_markets = markets_by_event.get(event_ticker) or []
        if forecast is None or not winner or not event_markets:
            continue
        buckets = []
        for market in event_markets:
            parsed = parse_temperature_bucket(market)
            if parsed is not None:
                buckets.append(parsed)
        if not buckets:
            continue

        # Deterministic one-hot bucket baseline (NOT a calibrated distribution).
        raw_residuals = [0.0]
        raw_probs = bucket_probabilities_from_residuals(
            forecast_high=forecast,
            residuals=raw_residuals,
            buckets=buckets,
        )

        prior = [
            r
            for r in run_rows[:idx]
            if parse_float(r.get("residual_f")) is not None
            and (r.get("forecast_stream") or "") == forecast_stream
            and r.get("run_id") == run_id
        ]
        if len(prior) < MIN_RUN_HISTORY:
            continue
        pool, _level, _meta = select_residual_pool(prior, target_date=target_date)
        if len(pool) < MIN_RUN_HISTORY:
            continue
        cal_probs = bucket_probabilities_from_residuals(
            forecast_high=forecast,
            residuals=pool,
            buckets=buckets,
        )

        oos_dates.append(target_date)
        raw_briers.append(multiclass_brier(raw_probs, winner))
        cal_briers.append(multiclass_brier(cal_probs, winner))
        raw_ll.append(log_loss(raw_probs, winner))
        cal_ll.append(log_loss(cal_probs, winner))
        raw_top.append(1 if max(raw_probs, key=raw_probs.get) == winner else 0)
        cal_top.append(1 if max(cal_probs, key=cal_probs.get) == winner else 0)
        raw_pw.append(float(raw_probs.get(winner, 0.0)))
        cal_pw.append(float(cal_probs.get(winner, 0.0)))

    def _pack(briers, lls, tops, pws, *, kind: str) -> dict[str, Any]:
        if not briers:
            return {"N": 0, "kind": kind}
        return {
            "N": len(briers),
            "kind": kind,
            "mean_event_multiclass_brier": statistics.fmean(briers),
            "mean_log_loss": statistics.fmean(lls),
            "top_bucket_accuracy": statistics.fmean(tops),
            "mean_probability_on_winner": statistics.fmean(pws),
        }

    return {
        "forecast_stream": forecast_stream,
        "run_id": run_id,
        "deterministic_one_hot_bucket_baseline": _pack(
            raw_briers,
            raw_ll,
            raw_top,
            raw_pw,
            kind="deterministic one-hot bucket baseline",
        ),
        "calibrated_hrrr": _pack(
            cal_briers,
            cal_ll,
            cal_top,
            cal_pw,
            kind="calibrated residual probability distribution",
        ),
        "comparable_n": len(oos_dates),
        "oos_dates": oos_dates,
    }


def _gfs_walkforward_oos(
    gfs_rows: list[dict[str, Any]],
    *,
    markets_by_event: dict[str, list[dict[str, Any]]],
    run_id: str,
) -> dict[str, dict[str, Any]]:
    """Return {date: {forecast, cal_probs metrics bits}} for GFS OOS dates."""
    run_rows = [
        r
        for r in gfs_rows
        if r.get("run_id") == run_id and parse_float(r.get("residual_f")) is not None
    ]
    run_rows.sort(key=lambda r: str(r.get("target_date")))
    out: dict[str, dict[str, Any]] = {}

    for idx, row in enumerate(run_rows):
        target_date = str(row["target_date"])
        forecast = parse_float(row.get("forecast_high_f"))
        winner = str(row.get("winning_bucket_label") or "")
        event_ticker = str(row.get("event_ticker") or "")
        event_markets = markets_by_event.get(event_ticker) or []
        if forecast is None or not winner or not event_markets:
            continue
        buckets = []
        for market in event_markets:
            parsed = parse_temperature_bucket(market)
            if parsed is not None:
                buckets.append(parsed)
        if not buckets:
            continue
        prior = run_rows[:idx]
        if len(prior) < MIN_RUN_HISTORY:
            continue
        pool, _level, _meta = select_residual_pool(prior, target_date=target_date)
        if len(pool) < MIN_RUN_HISTORY:
            continue
        cal_probs = bucket_probabilities_from_residuals(
            forecast_high=forecast,
            residuals=pool,
            buckets=buckets,
        )
        out[target_date] = {
            "forecast_high_f": forecast,
            "actual_high_f": parse_float(row.get("actual_high_f")),
            "residual_f": parse_float(row.get("residual_f")),
            "winning_bucket_label": winner,
            "cal_probs": cal_probs,
            "event_ticker": event_ticker,
        }
    return out


def _hrrr_walkforward_oos(
    hrrr_rows: list[dict[str, Any]],
    *,
    markets_by_event: dict[str, list[dict[str, Any]]],
    forecast_stream: str,
    run_id: str,
) -> dict[str, dict[str, Any]]:
    scored = walkforward_hrrr_scores(
        hrrr_rows,
        markets_by_event=markets_by_event,
        forecast_stream=forecast_stream,
        run_id=run_id,
    )
    # Rebuild per-date calibrated probs for shared comparison.
    run_rows = filter_hrrr_stream(
        hrrr_rows, forecast_stream=forecast_stream, run_id=run_id
    )
    run_rows = [r for r in run_rows if parse_float(r.get("residual_f")) is not None]
    run_rows.sort(key=lambda r: str(r.get("target_date")))
    out: dict[str, dict[str, Any]] = {}
    oos_set = set(scored.get("oos_dates") or [])

    for idx, row in enumerate(run_rows):
        target_date = str(row["target_date"])
        if target_date not in oos_set:
            continue
        forecast = parse_float(row.get("forecast_high_f"))
        winner = str(row.get("winning_bucket_label") or "")
        event_ticker = str(row.get("event_ticker") or "")
        event_markets = markets_by_event.get(event_ticker) or []
        if forecast is None or not winner or not event_markets:
            continue
        buckets = []
        for market in event_markets:
            parsed = parse_temperature_bucket(market)
            if parsed is not None:
                buckets.append(parsed)
        if not buckets:
            continue
        prior = [
            r
            for r in run_rows[:idx]
            if (r.get("forecast_stream") or "") == forecast_stream
            and r.get("run_id") == run_id
        ]
        pool, _level, _meta = select_residual_pool(prior, target_date=target_date)
        if len(pool) < MIN_RUN_HISTORY:
            continue
        cal_probs = bucket_probabilities_from_residuals(
            forecast_high=forecast,
            residuals=pool,
            buckets=buckets,
        )
        out[target_date] = {
            "forecast_high_f": forecast,
            "actual_high_f": parse_float(row.get("actual_high_f")),
            "residual_f": parse_float(row.get("residual_f")),
            "winning_bucket_label": winner,
            "cal_probs": cal_probs,
            "event_ticker": event_ticker,
        }
    return out


def shared_gfs_hrrr_evaluation(
    hrrr_rows: list[dict[str, Any]],
    gfs_rows: list[dict[str, Any]],
    *,
    markets_by_event: dict[str, list[dict[str, Any]]],
    hrrr_forecast_stream: str,
    hrrr_run_id: str,
    gfs_run_id: str = "prev_12z",
) -> dict[str, Any]:
    """Apples-to-apples comparison: intersect dates FIRST, then score both.

    Requires: valid GFS + HRRR forecasts, settlement, and walk-forward
    calibration eligibility for BOTH models on the shared dates.
    """
    gfs_oos = _gfs_walkforward_oos(
        gfs_rows, markets_by_event=markets_by_event, run_id=gfs_run_id
    )
    hrrr_oos = _hrrr_walkforward_oos(
        hrrr_rows,
        markets_by_event=markets_by_event,
        forecast_stream=hrrr_forecast_stream,
        run_id=hrrr_run_id,
    )
    shared_dates = sorted(set(gfs_oos) & set(hrrr_oos))
    if not shared_dates:
        return {
            "shared_evaluation_n": 0,
            "hrrr_forecast_stream": hrrr_forecast_stream,
            "hrrr_run_id": hrrr_run_id,
            "gfs_run_id": gfs_run_id,
            "reason": "no overlapping OOS dates with both calibrated models",
            "policy": "descriptive_only_no_winner_selection",
        }

    gfs_subset = [gfs_oos[d] for d in shared_dates]
    hrrr_subset = [hrrr_oos[d] for d in shared_dates]

    def _cont(rows: list[dict[str, Any]]) -> dict[str, Any]:
        residuals = [float(r["residual_f"]) for r in rows if r.get("residual_f") is not None]
        if not residuals:
            return {"N": 0}
        errors = [-r for r in residuals]
        return {
            "N": len(residuals),
            "bias": statistics.fmean(errors),
            "MAE": statistics.fmean(abs(e) for e in errors),
            "RMSE": math.sqrt(statistics.fmean(e * e for e in errors)),
        }

    def _prob(rows: list[dict[str, Any]]) -> dict[str, Any]:
        briers: list[float] = []
        lls: list[float] = []
        tops: list[int] = []
        pws: list[float] = []
        for row in rows:
            probs = row.get("cal_probs") or {}
            winner = str(row.get("winning_bucket_label") or "")
            if not probs or not winner:
                continue
            briers.append(multiclass_brier(probs, winner))
            lls.append(log_loss(probs, winner))
            tops.append(1 if max(probs, key=probs.get) == winner else 0)
            pws.append(float(probs.get(winner, 0.0)))
        if not briers:
            return {"N": 0}
        return {
            "N": len(briers),
            "kind": "calibrated residual probability distribution",
            "mean_event_multiclass_brier": statistics.fmean(briers),
            "mean_log_loss": statistics.fmean(lls),
            "top_bucket_accuracy": statistics.fmean(tops),
            "mean_probability_on_winner": statistics.fmean(pws),
        }

    return {
        "shared_evaluation_n": len(shared_dates),
        "date_range": {"start": shared_dates[0], "end": shared_dates[-1]},
        "shared_dates": shared_dates,
        "hrrr_forecast_stream": hrrr_forecast_stream,
        "hrrr_run_id": hrrr_run_id,
        "gfs_run_id": gfs_run_id,
        "continuous_forecast_error": {
            "gfs": _cont(gfs_subset),
            "hrrr": _cont(hrrr_subset),
        },
        "probabilistic_forecast_quality": {
            "calibrated_gfs": _prob(gfs_subset),
            "calibrated_hrrr": _prob(hrrr_subset),
        },
        "policy": "descriptive_only_no_winner_selection",
        "note": (
            "Metrics computed ONLY on the date intersection where both models "
            "have valid forecasts, settlement, and walk-forward calibration. "
            "Do not compare unequal-N full samples as model skill."
        ),
    }


def compare_raw_point_identical_dates(
    hrrr_rows: list[dict[str, Any]],
    gfs_rows: list[dict[str, Any]],
    *,
    hrrr_forecast_stream: str,
    hrrr_run_id: str,
    gfs_run_id: str = "prev_12z",
) -> dict[str, Any]:
    """Raw continuous error on shared dates (settlement present). Not probabilistic."""
    hrrr_by_date = {
        str(r["target_date"]): r
        for r in filter_hrrr_stream(
            hrrr_rows, forecast_stream=hrrr_forecast_stream, run_id=hrrr_run_id
        )
        if parse_float(r.get("forecast_high_f")) is not None
    }
    gfs_by_date = {
        str(r["target_date"]): r
        for r in gfs_rows
        if r.get("run_id") == gfs_run_id
        and (r.get("settlement_source_regime") or "") == REGIME_NWS_CLI_KNYC
        and parse_float(r.get("forecast_high_f")) is not None
    }
    common = sorted(set(hrrr_by_date) & set(gfs_by_date))
    if not common:
        return {
            "shared_evaluation_n": 0,
            "hrrr_forecast_stream": hrrr_forecast_stream,
            "hrrr_run_id": hrrr_run_id,
            "gfs_run_id": gfs_run_id,
            "reason": "no overlapping dates with both forecasts",
        }
    return {
        "shared_evaluation_n": len(common),
        "hrrr_forecast_stream": hrrr_forecast_stream,
        "hrrr_run_id": hrrr_run_id,
        "gfs_run_id": gfs_run_id,
        "date_range": {"start": common[0], "end": common[-1]},
        "raw_gfs": _error_metrics([gfs_by_date[d] for d in common]),
        "raw_hrrr": _error_metrics([hrrr_by_date[d] for d in common]),
        "kind": "raw point-forecast continuous error on shared dates",
        "policy": "descriptive_only_no_winner_selection",
    }


def run_phase4_hrrr_evaluation(
    markets: list[dict[str, Any]],
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Build HRRR residual history + per-stream walk-forward + shared GFS compare."""
    write_methodology_report()
    rows = build_hrrr_calibration_rows(markets, progress=progress)
    path = write_hrrr_calibration_csv(rows) if rows else HRRR_CALIBRATION_CSV

    exact_rows = filter_hrrr_stream(rows, forecast_stream=HRRR_STREAM_EXACT)
    prev_rows = filter_hrrr_stream(rows, forecast_stream=HRRR_STREAM_PREVIOUS_DAY1)

    by_stream_run: dict[str, Any] = {}
    for stream in (HRRR_STREAM_EXACT, HRRR_STREAM_PREVIOUS_DAY1):
        stream_rows = filter_hrrr_stream(rows, forecast_stream=stream)
        for run_id in sorted({str(r.get("run_id")) for r in stream_rows}):
            subset = filter_hrrr_stream(
                stream_rows, forecast_stream=stream, run_id=run_id
            )
            key = f"{stream}/{run_id}"
            by_stream_run[key] = {
                **_error_metrics(subset),
                "forecast_stream": stream,
                "run_id": run_id,
                "benchmark_kind": (subset[0].get("benchmark_kind") if subset else None),
            }

    markets_by_event = group_markets_by_event(markets)
    walkforward: dict[str, Any] = {}
    for key, meta in by_stream_run.items():
        if meta.get("N", 0) >= MIN_RUN_HISTORY:
            walkforward[key] = walkforward_hrrr_scores(
                rows,
                markets_by_event=markets_by_event,
                forecast_stream=str(meta["forecast_stream"]),
                run_id=str(meta["run_id"]),
            )

    gfs_rows = nws_cli_rows(load_calibration_csv())

    # Prefer previous_day1 for shared compare when coverage allows; also report
    # one fixed-cycle exact stream if available.
    shared_comparisons: dict[str, Any] = {}
    raw_shared: dict[str, Any] = {}
    for stream, run_id in (
        (HRRR_STREAM_PREVIOUS_DAY1, "hrrr_previous_day1"),
        (HRRR_STREAM_EXACT, "hrrr_exact_12z"),
    ):
        key = f"{stream}/{run_id}"
        raw_shared[key] = compare_raw_point_identical_dates(
            rows,
            gfs_rows,
            hrrr_forecast_stream=stream,
            hrrr_run_id=run_id,
            gfs_run_id="prev_12z",
        )
        shared_comparisons[key] = shared_gfs_hrrr_evaluation(
            rows,
            gfs_rows,
            markets_by_event=markets_by_event,
            hrrr_forecast_stream=stream,
            hrrr_run_id=run_id,
            gfs_run_id="prev_12z",
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": HRRR_MODEL,
        "publication_latency_hours": HRRR_PUBLICATION_LATENCY.total_seconds() / 3600.0,
        "operational_selection_policy": HRRR_OPERATIONAL_SELECTION_POLICY,
        "fixed_cycle_benchmark_label": HRRR_FIXED_CYCLE_BENCHMARK_LABEL,
        "residual_pool_policy": (
            "HRRR residuals completely separate from GFS; "
            "exact-run and previous_day1 streams never pooled"
        ),
        "calibration_csv": str(path),
        "row_count": len(rows),
        "hrrr_exact_run_rows": len(exact_rows),
        "hrrr_previous_day1_rows": len(prev_rows),
        "exact_run_residual_pool_n_by_run": {
            k: v["N"]
            for k, v in by_stream_run.items()
            if v.get("forecast_stream") == HRRR_STREAM_EXACT
        },
        "previous_day1_residual_pool_n": len(prev_rows),
        "coverage_by_stream_run": by_stream_run,
        "walkforward": walkforward,
        "raw_point_shared_dates": raw_shared,
        "shared_gfs_hrrr_calibrated": shared_comparisons,
        "model_combination_policy": MODEL_COMBINATION_POLICY,
        "phase5_candidate": "NBM",
        # Unused by design — kept so callers see GFS STANDARDIZED_RUNS is not ops policy.
        "gfs_standardized_runs_note": (
            f"{len(STANDARDIZED_RUNS)} GFS runs are independent of HRRR operational policy"
        ),
    }
    cache.write_json(HRRR_EVAL_REPORT, report)
    return report

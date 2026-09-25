"""Framework-agnostic orchestration for weather research outputs.

Returns dataclasses and plain Python structures only.
No FastAPI / Pydantic / HTTP adapter concepts belong here.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from historical_weather import get_gfs_run_high
from kalshi import cache
from kalshi.client import KalshiClient, get_open_markets
from nws import get_today_knyc_summary
from research.weather.calibration import (
    CALIBRATION_CSV,
    ensure_weather_cache_dirs,
    lead_hours,
    load_calibration_csv,
    metrics_by_run,
    model_run_init_utc,
    nws_cli_rows,
    parse_float,
)
from research.weather.models import (
    CALIBRATION_METHOD_DIRECT_CLINYC,
    CALIBRATION_METHOD_NWS,
    CALIBRATION_METHOD_TRANSFER,
    DECISION_NO_BET,
    DECISION_RESEARCH_ONLY,
    GFS_PUBLICATION_LATENCY,
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_REQUIRED_EDGE,
    MIN_RUN_HISTORY,
    PREDICTION_STATUS_INSUFFICIENT_TARGET_REGIME_HISTORY,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    SERIES_TICKER,
    STANDARDIZED_RUNS,
    TARGET_REGIME_MATCH_DIRECT,
    TARGET_REGIME_MATCH_EXPERIMENTAL_TRANSFER,
    TARGET_REGIME_MATCH_NWS,
    TARGET_REGIME_MATCH_UNVALIDATED,
    TARGET_REGIME_MATCH_VALIDATED_TRANSFER,
    TRANSFER_STATUS_EXPERIMENTAL,
    TRANSFER_STATUS_NONE,
    TRANSFER_STATUS_VALIDATED,
    WeatherPrediction,
    WeatherTradeEvaluation,
)
from research.weather.probability import (
    choose_operational_run,
    predict_from_calibration,
)
from research.weather.resolution import (
    WeatherResolution,
    build_weather_resolution,
    get_event_date,
    group_markets_by_event,
    is_range_bucket_event,
)
from weather import get_nyc_high_ensemble, get_nyc_high_forecast


BACKTEST_REPORT_PATH = cache.RESULTS_ROOT / "weather_model_backtest.json"
PAIRS_CSV = cache.REPO_ROOT / "data" / "weather" / "calibration" / "knyc_clinyc_pairs.csv"
REGIMES_CSV = cache.RESULTS_ROOT / "kxhighny_settlement_regimes.csv"


def _market_quote(market: dict[str, Any]) -> dict[str, Any]:
    """Kalshi quote fields kept outside WeatherPrediction."""

    def _f(key: str) -> float | None:
        raw = market.get(key)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    yes_bid = _f("yes_bid_dollars")
    yes_ask = _f("yes_ask_dollars")
    mid = None
    spread = None
    if yes_bid is not None and yes_ask is not None:
        mid = (yes_bid + yes_ask) / 2.0
        spread = yes_ask - yes_bid
    return {
        "ticker": market.get("ticker"),
        "label": market.get("yes_sub_title") or market.get("title"),
        "yes_bid_dollars": yes_bid,
        "yes_ask_dollars": yes_ask,
        "mid_dollars": mid,
        "spread_dollars": spread,
        "status": market.get("status"),
        "result": market.get("result"),
    }


def _choose_calibration_target(
    live_regime: str,
    *,
    clinyc_operationally_eligible: bool = False,
    transfer_validated: bool = False,
) -> tuple[str, str, bool, str, str]:
    """Return (cal_target, method, transfer_validated, match_label, transfer_status).

    Does NOT lower methodology thresholds.
    Identity transfer remains experimental until prospective validation fires.
    Never accepts a manual transfer_validated override from callers for CLINYC —
    only the frozen hypothesis evaluation may set transfer_validated=True.
    """
    if live_regime == REGIME_NWS_CLI_KNYC:
        return (
            REGIME_NWS_CLI_KNYC,
            CALIBRATION_METHOD_NWS,
            True,
            TARGET_REGIME_MATCH_NWS,
            TRANSFER_STATUS_NONE,
        )
    if live_regime == REGIME_WEATHER_COMPANY_CLINYC:
        if clinyc_operationally_eligible:
            return (
                REGIME_WEATHER_COMPANY_CLINYC,
                CALIBRATION_METHOD_DIRECT_CLINYC,
                True,
                TARGET_REGIME_MATCH_DIRECT,
                TRANSFER_STATUS_NONE,
            )
        # Identity transfer: use NWS residuals as proxy for CLINYC settlement.
        # Validated only via prospective hypothesis — never from development pairs.
        if transfer_validated:
            return (
                REGIME_NWS_CLI_KNYC,
                CALIBRATION_METHOD_TRANSFER,
                True,
                TARGET_REGIME_MATCH_VALIDATED_TRANSFER,
                TRANSFER_STATUS_VALIDATED,
            )
        return (
            REGIME_NWS_CLI_KNYC,
            CALIBRATION_METHOD_TRANSFER,
            False,
            TARGET_REGIME_MATCH_EXPERIMENTAL_TRANSFER,
            TRANSFER_STATUS_EXPERIMENTAL,
        )
    return (
        REGIME_NWS_CLI_KNYC,
        CALIBRATION_METHOD_NWS,
        False,
        TARGET_REGIME_MATCH_UNVALIDATED,
        TRANSFER_STATUS_EXPERIMENTAL,
    )


def _count_regime_residuals(rows: list[dict[str, Any]], regime: str) -> int:
    return sum(
        1
        for r in rows
        if (r.get("settlement_source_regime") or "") == regime
        and parse_float(r.get("residual_f")) is not None
    )


def build_live_event_bundle(
    *,
    event_ticker: str,
    event_markets: list[dict[str, Any]],
    calibration_rows: list[dict[str, Any]],
    client: KalshiClient,
    series_meta: dict[str, Any] | None,
    gfs_daily: dict[str, Any],
    ensemble: dict[str, Any],
    knyc_summary: dict[str, Any] | None,
    as_of: datetime | None = None,
) -> dict[str, Any] | None:
    """Assemble one live event: resolution + prediction + market quotes + evidence."""
    if not is_range_bucket_event(event_markets):
        return None
    target_date = get_event_date(event_ticker)
    if not target_date:
        return None

    as_of = as_of or datetime.now(timezone.utc)
    event_meta: dict[str, Any] = {}
    try:
        event_meta = client.get_event(event_ticker)
        if isinstance(event_meta, dict) and "event" in event_meta:
            event_meta = event_meta["event"]
    except Exception:  # noqa: BLE001
        event_meta = {}

    resolution = build_weather_resolution(
        event_ticker=event_ticker,
        markets=event_markets,
        series_meta=series_meta,
        event_meta=event_meta if isinstance(event_meta, dict) else None,
    )

    forecasts: dict[str, float | None] = {}
    for run in STANDARDIZED_RUNS:
        forecasts[str(run["run_id"])] = get_gfs_run_high(
            target_date,
            int(run["run_date_offset"]),
            int(run["run_hour"]),
        )

    live_point = None
    raw_live = gfs_daily.get(target_date)
    if raw_live is not None:
        try:
            live_point = float(raw_live)
        except (TypeError, ValueError):
            live_point = None

    if live_point is not None:
        for run_id, value in list(forecasts.items()):
            if value is None:
                forecasts[run_id] = live_point

    run_id = choose_operational_run(
        target_date=target_date,
        prediction_as_of=as_of,
        forecasts=forecasts,
    )
    if run_id is None:
        run_id = "day_00z"
        if forecasts.get(run_id) is None and live_point is not None:
            forecasts[run_id] = live_point

    run = next(r for r in STANDARDIZED_RUNS if r["run_id"] == run_id)
    forecast_high = forecasts.get(run_id)
    if forecast_high is None:
        forecast_high = live_point
    init = model_run_init_utc(
        target_date, int(run["run_date_offset"]), int(run["run_hour"])
    )

    gefs_members = list(ensemble.get(target_date) or [])
    if not gefs_members:
        for key, members in ensemble.items():
            if str(key).startswith(target_date) or target_date in str(key):
                gefs_members = list(members)
                break

    from research.weather.transfer import (
        assess_direct_clinyc_eligibility,
        load_transfer_assessment,
    )

    eligibility = assess_direct_clinyc_eligibility(calibration_rows)
    clinyc_eligible = bool(eligibility.get("direct_clinyc_operationally_eligible"))
    # transfer_validated ONLY from frozen prospective assessment artifact — never
    # from a request parameter or UI toggle.
    transfer_art = load_transfer_assessment() or {}
    transfer_validated_flag = bool(transfer_art.get("transfer_validated"))

    cal_target, cal_method, transfer_validated, match_label, t_status = (
        _choose_calibration_target(
            resolution.settlement_source_regime,
            clinyc_operationally_eligible=clinyc_eligible,
            transfer_validated=transfer_validated_flag,
        )
    )

    clinyc_n = _count_regime_residuals(
        calibration_rows, REGIME_WEATHER_COMPANY_CLINYC
    )

    same_regime_ok = resolution.settlement_source_regime == REGIME_NWS_CLI_KNYC
    prediction = predict_from_calibration(
        event_ticker=event_ticker,
        target_date=target_date,
        as_of=as_of.isoformat(),
        run_id=run_id,
        run_label=str(run["label"])
        + (" (live GFS fallback)" if live_point is not None else ""),
        forecast_high=forecast_high,
        markets=event_markets,
        calibration_rows=calibration_rows,
        calibration_target_regime=cal_target,
        live_target_regime=resolution.settlement_source_regime,
        settlement_source_transfer_validated=(
            transfer_validated if not same_regime_ok else True
        ),
        resolution_certainty=resolution.resolution_certainty,
        lead_hours=lead_hours(target_date, init),
        gefs_members=gefs_members or None,
        calibration_method=cal_method,
        transfer_status=t_status,
        provenance={
            "operational_run_policy": (
                "latest exact GFS run with "
                "run_init + GFS_PUBLICATION_LATENCY <= prediction_as_of"
            ),
            "live_deterministic_gfs_fallback": live_point is not None,
            "live_has_observations": True,
            "historical_replay_excludes_observations": True,
            "calibration_method": cal_method,
            "transfer_status": t_status,
            "target_regime_match": match_label,
            "clinyc_residual_rows": clinyc_n,
            "direct_clinyc_dataset_exists": eligibility.get(
                "direct_clinyc_dataset_exists"
            ),
            "direct_clinyc_operationally_eligible": clinyc_eligible,
            "decision": DECISION_RESEARCH_ONLY,
            "no_bet": DECISION_NO_BET,
        },
    )

    # Direct CLINYC path with insufficient history → first-class status.
    if (
        cal_method == CALIBRATION_METHOD_DIRECT_CLINYC
        and prediction.prediction_status
        in {
            "BURN_IN",
            "INSUFFICIENT_HISTORY",
        }
    ):
        prediction = replace(
            prediction,
            prediction_status=PREDICTION_STATUS_INSUFFICIENT_TARGET_REGIME_HISTORY,
            warnings=list(prediction.warnings)
            + [
                "INSUFFICIENT_TARGET_REGIME_HISTORY: direct CLINYC residual "
                f"history below frozen MIN_N_* thresholds; "
                "thresholds not lowered; probabilities not manufactured"
            ],
            bucket_probabilities={
                k: 0.0 for k in prediction.bucket_probabilities
            },
            provenance={
                **dict(prediction.provenance),
                "target_regime_match": match_label,
                "direct_calibration_status": "INSUFFICIENT_HISTORY",
            },
        )

    # Identity transfer remains RESEARCH_ONLY / NO_BET until prospectively validated.
    if cal_method == CALIBRATION_METHOD_TRANSFER and not transfer_validated:
        prediction = replace(
            prediction,
            warnings=list(prediction.warnings)
            + [
                "RESEARCH_ONLY / NO_BET: knyc_to_clinyc_identity_transfer is "
                "experimental; settlement_source_transfer_validated=false"
            ],
            provenance={
                **dict(prediction.provenance),
                "research_gate": DECISION_RESEARCH_ONLY,
                "equivalent_decision": DECISION_NO_BET,
            },
        )

    evaluation = WeatherTradeEvaluation(
        prediction=prediction,
        decision=DECISION_RESEARCH_ONLY,
        notes=[
            "RESEARCH_ONLY — no bet sizing / ROI optimization",
            f"Equivalent decision under research gate: {DECISION_NO_BET}",
            f"MIN_REQUIRED_EDGE={MIN_REQUIRED_EDGE} (unvalidated)",
        ],
    )

    return {
        "event_ticker": event_ticker,
        "target_date": target_date,
        "resolution": resolution,
        "prediction": prediction,
        "research_status": {
            "mode": DECISION_RESEARCH_ONLY,
            "decision": evaluation.decision,
            "trade_recommendations": "DISABLED",
            "settlement_source_transfer_validated": (
                prediction.settlement_source_transfer_validated
            ),
            "transfer_status": prediction.transfer_status,
            "target_regime_match": prediction.provenance.get("target_regime_match"),
            "calibration_method": prediction.calibration_method
            or prediction.provenance.get("calibration_method"),
            "direct_clinyc_dataset_exists": eligibility.get(
                "direct_clinyc_dataset_exists"
            ),
            "direct_clinyc_operationally_eligible": clinyc_eligible,
        },
        "evidence": {
            "gfs": {
                "run_id": run_id,
                "run_label": run["label"],
                "initialized_at": init.isoformat(),
                "considered_available_at": (
                    init + GFS_PUBLICATION_LATENCY
                ).isoformat(),
                "forecast_high": forecast_high,
                "live_deterministic_high": live_point,
            },
            "gefs": {
                "member_count": prediction.gefs_member_count,
                "mean": prediction.gefs_mean,
                "median": prediction.gefs_median,
                "std": prediction.gefs_std,
                "min": prediction.gefs_min,
                "max": prediction.gefs_max,
            },
            "observations": {
                "note": (
                    "KNYC observations are supporting meteorological evidence. "
                    "Current settlement source may be CLINYC."
                ),
                "knyc": knyc_summary,
            },
        },
        "kalshi_markets": [_market_quote(m) for m in event_markets],
        "warnings": list(resolution.warnings) + list(prediction.warnings),
    }


def get_live_weather_events(
    *,
    client: KalshiClient | None = None,
) -> dict[str, Any]:
    """Current open KXHIGHNY predictions. May fetch/cache live weather + open markets."""
    ensure_weather_cache_dirs()
    client = client or KalshiClient()
    rows = load_calibration_csv()
    generated_at = datetime.now(timezone.utc).isoformat()

    series_meta: dict[str, Any] = {}
    try:
        series_meta = client.get_series(SERIES_TICKER)
    except Exception:  # noqa: BLE001
        series_meta = {}

    markets = get_open_markets(SERIES_TICKER, client=client)
    grouped = group_markets_by_event(markets)

    gfs_daily = {
        str(row.get("date")): row.get("high")
        for row in get_nyc_high_forecast()
        if row.get("date") is not None
    }
    ensemble = get_nyc_high_ensemble()
    knyc = get_today_knyc_summary()
    as_of = datetime.now(timezone.utc)

    events: list[dict[str, Any]] = []
    for event_ticker, event_markets in sorted(
        grouped.items(),
        key=lambda item: get_event_date(item[0]) or "",
    ):
        bundle = build_live_event_bundle(
            event_ticker=event_ticker,
            event_markets=event_markets,
            calibration_rows=rows,
            client=client,
            series_meta=series_meta if isinstance(series_meta, dict) else None,
            gfs_daily=gfs_daily,
            ensemble=ensemble,
            knyc_summary=knyc,
            as_of=as_of,
        )
        if bundle is not None:
            events.append(bundle)

    return {
        "generated_at": generated_at,
        "mode": DECISION_RESEARCH_ONLY,
        "calibration_csv_loaded": bool(rows),
        "events": events,
    }


def get_weather_event_detail(
    event_ticker: str,
    *,
    client: KalshiClient | None = None,
) -> dict[str, Any] | None:
    """Detailed bundle for one event ticker (open markets preferred)."""
    payload = get_live_weather_events(client=client)
    for event in payload.get("events") or []:
        if event.get("event_ticker") == event_ticker:
            return {
                "generated_at": payload["generated_at"],
                "mode": payload["mode"],
                **event,
            }
    return None


def get_model_summary() -> dict[str, Any]:
    """Read existing calibration / backtest / transfer artifacts. Never rebuilds them."""
    ensure_weather_cache_dirs()
    rows = load_calibration_csv()
    backtest = cache.read_json(BACKTEST_REPORT_PATH, default=None)
    model_summary_available = bool(rows) or isinstance(backtest, dict)

    date_range = None
    if rows:
        dates = sorted({str(r.get("target_date")) for r in rows if r.get("target_date")})
        if dates:
            date_range = {"start": dates[0], "end": dates[-1]}

    nws_rows = nws_cli_rows(rows) if rows else []
    clinyc_residual_n = _count_regime_residuals(rows, REGIME_WEATHER_COMPANY_CLINYC)

    regimes: dict[str, int] = {}
    for row in rows:
        key = str(
            row.get("target_regime")
            or row.get("settlement_source_regime")
            or "unknown"
        )
        regimes[key] = regimes.get(key, 0) + 1

    from research.weather.transfer import (
        HYPOTHESIS_PATH,
        assess_direct_clinyc_eligibility,
        load_transfer_assessment,
    )
    from research.weather.phase3 import (
        DIRECT_CLINYC_OOS_PATH,
        METHOD_COMPARE_PATH,
        PROVENANCE_AUDIT_PATH,
    )

    transfer_art = load_transfer_assessment()
    hypothesis = cache.read_json(HYPOTHESIS_PATH, default=None)
    provenance = cache.read_json(PROVENANCE_AUDIT_PATH, default=None)
    direct_oos = cache.read_json(DIRECT_CLINYC_OOS_PATH, default=None)
    method_cmp = cache.read_json(METHOD_COMPARE_PATH, default=None)
    pairs_report = cache.read_json(
        cache.RESULTS_ROOT / "knyc_clinyc_difference_report.json", default=None
    )

    pairs_n = 0
    if isinstance(pairs_report, dict):
        pairs_n = int(pairs_report.get("twc_paired_N") or pairs_report.get("N") or 0)
    if rows:
        eligibility = assess_direct_clinyc_eligibility(
            rows, paired_clinyc_event_n=pairs_n or None
        )
    elif pairs_n:
        eligibility = {
            "direct_clinyc_dataset_exists": True,
            "direct_clinyc_operationally_eligible": False,
            "prediction_status": "INSUFFICIENT_TARGET_REGIME_HISTORY",
            "by_run": {},
            "paired_clinyc_event_n": pairs_n,
        }
    else:
        eligibility = {
            "direct_clinyc_dataset_exists": False,
            "direct_clinyc_operationally_eligible": False,
            "prediction_status": "INSUFFICIENT_TARGET_REGIME_HISTORY",
            "by_run": {},
        }

    settlement_transfer: dict[str, Any]
    if transfer_art:
        settlement_transfer = {
            "available": True,
            "stale": False,
            "last_updated": None,
            "source_regime": transfer_art.get("source_regime"),
            "target_regime": transfer_art.get("target_regime"),
            "development_n": transfer_art.get("development_n")
            or transfer_art.get("observed_pair_n"),
            "prospective_n": transfer_art.get("prospective_n"),
            "prospective_target_n": transfer_art.get("prospective_target_n"),
            "exact_integer_mismatches": transfer_art.get("exact_integer_mismatches"),
            "same_bucket_mismatches": transfer_art.get("same_bucket_mismatches"),
            "exact_match_rate": transfer_art.get("exact_integer_match_rate"),
            "same_bucket_rate": transfer_art.get("same_bucket_rate"),
            "integer_mismatch_rate_observed": transfer_art.get(
                "integer_mismatch_rate_observed"
            ),
            "bucket_mismatch_rate_observed": transfer_art.get(
                "bucket_mismatch_rate_observed"
            ),
            "integer_mismatch_95_upper": transfer_art.get("integer_mismatch_95_upper"),
            "bucket_mismatch_95_upper": transfer_art.get("bucket_mismatch_95_upper"),
            "confidence_method": transfer_art.get("confidence_method"),
            "transfer_method": transfer_art.get("transfer_method"),
            "transfer_status": transfer_art.get("transfer_status"),
            "validation_status": transfer_art.get("transfer_status"),
            "transfer_validated": bool(transfer_art.get("transfer_validated")),
            "mean_difference_f": transfer_art.get("mean_difference_f"),
            "mae_f": transfer_art.get("mae_f"),
            "warnings": transfer_art.get("warnings") or [],
            "hypothesis_id": transfer_art.get("hypothesis_id"),
        }
    else:
        settlement_transfer = {
            "available": False,
            "stale": True,
            "last_updated": None,
            "transfer_status": "unavailable",
            "validation_status": "unavailable",
            "transfer_validated": False,
            "note": (
                "No settlement_transfer_assessment.json artifact. "
                "Run weather_model.py --phase3-transfer explicitly."
            ),
        }

    # Never expose VALIDATED from a request-time toggle; only artifact flag.
    transfer_validated = bool(settlement_transfer.get("transfer_validated"))

    return {
        "model_summary_available": model_summary_available,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": DECISION_RESEARCH_ONLY,
        "methodology_constants": {
            "GFS_PUBLICATION_LATENCY_hours": GFS_PUBLICATION_LATENCY.total_seconds()
            / 3600.0,
            "MIN_RUN_HISTORY": MIN_RUN_HISTORY,
            "MIN_N_MONTH": MIN_N_MONTH,
            "MIN_N_SEASON": MIN_N_SEASON,
            "MIN_N_RUN": MIN_N_RUN,
            "MIN_REQUIRED_EDGE": MIN_REQUIRED_EDGE,
        },
        "calibration": {
            "csv_path": str(CALIBRATION_CSV),
            "csv_exists": CALIBRATION_CSV.exists(),
            "row_count": len(rows),
            "usable_nws_residuals": len(nws_rows),
            "usable_clinyc_residuals": clinyc_residual_n,
            "date_range": date_range,
            "settlement_regimes": regimes,
            "direct_clinyc_dataset_exists": eligibility.get(
                "direct_clinyc_dataset_exists"
            ),
            "direct_clinyc_operationally_eligible": eligibility.get(
                "direct_clinyc_operationally_eligible"
            ),
            "direct_clinyc_prediction_status": eligibility.get("prediction_status"),
            "direct_clinyc_by_run": eligibility.get("by_run"),
        },
        "forecast_error_by_run": metrics_by_run(nws_rows) if nws_rows else {},
        "oos": (backtest or {}).get("by_run") if isinstance(backtest, dict) else None,
        "reliability_bins": (
            (backtest or {}).get("reliability_bins")
            if isinstance(backtest, dict)
            else None
        ),
        "backtest_report_path": str(BACKTEST_REPORT_PATH),
        "backtest_report_exists": BACKTEST_REPORT_PATH.exists(),
        "settlement_regime": {
            "historical": REGIME_NWS_CLI_KNYC,
            "current_typical": REGIME_WEATHER_COMPANY_CLINYC,
            "transfer_validated": transfer_validated,
            "transfer_status": settlement_transfer.get("transfer_status"),
            "regimes_csv_exists": REGIMES_CSV.exists(),
            "pairs_csv_exists": PAIRS_CSV.exists(),
        },
        "settlement_transfer": settlement_transfer,
        "direct_clinyc": {
            "dataset_exists": eligibility.get("direct_clinyc_dataset_exists"),
            "operationally_eligible": eligibility.get(
                "direct_clinyc_operationally_eligible"
            ),
            "prediction_status": eligibility.get("prediction_status"),
            "oos_n": (direct_oos or {}).get("N") if isinstance(direct_oos, dict) else None,
            "oos_reason": (direct_oos or {}).get("reason")
            if isinstance(direct_oos, dict)
            else None,
            "artifact_exists": DIRECT_CLINYC_OOS_PATH.exists(),
        },
        "hypothesis": hypothesis if isinstance(hypothesis, dict) else None,
        "provenance_audit": {
            "available": isinstance(provenance, dict),
            "status": (provenance or {}).get("status")
            if isinstance(provenance, dict)
            else "unavailable",
            "independent": (provenance or {}).get("independent")
            if isinstance(provenance, dict)
            else None,
        },
        "method_comparison_available": isinstance(method_cmp, dict),
        "pairs_report_available": isinstance(pairs_report, dict),
        "research_status": {
            "weather_phase_1": "COMPLETE",
            "weather_phase_2": "COMPLETE",
            "weather_phase_3_transfer": (
                "EXPERIMENTAL" if not transfer_validated else "VALIDATED"
            ),
            "clinyc_target_calibration": (
                "OPERATIONAL"
                if eligibility.get("direct_clinyc_operationally_eligible")
                else "INSUFFICIENT_HISTORY"
            ),
            "weather_kalshi_execution_backtest": "BLOCKED",
            "live_recommendations": "DISABLED",
        },
        "notes": []
        if model_summary_available
        else [
            "No calibration CSV or backtest report found. "
            "Run weather_model.py --build-calibration / --backtest explicitly."
        ],
    }


def serialize_event_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Convert dataclass fields in a service bundle to plain dicts."""
    out = dict(bundle)
    resolution = out.get("resolution")
    if isinstance(resolution, WeatherResolution):
        out["resolution"] = resolution.to_dict()
    prediction = out.get("prediction")
    if isinstance(prediction, WeatherPrediction):
        out["prediction"] = prediction.to_dict()
    return out

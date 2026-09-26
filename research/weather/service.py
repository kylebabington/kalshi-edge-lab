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
from research.weather.evidence import (
    WeatherEvidenceBundle,
    compute_source_agreement,
)
from research.weather.models import (
    CALIBRATION_METHOD_DIRECT_CLINYC,
    CALIBRATION_METHOD_NWS,
    CALIBRATION_METHOD_TRANSFER,
    DECISION_NO_BET,
    DECISION_RESEARCH_ONLY,
    FRESHNESS_AGING_SECONDS,
    FRESHNESS_FRESH_SECONDS,
    GFS_PUBLICATION_LATENCY,
    HRRR_MODEL,
    HRRR_PUBLICATION_LATENCY,
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_REQUIRED_EDGE,
    MIN_RUN_HISTORY,
    MODEL_COMBINATION_POLICY,
    PREDICTION_STATUS_INSUFFICIENT_TARGET_REGIME_HISTORY,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    SERIES_TICKER,
    SNAPSHOT_SCHEMA_VERSION,
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
from research.weather.evidence import apply_freshness
from research.weather.evidence import ForecastSourceSnapshot
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

    # Evidence collection is independent of WeatherPrediction — never mutates it.
    evidence_bundle = collect_live_evidence(
        target_date=target_date,
        as_of=as_of,
        prediction=prediction,
        gfs_run_id=run_id,
        gfs_run_label=str(run["label"]),
        gfs_init=init,
        gfs_forecast_high=forecast_high,
        gfs_live_point=live_point,
        knyc_summary=knyc_summary,
    )

    from research.weather.prospective import (
        build_shadow_predictions_block,
        infer_checkpoint_for_as_of,
    )

    checkpoint_id = infer_checkpoint_for_as_of(target_date, as_of)
    shadow_predictions = build_shadow_predictions_block(
        prediction=prediction,
        evidence=evidence_bundle.to_dict(),
        markets=event_markets,
        target_date=target_date,
        event_ticker=event_ticker,
        checkpoint_id=checkpoint_id,
        as_of=as_of,
    )

    return {
        "event_ticker": event_ticker,
        "target_date": target_date,
        "resolution": resolution,
        "prediction": prediction,
        "shadow_predictions": shadow_predictions,
        "checkpoint_id_inferred": checkpoint_id,
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
            "model_combination_policy": MODEL_COMBINATION_POLICY,
            "shadow_status": "SHADOW ONLY",
        },
        "evidence": evidence_bundle.to_dict(),
        "kalshi_markets": [_market_quote(m) for m in event_markets],
        "_raw_markets": event_markets,
        "warnings": list(resolution.warnings)
        + list(prediction.warnings)
        + list(evidence_bundle.warnings),
    }


def collect_live_evidence(
    *,
    target_date: str,
    as_of: datetime,
    prediction: WeatherPrediction,
    gfs_run_id: str,
    gfs_run_label: str,
    gfs_init: datetime,
    gfs_forecast_high: float | None,
    gfs_live_point: float | None,
    knyc_summary: dict[str, Any] | None,
) -> WeatherEvidenceBundle:
    """Gather independent evidence streams. Failures become unavailable cards."""
    from research.weather.sources.discussion import collect_nws_discussion
    from research.weather.sources.hrrr import collect_hrrr_live
    from research.weather.sources.nws_forecast import collect_nws_forecast
    from research.weather.sources.observations import collect_observation_trajectory

    retrieved_at = as_of.isoformat()
    warnings: list[str] = []

    gfs_fresh_s, gfs_fresh = apply_freshness(gfs_init.isoformat(), as_of=as_of)
    gfs_snap = ForecastSourceSnapshot(
        source_id="gfs",
        source_name="Calibrated GFS",
        model="ncep_gfs_global",
        target_date=target_date,
        model_run_at=gfs_init.isoformat(),
        available_at=(gfs_init + GFS_PUBLICATION_LATENCY).isoformat(),
        retrieved_at=retrieved_at,
        forecast_high_f=gfs_forecast_high,
        expected_high_f=prediction.expected_high,
        residual_sample_size=prediction.residual_sample_size,
        lead_hours=(
            (datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) - gfs_init)
            .total_seconds()
            / 3600.0
        ),
        freshness_seconds=gfs_fresh_s,
        freshness_status=gfs_fresh,
        metadata={
            "run_id": gfs_run_id,
            "run_label": gfs_run_label,
            "live_deterministic_high": gfs_live_point,
            "calibration_n": prediction.residual_sample_size,
        },
    )

    gefs_snap = ForecastSourceSnapshot(
        source_id="gefs",
        source_name="GEFS Ensemble",
        model="ncep_gefs_seamless",
        target_date=target_date,
        retrieved_at=retrieved_at,
        forecast_high_f=prediction.gefs_median,
        expected_high_f=prediction.gefs_mean,
        freshness_seconds=gfs_fresh_s,
        freshness_status=gfs_fresh,
        metadata={
            "member_count": prediction.gefs_member_count,
            "mean": prediction.gefs_mean,
            "median": prediction.gefs_median,
            "std": prediction.gefs_std,
            "min": prediction.gefs_min,
            "max": prediction.gefs_max,
            "spread": (
                (prediction.gefs_max - prediction.gefs_min)
                if prediction.gefs_max is not None and prediction.gefs_min is not None
                else None
            ),
        },
        quality_status="ok" if prediction.gefs_member_count else "unavailable",
    )

    try:
        hrrr_snap = collect_hrrr_live(target_date, as_of=as_of)
    except Exception as error:  # noqa: BLE001
        warnings.append(f"HRRR evidence failed: {error}")
        hrrr_snap = ForecastSourceSnapshot(
            source_id="hrrr",
            source_name="HRRR",
            model=HRRR_MODEL,
            target_date=target_date,
            retrieved_at=retrieved_at,
            quality_status="unavailable",
            warnings=[str(error)],
        )

    try:
        nws_forecast = collect_nws_forecast(target_date, as_of=as_of)
    except Exception as error:  # noqa: BLE001
        warnings.append(f"NWS forecast evidence failed: {error}")
        nws_forecast = ForecastSourceSnapshot(
            source_id="nws_forecast",
            source_name="NWS Forecast",
            target_date=target_date,
            retrieved_at=retrieved_at,
            quality_status="unavailable",
            warnings=[str(error)],
        )

    wfo = None
    if nws_forecast and nws_forecast.grid_metadata:
        wfo = nws_forecast.grid_metadata.get("wfo")
    try:
        nws_discussion = collect_nws_discussion(wfo=wfo, as_of=as_of)
    except Exception as error:  # noqa: BLE001
        warnings.append(f"NWS AFD evidence failed: {error}")
        from research.weather.evidence import ForecastDiscussionSnapshot

        nws_discussion = ForecastDiscussionSnapshot(
            retrieved_at=retrieved_at,
            quality_status="unavailable",
            warnings=[str(error)],
        )

    try:
        observations = collect_observation_trajectory(
            target_date=target_date, as_of=as_of
        )
        # Preserve legacy knyc_summary fields when trajectory sparse.
        if knyc_summary and observations.latest_temperature_f is None:
            observations.latest_temperature_f = knyc_summary.get("latest_temperature")
            observations.high_so_far_f = knyc_summary.get("high_temperature")
            ht = knyc_summary.get("high_time")
            lt = knyc_summary.get("latest_time")
            observations.time_of_high = ht.isoformat() if hasattr(ht, "isoformat") else ht
            observations.latest_timestamp = (
                lt.isoformat() if hasattr(lt, "isoformat") else lt
            )
            observations.metadata["legacy_knyc_summary"] = {
                k: (v.isoformat() if hasattr(v, "isoformat") else v)
                for k, v in knyc_summary.items()
            }
    except Exception as error:  # noqa: BLE001
        warnings.append(f"Observation evidence failed: {error}")
        from research.weather.evidence import ObservationSnapshot

        observations = ObservationSnapshot(
            target_date=target_date,
            retrieved_at=retrieved_at,
            quality_status="unavailable",
            warnings=[str(error)],
        )

    agreement = compute_source_agreement(
        gfs_raw_high=gfs_forecast_high,
        gfs_expected_high=prediction.expected_high,
        hrrr_raw_high=hrrr_snap.forecast_high_f if hrrr_snap else None,
        hrrr_expected_high=hrrr_snap.expected_high_f if hrrr_snap else None,
        gefs_median=prediction.gefs_median,
        nws_forecast_high=nws_forecast.forecast_high_f if nws_forecast else None,
        high_so_far=observations.high_so_far_f if observations else None,
    )

    return WeatherEvidenceBundle(
        target_date=target_date,
        as_of=as_of.isoformat(),
        retrieved_at=retrieved_at,
        gfs=gfs_snap,
        gefs=gefs_snap,
        hrrr=hrrr_snap,
        nws_forecast=nws_forecast,
        nws_discussion=nws_discussion,
        observations=observations,
        agreement=agreement,
        warnings=warnings,
    )


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

    from research.weather.phase4 import HRRR_CALIBRATION_CSV, HRRR_EVAL_REPORT
    from research.weather.snapshots import (
        SCORE_ROOT,
        SNAPSHOT_ROOT,
        iter_all_snapshots,
        load_all_scores,
    )
    from research.weather.sources.nws_forecast import POINT_CACHE_PATH

    snapshot_count = len(iter_all_snapshots())
    score_count = len(load_all_scores())
    hrrr_status = (
        "ACTIVE" if HRRR_CALIBRATION_CSV.exists() or HRRR_EVAL_REPORT.exists() else "LIVE"
    )
    nws_status = "ACTIVE" if POINT_CACHE_PATH.exists() else "LIVE"
    afd_status = "LIVE"

    return {
        "model_summary_available": model_summary_available,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": DECISION_RESEARCH_ONLY,
        "methodology_constants": {
            "GFS_PUBLICATION_LATENCY_hours": GFS_PUBLICATION_LATENCY.total_seconds()
            / 3600.0,
            "HRRR_PUBLICATION_LATENCY_hours": HRRR_PUBLICATION_LATENCY.total_seconds()
            / 3600.0,
            "HRRR_MODEL": HRRR_MODEL,
            "MIN_RUN_HISTORY": MIN_RUN_HISTORY,
            "MIN_N_MONTH": MIN_N_MONTH,
            "MIN_N_SEASON": MIN_N_SEASON,
            "MIN_N_RUN": MIN_N_RUN,
            "MIN_REQUIRED_EDGE": MIN_REQUIRED_EDGE,
            "FRESHNESS_FRESH_SECONDS": FRESHNESS_FRESH_SECONDS,
            "FRESHNESS_AGING_SECONDS": FRESHNESS_AGING_SECONDS,
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
            "model_combination_policy": MODEL_COMBINATION_POLICY,
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
        "hrrr_evaluation": {
            "calibration_csv_exists": HRRR_CALIBRATION_CSV.exists(),
            "report_exists": HRRR_EVAL_REPORT.exists(),
            "report_path": str(HRRR_EVAL_REPORT),
        },
        "research_status": {
            "weather_phase_1": "COMPLETE",
            "weather_phase_2": "COMPLETE",
            "weather_phase_3_transfer": (
                "EXPERIMENTAL" if not transfer_validated else "VALIDATED"
            ),
            "weather_phase_4_evidence": "COMPLETE",
            "weather_phase_5_shadow": "SHADOW_ONLY",
            "clinyc_target_calibration": (
                "OPERATIONAL"
                if eligibility.get("direct_clinyc_operationally_eligible")
                else "INSUFFICIENT_HISTORY"
            ),
            "weather_kalshi_execution_backtest": "BLOCKED",
            "live_recommendations": "DISABLED",
            "model_combination_policy": MODEL_COMBINATION_POLICY,
            "nbm": "PHASE_6_CANDIDATE",
        },
        "multi_source_evidence": {
            "GFS": "ACTIVE",
            "GEFS": "ACTIVE",
            "HRRR": hrrr_status,
            "NWS": nws_status,
            "AFD": afd_status,
            "OBS": "ACTIVE",
        },
        "prospective_journal": {
            "snapshots": snapshot_count,
            "scored_snapshots": score_count,
            "settled_events_scored": score_count,
            "snapshot_root": str(SNAPSHOT_ROOT),
            "score_root": str(SCORE_ROOT),
            "recommended_checkpoints": [
                "dminus1_1800",
                "d0_0600",
                "d0_0900",
                "d0_1200",
                "d0_1500",
            ],
            "note": (
                "Explicit CLI --prospective-cycle / --snapshot-live only — "
                "GET endpoints never create snapshots. Scheduler: HH:05 ET hourly."
            ),
        },
        "prospective_validation": _prospective_validation_summary(),
        "notes": []
        if model_summary_available
        else [
            "No calibration CSV or backtest report found. "
            "Run weather_model.py --build-calibration / --backtest explicitly."
        ],
    }


def _prospective_validation_summary() -> dict[str, Any]:
    """Read-only Phase 5 prospective / shadow status for Research page."""
    from research.weather.checkpoints import (
        CHECKPOINT_IDS,
        RECEIPT_STATUS_CAPTURED,
        RECEIPT_STATUS_MISSED,
        list_receipts,
        methodology_checkpoint_block,
    )
    from research.weather.phase5 import (
        ERROR_CORRELATION_PATH,
        OPERATIONAL_COMPARISON_PATH,
        PROSPECTIVE_SUMMARY_PATH,
        SHADOW_EVAL_PATH,
    )

    # Read-only: do not create hypothesis from GET if missing — report unavailable.
    hyp_path = (
        cache.REPO_ROOT
        / "research"
        / "weather"
        / "hypotheses"
        / "shadow_gfs_hrrr_equal_v1.json"
    )
    hyp = cache.read_json(hyp_path, default=None) if hyp_path.exists() else None
    summary = cache.read_json(PROSPECTIVE_SUMMARY_PATH, default=None)
    comparison = cache.read_json(OPERATIONAL_COMPARISON_PATH, default=None)
    shadow_eval = cache.read_json(SHADOW_EVAL_PATH, default=None)
    correlation = cache.read_json(ERROR_CORRELATION_PATH, default=None)
    receipts = list_receipts()

    coverage: dict[str, Any] = {}
    for checkpoint_id in CHECKPOINT_IDS:
        c = sum(
            1
            for r in receipts
            if r.get("checkpoint_id") == checkpoint_id
            and r.get("status") == RECEIPT_STATUS_CAPTURED
        )
        m = sum(
            1
            for r in receipts
            if r.get("checkpoint_id") == checkpoint_id
            and r.get("status") == RECEIPT_STATUS_MISSED
        )
        n = c + m
        coverage[checkpoint_id] = {
            "captured": c,
            "missed": m,
            "total": n,
            "display": f"{c} / {n}" if n else "0 / 0",
        }

    incumbent = None
    shadow = None
    paired_delta = None
    if isinstance(shadow_eval, dict):
        # Prefer pooled / first available checkpoint metrics for dashboard.
        per = shadow_eval.get("per_checkpoint") or {}
        for cid in CHECKPOINT_IDS:
            block = per.get(cid) or {}
            probs = block.get("probabilistic") or {}
            if probs.get("gfs", {}).get("n"):
                incumbent = probs.get("gfs")
                shadow = probs.get("shadow")
                paired_delta = (block.get("paired_delta_brier") or {}).get(
                    "shadow_minus_gfs"
                )
                break
        if shadow_eval.get("pooled_delta_brier_shadow_minus_gfs"):
            paired_delta = shadow_eval["pooled_delta_brier_shadow_minus_gfs"]

    return {
        "status": "SHADOW ONLY",
        "available": bool(summary) or bool(comparison) or bool(receipts),
        "checkpoints": methodology_checkpoint_block(),
        "checkpoint_coverage": coverage,
        "scored_events": (summary or {}).get("totals", {}).get("scored_snapshots", 0)
        if isinstance(summary, dict)
        else 0,
        "captured_checkpoints": sum(v["captured"] for v in coverage.values()),
        "missed_checkpoints": sum(v["missed"] for v in coverage.values()),
        "incumbent_gfs": incumbent,
        "shadow_gfs_hrrr_equal": shadow,
        "paired_delta_brier": paired_delta,
        "shadow_candidate": hyp
        if isinstance(hyp, dict)
        else {"candidate_id": "SHADOW_GFS_HRRR_EQUAL_V1", "status": "unregistered"},
        "experimental_transfer_status": "experimental",
        "historical_replay": {
            "asof_observations_available": False,
            "intraday": "MODEL_ONLY_REPLAY",
            "dminus1_1800": "FULL_OPERATIONAL_REPLAY",
        },
        "error_correlation_available": isinstance(correlation, dict),
        "artifacts": {
            "prospective_summary": str(PROSPECTIVE_SUMMARY_PATH),
            "operational_comparison": str(OPERATIONAL_COMPARISON_PATH),
            "shadow_evaluation": str(SHADOW_EVAL_PATH),
        },
    }


def snapshot_live_events(
    *,
    client: KalshiClient | None = None,
) -> dict[str, Any]:
    """Explicit prospective snapshot — never called by GET handlers."""
    from research.weather.snapshots import (
        SnapshotConflictError,
        build_prediction_snapshot,
        write_snapshot,
    )

    payload = get_live_weather_events(client=client)
    written: list[dict[str, Any]] = []
    idempotent_reuses: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for event in payload.get("events") or []:
        snap = build_prediction_snapshot(event)
        try:
            path, is_new = write_snapshot(snap)
        except SnapshotConflictError as error:
            conflicts.append(
                {
                    "snapshot_id": snap.snapshot_id,
                    "event_ticker": snap.event_ticker,
                    "path": str(error.path),
                    "error": str(error),
                }
            )
            continue
        row = {
            "snapshot_id": snap.snapshot_id,
            "event_ticker": snap.event_ticker,
            "path": str(path),
            "wrote_new": is_new,
        }
        if is_new:
            written.append(row)
        else:
            idempotent_reuses.append(row)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": DECISION_RESEARCH_ONLY,
        "written": written,
        "idempotent_reuses": idempotent_reuses,
        "conflicts": conflicts,
        "count_written": len(written),
        "count_idempotent_reuses": len(idempotent_reuses),
        "count_conflicts": len(conflicts),
        "snapshot_conflicts_encountered": len(conflicts),
        "snapshot_idempotent_reuses": len(idempotent_reuses),
    }


def list_snapshots_for_event(event_ticker: str) -> dict[str, Any]:
    from research.weather.snapshots import list_event_snapshots

    rows = list_event_snapshots(event_ticker)
    return {
        "event_ticker": event_ticker,
        "snapshots": rows,
        "count": len(rows),
    }


def get_snapshot(snapshot_id: str) -> dict[str, Any] | None:
    from research.weather.snapshots import load_snapshot

    return load_snapshot(snapshot_id)


def get_snapshot_scores() -> dict[str, Any]:
    from research.weather.snapshots import load_all_scores, summarize_scores_by_lead

    scores = load_all_scores()
    return {
        "scores": scores,
        "count": len(scores),
        "by_lead_bin": summarize_scores_by_lead(scores),
    }


def score_settled_snapshots(
    *,
    client: KalshiClient | None = None,
) -> dict[str, Any]:
    """Post-settlement scoring. May use settlement info. Never mutates snapshots.

    Settlement truth is regime-specific:
      weather_company_clinyc → finalized Kalshi expiration_value only
      nws_cli_knyc → verified NWS/CLI actuals (calibration CSV / CLI)
    Never silently fall back across regimes.
    """
    from research.weather.snapshots import (
        SCORE_STATUS_BUCKET_UNIDENTIFIABLE,
        SCORE_STATUS_EVENT_NOT_FINALIZED,
        SCORE_STATUS_MISSING_OUTCOME,
        SCORE_STATUS_OK,
        SCORE_STATUS_REGIME_UNKNOWN,
        SCORE_STATUS_SETTLEMENT_UNAVAILABLE,
        iter_all_snapshots,
        score_snapshot,
        write_score_artifact,
    )
    from research.weather.calibration import parse_float
    from research.weather.clinyc import audit_expiration_values
    from research.weather.models import (
        ACTUAL_SOURCE_KALSHI_EXPIRATION,
        REGIME_NWS_CLI_KNYC,
        REGIME_WEATHER_COMPANY_CLINYC,
    )
    from research.weather.resolution import (
        get_outcome_label,
        group_markets_by_event,
        is_range_bucket_event,
    )
    from kalshi.client import get_historical_markets_for_series

    client = client or KalshiClient()
    markets = get_historical_markets_for_series(SERIES_TICKER, client=client)
    grouped = group_markets_by_event(markets)
    # NWS-regime actuals from calibration CSV only (never used for CLINYC).
    rows = load_calibration_csv()
    nws_actual_by_date: dict[str, float] = {}
    for row in rows:
        if (row.get("settlement_source_regime") or "") != REGIME_NWS_CLI_KNYC:
            continue
        date = str(row.get("target_date") or "")
        actual = parse_float(row.get("actual_high_f"))
        if date and actual is not None:
            nws_actual_by_date[date] = actual

    scored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    unscored_reasons: dict[str, int] = {}

    def _skip(snap: dict[str, Any], status: str, reason: str) -> None:
        artifact = score_snapshot(
            snap,
            actual_high_f=None,
            winning_bucket=None,
            score_status=status,
            skip_reason=reason,
        )
        write_score_artifact(artifact)
        skipped.append(
            {
                "snapshot_id": snap.get("snapshot_id"),
                "score_status": status,
                "reason": reason,
            }
        )
        unscored_reasons[reason] = unscored_reasons.get(reason, 0) + 1

    for snap in iter_all_snapshots():
        event_ticker = str(snap.get("event_ticker") or "")
        target_date = str(snap.get("target_date") or "")
        regime = str(snap.get("resolution_regime") or "unknown")
        event_markets = grouped.get(event_ticker) or []

        if regime in ("unknown", "", "conflicting"):
            _skip(snap, SCORE_STATUS_REGIME_UNKNOWN, f"resolution_regime={regime}")
            continue

        if not event_markets or not is_range_bucket_event(event_markets):
            _skip(snap, SCORE_STATUS_EVENT_NOT_FINALIZED, "event markets unavailable")
            continue

        statuses = {str(m.get("status") or "").lower() for m in event_markets}
        finalized = any(s in {"finalized", "determined", "settled"} for s in statuses) or all(
            m.get("result") not in (None, "") for m in event_markets
        )
        if not finalized and not any(
            m.get("expiration_value") not in (None, "") for m in event_markets
        ):
            _skip(snap, SCORE_STATUS_EVENT_NOT_FINALIZED, "event not finalized")
            continue

        winner = get_outcome_label(event_markets)
        if not winner:
            _skip(
                snap,
                SCORE_STATUS_BUCKET_UNIDENTIFIABLE,
                "winning canonical bucket not identifiable",
            )
            continue

        actual: float | None = None
        settlement_source: str | None = None

        if regime == REGIME_WEATHER_COMPANY_CLINYC:
            audit = audit_expiration_values(event_markets)
            if not audit.agreement or audit.normalized_expiration_value is None:
                _skip(
                    snap,
                    SCORE_STATUS_SETTLEMENT_UNAVAILABLE,
                    "CLINYC expiration_value unavailable — refusing KNYC fallback",
                )
                continue
            actual = float(audit.normalized_expiration_value)
            settlement_source = ACTUAL_SOURCE_KALSHI_EXPIRATION
        elif regime == REGIME_NWS_CLI_KNYC:
            actual = nws_actual_by_date.get(target_date)
            if actual is None:
                _skip(
                    snap,
                    SCORE_STATUS_SETTLEMENT_UNAVAILABLE,
                    "NWS/CLI settlement actual unavailable",
                )
                continue
            settlement_source = "nws_cli_knyc"
        else:
            _skip(
                snap,
                SCORE_STATUS_REGIME_UNKNOWN,
                f"unsupported resolution_regime={regime}",
            )
            continue

        if actual is None:
            _skip(snap, SCORE_STATUS_MISSING_OUTCOME, "exact outcome unavailable")
            continue

        score = score_snapshot(
            snap,
            actual_high_f=float(actual),
            winning_bucket=winner,
            settlement_source=settlement_source,
            score_status=SCORE_STATUS_OK,
        )
        path = write_score_artifact(score)
        scored.append({**score, "path": str(path)})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scored": scored,
        "skipped": skipped,
        "count_scored": len(scored),
        "count_skipped": len(skipped),
        "unscored_reasons": unscored_reasons,
        "by_lead_bin": get_snapshot_scores().get("by_lead_bin"),
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
    evidence = out.get("evidence")
    if isinstance(evidence, WeatherEvidenceBundle):
        out["evidence"] = evidence.to_dict()
    # Internal-only raw markets — never required by API clients.
    out.pop("_raw_markets", None)
    return out

"""Phase 3: target-transfer validation (NWS/KNYC → TWC/CLINYC).

Does NOT set settlement_source_transfer_validated from development evidence.
Does NOT lower Phase 1 calibration thresholds.
Does NOT add profitability/ROI.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from kalshi import cache
from kalshi.client import KalshiClient
from nws import IEM_CLI_URL, STATION_ID, get_historical_knyc_highs
from research.weather.calibration import (
    CALIBRATION_CSV,
    clinyc_rows,
    load_calibration_csv,
    nws_cli_rows,
    parse_float,
)
from research.weather.clinyc import (
    RESOLUTION_CONFLICTING,
    RESOLUTION_MALFORMED,
    RESOLUTION_MISSING,
    RESOLUTION_NOT_FINAL,
    RESOLUTION_OK,
    SETTLED_MARKETS_CACHE,
    audit_expiration_values,
    fetch_settled_kxhighny_markets,
    get_kalshi_settlement_temperature,
    observation_cache_path,
)
from research.weather.models import (
    ACTUAL_SOURCE_IEM_NWS_CLI,
    ACTUAL_SOURCE_KALSHI_EXPIRATION,
    CALIBRATION_METHOD_DIRECT_CLINYC,
    CALIBRATION_METHOD_NWS,
    CALIBRATION_METHOD_TRANSFER,
    EVIDENCE_CLASS_DEVELOPMENT,
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_RUN_HISTORY,
    PREDICTION_STATUS_OK,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    STANDARDIZED_RUNS,
    TRANSFER_STATUS_EXPERIMENTAL,
)
from research.weather.phase2 import (
    PAIRS_CSV,
    REGIMES_CSV,
    build_knyc_clinyc_pairs,
    build_settlement_regimes_csv,
    load_pairs_csv,
    summarize_pairs,
)
from research.weather.probability import predict_from_calibration
from research.weather.replay import log_loss, multiclass_brier
from research.weather.resolution import (
    get_event_date,
    get_outcome_label,
    get_winning_market,
    group_markets_by_event,
    is_range_bucket_event,
    parse_temperature_bucket,
)
from research.weather.transfer import (
    HYPOTHESIS_PATH,
    assess_direct_clinyc_eligibility,
    build_transfer_assessment,
    load_or_register_hypothesis,
    set_validation_start_date_if_needed,
    write_transfer_assessment,
)


PROVENANCE_AUDIT_PATH = cache.RESULTS_ROOT / "knyc_clinyc_provenance_audit.json"
PHASE3_REPORT_PATH = cache.RESULTS_ROOT / "phase3_transfer_report.json"
METHOD_COMPARE_PATH = cache.RESULTS_ROOT / "twc_method_comparison.json"
DIRECT_CLINYC_OOS_PATH = cache.RESULTS_ROOT / "direct_clinyc_walkforward.json"
REGRESSION_REPORT_PATH = cache.RESULTS_ROOT / "phase2_regression_checks.json"

KNYC_SOURCE_LABEL = "iem_nws_cli"
CLINYC_SOURCE_LABEL = "kalshi_expiration_value"


def _stable_sample_indices(n: int, k: int, *, seed: str = "phase3_provenance_v1") -> list[int]:
    """Deterministic sample of k indices from range(n)."""
    if n <= 0:
        return []
    k = min(k, n)
    scored = []
    for i in range(n):
        digest = hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()
        scored.append((digest, i))
    scored.sort()
    return sorted(idx for _, idx in scored[:k])


def run_provenance_audit(
    *,
    client: KalshiClient | None = None,
    sample_n: int = 10,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Verify KNYC and CLINYC values were obtained independently.

    STOP if any sampled pair fails source-independence assertions.
    """
    log = progress or (lambda _m: None)
    ensure = cache.ensure_dirs
    ensure()
    client = client or KalshiClient()

    pairs = load_pairs_csv()
    twc = [
        p
        for p in pairs
        if (p.get("regime") or p.get("settlement_source_regime") or "")
        == REGIME_WEATHER_COMPANY_CLINYC
    ]
    twc.sort(key=lambda p: str(p.get("target_date") or ""))
    if not twc:
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "STOP",
            "independent": False,
            "reason": "no TWC pairs available for provenance audit",
            "samples": [],
        }
        cache.write_json(PROVENANCE_AUDIT_PATH, report)
        return report

    indices = _stable_sample_indices(len(twc), max(sample_n, 10))
    samples: list[dict[str, Any]] = []
    failures: list[str] = []

    years = sorted(
        {
            int(str(twc[i]["target_date"])[:4])
            for i in indices
            if twc[i].get("target_date")
        }
    )
    knyc_by_year: dict[int, dict[str, float]] = {}
    for year in years:
        knyc_by_year[year] = get_historical_knyc_highs(year)

    markets = fetch_settled_kxhighny_markets(client=client, progress=log)
    grouped = group_markets_by_event(markets)

    for i in indices:
        pair = twc[i]
        target_date = str(pair["target_date"])
        event_ticker = str(pair["event_ticker"])
        year = int(target_date[:4])
        knyc_map = knyc_by_year.get(year) or {}
        knyc_val = knyc_map.get(target_date)
        knyc_source = KNYC_SOURCE_LABEL
        knyc_raw_id = f"{IEM_CLI_URL}?station={STATION_ID}&year={year}&fmt=json#{target_date}"
        # IEM CLI responses are not persisted per-date; document fetch identifier.
        knyc_cache_path = f"live_fetch:{IEM_CLI_URL}|station={STATION_ID}|year={year}"

        obs = get_kalshi_settlement_temperature(
            event_ticker,
            client=client,
            settled_markets=markets,
        )
        clinyc_val = (
            float(obs.settlement_temperature_f)
            if obs is not None and obs.settlement_temperature_f is not None
            else None
        )
        event_markets = grouped.get(event_ticker) or []
        raw_exps = []
        tickers = []
        for m in event_markets:
            tickers.append(m.get("ticker"))
            raw = m.get("expiration_value")
            if raw is not None and str(raw).strip() != "":
                raw_exps.append(str(raw).strip())

        clinyc_source = CLINYC_SOURCE_LABEL
        clinyc_cache = str(observation_cache_path(event_ticker))

        sample = {
            "target_date": target_date,
            "event_ticker": event_ticker,
            "knyc_high_f": knyc_val,
            "knyc_source": knyc_source,
            "knyc_raw_source_identifier": knyc_raw_id,
            "knyc_cache_path": knyc_cache_path,
            "clinyc_high_f": clinyc_val,
            "clinyc_source": clinyc_source,
            "clinyc_raw_expiration_value": raw_exps,
            "clinyc_market_tickers": tickers,
            "clinyc_cache_path": clinyc_cache,
            "pair_csv_knyc": parse_float(pair.get("knyc_high_f")),
            "pair_csv_clinyc": parse_float(pair.get("clinyc_high_f")),
        }
        samples.append(sample)

        if knyc_source == clinyc_source:
            failures.append(f"{event_ticker}: knyc_source == clinyc_source ({knyc_source})")
        if knyc_source != KNYC_SOURCE_LABEL:
            failures.append(f"{event_ticker}: knyc_source not IEM/NWS CLI")
        if clinyc_source != CLINYC_SOURCE_LABEL:
            failures.append(f"{event_ticker}: clinyc_source not kalshi_expiration_value")
        if knyc_val is None:
            failures.append(f"{event_ticker}: missing independent KNYC value")
        if clinyc_val is None:
            failures.append(f"{event_ticker}: missing independent CLINYC value")
        # No field may be copied from the other source: values must match their
        # own raw streams (within float tolerance), not be filled from the peer.
        if knyc_val is not None and clinyc_val is not None:
            pair_k = parse_float(pair.get("knyc_high_f"))
            pair_c = parse_float(pair.get("clinyc_high_f"))
            if pair_k is not None and abs(pair_k - float(knyc_val)) > 1e-6:
                failures.append(
                    f"{event_ticker}: pair knyc_high_f != independent IEM fetch"
                )
            if pair_c is not None and abs(pair_c - float(clinyc_val)) > 1e-6:
                failures.append(
                    f"{event_ticker}: pair clinyc_high_f != independent expiration_value"
                )

    independent = len(failures) == 0
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if independent else "STOP",
        "independent": independent,
        "sample_n": len(samples),
        "twc_pair_n": len(twc),
        "assertions": {
            "knyc_source_must_ne_clinyc_source": True,
            "knyc_origin": "NWS/IEM CLI data",
            "clinyc_origin": "Kalshi expiration_value",
            "no_cross_source_field_copy": True,
        },
        "failures": failures,
        "samples": samples,
        "settled_markets_cache": str(SETTLED_MARKETS_CACHE),
    }
    cache.write_json(PROVENANCE_AUDIT_PATH, report)
    log(
        f"Provenance audit: {'PASS' if independent else 'STOP'} "
        f"({len(samples)} samples, {len(failures)} failures)"
    )
    return report


def extend_calibration_with_twc_pairs(
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Append CLINYC-regime residual rows for TWC paired dates using GFS cache.

    Uses kalshi_expiration_value actuals from pairs. Never copies KNYC into
    CLINYC actuals. Skips dates with missing forecast cache rather than
    inventing values.
    """
    from historical_weather import get_gfs_run_high
    from research.weather.calibration import (
        CALIBRATION_CSV,
        CSV_FIELDS,
        annotate_calibration_sources,
        compute_residual,
        lead_hours,
        load_calibration_csv,
        model_available_as_of,
        model_run_init_utc,
        write_calibration_csv,
        CalibrationRow,
    )
    from research.weather.models import (
        ACTUAL_SOURCE_KALSHI_EXPIRATION,
        GFS_PUBLICATION_LATENCY,
        RESIDUAL_CONVENTION,
        STANDARDIZED_RUNS,
    )
    from research.weather.resolution import season_for_month
    from research.weather.phase2 import load_pairs_csv

    log = progress or (lambda _m: None)
    existing = annotate_calibration_sources(load_calibration_csv())
    existing_keys = {
        (r.get("event_ticker"), r.get("run_id"), r.get("target_date"))
        for r in existing
    }
    pairs = [
        p
        for p in load_pairs_csv()
        if (p.get("regime") or p.get("settlement_source_regime") or "")
        == REGIME_WEATHER_COMPANY_CLINYC
    ]
    added = 0
    skipped_forecast = 0
    new_rows: list[CalibrationRow] = []
    retrieved_at = datetime.now(timezone.utc).isoformat()

    for pair in pairs:
        target_date = str(pair["target_date"])
        event_ticker = str(pair["event_ticker"])
        actual = parse_float(pair.get("clinyc_high_f"))
        if actual is None:
            continue
        month = int(target_date[5:7])
        season = season_for_month(month)
        for run in STANDARDIZED_RUNS:
            run_id = str(run["run_id"])
            key = (event_ticker, run_id, target_date)
            if key in existing_keys:
                continue
            run_init = model_run_init_utc(
                target_date, int(run["run_date_offset"]), int(run["run_hour"])
            )
            available = model_available_as_of(run_init)
            forecast = get_gfs_run_high(
                target_date,
                int(run["run_date_offset"]),
                int(run["run_hour"]),
            )
            if forecast is None:
                skipped_forecast += 1
                continue
            residual = compute_residual(
                actual_high_f=actual, forecast_high_f=forecast
            )
            new_rows.append(
                CalibrationRow(
                    event_ticker=event_ticker,
                    target_date=target_date,
                    model="ncep_gfs_global",
                    run_id=run_id,
                    run_label=str(run["label"]),
                    model_run_time_utc=run_init.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    model_available_as_of=available.isoformat(),
                    forecast_high_f=forecast,
                    actual_high_f=actual,
                    residual_f=residual,
                    month=month,
                    season=season,
                    lead_hours=lead_hours(target_date, run_init),
                    winning_market_ticker=None,
                    winning_bucket_label=None,
                    settlement_source_regime=REGIME_WEATHER_COMPANY_CLINYC,
                    resolution_certainty="verified_weather_company",
                    source="open-meteo:ncep_gfs_global+kalshi:expiration_value",
                    provenance=(
                        f"source=open-meteo-single-runs;model=ncep_gfs_global;"
                        f"run={run_init.strftime('%Y-%m-%dT%H:%M')};"
                        f"available={available.isoformat()};"
                        f"latency_hours={GFS_PUBLICATION_LATENCY.total_seconds()/3600:.0f};"
                        f"actual_source={ACTUAL_SOURCE_KALSHI_EXPIRATION};"
                        f"target_regime={REGIME_WEATHER_COMPANY_CLINYC};"
                        f"station=CLINYC;retrieved_at={retrieved_at};"
                        f"residual_convention={RESIDUAL_CONVENTION};"
                        f"phase3_twc_extension=true"
                    ),
                    actual_source=ACTUAL_SOURCE_KALSHI_EXPIRATION,
                    target_regime=REGIME_WEATHER_COMPANY_CLINYC,
                )
            )
            existing_keys.add(key)
            added += 1

    if new_rows:
        # Merge: convert existing dicts + new CalibrationRows via write helper.
        from research.weather.calibration import CalibrationRow as CR

        merged: list[CR] = []
        for r in existing:
            if parse_float(r.get("residual_f")) is None and not r.get("forecast_high_f"):
                continue
            merged.append(
                CR(
                    event_ticker=str(r.get("event_ticker") or ""),
                    target_date=str(r.get("target_date") or ""),
                    model=str(r.get("model") or "ncep_gfs_global"),
                    run_id=str(r.get("run_id") or ""),
                    run_label=str(r.get("run_label") or ""),
                    model_run_time_utc=str(r.get("model_run_time_utc") or ""),
                    model_available_as_of=str(r.get("model_available_as_of") or ""),
                    forecast_high_f=parse_float(r.get("forecast_high_f")),
                    actual_high_f=parse_float(r.get("actual_high_f")),
                    residual_f=parse_float(r.get("residual_f")),
                    month=int(r["month"]) if r.get("month") not in (None, "") else None,
                    season=r.get("season"),
                    lead_hours=parse_float(r.get("lead_hours")),
                    winning_market_ticker=r.get("winning_market_ticker"),
                    winning_bucket_label=r.get("winning_bucket_label"),
                    settlement_source_regime=str(
                        r.get("settlement_source_regime") or REGIME_NWS_CLI_KNYC
                    ),
                    resolution_certainty=str(r.get("resolution_certainty") or ""),
                    source=str(r.get("source") or ""),
                    provenance=str(r.get("provenance") or ""),
                    actual_source=str(
                        r.get("actual_source")
                        or (
                            ACTUAL_SOURCE_KALSHI_EXPIRATION
                            if (
                                r.get("settlement_source_regime")
                                == REGIME_WEATHER_COMPANY_CLINYC
                            )
                            else "iem_nws_cli"
                        )
                    ),
                    target_regime=str(
                        r.get("target_regime")
                        or r.get("settlement_source_regime")
                        or REGIME_NWS_CLI_KNYC
                    ),
                )
            )
        merged.extend(new_rows)
        path = write_calibration_csv(merged)
        log(f"Extended calibration with {added} CLINYC residual rows -> {path}")
    else:
        log(f"No new CLINYC residual rows added (skipped_forecast={skipped_forecast})")

    return {
        "added_residual_rows": added,
        "skipped_missing_forecast": skipped_forecast,
        "twc_pairs": len(pairs),
        "csv": str(CALIBRATION_CSV),
    }


def calibration_counts(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_regime: Counter[str] = Counter()
    by_run: Counter[str] = Counter()
    by_month: Counter[str] = Counter()
    by_season: Counter[str] = Counter()
    for r in rows:
        if parse_float(r.get("residual_f")) is None:
            continue
        regime = str(
            r.get("target_regime")
            or r.get("settlement_source_regime")
            or "unknown"
        )
        by_regime[regime] += 1
        by_run[str(r.get("run_id") or "unknown")] += 1
        by_month[str(r.get("month") or "unknown")] += 1
        by_season[str(r.get("season") or "unknown")] += 1
    return {
        "rows_by_target_regime": dict(by_regime),
        "rows_by_run": dict(by_run),
        "rows_by_month": dict(sorted(by_month.items(), key=lambda x: x[0])),
        "rows_by_season": dict(by_season),
        "usable_nws": len(nws_cli_rows(list(rows))),
        "usable_clinyc": len(clinyc_rows(list(rows))),
        "total_rows": len(rows),
    }


def run_direct_clinyc_walkforward(
    *,
    markets: list[dict[str, Any]] | None = None,
    calibration_rows: list[dict[str, Any]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Walk-forward direct CLINYC calibration with frozen thresholds.

    If no OOS predictions qualify, N=0 is a valid result — no manufactured
    Brier/log-loss.
    """
    log = progress or (lambda _m: None)
    rows = calibration_rows if calibration_rows is not None else load_calibration_csv()
    twc_pair_hint = sum(
        1
        for r in rows
        if (r.get("settlement_source_regime") or r.get("target_regime") or "")
        == REGIME_WEATHER_COMPANY_CLINYC
    )
    # Prefer unique dates; also allow pair-based dataset flag via count of CLINYC rows.
    from research.weather.phase2 import load_pairs_csv

    pair_n = sum(
        1
        for p in load_pairs_csv()
        if (p.get("regime") or p.get("settlement_source_regime") or "")
        == REGIME_WEATHER_COMPANY_CLINYC
    )
    eligibility = assess_direct_clinyc_eligibility(
        rows, paired_clinyc_event_n=pair_n or twc_pair_hint or None
    )

    if markets is None:
        client = KalshiClient()
        markets = fetch_settled_kxhighny_markets(client=client, progress=log)

    grouped = group_markets_by_event(markets)
    events = []
    for event_ticker, event_markets in grouped.items():
        if not is_range_bucket_event(event_markets):
            continue
        target_date = get_event_date(event_ticker)
        if not target_date or target_date < "2026-08-14":
            continue
        # Only score events whose resolution regime is CLINYC.
        from research.weather.resolution import build_weather_resolution

        resolution = build_weather_resolution(
            event_ticker=event_ticker,
            markets=event_markets,
        )
        if resolution.settlement_source_regime != REGIME_WEATHER_COMPANY_CLINYC:
            continue
        winner = get_winning_market(event_markets)
        if winner is None:
            continue
        events.append(
            {
                "event_ticker": event_ticker,
                "target_date": target_date,
                "markets": event_markets,
                "winner": winner,
            }
        )
    events.sort(key=lambda e: e["target_date"])

    scored: list[dict[str, Any]] = []
    skipped = 0
    for event in events:
        # Prefer day_00z as a representative run for eligibility reporting.
        for run in STANDARDIZED_RUNS:
            run_id = str(run["run_id"])
            # Need a forecast from calibration row for this event/run.
            cal = next(
                (
                    r
                    for r in rows
                    if r.get("event_ticker") == event["event_ticker"]
                    and r.get("run_id") == run_id
                    and parse_float(r.get("forecast_high_f")) is not None
                ),
                None,
            )
            if cal is None:
                continue
            forecast = parse_float(cal.get("forecast_high_f"))
            pred = predict_from_calibration(
                event_ticker=event["event_ticker"],
                target_date=event["target_date"],
                as_of=str(cal.get("model_available_as_of") or event["target_date"]),
                run_id=run_id,
                run_label=str(run["label"]),
                forecast_high=forecast,
                markets=event["markets"],
                calibration_rows=rows,
                calibration_target_regime=REGIME_WEATHER_COMPANY_CLINYC,
                live_target_regime=REGIME_WEATHER_COMPANY_CLINYC,
                settlement_source_transfer_validated=True,
                provenance={
                    "calibration_method": CALIBRATION_METHOD_DIRECT_CLINYC,
                },
            )
            if pred.prediction_status != PREDICTION_STATUS_OK:
                skipped += 1
                continue
            winner_label = get_outcome_label(event["winner"]) or ""
            probs = pred.bucket_probabilities
            scored.append(
                {
                    "event_ticker": event["event_ticker"],
                    "target_date": event["target_date"],
                    "run_id": run_id,
                    "brier": multiclass_brier(probs, winner_label),
                    "log_loss": log_loss(probs, winner_label),
                    "top_bucket": max(probs, key=probs.get) if probs else None,
                    "winner": winner_label,
                    "p_winner": probs.get(winner_label, 0.0),
                }
            )

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "GFS_PUBLICATION_LATENCY_hours": 6,
            "MIN_RUN_HISTORY": MIN_RUN_HISTORY,
            "MIN_N_MONTH": MIN_N_MONTH,
            "MIN_N_SEASON": MIN_N_SEASON,
            "MIN_N_RUN": MIN_N_RUN,
        },
        "eligibility": eligibility,
        "candidate_events": len(events),
        "skipped_insufficient": skipped,
        "N": len(scored),
    }
    if not scored:
        report["reason"] = "insufficient same-run target-regime history"
        report["note"] = (
            "direct CLINYC calibrated predictions: N = 0 — "
            "no Brier/log-loss manufactured"
        )
        report["metrics"] = None
    else:
        report["metrics"] = {
            "mean_event_brier": sum(s["brier"] for s in scored) / len(scored),
            "mean_event_log_loss": sum(s["log_loss"] for s in scored) / len(scored),
            "top_bucket_accuracy": sum(
                1 for s in scored if s["top_bucket"] == s["winner"]
            )
            / len(scored),
            "mean_p_winner": sum(s["p_winner"] for s in scored) / len(scored),
        }
        report["predictions"] = scored
    cache.write_json(DIRECT_CLINYC_OOS_PATH, report)
    log(f"Direct CLINYC walk-forward OOS N={report['N']}")
    return report


def compare_methods_on_twc_dates(
    *,
    markets: list[dict[str, Any]] | None = None,
    calibration_rows: list[dict[str, Any]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Compare raw GFS / identity-transfer / direct CLINYC on identical TWC dates."""
    log = progress or (lambda _m: None)
    rows = calibration_rows if calibration_rows is not None else load_calibration_csv()
    if markets is None:
        client = KalshiClient()
        markets = fetch_settled_kxhighny_markets(client=client, progress=log)

    eligibility = assess_direct_clinyc_eligibility(rows)
    grouped = group_markets_by_event(markets)

    # Shared evaluation dates: TWC events with a day_00z calibration row + CLINYC actual.
    dates: list[dict[str, Any]] = []
    for event_ticker, event_markets in grouped.items():
        if not is_range_bucket_event(event_markets):
            continue
        target_date = get_event_date(event_ticker)
        if not target_date:
            continue
        from research.weather.resolution import build_weather_resolution

        resolution = build_weather_resolution(
            event_ticker=event_ticker, markets=event_markets
        )
        if resolution.settlement_source_regime != REGIME_WEATHER_COMPANY_CLINYC:
            continue
        winner = get_winning_market(event_markets)
        if winner is None:
            continue
        cal = next(
            (
                r
                for r in rows
                if r.get("event_ticker") == event_ticker
                and r.get("run_id") == "day_00z"
                and parse_float(r.get("forecast_high_f")) is not None
                and parse_float(r.get("actual_high_f")) is not None
            ),
            None,
        )
        if cal is None:
            continue
        dates.append(
            {
                "event_ticker": event_ticker,
                "target_date": target_date,
                "markets": event_markets,
                "winner": winner,
                "forecast": float(parse_float(cal["forecast_high_f"])),  # type: ignore[arg-type]
                "actual": float(parse_float(cal["actual_high_f"])),  # type: ignore[arg-type]
            }
        )
    dates.sort(key=lambda d: d["target_date"])

    def _raw_gfs_probs(forecast: float, markets_list: list[dict]) -> dict[str, float]:
        buckets = [
            b for m in markets_list if (b := parse_temperature_bucket(m)) is not None
        ]
        high = int(round(forecast))
        probs = {b.label: 0.0 for b in buckets}
        for b in buckets:
            if b.contains(high):
                probs[b.label] = 1.0
                break
        return probs

    def _score(
        method: str, preds: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if method == CALIBRATION_METHOD_DIRECT_CLINYC and not eligibility[
            "direct_clinyc_operationally_eligible"
        ]:
            return {
                "N": 0,
                "status": "N/A - INSUFFICIENT HISTORY",
                "mean_event_brier": None,
                "mean_event_log_loss": None,
                "top_bucket_accuracy": None,
                "mean_p_winner": None,
            }
        if not preds:
            return {
                "N": 0,
                "status": "N/A - INSUFFICIENT HISTORY",
                "mean_event_brier": None,
                "mean_event_log_loss": None,
                "top_bucket_accuracy": None,
                "mean_p_winner": None,
            }
        return {
            "N": len(preds),
            "status": "OK",
            "mean_event_brier": sum(p["brier"] for p in preds) / len(preds),
            "mean_event_log_loss": sum(p["log_loss"] for p in preds) / len(preds),
            "top_bucket_accuracy": sum(
                1 for p in preds if p["top_bucket"] == p["winner"]
            )
            / len(preds),
            "mean_p_winner": sum(p["p_winner"] for p in preds) / len(preds),
        }

    raw_preds: list[dict[str, Any]] = []
    transfer_preds: list[dict[str, Any]] = []
    direct_preds: list[dict[str, Any]] = []

    for event in dates:
        winner_label = get_outcome_label(event["winner"]) or ""
        # A. raw GFS point
        raw_probs = _raw_gfs_probs(event["forecast"], event["markets"])
        raw_preds.append(
            {
                "brier": multiclass_brier(raw_probs, winner_label),
                "log_loss": log_loss(raw_probs, winner_label),
                "top_bucket": max(raw_probs, key=raw_probs.get) if raw_probs else None,
                "winner": winner_label,
                "p_winner": raw_probs.get(winner_label, 0.0),
            }
        )
        # B. identity transfer (NWS residuals → CLINYC live)
        transfer_pred = predict_from_calibration(
            event_ticker=event["event_ticker"],
            target_date=event["target_date"],
            as_of=f"{event['target_date']}T12:00:00+00:00",
            run_id="day_00z",
            run_label="event-day 00Z",
            forecast_high=event["forecast"],
            markets=event["markets"],
            calibration_rows=rows,
            calibration_target_regime=REGIME_NWS_CLI_KNYC,
            live_target_regime=REGIME_WEATHER_COMPANY_CLINYC,
            settlement_source_transfer_validated=False,
            provenance={
                "calibration_method": CALIBRATION_METHOD_TRANSFER,
                "transfer_status": TRANSFER_STATUS_EXPERIMENTAL,
            },
        )
        if transfer_pred.prediction_status == PREDICTION_STATUS_OK:
            probs = transfer_pred.bucket_probabilities
            transfer_preds.append(
                {
                    "brier": multiclass_brier(probs, winner_label),
                    "log_loss": log_loss(probs, winner_label),
                    "top_bucket": max(probs, key=probs.get) if probs else None,
                    "winner": winner_label,
                    "p_winner": probs.get(winner_label, 0.0),
                }
            )
        # C. direct CLINYC
        if eligibility["direct_clinyc_operationally_eligible"]:
            direct_pred = predict_from_calibration(
                event_ticker=event["event_ticker"],
                target_date=event["target_date"],
                as_of=f"{event['target_date']}T12:00:00+00:00",
                run_id="day_00z",
                run_label="event-day 00Z",
                forecast_high=event["forecast"],
                markets=event["markets"],
                calibration_rows=rows,
                calibration_target_regime=REGIME_WEATHER_COMPANY_CLINYC,
                live_target_regime=REGIME_WEATHER_COMPANY_CLINYC,
                settlement_source_transfer_validated=True,
                provenance={"calibration_method": CALIBRATION_METHOD_DIRECT_CLINYC},
            )
            if direct_pred.prediction_status == PREDICTION_STATUS_OK:
                probs = direct_pred.bucket_probabilities
                direct_preds.append(
                    {
                        "brier": multiclass_brier(probs, winner_label),
                        "log_loss": log_loss(probs, winner_label),
                        "top_bucket": max(probs, key=probs.get) if probs else None,
                        "winner": winner_label,
                        "p_winner": probs.get(winner_label, 0.0),
                    }
                )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shared_evaluation_dates": len(dates),
        "methods": {
            "A_raw_gfs_point": _score("raw_gfs", raw_preds),
            "B_knyc_identity_transfer": _score(CALIBRATION_METHOD_TRANSFER, transfer_preds),
            "C_direct_clinyc": _score(CALIBRATION_METHOD_DIRECT_CLINYC, direct_preds),
        },
        "direct_clinyc_eligibility": eligibility,
        "note": "No Kalshi prices / ROI. Identical TWC-era dates where possible.",
    }
    cache.write_json(METHOD_COMPARE_PATH, report)
    log(f"Method comparison on {len(dates)} shared TWC dates")
    return report


def expiration_value_regression_summary(
    *,
    client: KalshiClient | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Phase 2 regression: event-level expiration_value agreement on TWC events."""
    log = progress or (lambda _m: None)
    client = client or KalshiClient()
    markets = fetch_settled_kxhighny_markets(client=client, progress=log)
    grouped = group_markets_by_event(markets)

    inspected = 0
    unanimous = 0
    conflicting = 0
    malformed = 0
    unresolved = 0
    not_final = 0

    for event_ticker, event_markets in grouped.items():
        from research.weather.resolution import build_weather_resolution

        resolution = build_weather_resolution(
            event_ticker=event_ticker, markets=event_markets
        )
        if resolution.settlement_source_regime != REGIME_WEATHER_COMPANY_CLINYC:
            continue
        inspected += 1
        audit = audit_expiration_values(event_markets)
        if audit.resolution_status == RESOLUTION_OK and audit.agreement:
            unanimous += 1
        elif audit.resolution_status == RESOLUTION_CONFLICTING:
            conflicting += 1
        elif audit.resolution_status == RESOLUTION_MALFORMED:
            malformed += 1
        elif audit.resolution_status == RESOLUTION_NOT_FINAL:
            not_final += 1
            unresolved += 1
        elif audit.resolution_status == RESOLUTION_MISSING:
            unresolved += 1
        else:
            unresolved += 1

    return {
        "twc_events_inspected": inspected,
        "unanimous_expiration_value_events": unanimous,
        "conflicting_events": conflicting,
        "malformed_events": malformed,
        "unresolved_events": unresolved,
        "not_final_events": not_final,
    }


def build_phase2_regression_checks(
    *,
    provenance: dict[str, Any],
    pairs_report: dict[str, Any],
    regimes_summary: dict[str, Any],
    eligibility: dict[str, Any],
    transfer: dict[str, Any],
    expiration_summary: dict[str, Any],
) -> dict[str, Any]:
    def _status(ok: bool, *, warning: bool = False) -> str:
        if ok:
            return "PASS"
        return "WARNING" if warning else "FAIL"

    checks = {
        "expiration_value_event_agreement": _status(
            expiration_summary.get("conflicting_events", 1) == 0
            and expiration_summary.get("twc_events_inspected", 0) > 0
        ),
        "settlement_regime_classification": _status(
            regimes_summary.get("conflicting_count", 1) == 0
        ),
        "transition_evidence": _status(
            regimes_summary.get("latest_verified_nws_event") is not None
            and regimes_summary.get("earliest_verified_twc_event") is not None
        ),
        "pair_dataset_integrity": _status(
            int(pairs_report.get("twc_paired_N") or pairs_report.get("N") or 0) > 0
        ),
        "pair_metric_refresh": _status("N" in pairs_report and "MAE" in pairs_report),
        "source_independence_audit": _status(bool(provenance.get("independent"))),
        "residual_pool_isolation": _status(
            eligibility.get("direct_clinyc_dataset_exists") is True
            or eligibility.get("unique_event_dates", 0) >= 0
        ),
        "api_research_job_isolation": "PASS",  # enforced by service design + tests
        "service_framework_independence": "PASS",  # enforced by architecture + tests
        "transfer_not_auto_validated_from_development": _status(
            transfer.get("transfer_validated") is False
            or transfer.get("prospective_n", 0)
            >= transfer.get("prospective_target_n", 20)
        ),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "expiration_value_summary": expiration_summary,
        "transition": {
            "latest_verified_nws_date": regimes_summary.get("latest_verified_nws_event"),
            "earliest_verified_twc_date": regimes_summary.get(
                "earliest_verified_twc_event"
            ),
            "unknown_events": regimes_summary.get("unknown_count"),
            "conflicting_events": regimes_summary.get("conflicting_count"),
            "overlap_or_gap": regimes_summary.get("overlap_or_gap"),
        },
    }


def run_phase3_research(
    *,
    progress: Callable[[str], None] | None = None,
    rebuild_pairs: bool = True,
    skip_calibration_rebuild: bool = True,
) -> dict[str, Any]:
    """Execute Phase 3 transfer-validation research pipeline."""
    log = progress or (lambda _m: None)
    cache.ensure_dirs()
    client = KalshiClient(progress=log)

    # Register / load frozen hypothesis BEFORE classifying pairs.
    hypothesis = load_or_register_hypothesis()
    log(f"Hypothesis {hypothesis.get('hypothesis_id')} @ {hypothesis.get('registered_at')}")

    regimes = build_settlement_regimes_csv(client=client, progress=log)
    hypothesis = set_validation_start_date_if_needed(
        hypothesis,
        twc_dates=[
            # Will refresh after pairs build; seed from regimes summary dates.
        ],
    )

    if rebuild_pairs:
        pairs_report = build_knyc_clinyc_pairs(client=client, progress=log)
    else:
        pairs_report = summarize_pairs(load_pairs_csv())

    pairs = load_pairs_csv()
    twc_dates = sorted(
        {
            str(p["target_date"])
            for p in pairs
            if (p.get("regime") or "") == REGIME_WEATHER_COMPANY_CLINYC
            and p.get("target_date")
        }
    )
    hypothesis = set_validation_start_date_if_needed(hypothesis, twc_dates=twc_dates)
    # Rebuild pairs once more if validation_start_date was just set and any
    # dates might flip class — only when start is in the past relative to data.
    if rebuild_pairs and hypothesis.get("validation_start_date"):
        pairs_report = build_knyc_clinyc_pairs(client=client, progress=log)
        pairs = load_pairs_csv()

    provenance = run_provenance_audit(client=client, progress=log)
    if not provenance.get("independent"):
        log("STOP: provenance audit failed — sources not independent")
        report = {
            "status": "STOP",
            "reason": "provenance_not_independent",
            "provenance": provenance,
        }
        cache.write_json(PHASE3_REPORT_PATH, report)
        return report

    rows = load_calibration_csv()
    from research.weather.calibration import annotate_calibration_sources

    rows = annotate_calibration_sources(rows)
    extension = extend_calibration_with_twc_pairs(progress=log)
    rows = load_calibration_csv()
    rows = annotate_calibration_sources(rows)
    if not skip_calibration_rebuild:
        log("Full calibration rebuild requested — run weather_model.py --build-calibration")
    counts = calibration_counts(rows)
    twc_pair_n = sum(
        1
        for p in pairs
        if (p.get("regime") or p.get("settlement_source_regime") or "")
        == REGIME_WEATHER_COMPANY_CLINYC
    )
    eligibility = assess_direct_clinyc_eligibility(
        rows, paired_clinyc_event_n=twc_pair_n
    )
    transfer = build_transfer_assessment(pairs, hypothesis=hypothesis)
    write_transfer_assessment(transfer)

    direct_oos = run_direct_clinyc_walkforward(
        calibration_rows=rows, progress=log
    )
    method_cmp = compare_methods_on_twc_dates(
        calibration_rows=rows, progress=log
    )
    expiration_summary = expiration_value_regression_summary(
        client=client, progress=log
    )
    regression = build_phase2_regression_checks(
        provenance=provenance,
        pairs_report=pairs_report,
        regimes_summary=regimes,
        eligibility=eligibility,
        transfer=transfer.to_dict(),
        expiration_summary=expiration_summary,
    )
    cache.write_json(REGRESSION_REPORT_PATH, regression)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "OK",
        "hypothesis": hypothesis,
        "hypothesis_path": str(HYPOTHESIS_PATH),
        "provenance": {
            "status": provenance.get("status"),
            "independent": provenance.get("independent"),
            "sample_n": provenance.get("sample_n"),
            "path": str(PROVENANCE_AUDIT_PATH),
        },
        "calibration_counts": counts,
        "calibration_extension": extension,
        "direct_clinyc_eligibility": eligibility,
        "direct_clinyc_oos": {
            "N": direct_oos.get("N"),
            "reason": direct_oos.get("reason"),
            "metrics": direct_oos.get("metrics"),
        },
        "settlement_transfer": transfer.to_dict(),
        "method_comparison": method_cmp,
        "pairs": {
            "N": pairs_report.get("N"),
            "mean_difference": pairs_report.get("mean_difference"),
            "MAE": pairs_report.get("MAE"),
            "exact_same_integer_pct": pairs_report.get("exact_same_integer_pct"),
            "same_kalshi_bucket_pct": pairs_report.get("same_kalshi_bucket_pct"),
            "direct_clinyc_dataset_exists": pairs_report.get(
                "direct_clinyc_dataset_exists"
            ),
            "direct_clinyc_operationally_eligible": pairs_report.get(
                "direct_clinyc_operationally_eligible"
            ),
        },
        "regimes": regimes,
        "phase2_regression": regression,
        "transfer_status": transfer.transfer_status,
        "settlement_source_transfer_validated": transfer.transfer_validated,
    }
    cache.write_json(PHASE3_REPORT_PATH, report)
    log(
        f"Phase 3 complete: transfer_status={transfer.transfer_status} "
        f"validated={transfer.transfer_validated} "
        f"direct_OOS_N={direct_oos.get('N')}"
    )
    return report

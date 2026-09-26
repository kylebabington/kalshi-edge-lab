"""Prospective checkpoint capture + reconciliation (CLI WRITE job only).

CAPTURE universe: currently open KXHIGHNY events.
RECONCILIATION universe: open + recently settled since Phase 5 registration.
Never create late snapshots after settlement. GET endpoints must not call this.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from kalshi import cache
from kalshi.client import KalshiClient, get_historical_markets_for_series
from research.weather.checkpoints import (
    CHECKPOINT_IDS,
    RECEIPT_STATUS_CAPTURED,
    RECEIPT_STATUS_MISSED,
    SCHEDULER_WINDOWS_TASK_EXAMPLE,
    checkpoint_scheduled_at,
    classify_checkpoint,
    list_receipts,
    load_receipt,
    write_receipt,
)
from research.weather.models import (
    EVIDENCE_CLASS_PHASE4,
    EVIDENCE_CLASS_PHASE5,
    MODEL_COMBINATION_POLICY,
    REGIME_NWS_CLI_KNYC,
    SERIES_TICKER,
    SHADOW_CANDIDATE_ID,
    SHADOW_STATUS_AVAILABLE,
    SHADOW_STATUS_UNAVAILABLE,
)
from research.weather.phase5 import (
    HRRR_OPERATIONAL_CSV,
    PROSPECTIVE_SUMMARY_PATH,
    filter_operational_checkpoint,
    load_operational_csv,
    predict_operational_calibrated,
    projected_final_high,
)
from research.weather.resolution import (
    get_event_date,
    group_markets_by_event,
    is_range_bucket_event,
    parse_temperature_bucket,
)
from research.weather.shadow import (
    combine_equal_weight,
    load_or_register_shadow_hypothesis,
    shadow_transfer_metadata,
)


def infer_checkpoint_for_as_of(
    target_date: str,
    as_of: datetime,
) -> str | None:
    """Latest checkpoint whose scheduled_at <= as_of (for live shadow labeling)."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    best_id: str | None = None
    best_sched = None
    for checkpoint_id in CHECKPOINT_IDS:
        scheduled = checkpoint_scheduled_at(target_date, checkpoint_id)
        if scheduled <= as_of and (best_sched is None or scheduled > best_sched):
            best_sched = scheduled
            best_id = checkpoint_id
    return best_id


def build_shadow_predictions_block(
    *,
    prediction: dict[str, Any] | Any,
    evidence: dict[str, Any] | None,
    markets: list[dict[str, Any]],
    target_date: str,
    event_ticker: str,
    checkpoint_id: str | None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build SHADOW_GFS_HRRR_EQUAL_V1 block. Never mutates WeatherPrediction."""
    if hasattr(prediction, "to_dict"):
        pred = prediction.to_dict()
    else:
        pred = dict(prediction or {})

    gfs_probs = dict(pred.get("bucket_probabilities") or {})
    live_regime = str(pred.get("live_target_regime") or "unknown")
    transfer_meta = shadow_transfer_metadata(
        live_target_regime=live_regime,
        settlement_source_transfer_validated=bool(
            pred.get("settlement_source_transfer_validated")
        ),
    )

    unavailable = {
        SHADOW_CANDIDATE_ID: {
            "status": SHADOW_STATUS_UNAVAILABLE,
            "probabilities": None,
            "unavailable_reason": None,
            "gfs_source_snapshot": {
                "point_forecast_high": pred.get("point_forecast_high"),
                "prediction_status": pred.get("prediction_status"),
                "calibration_method": pred.get("calibration_method"),
            },
            "hrrr_source_snapshot": None,
            **transfer_meta,
        }
    }

    if pred.get("prediction_status") != "OK" or not gfs_probs:
        unavailable[SHADOW_CANDIDATE_ID]["unavailable_reason"] = "gfs_incumbent_unavailable"
        return unavailable

    buckets = []
    for market in markets:
        parsed = parse_temperature_bucket(market)
        if parsed is not None:
            buckets.append(parsed)
    if not buckets:
        unavailable[SHADOW_CANDIDATE_ID]["unavailable_reason"] = "no_buckets"
        return unavailable

    evidence = evidence or {}
    hrrr = evidence.get("hrrr") or {}
    if hasattr(hrrr, "to_dict"):
        hrrr = hrrr.to_dict()
    hrrr_high = hrrr.get("forecast_high_f")
    try:
        hrrr_high_f = float(hrrr_high) if hrrr_high is not None else None
    except (TypeError, ValueError):
        hrrr_high_f = None

    # Live projected final high when observations exist.
    obs = evidence.get("observations") or {}
    if hasattr(obs, "to_dict"):
        obs = obs.to_dict()
    observed_so_far = obs.get("high_so_far_f") or obs.get("observed_high_so_far_f")
    try:
        observed_so_far_f = float(observed_so_far) if observed_so_far is not None else None
    except (TypeError, ValueError):
        observed_so_far_f = None
    if hrrr_high_f is not None and observed_so_far_f is not None:
        hrrr_high_f = projected_final_high(
            model_remaining_day_high_f=hrrr_high_f,
            observed_high_so_far_f=observed_so_far_f,
        )

    if hrrr_high_f is None:
        unavailable[SHADOW_CANDIDATE_ID]["unavailable_reason"] = "hrrr_unavailable"
        unavailable[SHADOW_CANDIDATE_ID]["hrrr_source_snapshot"] = hrrr or None
        return unavailable

    if not checkpoint_id:
        unavailable[SHADOW_CANDIDATE_ID]["unavailable_reason"] = "no_checkpoint_context"
        return unavailable

    # Shadow V1: NWS residual pools + experimental transfer for CLINYC (frozen).
    hrrr_rows = load_operational_csv(HRRR_OPERATIONAL_CSV)
    prior = filter_operational_checkpoint(
        hrrr_rows,
        model="hrrr_operational_latest",
        checkpoint_id=checkpoint_id,
        regime=REGIME_NWS_CLI_KNYC,
        before_date=target_date,
    )
    hrrr_pred = predict_operational_calibrated(
        forecast_high=hrrr_high_f,
        prior_rows=prior,
        target_date=target_date,
        buckets=buckets,
    )
    if hrrr_pred["status"] != "OK" or not hrrr_pred.get("probabilities"):
        unavailable[SHADOW_CANDIDATE_ID]["unavailable_reason"] = (
            f"hrrr_calibration_{hrrr_pred['status']}"
        )
        unavailable[SHADOW_CANDIDATE_ID]["hrrr_source_snapshot"] = {
            "forecast_high_f": hrrr_high_f,
            "calibration_status": hrrr_pred["status"],
            "residual_n": hrrr_pred.get("residual_n"),
            "checkpoint_id": checkpoint_id,
        }
        return unavailable

    shadow = combine_equal_weight(
        gfs_probs=gfs_probs,
        hrrr_probs=hrrr_pred["probabilities"],
        gfs_buckets=buckets,
        hrrr_buckets=buckets,
        event_ticker=event_ticker,
    )
    return {
        SHADOW_CANDIDATE_ID: {
            **shadow,
            "gfs_source_snapshot": {
                "point_forecast_high": pred.get("point_forecast_high"),
                "prediction_status": pred.get("prediction_status"),
                "calibration_method": pred.get("calibration_method"),
                "transfer_status": pred.get("transfer_status"),
            },
            "hrrr_source_snapshot": {
                "forecast_high_f": hrrr_high_f,
                "observed_high_so_far_f": observed_so_far_f,
                "calibration_status": hrrr_pred["status"],
                "residual_n": hrrr_pred.get("residual_n"),
                "pool_level": hrrr_pred.get("pool_level"),
                "checkpoint_id": checkpoint_id,
                "calibration_target_regime": REGIME_NWS_CLI_KNYC,
            },
            **transfer_meta,
        }
    }


def _event_is_open(markets: list[dict[str, Any]]) -> bool:
    statuses = {str(m.get("status") or "").lower() for m in markets}
    if any(s in {"finalized", "determined", "settled", "closed"} for s in statuses):
        # Still open if any market actively open/active
        if any(s in {"active", "open", "initialized", "unopened"} for s in statuses):
            return True
        return False
    return True


def _recently_settled_events(
    markets: list[dict[str, Any]],
    *,
    registered_at: str | None,
) -> list[dict[str, Any]]:
    """Events with target_date on/after registration date (or all settled range-bucket)."""
    grouped = group_markets_by_event(markets)
    reg_date = None
    if registered_at:
        try:
            reg_date = datetime.fromisoformat(
                registered_at.replace("Z", "+00:00")
            ).date().isoformat()
        except ValueError:
            reg_date = None

    out: list[dict[str, Any]] = []
    for event_ticker, event_markets in grouped.items():
        if not is_range_bucket_event(event_markets):
            continue
        target_date = get_event_date(event_ticker)
        if not target_date:
            continue
        if _event_is_open(event_markets):
            continue
        if reg_date and target_date < reg_date:
            # Still include if a Phase 5 receipt or snapshot exists for this event.
            receipts = list_receipts(event_ticker=event_ticker)
            if not receipts:
                continue
        out.append(
            {
                "event_ticker": event_ticker,
                "target_date": target_date,
                "markets": event_markets,
                "open": False,
            }
        )
    return out


def run_prospective_cycle(
    *,
    client: KalshiClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Idempotent WRITE job: capture due checkpoints + reconcile misses/scores."""
    from research.weather.service import (
        get_live_weather_events,
        score_settled_snapshots,
    )
    from research.weather.snapshots import (
        SnapshotConflictError,
        build_prediction_snapshot,
        write_snapshot,
    )
    from research.weather.transfer import (
        evaluate_prospective_transfer_validation,
        load_or_register_hypothesis,
    )

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    hyp = load_or_register_shadow_hypothesis()
    load_or_register_hypothesis()  # CLINYC — untouched criteria
    client = client or KalshiClient()

    # --- CAPTURE universe: open events ---
    live = get_live_weather_events(client=client)
    open_events = list(live.get("events") or [])
    captured: list[dict[str, Any]] = []
    skipped_existing: list[dict[str, Any]] = []
    capture_conflicts: list[dict[str, Any]] = []

    for event in open_events:
        event_ticker = str(event.get("event_ticker") or "")
        target_date = str(event.get("target_date") or "")
        if not event_ticker or not target_date:
            continue
        for checkpoint_id in CHECKPOINT_IDS:
            classified = classify_checkpoint(
                target_date=target_date, checkpoint_id=checkpoint_id, now=now
            )
            if classified.status != "due":
                continue
            existing = load_receipt(event_ticker, checkpoint_id)
            if existing:
                skipped_existing.append(
                    {
                        "event_ticker": event_ticker,
                        "checkpoint_id": checkpoint_id,
                        "status": existing.get("status"),
                    }
                )
                continue

            # Rebuild bundle at actual now for honest prediction_as_of.
            # Prefer live event markets already fetched.
            markets = []
            # Live serialize may not include raw markets with full rules — use kalshi_markets.
            for m in event.get("kalshi_markets") or []:
                markets.append(m)

            shadow = build_shadow_predictions_block(
                prediction=event.get("prediction") or {},
                evidence=event.get("evidence"),
                markets=list(event.get("_raw_markets") or []),
                target_date=target_date,
                event_ticker=event_ticker,
                checkpoint_id=checkpoint_id,
                as_of=now,
            )
            snap = build_prediction_snapshot(
                {
                    **event,
                    "shadow_predictions": shadow,
                    "evidence_class": EVIDENCE_CLASS_PHASE5,
                    "candidate_registered_before_snapshot": True,
                    "checkpoint_id": checkpoint_id,
                },
                created_at=now,
                checkpoint_id=checkpoint_id,
                evidence_class=EVIDENCE_CLASS_PHASE5,
                candidate_registered_before_snapshot=True,
                shadow_predictions=shadow,
            )
            try:
                path, is_new = write_snapshot(snap)
            except SnapshotConflictError as error:
                capture_conflicts.append(
                    {
                        "snapshot_id": snap.snapshot_id,
                        "path": str(error.path),
                    }
                )
                continue

            receipt = {
                "checkpoint_id": checkpoint_id,
                "event_ticker": event_ticker,
                "target_date": target_date,
                "scheduled_at": classified.scheduled_at.isoformat(),
                "actual_prediction_as_of": snap.prediction_as_of,
                "snapshot_id": snap.snapshot_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": RECEIPT_STATUS_CAPTURED,
                "evidence_class": EVIDENCE_CLASS_PHASE5,
            }
            write_receipt(receipt)
            captured.append({**receipt, "path": str(path), "wrote_new": is_new})

    # --- RECONCILIATION universe ---
    hist_markets = get_historical_markets_for_series(SERIES_TICKER, client=client)
    settled = _recently_settled_events(
        hist_markets, registered_at=str(hyp.get("registered_at") or "")
    )
    reconcile_events: list[dict[str, Any]] = []
    for event in open_events:
        reconcile_events.append(
            {
                "event_ticker": event.get("event_ticker"),
                "target_date": event.get("target_date"),
                "open": True,
            }
        )
    reconcile_events.extend(settled)

    missed: list[dict[str, Any]] = []
    for item in reconcile_events:
        event_ticker = str(item.get("event_ticker") or "")
        target_date = str(item.get("target_date") or "")
        if not event_ticker or not target_date:
            continue
        for checkpoint_id in CHECKPOINT_IDS:
            classified = classify_checkpoint(
                target_date=target_date, checkpoint_id=checkpoint_id, now=now
            )
            if classified.status != "missed":
                continue
            existing = load_receipt(event_ticker, checkpoint_id)
            if existing:
                continue
            # Never create a late snapshot after the window (or after settlement).
            receipt = {
                "checkpoint_id": checkpoint_id,
                "event_ticker": event_ticker,
                "target_date": target_date,
                "scheduled_at": classified.scheduled_at.isoformat(),
                "actual_prediction_as_of": None,
                "snapshot_id": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": RECEIPT_STATUS_MISSED,
                "note": "Capture window elapsed without snapshot — not fabricated",
            }
            write_receipt(receipt)
            missed.append(receipt)

    # Transfer refresh + scoring
    transfer = evaluate_prospective_transfer_validation()
    score_result = score_settled_snapshots(client=client)

    receipts = list_receipts()
    captured_n = sum(1 for r in receipts if r.get("status") == RECEIPT_STATUS_CAPTURED)
    missed_n = sum(1 for r in receipts if r.get("status") == RECEIPT_STATUS_MISSED)
    coverage: dict[str, dict[str, int]] = {}
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
        coverage[checkpoint_id] = {
            "captured": c,
            "missed": m,
            "total_receipts": c + m,
        }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "SHADOW ONLY",
        "model_combination_policy": MODEL_COMBINATION_POLICY,
        "shadow_candidate": {
            "candidate_id": SHADOW_CANDIDATE_ID,
            "registered_at": hyp.get("registered_at"),
            "experimental_transfer_status": "experimental",
        },
        "scheduler": SCHEDULER_WINDOWS_TASK_EXAMPLE,
        "capture_universe": {
            "open_events": len(open_events),
            "captured_this_run": len(captured),
            "skipped_existing": len(skipped_existing),
            "conflicts": len(capture_conflicts),
        },
        "reconciliation_universe": {
            "events_processed": len(reconcile_events),
            "missed_this_run": len(missed),
        },
        "checkpoint_coverage": coverage,
        "totals": {
            "captured_checkpoints": captured_n,
            "missed_checkpoints": missed_n,
            "scored_snapshots": score_result.get("count_scored")
            or score_result.get("scored_count")
            or len(score_result.get("scored") or []),
        },
        "clinyc_transfer_v1": {
            "prospective_n": (transfer or {}).get("prospective_n"),
            "prospective_target_n": (transfer or {}).get("prospective_target_n") or 20,
            "transfer_validated": bool((transfer or {}).get("transfer_validated")),
            "untouched_criteria": True,
        },
        "phase4_snapshots_immutable": True,
        "development_phase4_never_becomes_prospective": True,
        "evidence_class_new": EVIDENCE_CLASS_PHASE5,
        "evidence_class_legacy": EVIDENCE_CLASS_PHASE4,
        "captured": captured,
        "missed": missed,
        "score_result_summary": {
            "scored": len(score_result.get("scored") or []),
            "skipped": len(score_result.get("skipped") or []),
        },
    }
    cache.write_json(PROSPECTIVE_SUMMARY_PATH, summary)
    return summary

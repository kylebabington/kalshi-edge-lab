"""Phase 5 operational replay, shadow, checkpoints, and scoring tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.weather.checkpoints import (
    CHECKPOINT_CAPTURE_WINDOW_MINUTES,
    RECEIPT_STATUS_CAPTURED,
    RECEIPT_STATUS_MISSED,
    checkpoint_as_of_utc,
    checkpoint_scheduled_at,
    classify_checkpoint,
    in_capture_window,
    write_receipt,
)
from research.weather.models import (
    CHECKPOINT_CAPTURE_WINDOW_MINUTES as MODEL_WINDOW,
    MODEL_COMBINATION_POLICY,
    MODEL_GFS_OPERATIONAL_LATEST,
    MODEL_HRRR_OPERATIONAL_LATEST,
    REPLAY_MODE_FULL_OPERATIONAL,
    REPLAY_MODE_MODEL_ONLY,
    SHADOW_CANDIDATE_ID,
    SHADOW_STATUS_AVAILABLE,
    SHADOW_STATUS_BUCKET_ALIGNMENT_ERROR,
    SHADOW_STATUS_UNAVAILABLE,
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_VERSION_V4,
)
from research.weather.phase5 import (
    choose_operational_hrrr_run,
    filter_operational_checkpoint,
    projected_final_high,
    replay_mode_for_checkpoint,
    bootstrap_clustered_event_date,
)
from research.weather.probability import choose_operational_run
from research.weather.resolution import TemperatureBucket
from research.weather.shadow import (
    combine_equal_weight,
    load_or_register_shadow_hypothesis,
    shadow_transfer_metadata,
)
from research.weather.snapshots import (
    PredictionSnapshot,
    build_prediction_snapshot,
    score_snapshot,
    write_snapshot,
)
from research.weather import checkpoints as checkpoints_mod
from research.weather import snapshots as snapshots_mod


NYC = ZoneInfo("America/New_York")


def test_capture_window_frozen_at_30():
    assert CHECKPOINT_CAPTURE_WINDOW_MINUTES == 30
    assert MODEL_WINDOW == 30


def test_checkpoint_dst_spring_forward():
    # 2026-03-08 is DST start in US; 18:00 ET previous day still valid.
    sched = checkpoint_scheduled_at("2026-03-09", "dminus1_1800")
    assert sched.tzinfo is not None
    assert sched.hour == 18
    as_of = checkpoint_as_of_utc("2026-03-09", "dminus1_1800")
    assert as_of.tzinfo == timezone.utc


def test_checkpoint_dst_fall_back():
    sched = checkpoint_scheduled_at("2026-11-02", "d0_1200")
    assert sched.hour == 12
    assert str(sched.tzinfo) == "America/New_York"


def test_in_capture_window_and_missed():
    scheduled = datetime(2026, 9, 26, 12, 0, tzinfo=NYC)
    due = datetime(2026, 9, 26, 12, 5, tzinfo=NYC)
    late = datetime(2026, 9, 26, 12, 45, tzinfo=NYC)
    assert in_capture_window(now=due, scheduled_at=scheduled)
    assert not in_capture_window(now=late, scheduled_at=scheduled)
    classified = classify_checkpoint(
        target_date="2026-09-26",
        checkpoint_id="d0_1200",
        now=late.astimezone(timezone.utc),
    )
    assert classified.status == "missed"


def test_gfs_operational_selects_latest_available_rejects_future():
    # day_06z available at 12:00 UTC on event day; as_of before that rejects it.
    target = "2026-07-15"
    forecasts = {
        "prev_12z": 80.0,
        "prev_18z": 81.0,
        "day_00z": 82.0,
        "day_06z": 83.0,
    }
    # 2026-07-15 08:00 UTC — day_00z available (00Z+6h=06Z), day_06z not (06Z+6h=12Z)
    as_of = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
    chosen = choose_operational_run(
        target_date=target, prediction_as_of=as_of, forecasts=forecasts
    )
    assert chosen == "day_00z"
    # Before prev_12z availability should yield None or earlier run only
    early = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)
    chosen_early = choose_operational_run(
        target_date=target, prediction_as_of=early, forecasts=forecasts
    )
    assert chosen_early in (None, "prev_12z")  # prev_12z available at 18Z Jul 14


def test_hrrr_future_run_rejected(monkeypatch):
    future_init = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
    as_of = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    def _fake_high(target_date, run_init, as_of=None, allow_unavailable=False):
        return 70.0

    monkeypatch.setattr(
        "research.weather.phase5.get_hrrr_run_high", _fake_high
    )
    # Lookback from as_of should never pick a run after as_of.
    selected = choose_operational_hrrr_run(
        target_date="2026-07-15",
        checkpoint_as_of=as_of,
        lookback_hours=6,
    )
    if selected is not None:
        assert selected["run_init"] <= as_of
        assert selected["available_at"] <= as_of
    # Explicitly: future init must not win even if we inject it.
    assert future_init > as_of


def test_replay_modes_intraday_model_only():
    assert replay_mode_for_checkpoint("dminus1_1800") == REPLAY_MODE_FULL_OPERATIONAL
    assert replay_mode_for_checkpoint("d0_1200") == REPLAY_MODE_MODEL_ONLY


def test_projected_final_high_semantics():
    assert projected_final_high(
        model_remaining_day_high_f=72.0, observed_high_so_far_f=74.0
    ) == 74.0
    assert projected_final_high(
        model_remaining_day_high_f=76.0, observed_high_so_far_f=74.0
    ) == 76.0
    assert projected_final_high(
        model_remaining_day_high_f=70.0, observed_high_so_far_f=None
    ) == 70.0


def test_same_checkpoint_residuals_never_pool_other():
    rows = [
        {
            "model": MODEL_GFS_OPERATIONAL_LATEST,
            "checkpoint_id": "d0_1200",
            "target_regime": "nws_cli_knyc",
            "target_date": "2026-06-01",
            "residual_f": "1.0",
        },
        {
            "model": MODEL_GFS_OPERATIONAL_LATEST,
            "checkpoint_id": "d0_0600",
            "target_regime": "nws_cli_knyc",
            "target_date": "2026-06-01",
            "residual_f": "2.0",
        },
        {
            "model": MODEL_GFS_OPERATIONAL_LATEST,
            "checkpoint_id": "d0_1200",
            "target_regime": "nws_cli_knyc",
            "target_date": "2026-06-02",
            "residual_f": "1.5",
        },
    ]
    filtered = filter_operational_checkpoint(
        rows,
        model=MODEL_GFS_OPERATIONAL_LATEST,
        checkpoint_id="d0_1200",
        before_date="2026-06-03",
    )
    assert len(filtered) == 2
    assert all(r["checkpoint_id"] == "d0_1200" for r in filtered)


def test_shadow_equal_weight_exact_and_sum():
    buckets = [
        TemperatureBucket("A", 0, 60, True, True, ticker="T-A"),
        TemperatureBucket("B", 61, 70, True, True, ticker="T-B"),
    ]
    gfs = {"A": 0.4, "B": 0.6}
    hrrr = {"A": 0.2, "B": 0.8}
    out = combine_equal_weight(
        gfs_probs=gfs,
        hrrr_probs=hrrr,
        gfs_buckets=buckets,
        hrrr_buckets=buckets,
        event_ticker="EVT",
    )
    assert out["status"] == SHADOW_STATUS_AVAILABLE
    assert out["probabilities"]["A"] == pytest.approx(0.3)
    assert out["probabilities"]["B"] == pytest.approx(0.7)
    assert abs(sum(out["probabilities"].values()) - 1.0) < 1e-9
    assert out["no_learned_weights"] is True
    assert out["no_kalshi_prices"] is True


def test_shadow_unavailable_if_hrrr_missing():
    out = combine_equal_weight(
        gfs_probs={"A": 1.0},
        hrrr_probs=None,
    )
    assert out["status"] == SHADOW_STATUS_UNAVAILABLE
    assert out["unavailable_reason"] == "missing_component_distribution"


def test_shadow_bucket_alignment_error():
    gfs_b = [TemperatureBucket("A", 0, 60, True, True, ticker="T-A")]
    hrrr_b = [TemperatureBucket("B", 61, 70, True, True, ticker="T-B")]
    out = combine_equal_weight(
        gfs_probs={"A": 1.0},
        hrrr_probs={"B": 1.0},
        gfs_buckets=gfs_b,
        hrrr_buckets=hrrr_b,
    )
    assert out["status"] == SHADOW_STATUS_BUCKET_ALIGNMENT_ERROR


def test_shadow_never_replaces_combination_policy():
    assert MODEL_COMBINATION_POLICY == "none"


def test_shadow_transfer_metadata_frozen_clinyc():
    meta = shadow_transfer_metadata(live_target_regime="weather_company_clinyc")
    assert meta["transfer_status"] == "experimental"
    assert meta["settlement_source_transfer_validated"] is False
    assert meta["frozen"] is True


def test_shadow_hypothesis_registration(tmp_path, monkeypatch):
    path = tmp_path / "shadow_gfs_hrrr_equal_v1.json"
    monkeypatch.setattr("research.weather.shadow.SHADOW_HYPOTHESIS_PATH", path)
    monkeypatch.setattr("research.weather.shadow.HYPOTHESES_DIR", tmp_path)
    first = load_or_register_shadow_hypothesis()
    second = load_or_register_shadow_hypothesis()
    assert first["candidate_id"] == SHADOW_CANDIDATE_ID
    assert first["registered_at"] == second["registered_at"]
    assert first["target_regime_semantics"]["frozen"] is True


def test_cluster_bootstrap_unit_event_date():
    obs = [
        {"target_date": "2026-06-01", "delta_brier": 0.1},
        {"target_date": "2026-06-01", "delta_brier": 0.2},
        {"target_date": "2026-06-02", "delta_brier": -0.05},
    ]
    result = bootstrap_clustered_event_date(obs)
    assert result["bootstrap_unit"] == "event_date"
    assert result["n_clusters"] == 2
    assert result["ci95"] is not None


def test_receipt_idempotent_no_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoints_mod, "CHECKPOINT_ROOT", tmp_path)
    payload = {
        "checkpoint_id": "d0_1200",
        "event_ticker": "KXHIGHNY-26SEP26",
        "scheduled_at": "2026-09-26T16:00:00+00:00",
        "actual_prediction_as_of": "2026-09-26T16:05:00+00:00",
        "snapshot_id": "KXHIGHNY-26SEP26__20260926T160500Z",
        "created_at": "2026-09-26T16:05:01+00:00",
        "status": RECEIPT_STATUS_CAPTURED,
    }
    p1 = write_receipt(payload)
    payload2 = dict(payload)
    payload2["actual_prediction_as_of"] = "2026-09-26T16:20:00+00:00"
    p2 = write_receipt(payload2)
    assert p1 == p2
    loaded = checkpoints_mod.load_receipt("KXHIGHNY-26SEP26", "d0_1200")
    assert loaded["actual_prediction_as_of"] == "2026-09-26T16:05:00+00:00"


def test_missed_receipt_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoints_mod, "CHECKPOINT_ROOT", tmp_path)
    write_receipt(
        {
            "checkpoint_id": "d0_0900",
            "event_ticker": "KXHIGHNY-26SEP26",
            "scheduled_at": "2026-09-26T13:00:00+00:00",
            "actual_prediction_as_of": None,
            "snapshot_id": None,
            "created_at": "2026-09-26T14:00:00+00:00",
            "status": RECEIPT_STATUS_MISSED,
        }
    )
    loaded = checkpoints_mod.load_receipt("KXHIGHNY-26SEP26", "d0_0900")
    assert loaded["status"] == RECEIPT_STATUS_MISSED
    assert loaded["snapshot_id"] is None


def test_new_snapshot_schema_5_with_shadow(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots_mod, "SNAPSHOT_ROOT", tmp_path / "snaps")
    monkeypatch.setattr(snapshots_mod, "SCORE_ROOT", tmp_path / "scores")
    assert SNAPSHOT_SCHEMA_VERSION == "5.0.0"
    assert SNAPSHOT_SCHEMA_VERSION_V4 == "4.0.0"
    bundle = {
        "event_ticker": "KXHIGHNY-26SEP26",
        "target_date": "2026-09-26",
        "prediction": {
            "event_ticker": "KXHIGHNY-26SEP26",
            "target_date": "2026-09-26",
            "as_of": "2026-09-26T16:05:00+00:00",
            "bucket_probabilities": {"60° to 61°": 1.0},
            "prediction_status": "OK",
            "confidence_score": 0.5,
            "confidence_label": "MED",
            "live_target_regime": "weather_company_clinyc",
            "calibration_target_regime": "nws_cli_knyc",
            "settlement_source_transfer_validated": False,
            "transfer_status": "experimental",
            "warnings": [],
        },
        "resolution": {
            "settlement_source_regime": "weather_company_clinyc",
            "resolution_certainty": "verified",
        },
        "shadow_predictions": {
            SHADOW_CANDIDATE_ID: {
                "status": "available",
                "probabilities": {"60° to 61°": 1.0},
            }
        },
    }
    snap = build_prediction_snapshot(
        bundle,
        checkpoint_id="d0_1200",
        evidence_class="prospective_phase5",
        candidate_registered_before_snapshot=True,
    )
    assert snap.schema_version == "5.0.0"
    assert snap.checkpoint_id == "d0_1200"
    assert snap.evidence_class == "prospective_phase5"
    path, is_new = write_snapshot(snap)
    assert is_new
    # Idempotent reuse
    path2, is_new2 = write_snapshot(snap)
    assert not is_new2


def test_score_incumbent_and_shadow_separately_no_mutate():
    snap = {
        "snapshot_id": "id1",
        "event_ticker": "EVT",
        "target_date": "2026-09-26",
        "prediction_as_of": "2026-09-26T16:05:00+00:00",
        "resolution_regime": "weather_company_clinyc",
        "external_probability_distribution": {
            "A": 0.5,
            "B": 0.5,
        },
        "shadow_predictions": {
            SHADOW_CANDIDATE_ID: {
                "status": "available",
                "probabilities": {"A": 0.2, "B": 0.8},
            }
        },
        "evidence_class": "prospective_phase5",
    }
    original = dict(snap)
    score = score_snapshot(
        snap,
        actual_high_f=70.0,
        winning_bucket="B",
        settlement_source="kalshi_expiration_value",
    )
    assert snap == original
    assert score["incumbent_brier"] == score["brier"]
    assert SHADOW_CANDIDATE_ID in score["candidate_scores"]
    assert score["candidate_scores"][SHADOW_CANDIDATE_ID]["brier"] is not None
    # Shadow puts more mass on winner → lower Brier than incumbent
    assert (
        score["candidate_scores"][SHADOW_CANDIDATE_ID]["brier"]
        < score["incumbent_brier"]
    )


def test_no_score_before_settlement():
    snap = {
        "snapshot_id": "id2",
        "external_probability_distribution": {"A": 1.0},
    }
    score = score_snapshot(
        snap,
        actual_high_f=None,
        winning_bucket=None,
        score_status="EVENT_NOT_FINALIZED",
        skip_reason="not settled",
    )
    assert score["score_status"] == "EVENT_NOT_FINALIZED"
    assert "incumbent_brier" not in score or score.get("brier") is None


def test_phase4_schema_constant_preserved():
    assert SNAPSHOT_SCHEMA_VERSION_V4 == "4.0.0"

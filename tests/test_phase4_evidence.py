"""Phase 4 evidence / HRRR / snapshot tests (incl. six completion amendments)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.weather.evidence import (
    compute_source_agreement,
    disagreement_label,
    evidence_available_at_gate,
    freshness_status_from_seconds,
    retrieved_at_gate,
)
from research.weather.models import (
    FRESHNESS_AGING_SECONDS,
    FRESHNESS_FRESH_SECONDS,
    HRRR_MODEL,
    HRRR_OPERATIONAL_SELECTION_POLICY,
    HRRR_PUBLICATION_LATENCY,
    HRRR_STREAM_EXACT,
    HRRR_STREAM_PREVIOUS_DAY1,
    MODEL_COMBINATION_POLICY,
    SNAPSHOT_SCHEMA_VERSION,
    WeatherPrediction,
)
from research.weather.phase4 import (
    filter_hrrr_stream,
    write_methodology_report,
)
from research.weather.snapshots import (
    SCORE_STATUS_SETTLEMENT_UNAVAILABLE,
    SnapshotConflictError,
    build_prediction_snapshot,
    lead_bin_hours,
    load_snapshot,
    score_snapshot,
    write_score_artifact,
    write_snapshot,
)
from research.weather.sources.discussion import extract_afd_structure, parse_afd_product
from research.weather.sources.hrrr import (
    hrrr_available_as_of,
    is_hrrr_run_available,
    parse_hrrr_hourly_payload,
)
from research.weather.sources.nws_forecast import parse_nws_forecast_payloads
from research.weather.sources.observations import build_observation_trajectory


def _sample_prediction(**overrides) -> WeatherPrediction:
    base = dict(
        event_ticker="KXHIGHNY-26SEP26",
        target_date="2026-09-26",
        as_of="2026-09-26T12:00:00+00:00",
        forecast_run="day_00z",
        run_id="day_00z",
        point_forecast_high=60.0,
        residual_sample_size=10,
        expected_high=60.5,
        median_high=60.0,
        p10_high=57.0,
        p25_high=59.0,
        p50_high=60.0,
        p75_high=62.0,
        p90_high=64.0,
        bucket_probabilities={"60° to 61°": 1.0},
        confidence_score=0.4,
        confidence_label="MEDIUM",
        model_agreement="UNKNOWN",
        calibration_quality="ok",
        prediction_status="OK",
        calibration_target_regime="nws_cli_knyc",
        live_target_regime="nws_cli_knyc",
        settlement_source_transfer_validated=True,
    )
    base.update(overrides)
    return WeatherPrediction(**base)


def test_freshness_thresholds():
    assert freshness_status_from_seconds(100) == "fresh"
    assert freshness_status_from_seconds(FRESHNESS_FRESH_SECONDS) == "fresh"
    assert freshness_status_from_seconds(FRESHNESS_FRESH_SECONDS + 1) == "aging"
    assert freshness_status_from_seconds(FRESHNESS_AGING_SECONDS + 1) == "stale"
    assert freshness_status_from_seconds(None) == "unavailable"


def test_disagreement_labels():
    assert disagreement_label(1.0) == "LOW DISAGREEMENT"
    assert disagreement_label(3.0) == "MODERATE DISAGREEMENT"
    assert disagreement_label(5.0) == "HIGH DISAGREEMENT"


def test_nws_point_parsing_hourly_and_grid_optional():
    target = "2026-09-26"
    forecast_payload = {
        "properties": {
            "updateTime": "2026-09-26T10:00:00+00:00",
            "periods": [
                {
                    "startTime": "2026-09-26T12:00:00-04:00",
                    "endTime": "2026-09-26T18:00:00-04:00",
                    "isDaytime": True,
                    "temperature": 72,
                    "temperatureUnit": "F",
                }
            ],
        }
    }
    hourly_payload = {
        "properties": {
            "updateTime": "2026-09-26T10:00:00+00:00",
            "periods": [
                {
                    "startTime": "2026-09-26T13:00:00-04:00",
                    "temperature": 70,
                    "temperatureUnit": "F",
                },
                {
                    "startTime": "2026-09-26T15:00:00-04:00",
                    "temperature": 74,
                    "temperatureUnit": "F",
                },
            ],
        }
    }
    # Grid missing optional humidity/skyCover — must not fail.
    grid_payload = {
        "properties": {
            "updateTime": "2026-09-26T10:00:00+00:00",
            "temperature": {
                "values": [
                    {
                        "validTime": "2026-09-26T17:00:00+00:00/PT1H",
                        "value": 22.0,
                    }
                ]
            },
        }
    }
    point = {"latitude": 40.77, "longitude": -73.97, "wfo": "OKX", "gridId": "OKX"}
    snap = parse_nws_forecast_payloads(
        target_date=target,
        point=point,
        forecast_payload=forecast_payload,
        hourly_payload=hourly_payload,
        grid_payload=grid_payload,
    )
    assert snap.forecast_high_f == 72
    assert snap.hourly_high_f == 74
    assert "temperature" in (snap.grid_metadata.get("grid_day") or {})
    assert "relativeHumidity" not in (snap.grid_metadata.get("grid_day") or {})


def test_afd_extraction_and_unavailable_does_not_raise():
    text = """
.SYNOPSIS...
High pressure builds tonight.
High confidence in temperatures near 60.
Models disagree on cloud cover.
HRRR and GFS differ on timing.
Adjusted highs downward from previous forecast.
"""
    extracted = extract_afd_structure(text)
    assert extracted["confidence_wording"]
    assert extracted["model_references"]
    snap = parse_afd_product(
        {
            "properties": {
                "productText": text,
                "issuanceTime": "2026-09-26T09:00:00+00:00",
                "id": "AFDOKX",
                "issuingOffice": "https://api.weather.gov/offices/OKX",
            }
        }
    )
    assert snap.wfo == "OKX"
    assert snap.raw_text
    assert snap.issued_at


def test_hrrr_publication_latency_and_future_run_rejected():
    as_of = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    future = as_of + timedelta(hours=1)
    assert not is_hrrr_run_available(run_init=future, as_of=as_of)

    recent = as_of - timedelta(hours=1)
    # 1h ago is not yet available under 3h latency
    assert not is_hrrr_run_available(run_init=recent, as_of=as_of)

    old = as_of - timedelta(hours=4)
    assert is_hrrr_run_available(run_init=old, as_of=as_of)
    assert hrrr_available_as_of(old) == old + HRRR_PUBLICATION_LATENCY

    payload = {
        "hourly": {
            "time": ["2026-09-26T12:00", "2026-09-26T13:00"],
            "temperature_2m": [68.0, 70.0],
        }
    }
    rejected = parse_hrrr_hourly_payload(
        payload, "2026-09-26", run_init=future, as_of=as_of
    )
    assert rejected.quality_status == "unavailable"
    ok = parse_hrrr_hourly_payload(payload, "2026-09-26", run_init=old, as_of=as_of)
    assert ok.forecast_high_f == 70.0
    assert ok.model == HRRR_MODEL


def test_hrrr_streams_never_pooled():
    from research.weather.phase4 import HRRR_CSV_FIELDS

    assert "forecast_stream" in HRRR_CSV_FIELDS
    assert MODEL_COMBINATION_POLICY == "none"
    assert HRRR_OPERATIONAL_SELECTION_POLICY == "latest_available_by_latency"

    rows = [
        {
            "forecast_stream": HRRR_STREAM_EXACT,
            "run_id": "hrrr_exact_12z",
            "residual_f": "1.0",
        },
        {
            "forecast_stream": HRRR_STREAM_PREVIOUS_DAY1,
            "run_id": "hrrr_previous_day1",
            "residual_f": "2.0",
        },
    ]
    exact = filter_hrrr_stream(rows, forecast_stream=HRRR_STREAM_EXACT)
    prev = filter_hrrr_stream(rows, forecast_stream=HRRR_STREAM_PREVIOUS_DAY1)
    assert len(exact) == 1
    assert exact[0]["run_id"] == "hrrr_exact_12z"
    assert len(prev) == 1
    # Mixing streams must never inflate N for either pool.
    assert len(exact) + len(prev) == len(rows)


def test_available_at_gate_allows_future_valid_times():
    """Forecast valid_to may be after prediction_as_of; available_at may not."""
    prediction_as_of = "2026-09-26T12:00:00+00:00"

    # VALID: available before as_of, forecast valid into the afternoon.
    assert evidence_available_at_gate(
        available_at="2026-09-26T11:20:00+00:00",
        prediction_as_of=prediction_as_of,
    )

    # INVALID: available after as_of (look-ahead).
    assert not evidence_available_at_gate(
        available_at="2026-09-26T12:35:00+00:00",
        prediction_as_of=prediction_as_of,
    )

    # NWS / AFD issued after as_of => unavailable.
    assert not evidence_available_at_gate(
        issued_at="2026-09-26T13:00:00+00:00",
        prediction_as_of=prediction_as_of,
    )
    assert evidence_available_at_gate(
        issued_at="2026-09-26T11:00:00+00:00",
        prediction_as_of=prediction_as_of,
    )

    # retrieved_at must be <= snapshot created_at for live capture.
    assert retrieved_at_gate(
        retrieved_at="2026-09-26T12:00:00+00:00",
        snapshot_created_at="2026-09-26T12:00:05+00:00",
    )
    assert not retrieved_at_gate(
        retrieved_at="2026-09-26T12:01:00+00:00",
        snapshot_created_at="2026-09-26T12:00:00+00:00",
    )


def test_observation_trajectory():
    base = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    obs = [
        {"timestamp": base, "temperature_f": 55.0, "description": "Clear"},
        {
            "timestamp": base + timedelta(hours=1),
            "temperature_f": 57.0,
            "description": "Clear",
        },
        {
            "timestamp": base + timedelta(hours=3),
            "temperature_f": 60.0,
            "description": "Sunny",
        },
        # future relative to as_of — must be excluded
        {
            "timestamp": base + timedelta(hours=5),
            "temperature_f": 99.0,
            "description": "Future",
        },
    ]
    as_of = base + timedelta(hours=3, minutes=5)
    snap = build_observation_trajectory(
        obs, target_date="2026-09-26", as_of=as_of
    )
    assert snap.latest_temperature_f == 60.0
    assert snap.high_so_far_f == 60.0
    assert snap.change_1h_f is not None or snap.change_3h_f is not None
    assert all(row["temperature_f"] != 99.0 for row in snap.trajectory)


def test_immutable_snapshot_idempotent_and_conflict(tmp_path, monkeypatch):
    from research.weather import snapshots as snapmod

    monkeypatch.setattr(snapmod, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(snapmod, "SCORE_ROOT", tmp_path / "scores")

    prediction = _sample_prediction()
    bundle = {
        "event_ticker": "KXHIGHNY-26SEP26",
        "target_date": "2026-09-26",
        "resolution": {
            "settlement_source_regime": "nws_cli_knyc",
            "resolution_certainty": "verified",
        },
        "prediction": prediction,
        "evidence": {
            "gfs": {
                "forecast_high_f": 60.0,
                "available_at": "2026-09-26T06:00:00+00:00",
                "model_run_at": "2026-09-26T00:00:00+00:00",
            }
        },
        "kalshi_markets": [
            {
                "ticker": "X",
                "label": "60° to 61°",
                "yes_bid_dollars": 0.4,
                "yes_ask_dollars": 0.5,
                "mid_dollars": 0.45,
                "spread_dollars": 0.1,
            }
        ],
        "research_status": {"mode": "RESEARCH_ONLY"},
        "warnings": [],
    }
    snap = build_prediction_snapshot(bundle, created_at=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc))
    assert snap.schema_version == SNAPSHOT_SCHEMA_VERSION
    assert "yes_bid_dollars" not in snap.prediction
    assert snap.kalshi_quote_sidecar["markets"]

    path, wrote = write_snapshot(snap)
    assert wrote is True

    # Same content (different created_at) => idempotent reuse.
    snap2 = build_prediction_snapshot(
        bundle, created_at=datetime(2026, 9, 26, 12, 0, 5, tzinfo=timezone.utc)
    )
    path2, wrote2 = write_snapshot(snap2)
    assert wrote2 is False
    assert path == path2

    # Different research content, same identity => SNAPSHOT_CONFLICT.
    bundle_conflict = dict(bundle)
    bundle_conflict["evidence"] = {
        "gfs": {
            "forecast_high_f": 99.0,
            "available_at": "2026-09-26T06:00:00+00:00",
        }
    }
    snap_conflict = build_prediction_snapshot(
        bundle_conflict,
        created_at=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(SnapshotConflictError):
        write_snapshot(snap_conflict)

    loaded = load_snapshot(snap.snapshot_id)
    assert loaded is not None
    assert loaded["evidence"]["gfs"]["forecast_high_f"] == 60.0

    # Scoring is separate; snapshot unchanged.
    score = score_snapshot(
        loaded, actual_high_f=61.0, winning_bucket="60° to 61°"
    )
    assert score["score_status"] == "OK"
    score_path = write_score_artifact(score)
    assert score_path.exists()
    reloaded = load_snapshot(snap.snapshot_id)
    assert "actual_high_f" not in reloaded
    assert lead_bin_hours(score["lead_hours_to_settlement_approx"]) in {
        ">24h",
        "12–24h",
        "6–12h",
        "3–6h",
        "<3h",
        "unknown",
    }


def test_scoring_honors_regime_no_knyc_fallback_for_clinyc(tmp_path, monkeypatch):
    from research.weather import snapshots as snapmod

    monkeypatch.setattr(snapmod, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(snapmod, "SCORE_ROOT", tmp_path / "scores")

    prediction = _sample_prediction(
        calibration_target_regime="weather_company_clinyc",
        live_target_regime="weather_company_clinyc",
    )
    bundle = {
        "event_ticker": "KXHIGHNY-26SEP26",
        "target_date": "2026-09-26",
        "resolution": {
            "settlement_source_regime": "weather_company_clinyc",
            "resolution_certainty": "verified_weather_company",
        },
        "prediction": prediction,
        "evidence": {},
        "kalshi_markets": [],
        "research_status": {"mode": "RESEARCH_ONLY"},
        "warnings": [],
    }
    snap = build_prediction_snapshot(bundle)
    write_snapshot(snap)
    loaded = load_snapshot(snap.snapshot_id)
    assert loaded["resolution_regime"] == "weather_company_clinyc"

    # Missing CLINYC settlement => SETTLEMENT_UNAVAILABLE, not KNYC fill-in.
    unscored = score_snapshot(
        loaded,
        actual_high_f=None,
        winning_bucket=None,
        score_status=SCORE_STATUS_SETTLEMENT_UNAVAILABLE,
        skip_reason="CLINYC expiration_value unavailable — refusing KNYC fallback",
    )
    assert unscored["score_status"] == SCORE_STATUS_SETTLEMENT_UNAVAILABLE
    assert "KNYC" in (unscored.get("skip_reason") or "")
    write_score_artifact(unscored)
    # Snapshot untouched.
    assert "actual_high_f" not in load_snapshot(snap.snapshot_id)


def test_agreement_descriptive_only():
    agreement = compute_source_agreement(
        gfs_raw_high=70.0,
        hrrr_raw_high=68.0,
        nws_forecast_high=69.0,
        gefs_median=69.5,
        high_so_far=65.0,
    )
    assert agreement["policy"] == "descriptive_only_no_blend"
    assert agreement["max_min_disagreement_f"] == pytest.approx(2.0)


def test_methodology_report_written(tmp_path, monkeypatch):
    from kalshi import cache
    from research.weather import phase4

    monkeypatch.setattr(phase4, "METHODOLOGY_PATH", tmp_path / "weather_evidence_methodology.json")
    path = write_methodology_report()
    assert path.exists()
    payload = cache.read_json(path)
    assert payload["model_combination_policy"] == "none"
    hrrr = next(s for s in payload["sources"] if s["source_id"] == "hrrr")
    assert hrrr["operational_selection_policy"] == "latest_available_by_latency"
    streams = {s["forecast_stream"] for s in hrrr["forecast_streams"]}
    assert HRRR_STREAM_EXACT in streams
    assert HRRR_STREAM_PREVIOUS_DAY1 in streams
    assert payload["availability_assumptions"]["no_lookahead_gate"].startswith(
        "available_at"
    )

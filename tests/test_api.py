"""API adapter tests — no historical rebuild side effects."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.app import app
from research.weather.models import WeatherPrediction


client = TestClient(app)


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "research_only"


def test_model_summary_read_only():
    response = client.get("/api/weather/model/summary")
    assert response.status_code == 200
    payload = response.json()
    assert "model_summary_available" in payload
    assert payload["mode"] == "RESEARCH_ONLY"
    assert "methodology_constants" in payload
    # Must not invent trading recommendations.
    assert payload.get("research_status", {}).get("live_recommendations") == "DISABLED"


def test_weather_prediction_has_no_kalshi_price_fields():
    prediction = WeatherPrediction(
        event_ticker="KXHIGHNY-26SEP22",
        target_date="2026-09-22",
        as_of="2026-09-22T12:00:00+00:00",
        forecast_run="day_00z",
        run_id="day_00z",
        point_forecast_high=60.0,
        residual_sample_size=0,
        expected_high=None,
        median_high=None,
        p10_high=None,
        p25_high=None,
        p50_high=None,
        p75_high=None,
        p90_high=None,
        bucket_probabilities={},
        confidence_score=0.0,
        confidence_label="LOW",
        model_agreement="UNKNOWN",
        calibration_quality="none",
        prediction_status="INSUFFICIENT_TARGET_REGIME_HISTORY",
        calibration_target_regime="weather_company_clinyc",
        live_target_regime="weather_company_clinyc",
        settlement_source_transfer_validated=False,
    )
    keys = set(prediction.to_dict())
    for banned in (
        "yes_bid",
        "yes_ask",
        "yes_bid_dollars",
        "yes_ask_dollars",
        "mid",
        "spread",
        "last_price",
    ):
        assert banned not in keys

"""Shared builders for opportunity / AI tests (no network, no Ollama)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.ai.models import AIResponse
from research.opportunities.models import Opportunity

NY = ZoneInfo("America/New_York")
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
TRANSFER_REASONS = ["calibration N=114 (>= run threshold 80)", "model_agreement=LOW",
                    "settlement_source_transfer_validated=false",
                    "settlement source mismatch - experimental display only"]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def weather_event_ticker(now: datetime) -> tuple[str, str]:
    d = now.astimezone(NY).date()
    return f"KXHIGHNY-{d:%y}{MONTHS[d.month - 1]}{d:%d}", d.isoformat()


def make_opp(category: str = "weather", **kw: Any) -> Opportunity:
    """An unpriced opportunity with valid defaults; override any field or ``ctx`` keys."""
    ctx_over = kw.pop("ctx", {})
    if category == "weather":
        ctx = {"event_open": True, "transfer_validated": True, "transfer_status": "validated",
               "confidence_reasons": ["calibration N=114 (>= run threshold 80)"], "source_warnings": [],
               "evidence": {}, "percentiles": {}}
        base = dict(confidence_score=0.75, confidence_label="HIGH", confidence_source="weather_model",
                    model_agreement="HIGH", sample_size=114, calibration_quality="month_same_run:adequate")
    else:
        ctx = {"event_open": True, "capture_status": "WINDOW_CAPTURE", "checksum_ok": True,
               "contract_status": "ok", "cohort": "strict", "model_source": "calibrated",
               "candidates": {"frozen": 0.62, "calibrated": 0.6, "blend": 0.58},
               "candidate_unavailable": {}}
        base = dict(confidence_source=None, model_agreement="HIGH", sample_size=None, calibration_quality="calibrated")
    ctx.update(ctx_over)
    fields: dict[str, Any] = dict(
        id=f"{category}:T-1", ticker="T-1", event_ticker="EV-1", event_key="EV-1", category=category,
        title="Test market", contract_label="label", series="KXHIGHNY" if category == "weather" else "KXNFLGAME",
        model_probability=0.6, market_probability=0.45, yes_bid=0.44, yes_ask=0.46, spread=0.02,
        prediction_as_of="2026-10-15T16:00:00+00:00", quote_as_of="2026-10-15T16:00:00+00:00",
        data_age_seconds=600.0, prediction_status="OK", domain_context=ctx, **base)
    fields.update(kw)
    if "id" not in kw:
        fields["id"] = f"{category}:{fields['ticker']}"
    if {"yes_bid", "yes_ask"} & set(kw) and "spread" not in kw:
        b, a = fields["yes_bid"], fields["yes_ask"]
        fields["spread"] = (a - b) if a is not None and b is not None else None
    return Opportunity(**fields)


def write_fee_bundle(fee_dir: Path, series: str, multiplier: float = 1.0) -> None:
    fee_dir.mkdir(parents=True, exist_ok=True)
    (fee_dir / f"{series}.json").write_text(json.dumps({
        "fee_changes": [], "resolution_status": "ok",
        "series_metadata": {"fee_type": "quadratic", "fee_multiplier": multiplier}}), encoding="utf-8")


def weather_snapshot(now: datetime, *, status: str = "OK", age_s: float = 600, transfer_validated: bool = False,
                     confidence_score: float = 0.30, confidence_label: str = "LOW") -> dict:
    event, target = weather_event_ticker(now)
    as_of = now - timedelta(seconds=age_s)
    probs = {"63° or below": 0.30, "64° to 65°": 0.45, "66° to 67°": 0.20, "68° or above": 0.05}
    if status != "OK":
        probs = {k: 0.0 for k in probs}
    quotes = [("T64", "63° or below", 0.04, 0.05), ("B64.5", "64° to 65°", 0.40, 0.42),
              ("B66.5", "66° to 67°", 0.30, 0.33), ("T67", "68° or above", 0.20, 0.22)]
    return {
        "snapshot_id": f"{event}__x", "event_ticker": event, "target_date": target,
        "prediction_as_of": iso(as_of), "created_at": iso(as_of), "prediction_status": status,
        "transfer_status": "validated" if transfer_validated else "experimental",
        "calibration_method": "knyc_to_clinyc_identity_transfer",
        "external_probability_distribution": probs,
        "confidence": {"score": confidence_score, "label": confidence_label, "model_agreement": "LOW",
                       "calibration_quality": "same_run_global:adequate"},
        "prediction": {"settlement_source_transfer_validated": transfer_validated, "residual_sample_size": 114,
                       "expected_high": 64.8, "median_high": 65, "p10_high": 62, "p90_high": 68,
                       "provenance": {"confidence_reasons": TRANSFER_REASONS if not transfer_validated else
                                      TRANSFER_REASONS[:2]}},
        "research_status": {"settlement_source_transfer_validated": transfer_validated,
                            "transfer_status": "experimental", "target_regime_match": "EXPERIMENTAL_IDENTITY_TRANSFER"},
        "warnings": ["RESEARCH_ONLY / NO_BET: knyc_to_clinyc_identity_transfer is experimental"],
        "evidence": {
            "gfs": {"source_name": "Calibrated GFS", "forecast_high_f": 66.0, "model_run_at": iso(as_of),
                    "freshness_status": "fresh", "quality_status": "ok"},
            "observations": {"station_id": "KNYC", "latest_temperature_f": 60.1, "high_so_far_f": 61.0,
                             "latest_timestamp": iso(as_of)},
            "nws_discussion": {"wfo": "OKX", "product_id": "abc", "issued_at": iso(as_of),
                               "raw_text": "Highs in the mid 60s under partly cloudy skies.",
                               "temperature_discussion": ["Highs in the mid 60s"]},
            "agreement": {"disagreement_label": "LOW DISAGREEMENT", "max_min_disagreement_f": 1.2,
                          "sources": {"gfs_raw": 66.0, "nws_forecast_high": 65.0}},
        },
        "kalshi_quote_sidecar": {"markets": [
            {"ticker": f"{event}-{suffix}", "label": label, "yes_bid_dollars": bid, "yes_ask_dollars": ask,
             "mid_dollars": (bid + ask) / 2, "status": "active", "result": ""}
            for suffix, label, bid, ask in quotes]},
    }


def write_weather_snapshot(root: Path, snap: dict) -> Path:
    d = root / snap["event_ticker"]
    d.mkdir(parents=True, exist_ok=True)
    p = d / "latest.json"
    p.write_text(json.dumps(snap), encoding="utf-8")
    return p


def research_json(**over: Any) -> str:
    body = {
        "summary": "The model sits above the market because forecasts cluster in the mid 60s.",
        "supporting_evidence": [{"statement": "GFS forecast high is 66 F", "basis": "FACT", "evidence_ids": ["E1"]}],
        "contradictory_evidence": [{"statement": "Observations are running cool", "basis": "INFERENCE",
                                    "evidence_ids": ["E2"]}],
        "risk_factors": [{"statement": "Settlement source differs from calibration source", "basis": "FACT",
                          "evidence_ids": ["E1"]}],
        "important_changes": [],
        "unknowns": ["Afternoon cloud cover"],
        "research_confidence": "MODERATE",
        "model_probability_should_not_be_changed": True,
    }
    body.update(over)
    return json.dumps(body)


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self.text = text if text is not None else research_json()
        self.error = error
        self.calls: list[dict] = []

    def generate(self, prompt, *, system_prompt=None, json_schema=None):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt, "json_schema": json_schema})
        if self.error:
            raise self.error
        return AIResponse(text=self.text, provider=self.name, model=self.model, duration_s=0.01)

    def status(self):
        return {"provider": self.name, "model": self.model, "available": True, "code": "ok"}

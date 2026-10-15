"""AI research service, validation, caching and providers. Mocked; never needs Ollama or internet."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from research.ai.config import AISettings
from research.ai.evidence import SPORTS_DATA_GAP, gather_evidence
from research.ai.models import ResearchValidationError, claims_transfer_validated, validate_research
from research.ai.ollama_provider import OllamaProvider
from research.ai.prompts import SYSTEM_PROMPT, build_prompt
from research.ai.provider import (
    MalformedProviderResponse,
    ModelMissing,
    NullProvider,
    ProviderTimeout,
    ProviderUnavailable,
)
from research.ai.research_service import research_opportunity, research_state, run_auto_research
from research.ai.eligibility import check_eligibility
from research.opportunities.engine import evaluate
from research.opportunities.quality import TRANSFER_WARNING, QualitySettings
from tests.opportunity_helpers import FakeProvider, make_opp, research_json, write_fee_bundle

NOW = datetime(2026, 10, 15, 16, 10, tzinfo=timezone.utc)
TRANSFER_CTX = {"transfer_validated": False, "transfer_status": "experimental",
                "confidence_reasons": ["settlement_source_transfer_validated=false"],
                "evidence": {"nws_discussion": {"raw_text": "Highs in the mid 60s.", "issued_at": "2026-10-15T11:50:00Z",
                                                "product_id": "p1"}}}


@pytest.fixture
def settings(tmp_path: Path) -> AISettings:
    return AISettings(provider="ollama", cache_dir=tmp_path / "ai")


@pytest.fixture
def fee_dir(tmp_path: Path) -> Path:
    d = tmp_path / "fees"
    write_fee_bundle(d, "KXHIGHNY")
    write_fee_bundle(d, "KXNFLGAME")
    return d


def _opp(fee_dir, **kw):
    (o,) = evaluate([make_opp(**kw)], NOW, QualitySettings(), fee_dir=fee_dir)
    return o


# ---------------------------------------------------------------- service

def test_research_ok_then_cached(settings, fee_dir):
    opp, fake = _opp(fee_dir), FakeProvider()
    first = research_opportunity(opp, settings=settings, provider=fake, now=NOW)
    assert first["status"] == "ok" and first["freshness"] == "fresh"
    assert first["research"]["summary"] and first["note"].startswith("AI research does not modify")
    assert first["provider"] == "fake" and first["model"] == "fake-model"
    second = research_opportunity(opp, settings=settings, provider=fake, now=NOW + timedelta(minutes=5))
    assert second["status"] == "ok" and second["freshness"] == "cached"
    assert len(fake.calls) == 1
    research_opportunity(opp, settings=settings, provider=fake, force=True, now=NOW)
    assert len(fake.calls) == 2


def test_changed_inputs_mark_research_stale(settings, fee_dir):
    fake = FakeProvider()
    research_opportunity(_opp(fee_dir), settings=settings, provider=fake, now=NOW)
    state = research_state(_opp(fee_dir, model_probability=0.65), settings, now=NOW)
    assert state["freshness"] == "stale" and state["data_changed"] is True
    again = research_opportunity(_opp(fee_dir, model_probability=0.65), settings=settings, provider=fake, now=NOW)
    assert again["freshness"] == "fresh" and len(fake.calls) == 2


def test_old_research_is_stale(settings, fee_dir):
    research_opportunity(_opp(fee_dir), settings=settings, provider=FakeProvider(), now=NOW)
    state = research_state(_opp(fee_dir), settings, now=NOW + timedelta(hours=7))
    assert state["freshness"] == "stale" and state["data_changed"] is False


def test_ineligible_never_calls_provider(settings, fee_dir):
    fake = FakeProvider()
    out = research_opportunity(_opp(fee_dir, yes_ask=None), settings=settings, provider=fake, now=NOW)
    assert out["status"] == "ineligible" and out["eligibility"]["status"] == "BLOCKED"
    assert out["eligibility"]["blocking_reasons"] and fake.calls == []


@pytest.mark.parametrize("error, status, code", [
    (ProviderUnavailable("down", code="ollama_unreachable"), "unavailable", "ollama_unreachable"),
    (ProviderTimeout("slow"), "unavailable", "provider_timeout"),
    (ModelMissing("pull it"), "unavailable", "model_missing"),
    (MalformedProviderResponse("bad"), "error", "malformed_response"),
    (RuntimeError("boom"), "error", "provider_exception"),
])
def test_provider_failures_are_reported_not_raised(settings, fee_dir, error, status, code):
    out = research_opportunity(_opp(fee_dir), settings=settings, provider=FakeProvider(error=error), now=NOW)
    assert out["status"] == status and out["error"]["code"] == code and out["research"] is None
    assert not list(settings.cache_dir.glob("*.json"))


def test_null_provider_reports_ai_disabled(settings, fee_dir):
    out = research_opportunity(_opp(fee_dir), settings=settings, provider=NullProvider(), now=NOW)
    assert out["status"] == "unavailable" and out["error"]["code"] == "ai_disabled"


@pytest.mark.parametrize("text", ["not json at all", "[1, 2]", research_json(summary=""),
                                  research_json(model_probability_should_not_be_changed=False),
                                  research_json(research_confidence="CERTAIN")])
def test_malformed_ai_output_is_an_error_not_a_crash(settings, fee_dir, text):
    out = research_opportunity(_opp(fee_dir), settings=settings, provider=FakeProvider(text=text), now=NOW)
    assert out["status"] == "error" and out["error"]["code"] == "invalid_research_output"
    assert not list(settings.cache_dir.glob("*.json"))


def test_previous_research_survives_a_failed_refresh(settings, fee_dir):
    research_opportunity(_opp(fee_dir), settings=settings, provider=FakeProvider(), now=NOW)
    out = research_opportunity(_opp(fee_dir), settings=settings, force=True, now=NOW,
                               provider=FakeProvider(error=ProviderUnavailable("down")))
    assert out["status"] == "unavailable" and out["research"]["summary"]


# ---------------------------------------------------------------- validation

def test_fact_without_evidence_is_relabelled_unknown():
    out = validate_research(research_json(supporting_evidence=[
        {"statement": "A star player is injured", "basis": "FACT", "evidence_ids": ["E99"]}]),
        evidence_ids={"E1", "E2"})
    item = out["supporting_evidence"][0]
    assert item["basis"] == "UNKNOWN" and item["evidence_ids"] == [] and "validator_note" in item


def test_fenced_json_and_think_blocks_are_accepted():
    text = "<think>reasoning</think>```json\n" + research_json() + "\n```"
    assert validate_research(text, evidence_ids={"E1", "E2"})["research_confidence"] == "MODERATE"


def test_transfer_validation_claims_are_rejected():
    with pytest.raises(ResearchValidationError):
        validate_research(research_json(summary="The CLINYC transfer has been validated, so the model is reliable."),
                          evidence_ids={"E1"}, transfer_unvalidated=True)
    ok = validate_research(research_json(summary="The KNYC to CLINYC transfer is not validated."),
                           evidence_ids={"E1"}, transfer_unvalidated=True)
    assert ok["summary"]


def test_research_confidence_capped_with_unvalidated_transfer():
    out = validate_research(research_json(research_confidence="HIGH"), evidence_ids={"E1"},
                            transfer_unvalidated=True)
    assert out["research_confidence"] == "MODERATE" and out["research_confidence_capped"] is True


@pytest.mark.parametrize("sentence, claim", [
    ("The settlement-source transfer is now validated", True),
    ("Model is calibrated to CLINYC", True),
    ("Transfer remains unvalidated", False),
    ("The transfer has not been validated yet", False),
    ("GFS and HRRR agree", False),
    ("The KNYC to CLINYC transfer has been verified against 2 years of data", True),
    ("Probabilities use a validated transfer to CLINYC", True),
    ("The CLINYC settlement source lacks validation against KNYC", False),
    ("Transfer validation status: false (p=0.219 vs market 0.855)", False),
    ("Whether the CLINYC transfer is validated is unknown", False),
])
def test_claims_transfer_validated(sentence, claim):
    assert claims_transfer_validated(sentence) is claim


# ---------------------------------------------------------------- prompts and evidence

def test_weather_prompt_carries_transfer_warning_and_rules(fee_dir):
    opp = _opp(fee_dir, confidence_label="LOW", confidence_score=0.3, ctx=TRANSFER_CTX)
    elig = check_eligibility(opp, AISettings(provider="none"))
    evidence = gather_evidence(opp)
    prompt = build_prompt(opp, elig, evidence)
    assert TRANSFER_WARNING in prompt and "FACTS (deterministic" in prompt
    assert any(e.source_type == "forecast_discussion" for e in evidence)
    assert "never state or imply that it is validated" in SYSTEM_PROMPT
    assert "model_probability_should_not_be_changed" in SYSTEM_PROMPT


def test_weather_research_rejects_transfer_claim_end_to_end(settings, fee_dir):
    opp = _opp(fee_dir, confidence_label="LOW", confidence_score=0.3, ctx=TRANSFER_CTX)
    bad = FakeProvider(text=research_json(summary="Since the CLINYC transfer is validated, trust the model."))
    out = research_opportunity(opp, settings=settings, provider=bad, now=NOW)
    assert out["status"] == "error" and "validated" in out["error"]["message"]


def test_sports_evidence_states_data_gaps(fee_dir):
    evidence = gather_evidence(_opp(fee_dir, category="sports"))
    assert any(e.content == SPORTS_DATA_GAP for e in evidence)
    assert [e.id for e in evidence] == [f"E{i}" for i in range(1, len(evidence) + 1)]


# ---------------------------------------------------------------- auto research (off by default)

def test_auto_research_is_off_by_default(settings, fee_dir):
    fake = FakeProvider()
    assert run_auto_research([_opp(fee_dir)], settings, provider=fake) == [] and fake.calls == []


def test_auto_research_limits_to_top_n(tmp_path, fee_dir):
    s = AISettings(provider="ollama", auto_research=True, auto_top_n=2, cache_dir=tmp_path / "ai")
    opps = evaluate([make_opp(ticker=f"T{i}", model_probability=0.55 + i * 0.05) for i in range(4)],
                    NOW, QualitySettings(), fee_dir=fee_dir)
    fake = FakeProvider()
    out = run_auto_research(opps, s, provider=fake)
    assert [r["ticker"] for r in out] == ["T3", "T2"] and len(fake.calls) == 2


# ---------------------------------------------------------------- settings

def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "none")
    assert AISettings.from_env().enabled is False
    monkeypatch.setenv("AI_PROVIDER", "something-else")
    assert AISettings.from_env().provider == "none"
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setenv("AI_MIN_EDGE", "0.08")
    s = AISettings.from_env()
    assert (s.provider, s.model, s.min_edge, s.auto_research) == ("ollama", "llama3.2:3b", 0.08, False)


# ---------------------------------------------------------------- Ollama provider (mocked HTTP)

class _Resp:
    def __init__(self, status: int, payload=None, text: str = "") -> None:
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    def __init__(self, *responses) -> None:
        self.responses, self.bodies = list(responses), []

    def post(self, url, json=None, timeout=None):
        self.bodies.append(json)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, timeout=None):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _ollama(*responses) -> OllamaProvider:
    return OllamaProvider("http://localhost:11434", "qwen3:8b", timeout_s=5, session=_Session(*responses))


def test_ollama_success_sends_schema_and_no_thinking():
    p = _ollama(_Resp(200, {"message": {"content": research_json()}}))
    out = p.generate("prompt", system_prompt="sys", json_schema={"type": "object"})
    body = p.session.bodies[0]
    assert out.text and body["format"] == {"type": "object"} and body["stream"] is False and body["think"] is False
    assert body["options"]["num_ctx"] == 8192 and body["options"]["num_predict"] == 1500


def test_ollama_output_cut_at_token_limit_is_malformed():
    p = _ollama(_Resp(200, {"message": {"content": '{"summary": "trunc'}, "done_reason": "length"}))
    with pytest.raises(MalformedProviderResponse, match="output limit"):
        p.generate("prompt")


def test_schema_caps_list_lengths():
    from research.ai.models import RESEARCH_JSON_SCHEMA
    props = RESEARCH_JSON_SCHEMA["properties"]
    assert all(props[name]["maxItems"] == 4 for name in ("supporting_evidence", "risk_factors", "unknowns"))


def test_ollama_retries_without_think_for_models_without_it():
    p = _ollama(_Resp(400, text='{"error":"model does not support thinking"}'),
                _Resp(200, {"message": {"content": "{}"}}))
    p.generate("prompt")
    assert "think" not in p.session.bodies[1]


@pytest.mark.parametrize("response, error", [
    (requests.ConnectionError("refused"), ProviderUnavailable),
    (requests.Timeout("slow"), ProviderTimeout),
    (_Resp(404, text='{"error":"model not found"}'), ModelMissing),
    (_Resp(200, {"unexpected": True}), MalformedProviderResponse),
])
def test_ollama_failures_map_to_typed_errors(response, error):
    with pytest.raises(error):
        _ollama(response).generate("prompt")


def test_ollama_status_reports_offline_and_missing_model():
    assert _ollama(requests.ConnectionError("refused")).status()["code"] == "ollama_unreachable"
    missing = _ollama(_Resp(200, {"models": [{"name": "llama3.2:3b"}]})).status()
    assert missing["available"] is False and missing["code"] == "model_missing"
    ok = _ollama(_Resp(200, {"models": [{"name": "qwen3:8b"}]})).status()
    assert ok["available"] is True

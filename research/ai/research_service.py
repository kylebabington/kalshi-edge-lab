"""AI research orchestration: eligibility -> evidence -> prompt -> provider -> validation -> cache.

Never raises to callers and never recomputes a probability. Results carry:

* ``status``: ``ok`` | ``ineligible`` | ``unavailable`` (provider disabled/unreachable/missing model/timeout)
  | ``error`` (invalid output or unexpected provider failure)
* ``freshness`` of the research shown: ``fresh`` (generated now) | ``cached`` (inputs unchanged)
  | ``stale`` (inputs changed or too old) | ``unavailable`` (none exists)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from research.opportunities.models import QUALITY_BLOCKED, Opportunity

from . import cache
from .config import AISettings
from .eligibility import check_eligibility
from .evidence import gather_evidence
from .models import AI_NOTE, RESEARCH_JSON_SCHEMA, ResearchValidationError, validate_research
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_prompt
from .provider import AIProvider, ProviderError, get_provider

log = logging.getLogger(__name__)

UNAVAILABLE_CODES = {"ai_disabled", "ollama_unreachable", "provider_unavailable", "model_missing",
                     "provider_timeout"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_s(record: dict, now: datetime) -> float | None:
    try:
        created = datetime.fromisoformat(str(record.get("created_at")))
    except ValueError:
        return None
    return (now - created).total_seconds()


def research_state(opp: Opportunity, settings: AISettings, *, now: datetime | None = None) -> dict[str, Any]:
    """Cached research for ``opp`` and whether it still matches the current inputs. No provider call."""
    now = now or _now()
    record = cache.load(settings.cache_dir, opp.ticker)
    if record is None:
        return {"freshness": "unavailable", "data_changed": False, "record": None, "current_fingerprint": None}
    evidence = gather_evidence(opp)
    fp = cache.fingerprint(opp, evidence, provider=record.get("provider") or settings.provider,
                           model=record.get("model"), prompt_version=PROMPT_VERSION)
    changed = record.get("fingerprint") != fp
    age = _age_s(record, now)
    too_old = age is None or age > settings.research_max_age_s
    return {"freshness": "stale" if changed or too_old else "cached", "data_changed": changed,
            "record": record, "current_fingerprint": fp}


def ai_status_label(state: dict[str, Any]) -> str:
    if state["record"] is None:
        return "not_researched"
    return "stale" if state["freshness"] == "stale" else "researched"


def build_result(opp: Opportunity, *, status: str, eligibility: dict, state: dict | None = None,
            record: dict | None = None, freshness: str | None = None, error: dict | None = None,
            settings: AISettings) -> dict[str, Any]:
    state = state or {}
    record = record if record is not None else state.get("record")
    return {
        "ticker": opp.ticker,
        "status": status,
        "freshness": freshness or state.get("freshness") or "unavailable",
        "data_changed": bool(state.get("data_changed")),
        "eligibility": eligibility,
        "research": (record or {}).get("research"),
        "provider": (record or {}).get("provider") or settings.provider,
        "model": (record or {}).get("model") or settings.model,
        "created_at": (record or {}).get("created_at"),
        "fingerprint": (record or {}).get("fingerprint"),
        "evidence_ids": (record or {}).get("evidence_ids") or [],
        "duration_s": (record or {}).get("duration_s"),
        "prompt_version": (record or {}).get("prompt_version"),
        "error": error,
        "note": AI_NOTE,
    }


def research_opportunity(opp: Opportunity, *, settings: AISettings | None = None,
                         provider: AIProvider | None = None, force: bool = False,
                         now: datetime | None = None) -> dict[str, Any]:
    settings = settings or AISettings.from_env()
    now = now or _now()
    eligibility = check_eligibility(opp, settings)
    elig = eligibility.to_dict()
    state = research_state(opp, settings, now=now)
    if not eligibility.eligible:
        return build_result(opp, status="ineligible", eligibility=elig, state=state, settings=settings,
                       error={"code": "ineligible", "message": "; ".join(eligibility.blocking_reasons)})

    evidence = gather_evidence(opp)
    provider = provider or get_provider(settings)
    fp = cache.fingerprint(opp, evidence, provider=provider.name, model=provider.model,
                           prompt_version=PROMPT_VERSION)
    record = state.get("record")
    if not force and record and record.get("fingerprint") == fp and state["freshness"] == "cached":
        return build_result(opp, status="ok", eligibility=elig, state=state, settings=settings, freshness="cached")

    prompt = build_prompt(opp, eligibility, evidence)
    try:
        response = provider.generate(prompt, system_prompt=SYSTEM_PROMPT, json_schema=RESEARCH_JSON_SCHEMA)
    except ProviderError as error:
        log.warning("AI research for %s failed: %s (%s)", opp.ticker, error, error.code)
        return build_result(opp, status="unavailable" if error.code in UNAVAILABLE_CODES else "error",
                       eligibility=elig, state=state, settings=settings,
                       error={"code": error.code, "message": str(error)})
    except Exception as error:  # a provider bug must never break the API
        log.exception("AI provider crashed for %s", opp.ticker)
        return build_result(opp, status="error", eligibility=elig, state=state, settings=settings,
                       error={"code": "provider_exception", "message": f"{type(error).__name__}: {error}"})

    transfer_unvalidated = opp.category == "weather" and opp.domain_context.get("transfer_validated") is False
    try:
        research = validate_research(response.text, evidence_ids={e.id for e in evidence},
                                     transfer_unvalidated=transfer_unvalidated)
    except ResearchValidationError as error:
        log.warning("AI research for %s rejected: %s; output starts: %r", opp.ticker, error, response.text[:500])
        return build_result(opp, status="error", eligibility=elig, state=state, settings=settings,
                       error={"code": "invalid_research_output", "message": str(error)})

    new_record = {
        "ticker": opp.ticker, "fingerprint": fp, "created_at": now.isoformat(), "provider": response.provider,
        "model": response.model, "duration_s": response.duration_s, "prompt_version": PROMPT_VERSION,
        "evidence_ids": [e.id for e in evidence], "eligibility_status": eligibility.status,
        "inputs": {"model_probability": opp.model_probability, "yes_bid": opp.yes_bid, "yes_ask": opp.yes_ask,
                   "prediction_as_of": opp.prediction_as_of, "quote_as_of": opp.quote_as_of},
        "research": research,
    }
    try:
        cache.store(settings.cache_dir, new_record)
    except OSError:
        log.exception("could not cache AI research for %s", opp.ticker)
    return build_result(opp, status="ok", eligibility=elig, record=new_record, settings=settings, freshness="fresh")


def auto_research_candidates(opps: list[Opportunity], settings: AISettings) -> list[Opportunity]:
    """Conservative auto-research selection (only used when ``AI_AUTO_RESEARCH=true``): the top
    ``AI_AUTO_TOP_N`` non-blocked opportunities by attention score that pass the eligibility gate."""
    ranked = sorted((o for o in opps if o.quality_status != QUALITY_BLOCKED),
                    key=lambda o: (-o.attention_score, o.id))
    return [o for o in ranked if check_eligibility(o, settings).eligible][:max(0, settings.auto_top_n)]


def run_auto_research(opps: list[Opportunity], settings: AISettings | None = None,
                      provider: AIProvider | None = None) -> list[dict[str, Any]]:
    settings = settings or AISettings.from_env()
    if not settings.auto_research or not settings.enabled:
        return []
    return [research_opportunity(o, settings=settings, provider=provider)
            for o in auto_research_candidates(opps, settings)]

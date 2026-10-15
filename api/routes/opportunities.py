"""Opportunities and optional AI research (RESEARCH_ONLY / NO_BET; no execution).

GET endpoints read saved artifacts and never call the AI provider. Only
``POST /opportunities/{ticker}/research`` may call it, after the eligibility gate.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from api.schemas import AIResearchResponse, OpportunityDetailResponse, OpportunityListResponse
from research.ai.config import AISettings
from research.ai.eligibility import check_eligibility
from research.ai.evidence import gather_evidence
from research.ai.models import AI_NOTE
from research.ai.provider import get_provider
from research.ai.research_service import ai_status_label, build_result, research_opportunity, research_state
from research.opportunities.engine import OpportunityFilters, apply_filters, build_opportunities
from research.opportunities.models import Opportunity

router = APIRouter(tags=["opportunities"])

LIST_NOTE = ("attention_score ranks which markets deserve a closer look. It is not a probability, not an "
             "expected value and not a recommendation. Prices are saved quotes as of capture. "
             "RESEARCH_ONLY / NO_BET.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ai_summary(settings: AISettings) -> dict:
    return {"enabled": settings.enabled, "provider": settings.provider, "model": settings.model,
            "min_edge": settings.min_edge, "auto_research": settings.auto_research, "note": AI_NOTE}


def _item(opp: Opportunity, settings: AISettings, state: dict | None = None) -> dict:
    state = state or research_state(opp, settings)
    opp.ai_research_status = ai_status_label(state)
    out = opp.to_dict()
    out["eligibility"] = check_eligibility(opp, settings).to_dict()
    out["ai_research_freshness"] = state["freshness"]
    out["ai_researched_at"] = (state["record"] or {}).get("created_at")
    return out


def _find(ticker: str) -> Opportunity:
    for opp in build_opportunities(_now()):
        if opp.ticker == ticker:
            return opp
    raise HTTPException(status_code=404, detail=f"No current opportunity for ticker {ticker}")


@router.get("/opportunities", response_model=OpportunityListResponse)
def list_opportunities(
    category: Literal["weather", "sports"] | None = None,
    min_confidence: Literal["LOW", "MODERATE", "HIGH"] | None = None,
    min_edge: float | None = Query(None, ge=-1, le=1),
    freshness: Literal["fresh", "aging", "stale", "unavailable"] | None = None,
    quality: Literal["OK", "CAUTION", "BLOCKED"] | None = None,
    eligibility: Literal["ELIGIBLE", "ELIGIBLE_WITH_CAUTION", "BLOCKED"] | None = None,
    ai_status: Literal["researched", "not_researched", "stale"] | None = None,
    include_blocked: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict:
    settings = AISettings.from_env()
    now = _now()
    opps = build_opportunities(now)
    counts = {"total": len(opps), "by_quality": dict(Counter(o.quality_status for o in opps)),
              "by_category": dict(Counter(o.category for o in opps))}
    selected = apply_filters(opps, OpportunityFilters(
        category=category, min_confidence=min_confidence, min_edge=min_edge, freshness=freshness,
        include_blocked=include_blocked or eligibility == "BLOCKED", quality=quality))
    items = [_item(o, settings) for o in selected]
    if eligibility:
        items = [i for i in items if i["eligibility"]["status"] == eligibility]
    if ai_status:
        items = [i for i in items if i["ai_research_status"] == ai_status]
    pages = max(1, -(-len(items) // page_size))
    page = min(page, pages)
    return {"generated_at": now.isoformat(), "mode": "RESEARCH_ONLY", "note": LIST_NOTE,
            "ai": _ai_summary(settings), "total": len(items), "counts": counts, "page": page,
            "page_size": page_size, "pages": pages, "items": items[(page - 1) * page_size: page * page_size]}


@router.get("/opportunities/{ticker}", response_model=OpportunityDetailResponse)
def opportunity_detail(ticker: str) -> dict:
    settings = AISettings.from_env()
    opp = _find(ticker)
    state = research_state(opp, settings)
    elig = check_eligibility(opp, settings).to_dict()
    item = _item(opp, settings, state)
    research = build_result(opp, status="ok" if state["record"] else "not_researched", eligibility=elig,
                       state=state, settings=settings)
    return {"generated_at": _now().isoformat(), "opportunity": item,
            "evidence": [e.to_dict() for e in gather_evidence(opp)], "research": research,
            "ai": _ai_summary(settings), "note": LIST_NOTE}


@router.post("/opportunities/{ticker}/research", response_model=AIResearchResponse)
def opportunity_research(ticker: str, force: bool = False) -> dict:
    """Run (or reuse cached) AI research. Ineligible markets and unavailable providers return
    200 with an explicit status; the statistical numbers are never modified."""
    settings = AISettings.from_env()
    return research_opportunity(_find(ticker), settings=settings, force=force)


@router.get("/ai/status")
def ai_status() -> dict:
    settings = AISettings.from_env()
    return {**get_provider(settings).status(), **{"settings": _ai_summary(settings)}}

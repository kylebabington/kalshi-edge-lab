"""Constrained prompts. The AI interprets supplied evidence; it never sets probabilities."""

from __future__ import annotations

import json

from research.opportunities.models import Opportunity

from .eligibility import Eligibility
from .models import ResearchEvidence

PROMPT_VERSION = "research_prompt_v2"

SYSTEM_PROMPT = """You are a research assistant for a prediction-market research terminal.
A deterministic statistical model has ALREADY computed the probability, the edge and the expected value.
Your job is only to interpret the evidence supplied below for a human researcher.

Hard rules:
1. The statistical model probability is the source of truth. Never propose, adjust or recompute a probability,
   price, edge or expected value. Always set "model_probability_should_not_be_changed" to true.
2. Use ONLY the supplied evidence items (E1, E2, ...). Do not invent injuries, lineups, weather data,
   observations, forecasts, news, odds or market prices. Do not claim anything was verified unless an evidence
   item says so.
3. Label every statement's basis:
   FACT = directly stated in a cited evidence item (cite its ids in "evidence_ids");
   INFERENCE = your reasoning from cited evidence (cite the ids it rests on);
   UNKNOWN = information that would matter but is not in the evidence.
4. Do not give trading advice. Do not say buy, sell, bet or size a position.
5. Treat every WARNING in the input as true. If a warning says a settlement-source transfer is NOT validated,
   never state or imply that it is validated, verified or calibrated to the settlement source.
6. "research_confidence" rates how well the supplied evidence explains the model-vs-market difference
   (LOW, MODERATE or HIGH). It is not a probability.
7. Be concise: summary at most 3 sentences; at most 4 items per list, each one short sentence (under 30 words);
   leave a list empty when nothing applies. Answer with a single JSON object matching the schema."""


def build_prompt(opp: Opportunity, eligibility: Eligibility, evidence: list[ResearchEvidence]) -> str:
    facts = {
        "market": opp.title,
        "ticker": opp.ticker,
        "category": opp.category,
        "model_probability_yes": opp.model_probability,
        "market_mid_yes": opp.market_probability,
        "yes_bid": opp.yes_bid,
        "yes_ask": opp.yes_ask,
        "side_with_edge": opp.side,
        "probability_edge": opp.probability_edge,
        "ev_before_fees_per_contract": opp.expected_value_before_fees,
        "estimated_fee_per_contract": opp.estimated_fees,
        "ev_after_fees_per_contract": opp.expected_value_after_fees,
        "model_confidence": opp.confidence_label or "none (no validated metric)",
        "data_freshness": opp.data_freshness_status,
        "eligibility_status": eligibility.status,
    }
    lines = ["FACTS (deterministic, do not change):", json.dumps(facts, indent=1, default=str), ""]
    if eligibility.warnings:
        lines.append("WARNINGS (true; must be reflected in risks, never contradicted):")
        lines.extend(f"- {w}" for w in eligibility.warnings)
        lines.append("")
    lines.append("EVIDENCE:")
    for e in evidence:
        meta = ", ".join(x for x in (f"type={e.source_type}", f"reliability={e.reliability}",
                                     f"published={e.published_at}" if e.published_at else "",
                                     f"retrieved={e.retrieved_at}" if e.retrieved_at else "") if x)
        lines.append(f"[{e.id}] {e.title} - {e.source} ({meta})")
        lines.append(e.content)
        lines.append("")
    lines.append("Explain why the model and the market differ, what in the evidence supports or contradicts "
                 "the model, what changed recently, the main risks, and what is UNKNOWN. JSON only.")
    return "\n".join(lines)

"""Deterministic opportunity layer (RESEARCH_ONLY / NO_BET).

Normalizes existing weather and sports predictions plus their saved Kalshi quotes into
:class:`Opportunity` objects, assesses data quality and computes an explainable
``attention_score``. Nothing here calls an AI provider, fetches upstream data or places orders.

Dependency direction: raw prediction artifacts -> this package -> research.ai (never the reverse).
"""

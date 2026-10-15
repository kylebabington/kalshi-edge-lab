"""On-disk cache of AI research, one JSON file per ticker, keyed by an input fingerprint.

The fingerprint covers the ticker, model probability, quote, prediction/quote timestamps, the
non-methodology evidence content and timestamps, caution flag codes, provider, model and prompt
version. Research is regenerated only when one of those changes (or on an explicit refresh).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from kalshi import cache as kcache
from research.opportunities.models import SEVERITY_CAUTION, Opportunity

from .models import ResearchEvidence

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def fingerprint(opp: Opportunity, evidence: list[ResearchEvidence], *, provider: str, model: str | None,
                prompt_version: str) -> str:
    def r(x: float | None) -> float | None:
        return None if x is None else round(x, 4)

    body = {
        "ticker": opp.ticker,
        "model_probability": r(opp.model_probability),
        "yes_bid": r(opp.yes_bid),
        "yes_ask": r(opp.yes_ask),
        "prediction_as_of": opp.prediction_as_of,
        "quote_as_of": opp.quote_as_of,
        "caution_codes": sorted(f.code for f in opp.quality_flags if f.severity == SEVERITY_CAUTION),
        "evidence": [
            {"source": e.source, "published_at": e.published_at, "retrieved_at": e.retrieved_at,
             "sha256": hashlib.sha256(e.content.encode("utf-8")).hexdigest()}
            for e in evidence if e.source_type != "methodology"
        ],
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def path_for(cache_dir: Path, ticker: str) -> Path:
    return cache_dir / f"{_SAFE.sub('_', ticker)}.json"


def load(cache_dir: Path, ticker: str) -> dict[str, Any] | None:
    p = path_for(cache_dir, ticker)
    try:
        data = kcache.read_json(p, default=None)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("ticker") == ticker else None


def store(cache_dir: Path, record: dict[str, Any]) -> None:
    kcache.write_json(path_for(cache_dir, record["ticker"]), record)

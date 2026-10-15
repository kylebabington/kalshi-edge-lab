"""AI data structures and strict validation of structured research output."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

BASIS = ("FACT", "INFERENCE", "UNKNOWN")
RESEARCH_CONFIDENCE = ("LOW", "MODERATE", "HIGH")
LIST_FIELDS = ("supporting_evidence", "contradictory_evidence", "risk_factors", "important_changes")
MAX_ITEMS = 10
MAX_TEXT = 1000
# Small local models on CPU keep appending list items unless the grammar stops them.
SCHEMA_MAX_ITEMS = 4
AI_NOTE = "AI research does not modify the statistical model probability."


@dataclass(frozen=True)
class AIResponse:
    text: str
    provider: str
    model: str | None
    duration_s: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchEvidence:
    id: str
    source: str
    source_type: str
    title: str
    content: str
    published_at: str | None
    retrieved_at: str | None
    url: str | None
    reliability: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResearchValidationError(ValueError):
    """The provider answered, but not with valid structured research."""


RESEARCH_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        **{name: {"type": "array", "maxItems": SCHEMA_MAX_ITEMS, "items": {
            "type": "object",
            "properties": {"statement": {"type": "string"}, "basis": {"type": "string", "enum": list(BASIS)},
                           "evidence_ids": {"type": "array", "maxItems": SCHEMA_MAX_ITEMS,
                                            "items": {"type": "string"}}},
            "required": ["statement", "basis", "evidence_ids"]}} for name in LIST_FIELDS},
        "unknowns": {"type": "array", "maxItems": SCHEMA_MAX_ITEMS, "items": {"type": "string"}},
        "research_confidence": {"type": "string", "enum": list(RESEARCH_CONFIDENCE)},
        "model_probability_should_not_be_changed": {"type": "boolean"},
    },
    "required": ["summary", *LIST_FIELDS, "unknowns", "research_confidence",
                 "model_probability_should_not_be_changed"],
}

_NEGATION = re.compile(r"\b(not|unvalidated|no|never|without|unverified|pending|yet|cannot|isn't|hasn't|"
                       r"wasn't|unconfirmed|experimental|lack|lacks|lacking|absence|absent|missing|unknown|"
                       r"uncertain|unclear|whether|if|until|requires?|needs?|may|might|could|false)\b|n't",
                       re.IGNORECASE)
_TRANSFER_TOPIC = re.compile(r"transfer|clinyc|settlement[- ]source", re.IGNORECASE)
# Only positive assertions count; nouns like "validation" alone are not a claim.
_VALIDATION_WORD = re.compile(
    r"\b(is|are|was|were|been|now|fully|successfully|properly)\s+(validated|verified|confirmed)\b"
    r"|\bvalidated\s+(transfer|mapping|settlement|source|against|to|on|for)\b"
    r"|\bcalibrated\s+(to|on|against)\s+(the\s+)?clinyc", re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"(?<!\d)\.|\.(?!\d)|[;\n!?]")


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    text = re.sub(r"^<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                pass
    raise ResearchValidationError("response is not valid JSON")


def _clip(value: Any) -> str:
    return str(value).strip()[:MAX_TEXT]


def _items(raw: Any, name: str, evidence_ids: set[str]) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ResearchValidationError(f"'{name}' must be a list")
    out = []
    for item in raw[:MAX_ITEMS]:
        if isinstance(item, str):
            item = {"statement": item, "basis": "UNKNOWN", "evidence_ids": []}
        if not isinstance(item, dict) or not str(item.get("statement") or "").strip():
            raise ResearchValidationError(f"'{name}' items need a non-empty 'statement'")
        basis = str(item.get("basis") or "UNKNOWN").upper()
        if basis not in BASIS:
            basis = "UNKNOWN"
        ids = [str(x) for x in item.get("evidence_ids") or [] if str(x) in evidence_ids]
        note = None
        if basis == "FACT" and not ids:
            basis, note = "UNKNOWN", "labelled FACT but not tied to supplied evidence"
        out.append({"statement": _clip(item["statement"]), "basis": basis, "evidence_ids": ids,
                    **({"validator_note": note} if note else {})})
    return out


def claims_transfer_validated(text: str) -> bool:
    """True when a sentence asserts the KNYC->CLINYC transfer is validated (without negation)."""
    for sentence in _SENTENCE_BREAK.split(text or ""):
        if _TRANSFER_TOPIC.search(sentence) and _VALIDATION_WORD.search(sentence) \
                and not _NEGATION.search(sentence):
            return True
    return False


def validate_research(text: str, *, evidence_ids: set[str], transfer_unvalidated: bool = False) -> dict[str, Any]:
    """Parse and validate provider output. Raises :class:`ResearchValidationError` on any violation.

    * claims labelled FACT must cite supplied evidence ids, otherwise they are relabelled UNKNOWN
    * ``model_probability_should_not_be_changed`` must be ``true``
    * with an unvalidated settlement transfer, research confidence is capped at MODERATE and any
      claim that the transfer is validated rejects the whole response
    """
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise ResearchValidationError("response JSON must be an object")
    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise ResearchValidationError("missing 'summary'")
    if data.get("model_probability_should_not_be_changed") is not True:
        raise ResearchValidationError("'model_probability_should_not_be_changed' must be true")
    conf = str(data.get("research_confidence") or "").upper()
    if conf not in RESEARCH_CONFIDENCE:
        raise ResearchValidationError("'research_confidence' must be LOW, MODERATE or HIGH")
    out: dict[str, Any] = {"summary": _clip(summary)}
    for name in LIST_FIELDS:
        out[name] = _items(data.get(name), name, evidence_ids)
    unknowns = data.get("unknowns") or []
    out["unknowns"] = [_clip(u) for u in unknowns[:MAX_ITEMS]] if isinstance(unknowns, list) else []
    out["research_confidence"] = conf
    out["research_confidence_capped"] = False
    out["model_probability_should_not_be_changed"] = True
    if transfer_unvalidated:
        texts = [out["summary"], *(i["statement"] for n in LIST_FIELDS for i in out[n]), *out["unknowns"]]
        if any(claims_transfer_validated(t) for t in texts):
            raise ResearchValidationError("response implies the KNYC->CLINYC settlement transfer is validated")
        if conf == "HIGH":
            out["research_confidence"] = "MODERATE"
            out["research_confidence_capped"] = True
    return out

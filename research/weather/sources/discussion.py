"""NWS Area Forecast Discussion collector + lightweight structured extraction.

Does NOT convert prose into numerical probabilities.
WFO comes from point discovery — never permanently hardcoded to OKX.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import requests

from kalshi import cache
from research.weather.evidence import (
    FRESHNESS_UNAVAILABLE,
    QUALITY_OK,
    QUALITY_UNAVAILABLE,
    ForecastDiscussionSnapshot,
    apply_freshness,
)
from research.weather.sources.nws_forecast import (
    NWS_API_BASE,
    NWS_CACHE_DIR,
    NWS_HEADERS,
    resolve_nws_point,
)

AFD_PRODUCT_CODE = "AFD"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_afd_structure(text: str) -> dict[str, list[str]]:
    """Deterministic keyword/section extraction — no LLM, no probability shift."""
    if not text:
        return {
            "key_messages": [],
            "confidence_wording": [],
            "temperature_discussion": [],
            "cloud_cover_discussion": [],
            "precipitation_timing": [],
            "frontal_timing": [],
            "model_disagreement_mentions": [],
            "model_references": [],
            "forecast_change_reasons": [],
        }

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lower_lines = [(ln, ln.lower()) for ln in lines]

    def _match(patterns: list[str], *, limit: int = 8) -> list[str]:
        out: list[str] = []
        for original, lowered in lower_lines:
            if any(p in lowered for p in patterns):
                if original not in out:
                    out.append(original)
            if len(out) >= limit:
                break
        return out

    # Key messages: SYNOPSIS / KEY MESSAGE / SHORT TERM headers + first bullets.
    key_messages: list[str] = []
    for original, lowered in lower_lines:
        if re.search(r"^\.?synopsis", lowered) or "key message" in lowered:
            key_messages.append(original)
        elif key_messages and len(key_messages) < 6 and (
            original.startswith("-") or original.startswith("*") or original.isupper()
        ):
            key_messages.append(original)

    confidence = _match(
        [
            "high confidence",
            "low confidence",
            "moderate confidence",
            "uncertainty",
            "less certain",
            "more certain",
            "confidence is",
            "forecast confidence",
        ]
    )
    temperature = _match(
        [
            "temperature",
            "highs",
            "lows",
            "warm",
            "cool",
            "hot",
            "cold",
            "°",
            "degrees",
        ]
    )
    clouds = _match(["cloud", "sky cover", "overcast", "clear skies", "partly cloudy"])
    precip = _match(
        [
            "rain",
            "shower",
            "thunderstorm",
            "precip",
            "qpf",
            "timing of",
            "overnight",
            "this afternoon",
            "this morning",
        ]
    )
    frontal = _match(["front", "frontal", "cold front", "warm front", "trough", "boundary"])
    disagreement = _match(
        [
            "model disagreement",
            "models disagree",
            "model spread",
            "guidance differs",
            "models differ",
            "discrepancy",
            "outlier",
        ]
    )
    models = _match(
        [
            "hrrr",
            "gfs",
            "nbm",
            "nam",
            "ecmwf",
            "euro",
            "rap",
            "gem",
            "gefs",
            "model guidance",
        ]
    )
    changes = _match(
        [
            "adjusted",
            "raised",
            "lowered",
            "trended",
            "changed",
            "previous forecast",
            "earlier forecast",
            "update",
        ]
    )

    return {
        "key_messages": key_messages[:8],
        "confidence_wording": confidence,
        "temperature_discussion": temperature,
        "cloud_cover_discussion": clouds,
        "precipitation_timing": precip,
        "frontal_timing": frontal,
        "model_disagreement_mentions": disagreement,
        "model_references": models,
        "forecast_change_reasons": changes,
    }


def parse_afd_product(payload: dict[str, Any], *, retrieved_at: str | None = None) -> ForecastDiscussionSnapshot:
    """Parse NWS products API AFD JSON into a snapshot."""
    retrieved_at = retrieved_at or _now_iso()
    props = payload.get("properties") or payload
    # products endpoint may return @graph list
    if "@graph" in payload and isinstance(payload["@graph"], list) and payload["@graph"]:
        props = payload["@graph"][0]
    text = props.get("productText") or props.get("text") or ""
    issued = props.get("issuanceTime") or props.get("issueTime") or props.get("sent")
    product_id = props.get("id") or props.get("productName") or props.get("@id")
    wfo = props.get("issuingOffice") or props.get("office")
    if isinstance(wfo, str) and "/" in wfo:
        wfo = wfo.rstrip("/").split("/")[-1]
    extracted = extract_afd_structure(str(text))
    freshness_seconds, freshness_status = apply_freshness(issued)
    return ForecastDiscussionSnapshot(
        wfo=str(wfo) if wfo else None,
        product_id=str(product_id) if product_id else None,
        issued_at=str(issued) if issued else None,
        retrieved_at=retrieved_at,
        raw_text=str(text) if text else None,
        key_messages=extracted["key_messages"],
        confidence_wording=extracted["confidence_wording"],
        temperature_discussion=extracted["temperature_discussion"],
        cloud_cover_discussion=extracted["cloud_cover_discussion"],
        precipitation_timing=extracted["precipitation_timing"],
        frontal_timing=extracted["frontal_timing"],
        model_disagreement_mentions=extracted["model_disagreement_mentions"],
        model_references=extracted["model_references"],
        forecast_change_reasons=extracted["forecast_change_reasons"],
        freshness_seconds=freshness_seconds,
        freshness_status=freshness_status,
        quality_status=QUALITY_OK if text else QUALITY_UNAVAILABLE,
        warnings=[] if text else ["AFD product text empty"],
        metadata={"note": "Supporting evidence only — does not alter probabilities"},
    )


def collect_nws_discussion(
    *,
    wfo: str | None = None,
    as_of: datetime | None = None,
) -> ForecastDiscussionSnapshot:
    """Fetch current AFD for discovered WFO. Unavailable AFD does not raise."""
    retrieved_at = _now_iso()
    as_of = as_of or datetime.now(timezone.utc)
    try:
        if not wfo:
            point = resolve_nws_point()
            wfo = point.get("wfo")
        if not wfo:
            return ForecastDiscussionSnapshot(
                retrieved_at=retrieved_at,
                freshness_status=FRESHNESS_UNAVAILABLE,
                quality_status=QUALITY_UNAVAILABLE,
                warnings=["WFO unknown; AFD not fetched"],
            )

        # Latest product for office+type
        url = f"{NWS_API_BASE}/products/types/{AFD_PRODUCT_CODE}/locations/{wfo}"
        response = requests.get(url, headers=NWS_HEADERS, timeout=20)
        response.raise_for_status()
        listing = response.json()
        graph = listing.get("@graph") or listing.get("products") or []
        if not graph:
            return ForecastDiscussionSnapshot(
                wfo=str(wfo),
                retrieved_at=retrieved_at,
                freshness_status=FRESHNESS_UNAVAILABLE,
                quality_status=QUALITY_UNAVAILABLE,
                warnings=[f"No AFD products listed for WFO {wfo}"],
            )

        latest = graph[0]
        product_url = latest.get("@id") or latest.get("id") or latest.get("url")
        if not product_url:
            product_id = latest.get("id")
            if product_id and not str(product_id).startswith("http"):
                product_url = f"{NWS_API_BASE}/products/{product_id}"
            else:
                product_url = product_id
        if not product_url:
            return ForecastDiscussionSnapshot(
                wfo=str(wfo),
                retrieved_at=retrieved_at,
                freshness_status=FRESHNESS_UNAVAILABLE,
                quality_status=QUALITY_UNAVAILABLE,
                warnings=["AFD listing missing product URL"],
            )

        detail = requests.get(str(product_url), headers=NWS_HEADERS, timeout=20)
        detail.raise_for_status()
        payload = detail.json()
        NWS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = NWS_CACHE_DIR / f"afd_{wfo}_{stamp}.json"
        cache.write_json(path, payload)
        snap = parse_afd_product(payload, retrieved_at=retrieved_at)
        snap.wfo = snap.wfo or str(wfo)
        snap.raw_cache_path = str(path)
        # Recompute freshness vs as_of
        snap.freshness_seconds, snap.freshness_status = apply_freshness(
            snap.issued_at, as_of=as_of
        )
        return snap
    except Exception as error:  # noqa: BLE001
        return ForecastDiscussionSnapshot(
            wfo=str(wfo) if wfo else None,
            retrieved_at=retrieved_at,
            freshness_status=FRESHNESS_UNAVAILABLE,
            quality_status=QUALITY_UNAVAILABLE,
            warnings=[f"AFD unavailable: {error}"],
            metadata={"note": "AFD failure must not kill prediction"},
        )

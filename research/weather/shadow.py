"""SHADOW_GFS_HRRR_EQUAL_V1 — pre-registered equal-weight shadow challenger.

Does NOT own WeatherPrediction. No learned weights. No Kalshi prices.
Target-regime semantics are frozen: NWS calibration + experimental
KNYC→CLINYC identity transfer for CLINYC live events on BOTH components.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from kalshi import cache
from research.weather.models import (
    CALIBRATION_METHOD_NWS,
    CALIBRATION_METHOD_TRANSFER,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    SHADOW_CANDIDATE_ID,
    SHADOW_STATUS_AVAILABLE,
    SHADOW_STATUS_BUCKET_ALIGNMENT_ERROR,
    SHADOW_STATUS_UNAVAILABLE,
    TRANSFER_STATUS_EXPERIMENTAL,
    TRANSFER_STATUS_NONE,
)
from research.weather.probability import assert_probs_sum_to_one
from research.weather.resolution import TemperatureBucket

HYPOTHESES_DIR = cache.REPO_ROOT / "research" / "weather" / "hypotheses"
SHADOW_HYPOTHESIS_PATH = HYPOTHESES_DIR / "shadow_gfs_hrrr_equal_v1.json"


def _boundary_key(bucket: TemperatureBucket) -> tuple[Any, ...]:
    return (
        bucket.lower_bound,
        bucket.upper_bound,
        bucket.lower_inclusive,
        bucket.upper_inclusive,
    )


def canonical_bucket_identity(bucket: TemperatureBucket) -> str:
    """Prefer market ticker; fall back to boundary signature."""
    if bucket.ticker:
        return f"ticker:{bucket.ticker}"
    return "bounds:" + "|".join("" if v is None else str(v) for v in _boundary_key(bucket))


def _probs_by_canonical(
    probs: dict[str, float],
    buckets: Sequence[TemperatureBucket],
) -> dict[str, float]:
    """Map label-keyed probs onto canonical identities via bucket metadata."""
    out: dict[str, float] = {}
    for bucket in buckets:
        label = bucket.label
        if label not in probs:
            continue
        out[canonical_bucket_identity(bucket)] = float(probs[label])
    return out


def _label_by_canonical(buckets: Sequence[TemperatureBucket]) -> dict[str, str]:
    return {canonical_bucket_identity(b): b.label for b in buckets}


def bucket_universes_align(
    gfs_buckets: Sequence[TemperatureBucket],
    hrrr_buckets: Sequence[TemperatureBucket],
) -> tuple[bool, str | None]:
    gfs_ids = {canonical_bucket_identity(b) for b in gfs_buckets}
    hrrr_ids = {canonical_bucket_identity(b) for b in hrrr_buckets}
    if gfs_ids != hrrr_ids:
        return False, "canonical_bucket_ticker_set_mismatch"
    gfs_bounds = {_boundary_key(b) for b in gfs_buckets}
    hrrr_bounds = {_boundary_key(b) for b in hrrr_buckets}
    if gfs_bounds != hrrr_bounds:
        return False, "bucket_boundaries_mismatch"
    return True, None


def combine_equal_weight(
    *,
    gfs_probs: dict[str, float] | None,
    hrrr_probs: dict[str, float] | None,
    gfs_buckets: Sequence[TemperatureBucket] | None = None,
    hrrr_buckets: Sequence[TemperatureBucket] | None = None,
    event_ticker: str | None = None,
    gfs_event_ticker: str | None = None,
    hrrr_event_ticker: str | None = None,
    tol: float = 1e-6,
) -> dict[str, Any]:
    """Equal-weight blend by canonical bucket identity.

    Returns shadow block with status available | unavailable | BUCKET_ALIGNMENT_ERROR.
    Never partially combines; never infers missing buckets; never falls back to GFS alone.
    """
    base: dict[str, Any] = {
        "candidate_id": SHADOW_CANDIDATE_ID,
        "status": SHADOW_STATUS_UNAVAILABLE,
        "probabilities": None,
        "unavailable_reason": None,
        "definition": "0.5 * P_gfs + 0.5 * P_hrrr per canonical bucket",
        "no_learned_weights": True,
        "no_kalshi_prices": True,
    }

    if gfs_probs is None or hrrr_probs is None:
        base["unavailable_reason"] = "missing_component_distribution"
        return base
    if not gfs_probs or not hrrr_probs:
        base["unavailable_reason"] = "empty_component_distribution"
        return base

    gfs_et = gfs_event_ticker or event_ticker
    hrrr_et = hrrr_event_ticker or event_ticker
    if gfs_et and hrrr_et and gfs_et != hrrr_et:
        base["status"] = SHADOW_STATUS_BUCKET_ALIGNMENT_ERROR
        base["unavailable_reason"] = "event_ticker_mismatch"
        return base

    try:
        assert_probs_sum_to_one(gfs_probs, tol=tol)
        assert_probs_sum_to_one(hrrr_probs, tol=tol)
    except AssertionError as exc:
        base["status"] = SHADOW_STATUS_BUCKET_ALIGNMENT_ERROR
        base["unavailable_reason"] = f"probs_not_normalized: {exc}"
        return base

    # Prefer canonical alignment when bucket metadata is present.
    if gfs_buckets is not None and hrrr_buckets is not None:
        ok, reason = bucket_universes_align(gfs_buckets, hrrr_buckets)
        if not ok:
            base["status"] = SHADOW_STATUS_BUCKET_ALIGNMENT_ERROR
            base["unavailable_reason"] = reason
            return base
        gfs_c = _probs_by_canonical(gfs_probs, gfs_buckets)
        hrrr_c = _probs_by_canonical(hrrr_probs, hrrr_buckets)
        if set(gfs_c) != set(hrrr_c) or set(gfs_c) != {
            canonical_bucket_identity(b) for b in gfs_buckets
        }:
            base["status"] = SHADOW_STATUS_BUCKET_ALIGNMENT_ERROR
            base["unavailable_reason"] = "canonical_prob_coverage_mismatch"
            return base
        labels = _label_by_canonical(gfs_buckets)
        blended_c = {
            cid: 0.5 * float(gfs_c[cid]) + 0.5 * float(hrrr_c[cid]) for cid in gfs_c
        }
        total = sum(blended_c.values())
        if total > 0 and abs(total - 1.0) > tol:
            blended_c = {k: v / total for k, v in blended_c.items()}
        probabilities = {labels[cid]: blended_c[cid] for cid in blended_c}
    else:
        # Label-keyed fallback only when both sides share identical key sets.
        if set(gfs_probs.keys()) != set(hrrr_probs.keys()):
            base["status"] = SHADOW_STATUS_BUCKET_ALIGNMENT_ERROR
            base["unavailable_reason"] = "label_key_set_mismatch_without_bucket_metadata"
            return base
        probabilities = {
            k: 0.5 * float(gfs_probs[k]) + 0.5 * float(hrrr_probs[k]) for k in gfs_probs
        }
        total = sum(probabilities.values())
        if total > 0 and abs(total - 1.0) > tol:
            probabilities = {k: v / total for k, v in probabilities.items()}

    assert_probs_sum_to_one(probabilities, tol=tol)
    base["status"] = SHADOW_STATUS_AVAILABLE
    base["probabilities"] = probabilities
    base["unavailable_reason"] = None
    return base


def shadow_transfer_metadata(
    *,
    live_target_regime: str,
    settlement_source_transfer_validated: bool = False,
) -> dict[str, Any]:
    """Frozen V1 semantics for snapshot / research metadata."""
    if live_target_regime == REGIME_WEATHER_COMPANY_CLINYC:
        return {
            "live_target_regime": live_target_regime,
            "calibration_target_regime": REGIME_NWS_CLI_KNYC,
            "calibration_method": CALIBRATION_METHOD_TRANSFER,
            "transfer_status": TRANSFER_STATUS_EXPERIMENTAL,
            "settlement_source_transfer_validated": False,
            "gfs_component": "NWS-calibrated GFS + experimental KNYC→CLINYC identity transfer",
            "hrrr_component": "NWS-calibrated HRRR + experimental KNYC→CLINYC identity transfer",
            "frozen": True,
            "note": (
                "SHADOW_GFS_HRRR_EQUAL_V1 must not silently switch to direct CLINYC "
                "calibration; register a new candidate version instead."
            ),
        }
    return {
        "live_target_regime": live_target_regime,
        "calibration_target_regime": REGIME_NWS_CLI_KNYC,
        "calibration_method": CALIBRATION_METHOD_NWS,
        "transfer_status": TRANSFER_STATUS_NONE,
        "settlement_source_transfer_validated": settlement_source_transfer_validated,
        "gfs_component": "direct NWS-calibrated GFS",
        "hrrr_component": "direct NWS-calibrated HRRR",
        "frozen": True,
    }


def load_or_register_shadow_hypothesis() -> dict[str, Any]:
    """Write-once registration. Never overwrites registered_at."""
    HYPOTHESES_DIR.mkdir(parents=True, exist_ok=True)
    if SHADOW_HYPOTHESIS_PATH.exists():
        existing = cache.read_json(SHADOW_HYPOTHESIS_PATH, default=None)
        if isinstance(existing, dict) and existing.get("candidate_id") == SHADOW_CANDIDATE_ID:
            return existing

    payload = {
        "candidate_id": SHADOW_CANDIDATE_ID,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "shadow",
        "definition": (
            "0.5 * P_gfs_transferred + 0.5 * P_hrrr_transferred per canonical bucket; "
            "renormalize only for floating-point drift"
        ),
        "target_regime_semantics": {
            "nws_cli_knyc": "direct NWS residual calibration for both components",
            "weather_company_clinyc": (
                "NWS-calibrated GFS + HRRR with experimental KNYC→CLINYC identity "
                "transfer for BOTH components"
            ),
            "frozen": True,
            "note": (
                "Do not silently switch this candidate to direct CLINYC calibration; "
                "register a new version instead"
            ),
        },
        "required_sources": [
            "calibrated_gfs_operational",
            "calibrated_hrrr_operational",
        ],
        "incumbent_model": "calibrated_gfs",
        "bucket_alignment": "canonical_ticker_and_boundaries_required",
        "evaluation_metrics": [
            "brier",
            "log_loss",
            "top_bucket_accuracy",
            "mean_p_winner",
            "paired_delta_brier_bootstrap",
        ],
        "no_learned_weights": True,
        "no_kalshi_prices": True,
        "no_afd_adjustment": True,
        "no_gefs_weighting": True,
        "no_confidence_weighting": True,
        "no_dynamic_switching": True,
    }
    cache.write_json(SHADOW_HYPOTHESIS_PATH, payload)
    return payload

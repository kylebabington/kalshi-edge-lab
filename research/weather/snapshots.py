"""Immutable prospective PredictionSnapshot journal.

Snapshots are append-only timestamped files. Never rewrite with later knowledge.
Kalshi quotes live in a sidecar — never inside WeatherPrediction.
Scoring artifacts are separate from snapshots.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kalshi import cache
from research.weather.models import (
    DECISION_NO_BET,
    DECISION_RESEARCH_ONLY,
    EVIDENCE_CLASS_PHASE5,
    GFS_PUBLICATION_LATENCY,
    HRRR_MODEL,
    HRRR_OPERATIONAL_SELECTION_POLICY,
    HRRR_PUBLICATION_LATENCY,
    MODEL_COMBINATION_POLICY,
    SHADOW_CANDIDATE_ID,
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_VERSION_V4,
)

SNAPSHOT_ROOT = cache.REPO_ROOT / "data" / "weather" / "snapshots"
SCORE_ROOT = cache.REPO_ROOT / "data" / "weather" / "snapshot_scores"
SCORE_CSV = SCORE_ROOT / "snapshot_scores.csv"

LEAD_BINS = (
    (">24h", 24.0, float("inf")),
    ("12–24h", 12.0, 24.0),
    ("6–12h", 6.0, 12.0),
    ("3–6h", 3.0, 6.0),
    ("<3h", 0.0, 3.0),
)

SCORE_STATUS_OK = "OK"
SCORE_STATUS_SETTLEMENT_UNAVAILABLE = "SETTLEMENT_UNAVAILABLE"
SCORE_STATUS_MISSING_OUTCOME = "MISSING_OUTCOME"
SCORE_STATUS_EVENT_NOT_FINALIZED = "EVENT_NOT_FINALIZED"
SCORE_STATUS_REGIME_UNKNOWN = "REGIME_UNKNOWN"
SCORE_STATUS_BUCKET_UNIDENTIFIABLE = "BUCKET_UNIDENTIFIABLE"


class SnapshotConflictError(RuntimeError):
    """Same snapshot_id exists with different research content — never overwrite."""

    def __init__(self, snapshot_id: str, path: Path):
        self.snapshot_id = snapshot_id
        self.path = path
        super().__init__(
            f"SNAPSHOT_CONFLICT: {snapshot_id} already exists at {path} "
            f"with different content — refusing overwrite"
        )


def ensure_snapshot_dirs() -> None:
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    SCORE_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class PredictionSnapshot:
    snapshot_id: str
    schema_version: str

    created_at: str
    prediction_as_of: str

    event_ticker: str
    target_date: str

    resolution_regime: str
    resolution_certainty: str

    calibration_method: str | None
    transfer_status: str | None
    prediction_status: str

    external_probability_distribution: dict[str, float]
    confidence: dict[str, Any]

    evidence: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    calibration_artifact_id: str | None = None
    methodology_constants: dict[str, Any] = field(default_factory=dict)

    # Sidecar — recorded for research, MUST NOT enter WeatherPrediction
    kalshi_quote_sidecar: dict[str, Any] = field(default_factory=dict)

    prediction: dict[str, Any] = field(default_factory=dict)
    research_status: dict[str, Any] = field(default_factory=dict)

    # Phase 5 extensions (schema 5.0.0). Absent on immutable 4.0.0 snapshots.
    shadow_predictions: dict[str, Any] = field(default_factory=dict)
    evidence_class: str | None = None
    candidate_registered_before_snapshot: bool | None = None
    checkpoint_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def make_snapshot_id(event_ticker: str, prediction_as_of: datetime) -> str:
    return f"{event_ticker}__{_stamp(prediction_as_of)}"


def snapshot_path(event_ticker: str, prediction_as_of: datetime) -> Path:
    return SNAPSHOT_ROOT / event_ticker / f"{_stamp(prediction_as_of)}.json"


def latest_pointer_path(event_ticker: str) -> Path:
    return SNAPSHOT_ROOT / event_ticker / "latest.json"


def calibration_artifact_hash(csv_path: Path | None = None) -> str | None:
    from research.weather.calibration import CALIBRATION_CSV

    path = csv_path or CALIBRATION_CSV
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def build_prediction_snapshot(
    bundle: dict[str, Any],
    *,
    created_at: datetime | None = None,
    checkpoint_id: str | None = None,
    evidence_class: str | None = None,
    candidate_registered_before_snapshot: bool | None = None,
    shadow_predictions: dict[str, Any] | None = None,
) -> PredictionSnapshot:
    """Build snapshot from a live event bundle. Does not write.

    New writes use schema 5.0.0. Existing 4.0.0 files remain immutable.
    """
    created_at = created_at or datetime.now(timezone.utc)
    prediction = bundle.get("prediction")
    if hasattr(prediction, "to_dict"):
        pred_dict = prediction.to_dict()
    else:
        pred_dict = dict(prediction or {})

    resolution = bundle.get("resolution")
    if hasattr(resolution, "to_dict"):
        res_dict = resolution.to_dict()
    else:
        res_dict = dict(resolution or {})

    as_of_raw = pred_dict.get("as_of") or bundle.get("generated_at") or created_at.isoformat()
    try:
        as_of_dt = datetime.fromisoformat(str(as_of_raw).replace("Z", "+00:00"))
    except ValueError:
        as_of_dt = created_at

    event_ticker = str(bundle.get("event_ticker") or pred_dict.get("event_ticker"))
    snapshot_id = make_snapshot_id(event_ticker, as_of_dt)

    evidence = bundle.get("evidence") or {}
    if hasattr(evidence, "to_dict"):
        evidence = evidence.to_dict()

    shadow_block = shadow_predictions
    if shadow_block is None:
        shadow_block = dict(bundle.get("shadow_predictions") or {})

    return PredictionSnapshot(
        snapshot_id=snapshot_id,
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        created_at=created_at.isoformat(),
        prediction_as_of=as_of_dt.isoformat(),
        event_ticker=event_ticker,
        target_date=str(bundle.get("target_date") or pred_dict.get("target_date")),
        resolution_regime=str(
            res_dict.get("settlement_source_regime")
            or pred_dict.get("live_target_regime")
            or "unknown"
        ),
        resolution_certainty=str(res_dict.get("resolution_certainty") or "unknown"),
        calibration_method=pred_dict.get("calibration_method"),
        transfer_status=pred_dict.get("transfer_status"),
        prediction_status=str(pred_dict.get("prediction_status") or "UNKNOWN"),
        external_probability_distribution=dict(
            pred_dict.get("bucket_probabilities") or {}
        ),
        confidence={
            "score": pred_dict.get("confidence_score"),
            "label": pred_dict.get("confidence_label"),
            "model_agreement": pred_dict.get("model_agreement"),
            "calibration_quality": pred_dict.get("calibration_quality"),
        },
        evidence=dict(evidence),
        warnings=list(bundle.get("warnings") or []) + list(pred_dict.get("warnings") or []),
        calibration_artifact_id=calibration_artifact_hash(),
        methodology_constants={
            "GFS_PUBLICATION_LATENCY_hours": GFS_PUBLICATION_LATENCY.total_seconds()
            / 3600.0,
            "HRRR_PUBLICATION_LATENCY_hours": HRRR_PUBLICATION_LATENCY.total_seconds()
            / 3600.0,
            "HRRR_MODEL": HRRR_MODEL,
            "HRRR_OPERATIONAL_SELECTION_POLICY": HRRR_OPERATIONAL_SELECTION_POLICY,
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
            "legacy_snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION_V4,
            "model_combination_policy": MODEL_COMBINATION_POLICY,
            "decision": DECISION_RESEARCH_ONLY,
            "no_bet": DECISION_NO_BET,
            "shadow_candidate_id": SHADOW_CANDIDATE_ID,
        },
        kalshi_quote_sidecar={
            "markets": list(bundle.get("kalshi_markets") or []),
            "note": "Sidecar only — not used as WeatherPrediction features",
        },
        prediction=pred_dict,
        research_status=dict(bundle.get("research_status") or {}),
        shadow_predictions=shadow_block,
        evidence_class=evidence_class
        or bundle.get("evidence_class")
        or EVIDENCE_CLASS_PHASE5,
        candidate_registered_before_snapshot=(
            candidate_registered_before_snapshot
            if candidate_registered_before_snapshot is not None
            else bundle.get("candidate_registered_before_snapshot")
        ),
        checkpoint_id=checkpoint_id or bundle.get("checkpoint_id"),
    )


def _canonical_snapshot_payload(payload: dict[str, Any]) -> str:
    """Deterministic JSON for write-once identity checks.

    created_at is excluded — it is write metadata, not research content.
    """
    body = {k: v for k, v in payload.items() if k != "created_at"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def write_snapshot(
    snapshot: PredictionSnapshot,
    *,
    update_latest_pointer: bool = True,
) -> tuple[Path, bool]:
    """Atomic write-once. Returns (path, wrote_new).

    Same snapshot_id + identical canonical content => idempotent reuse.
    Same snapshot_id + different content => SnapshotConflictError (never overwrite).
    latest.json may be rewritten as a convenience pointer only.
    """
    ensure_snapshot_dirs()
    as_of_dt = datetime.fromisoformat(
        snapshot.prediction_as_of.replace("Z", "+00:00")
    )
    path = snapshot_path(snapshot.event_ticker, as_of_dt)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = snapshot.to_dict()
    if path.exists():
        existing = cache.read_json(path, default=None)
        if not isinstance(existing, dict):
            raise SnapshotConflictError(snapshot.snapshot_id, path)
        if existing.get("snapshot_id") != snapshot.snapshot_id:
            raise SnapshotConflictError(snapshot.snapshot_id, path)
        if _canonical_snapshot_payload(existing) == _canonical_snapshot_payload(payload):
            if update_latest_pointer:
                cache.write_json(latest_pointer_path(snapshot.event_ticker), existing)
            return path, False
        raise SnapshotConflictError(snapshot.snapshot_id, path)

    cache.write_json(path, payload)
    if update_latest_pointer:
        # Convenience pointer only — authoritative history is timestamped files.
        cache.write_json(latest_pointer_path(snapshot.event_ticker), payload)
    return path, True


def list_event_snapshots(event_ticker: str) -> list[dict[str, Any]]:
    ensure_snapshot_dirs()
    event_dir = SNAPSHOT_ROOT / event_ticker
    if not event_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(event_dir.glob("*.json")):
        if path.name == "latest.json":
            continue
        data = cache.read_json(path, default=None)
        if not isinstance(data, dict):
            continue
        rows.append(
            {
                "snapshot_id": data.get("snapshot_id"),
                "prediction_as_of": data.get("prediction_as_of"),
                "created_at": data.get("created_at"),
                "event_ticker": data.get("event_ticker"),
                "target_date": data.get("target_date"),
                "prediction_status": data.get("prediction_status"),
                "path": str(path),
            }
        )
    return rows


def load_snapshot(snapshot_id: str) -> dict[str, Any] | None:
    ensure_snapshot_dirs()
    # snapshot_id = EVENT__YYYYMMDDTHHMMSSZ
    if "__" not in snapshot_id:
        return None
    event_ticker, stamp = snapshot_id.split("__", 1)
    path = SNAPSHOT_ROOT / event_ticker / f"{stamp}.json"
    data = cache.read_json(path, default=None)
    return data if isinstance(data, dict) else None


def load_snapshot_by_path(path: Path) -> dict[str, Any] | None:
    data = cache.read_json(path, default=None)
    return data if isinstance(data, dict) else None


def iter_all_snapshots() -> list[dict[str, Any]]:
    ensure_snapshot_dirs()
    out: list[dict[str, Any]] = []
    for event_dir in sorted(SNAPSHOT_ROOT.iterdir() if SNAPSHOT_ROOT.exists() else []):
        if not event_dir.is_dir():
            continue
        for path in sorted(event_dir.glob("*.json")):
            if path.name == "latest.json":
                continue
            data = cache.read_json(path, default=None)
            if isinstance(data, dict):
                out.append(data)
    return out


def lead_bin_hours(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    for label, lo, hi in LEAD_BINS:
        if lo <= hours < hi:
            return label
    return "unknown"


def score_snapshot(
    snapshot: dict[str, Any],
    *,
    actual_high_f: float | None,
    winning_bucket: str | None,
    settlement_source: str | None = None,
    score_status: str = SCORE_STATUS_OK,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    """Produce a scoring artifact. Never mutates the snapshot.

    Callers must resolve settlement by the snapshot's resolution_regime.
    If settlement cannot be obtained for that regime, pass
    score_status=SETTLEMENT_UNAVAILABLE — do not silently substitute another
    regime's actuals.
    """
    from research.weather.replay import log_loss, multiclass_brier

    as_of = snapshot.get("prediction_as_of")
    target = snapshot.get("target_date")
    regime = str(snapshot.get("resolution_regime") or "unknown")

    if score_status != SCORE_STATUS_OK or actual_high_f is None or not winning_bucket:
        return {
            "snapshot_id": snapshot.get("snapshot_id"),
            "event_ticker": snapshot.get("event_ticker"),
            "target_date": target,
            "prediction_as_of": as_of,
            "resolution_regime": regime,
            "score_status": score_status
            if score_status != SCORE_STATUS_OK
            else SCORE_STATUS_MISSING_OUTCOME,
            "skip_reason": skip_reason or "scoring requirements not met",
            "actual_high_f": actual_high_f,
            "winning_bucket": winning_bucket,
            "settlement_source": settlement_source,
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "note": "Unscored — original PredictionSnapshot unchanged",
        }

    probs = dict(snapshot.get("external_probability_distribution") or {})
    if not probs:
        pred = snapshot.get("prediction") or {}
        probs = dict(pred.get("bucket_probabilities") or {})

    brier = multiclass_brier(probs, winning_bucket) if probs else None
    ll = log_loss(probs, winning_bucket) if probs else None
    top_label = max(probs, key=probs.get) if probs else None
    top_correct = top_label == winning_bucket if top_label else None
    p_winner = probs.get(winning_bucket)

    lead_hours = None
    if as_of and target:
        try:
            as_dt = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
            # Approximate settlement end-of-day UTC for lead descriptive bins.
            settle = datetime.strptime(str(target), "%Y-%m-%d").replace(
                hour=23, minute=59, tzinfo=timezone.utc
            )
            lead_hours = (settle - as_dt).total_seconds() / 3600.0
        except ValueError:
            lead_hours = None

    candidate_scores: dict[str, Any] = {}
    shadows = snapshot.get("shadow_predictions") or {}
    if isinstance(shadows, dict):
        for cand_id, block in shadows.items():
            if not isinstance(block, dict):
                continue
            status = block.get("status")
            cprobs = block.get("probabilities")
            if status != "available" or not isinstance(cprobs, dict) or not cprobs:
                candidate_scores[str(cand_id)] = {
                    "status": status or "unavailable",
                    "brier": None,
                    "log_loss": None,
                    "top_correct": None,
                    "p_winner": None,
                    "unavailable_reason": block.get("unavailable_reason"),
                }
                continue
            c_top = max(cprobs, key=cprobs.get)
            candidate_scores[str(cand_id)] = {
                "status": "available",
                "brier": multiclass_brier(cprobs, winning_bucket),
                "log_loss": log_loss(cprobs, winning_bucket),
                "top_correct": c_top == winning_bucket,
                "p_winner": cprobs.get(winning_bucket),
            }

    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "event_ticker": snapshot.get("event_ticker"),
        "target_date": target,
        "prediction_as_of": as_of,
        "resolution_regime": regime,
        "score_status": SCORE_STATUS_OK,
        "actual_high_f": actual_high_f,
        "winning_bucket": winning_bucket,
        "settlement_source": settlement_source,
        "brier": brier,
        "log_loss": ll,
        "incumbent_brier": brier,
        "incumbent_log_loss": ll,
        "top_prediction": top_label,
        "top_prediction_correct": top_correct,
        "probability_on_winner": p_winner,
        "candidate_scores": candidate_scores,
        "evidence_class": snapshot.get("evidence_class"),
        "checkpoint_id": snapshot.get("checkpoint_id"),
        "lead_hours_to_settlement_approx": lead_hours,
        "lead_bin": lead_bin_hours(lead_hours),
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "note": "Separate from PredictionSnapshot — original snapshot unchanged",
    }


def write_score_artifact(score: dict[str, Any]) -> Path:
    ensure_snapshot_dirs()
    snapshot_id = str(score.get("snapshot_id") or "unknown")
    path = SCORE_ROOT / f"{snapshot_id}.json"
    if path.exists():
        existing = cache.read_json(path, default=None)
        if isinstance(existing, dict) and existing.get("snapshot_id") == snapshot_id:
            return path
    cache.write_json(path, score)
    return path


def load_all_scores() -> list[dict[str, Any]]:
    ensure_snapshot_dirs()
    rows: list[dict[str, Any]] = []
    for path in sorted(SCORE_ROOT.glob("*.json")):
        data = cache.read_json(path, default=None)
        if isinstance(data, dict):
            rows.append(data)
    return rows


def summarize_scores_by_lead(scores: list[dict[str, Any]]) -> dict[str, Any]:
    bins: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in LEAD_BINS}
    bins["unknown"] = []
    for row in scores:
        bins.setdefault(str(row.get("lead_bin") or "unknown"), []).append(row)

    summary: dict[str, Any] = {}
    for label, rows in bins.items():
        if not rows:
            summary[label] = {"N": 0}
            continue
        briers = [r["brier"] for r in rows if r.get("brier") is not None]
        correct = [r["top_prediction_correct"] for r in rows if r.get("top_prediction_correct") is not None]
        summary[label] = {
            "N": len(rows),
            "mean_brier": sum(briers) / len(briers) if briers else None,
            "top_bucket_accuracy": (
                sum(1 for c in correct if c) / len(correct) if correct else None
            ),
        }
    return summary

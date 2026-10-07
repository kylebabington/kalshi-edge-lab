"""Phase 7: prospective validation of the Phase 6 v2.1 research pipeline.

Operational layer around the pinned method in phase7_method.py:

  * frozen protocol (write-once JSON) + frozen residual pool file (local)
  * live capture at the five registered checkpoints inside the existing
    30-minute window, saving every fetched response as evidence and logging
    every fetch outcome
  * write-once prediction records and receipts; a checkpoint whose prediction
    is finalized after the window closes is MISSED (diagnostics retained)
  * settlement-gated scoring: pending until a confirmed settlement exists,
    then an immutable final score; idempotent outcomes ledger
  * read-only progress report and offline reproduction

The legacy incumbent snapshot path is untouched; its probabilities are only
read at scoring time. SHADOW ONLY, RESEARCH_ONLY / NO_BET. No prices, trades
or weights.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Sequence

import requests

from kalshi import cache
from research.weather import phase7_method as method
from research.weather.asof_observations import climate_day_bounds_utc
from research.weather.checkpoints import (
    CHECKPOINT_IDS,
    NYC_TZ,
    capture_window_end,
    checkpoint_scheduled_at,
    in_capture_window,
    is_intraday_checkpoint,
    window_elapsed,
)
from research.weather.models import (
    CALIBRATION_METHOD_NWS,
    CALIBRATION_METHOD_TRANSFER,
    CHECKPOINT_CAPTURE_WINDOW_MINUTES,
    DECISION_NO_BET,
    DECISION_RESEARCH_ONLY,
    HRRR_MODEL,
    MODEL_GFS_OPERATIONAL_LATEST,
    MODEL_HRRR_OPERATIONAL_LATEST,
    REGIME_NWS_CLI_KNYC,
    REGIME_WEATHER_COMPANY_CLINYC,
    REPLAY_MODE_FULL_OPERATIONAL,
    SERIES_TICKER,
    TRANSFER_STATUS_EXPERIMENTAL,
    TRANSFER_STATUS_NONE,
)
from research.weather.phase5 import bootstrap_clustered_event_date
from research.weather.sources import knyc_history
from research.weather.sources.single_run_hourly import (
    FETCH_OUTCOME_HTTP_ERROR,
    FETCH_OUTCOME_NETWORK_ERROR,
    FETCH_OUTCOME_OK,
    FETCH_OUTCOME_UNUSABLE,
    classify_hourly_payload,
    fetch_run_hourly_logged,
    parse_hourly_series,
)

PROTOCOL_ID = "PHASE7_PROSPECTIVE_V1"
PROTOCOL_SCHEMA = "phase7_prospective_protocol_v1"
RECORD_SCHEMA = "phase7_prediction_record_v1"
SCORE_SCHEMA = "phase7_final_score_v1"
N_TARGET_DATES = 60
MIN_PAIRED_DATES = 30
INTRADAY_CHECKPOINTS = tuple(c for c in CHECKPOINT_IDS if is_intraday_checkpoint(c))

PHASE7_ROOT = cache.REPO_ROOT / "data" / "weather" / "phase7"
PROTOCOL_PATH = cache.REPO_ROOT / "research" / "weather" / "hypotheses" / "phase7_prospective_protocol_v1.json"

GFS_API_MODEL = "ncep_gfs_global"
API_MODEL = {MODEL_GFS_OPERATIONAL_LATEST: GFS_API_MODEL, MODEL_HRRR_OPERATIONAL_LATEST: HRRR_MODEL}
SHORT_MODEL = {MODEL_GFS_OPERATIONAL_LATEST: "gfs", MODEL_HRRR_OPERATIONAL_LATEST: "hrrr"}

POOL_FIELDS = [
    "model",
    "event_ticker",
    "target_date",
    "checkpoint_id",
    "target_regime",
    "replay_mode",
    "month",
    "season",
    "forecast_high_f",
    "actual_high_f",
    "residual_f",
]

RECEIPT_CAPTURED = "CAPTURED"
RECEIPT_MISSED = "MISSED"
MISSED_WINDOW_ELAPSED = "window_elapsed_without_capture"
MISSED_LATE_FINALIZATION = "finalized_after_capture_window"
MISSED_METHOD_DRIFT = "method_or_pool_drift_refused"
MISSED_NO_EVENT = "no_open_event_with_parseable_buckets"

RUN_FETCH_MAX_TRIES = 2
RUN_FETCH_RETRY_SLEEP_S = 3.0
IEM_TIMEOUT_S = 30
IEM_BACKOFF_S = (10, 20)

LABEL_INSUFFICIENT = "INSUFFICIENT"
LABEL_INTERIM = "DESCRIPTIVE_INTERIM"
LABEL_FINAL = "FINAL_PROTOCOL_ASSESSMENT"

INTERPRETATION = (
    "The primary result measures prospective performance of the transferred forecasting "
    "pipeline (NWS CLI KNYC calibration + experimental identity transfer) against CLINYC "
    "settlement. It does not establish KNYC/CLINYC identity; the CLINYC_TRANSFER_V1 "
    "experiment continues independently with unchanged criteria."
)


class Phase7Error(RuntimeError):
    pass


class Phase7ConflictError(Phase7Error):
    def __init__(self, path: Path):
        super().__init__(f"write-once artifact exists with different content: {path}")
        self.path = path


@dataclass(frozen=True)
class Phase7Paths:
    root: Path = PHASE7_ROOT
    protocol_path: Path = PROTOCOL_PATH

    @property
    def frozen_pool(self) -> Path:
        return self.root / "frozen_pools_v1.csv"

    @property
    def evidence(self) -> Path:
        return self.root / "evidence"

    @property
    def records(self) -> Path:
        return self.root / "records"

    @property
    def diagnostics(self) -> Path:
        return self.root / "diagnostics"

    @property
    def receipts(self) -> Path:
        return self.root / "receipts"

    @property
    def pending(self) -> Path:
        return self.root / "pending_scores"

    @property
    def scores(self) -> Path:
        return self.root / "scores"

    @property
    def outcomes_ledger(self) -> Path:
        return self.root / "outcomes_ledger.jsonl"

    @property
    def fetch_log(self) -> Path:
        return self.root / "fetch_log.jsonl"

    @property
    def dry_run(self) -> Path:
        return self.root / "dry_run"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def write_once_json(path: Path, payload: dict[str, Any], *, conflict_check: bool = True) -> bool:
    """Create ``path`` exclusively; identical re-writes are no-ops, others raise."""
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if not conflict_check:
            return False
        existing = json.loads(path.read_text(encoding="utf-8"))
        if _canonical(existing) == _canonical(json.loads(text)):
            return False
        raise Phase7ConflictError(path)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    return True


CODE_SUFFIXES = (".py", ".ps1")
CODE_NAME_PREFIXES = ("requirements",)


def is_code_path(path: str) -> bool:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.endswith(CODE_SUFFIXES) or (name.startswith(CODE_NAME_PREFIXES) and name.endswith(".txt"))


def git_state(repo_root: Path | None = None) -> dict[str, Any]:
    """Working-tree provenance. Diagnostic only: capture is gated by integrity_problems()."""
    root = repo_root or cache.REPO_ROOT

    def _run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None

    sha = _run(["rev-parse", "HEAD"])
    status = _run(["status", "--porcelain=v1", "-z", "--untracked-files=no"])
    if status is None:
        return {
            "code_git_sha": sha.strip() if sha else None,
            "working_tree_dirty": None,
            "working_tree_changed_paths": None,
            "code_dirty": None,
            "code_dirty_paths": None,
        }
    changed: list[str] = []
    entries = iter(status.split("\0"))
    for entry in entries:
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            next(entries, None)  # -z puts the rename source in the next field
        changed.append(path)
    changed.sort()
    code_paths = [p for p in changed if is_code_path(p)]
    return {
        "code_git_sha": sha.strip() if sha else None,
        "working_tree_dirty": bool(changed),
        "working_tree_changed_paths": changed,
        "code_dirty": bool(code_paths),
        "code_dirty_paths": code_paths,
    }


# ---------------------------------------------------------------------------
# Protocol dates, frozen pool, preflight, registration
# ---------------------------------------------------------------------------


def start_target_date_for(registered_at: datetime) -> date:
    """First climate date whose climate day and every checkpoint come after registration."""
    registered_at = registered_at.astimezone(timezone.utc)
    d = registered_at.astimezone(NYC_TZ).date()
    while True:
        day_start, _ = climate_day_bounds_utc(d)
        earliest = min(checkpoint_scheduled_at(d, c) for c in CHECKPOINT_IDS).astimezone(timezone.utc)
        if day_start > registered_at and earliest > registered_at:
            return d
        d += timedelta(days=1)


def protocol_target_dates(start: date, n: int = N_TARGET_DATES) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def in_protocol_window(protocol: dict[str, Any], target_date: str) -> bool:
    return protocol["start_target_date"] <= target_date <= protocol["end_target_date"]


def build_frozen_pool_rows(
    gfs_rows: Sequence[dict[str, Any]],
    hrrr_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Version-C nws_cli_knyc rows on keys where BOTH models are FULL_OPERATIONAL_REPLAY."""
    from research.weather.phase6 import eligible_keys, restrict_rows

    keys = eligible_keys(list(gfs_rows) + list(hrrr_rows))
    rows = [
        {k: r.get(k, "") for k in POOL_FIELDS}
        for r in restrict_rows(list(gfs_rows) + list(hrrr_rows), keys)
        if (r.get("target_regime") or "") == REGIME_NWS_CLI_KNYC
        and r.get("replay_mode") == REPLAY_MODE_FULL_OPERATIONAL
    ]
    rows.sort(key=lambda r: (r["model"], r["checkpoint_id"], r["target_date"]))
    return rows


def write_pool_csv(path: Path, rows: Sequence[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=POOL_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in POOL_FIELDS})
    data = buf.getvalue().encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def load_frozen_pool(paths: Phase7Paths, protocol: dict[str, Any]) -> list[dict[str, Any]]:
    digest = sha256_file(paths.frozen_pool)
    expected = protocol["calibration"]["frozen_pool_sha256"]
    if digest != expected:
        raise Phase7Error(f"frozen pool hash mismatch: {digest} != {expected}")
    with paths.frozen_pool.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def calibration_preflight(
    pool_rows: Sequence[dict[str, Any]],
    target_dates: Sequence[str],
) -> dict[str, Any]:
    """Forecast-independent pool level per checkpoint × model across the target dates."""
    out: dict[str, Any] = {}
    insufficient: list[dict[str, Any]] = []
    for checkpoint_id in CHECKPOINT_IDS:
        per_model: dict[str, Any] = {}
        for model in method.COMPONENT_MODELS:
            levels: dict[str, int] = {}
            statuses: dict[str, int] = {}
            sizes: set[int] = set()
            bad_dates: list[str] = []
            for d in target_dates:
                feas = method.calibration_feasibility(
                    pool_rows, model=model, checkpoint_id=checkpoint_id, target_date=d
                )
                levels[feas["pool_level"]] = levels.get(feas["pool_level"], 0) + 1
                statuses[feas["status"]] = statuses.get(feas["status"], 0) + 1
                sizes.add(int(feas["prior_rows_n"]))
                if feas["status"] != method.CALIBRATION_FEASIBLE:
                    bad_dates.append(d)
            months = sorted({str(r["target_date"])[:7] for r in pool_rows
                             if r["model"] == model and r["checkpoint_id"] == checkpoint_id})
            per_model[SHORT_MODEL[model]] = {
                "pool_rows_n": sorted(sizes),
                "pool_months": months,
                "selected_level_counts": levels,
                "status_counts": statuses,
                "insufficient_dates_n": len(bad_dates),
            }
            if bad_dates:
                insufficient.append(
                    {
                        "checkpoint_id": checkpoint_id,
                        "model": SHORT_MODEL[model],
                        "dates_n": len(bad_dates),
                        "first": bad_dates[0],
                        "last": bad_dates[-1],
                        "label": method.CALIBRATION_INSUFFICIENT_DETERMINISTIC,
                    }
                )
        out[checkpoint_id] = per_model
    return {
        "target_dates_n": len(target_dates),
        "by_checkpoint": out,
        "insufficient": insufficient,
        "checkpoints_unable_to_meet_thresholds": sorted({i["checkpoint_id"] for i in insufficient}),
        "policy": (
            "Thresholds are not lowered and no checkpoint is removed. Insufficient "
            "components stay in coverage reporting labeled "
            f"{method.CALIBRATION_INSUFFICIENT_DETERMINISTIC}; the pools are frozen, so the "
            "outcome is deterministic for the whole collection period."
        ),
    }


HRRR_SOURCE_INIT_STEP_H = 3
HRRR_SYNOPTIC_HOURS = (0, 6, 12, 18)
HRRR_HORIZON_H = {"synoptic": 48, "intermediate": 18}


def hrrr_expected_horizon_preflight(target_dates: Sequence[str]) -> dict[str, Any]:
    """Expected HRRR coverage under the Phase 5 predicate and the observed source.

    Open-Meteo Single Runs serves HRRR inits every 3 h (other hours: HTTP 400
    "requested model run is not available", observed 2026-10-02); 00/06/12/18Z
    reach 48 h, 03/09/15/21Z reach 18 h. Expectation only — capture decides live.
    """
    out: dict[str, Any] = {}
    structural: list[str] = []
    for checkpoint_id in CHECKPOINT_IDS:
        counts = {"expected_complete": 0, "expected_horizon_short": 0}
        inits: dict[str, int] = {}
        for d in target_dates:
            cutoff = checkpoint_scheduled_at(d, checkpoint_id).astimezone(timezone.utc)
            served = [
                c for c in method.hrrr_candidates(cutoff)
                if c["latency_eligible"] and _parse_dt(c["run_init"]).hour % HRRR_SOURCE_INIT_STEP_H == 0
            ]
            init = _parse_dt(served[0]["run_init"])
            kind = "synoptic" if init.hour in HRRR_SYNOPTIC_HOURS else "intermediate"
            last_valid = init + timedelta(hours=HRRR_HORIZON_H[kind])
            final_hour = climate_day_bounds_utc(d)[1] - timedelta(hours=1)
            key = "expected_complete" if last_valid >= final_hour else "expected_horizon_short"
            counts[key] += 1
            label = f"{init:%H}Z_{kind}_{HRRR_HORIZON_H[kind]}h"
            inits[label] = inits.get(label, 0) + 1
        out[checkpoint_id] = {**counts, "expected_selected_inits": inits}
        if counts["expected_horizon_short"]:
            structural.append(checkpoint_id)
    return {
        "basis": (
            "Phase 5 predicate (newest init with init+3h <= cutoff and a target-date value) on "
            "Open-Meteo Single Runs, which serves HRRR every 3 h; synoptic 48 h, intermediate 18 h"
        ),
        "by_checkpoint": out,
        "checkpoints_expected_hrrr_horizon_short": structural,
        "policy": "expectation only; an incomplete selected run is UNAVAILABLE, never replaced by an older run",
    }


def _source_csvs() -> dict[str, Path]:
    from research.weather.phase6 import GFS_OBS_REPLAY_CSV, HRRR_OBS_REPLAY_CSV

    return {"gfs_v2_1_csv": GFS_OBS_REPLAY_CSV, "hrrr_v2_1_csv": HRRR_OBS_REPLAY_CSV}


def preflight(
    *,
    registered_at: datetime,
    gfs_rows: Sequence[dict[str, Any]] | None = None,
    hrrr_rows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from research.weather.phase5 import load_operational_csv

    csvs = _source_csvs()
    gfs_rows = load_operational_csv(csvs["gfs_v2_1_csv"]) if gfs_rows is None else gfs_rows
    hrrr_rows = load_operational_csv(csvs["hrrr_v2_1_csv"]) if hrrr_rows is None else hrrr_rows
    pool = build_frozen_pool_rows(gfs_rows, hrrr_rows)
    start = start_target_date_for(registered_at)
    dates = protocol_target_dates(start)
    return {
        "registered_at_assumed": _iso(registered_at),
        "start_target_date": dates[0],
        "end_target_date": dates[-1],
        "pool_rows": pool,
        "preflight": calibration_preflight(pool, dates),
        "hrrr_horizon_preflight": hrrr_expected_horizon_preflight(dates),
    }


def register_protocol(
    *,
    registered_at: datetime | None = None,
    paths: Phase7Paths | None = None,
    gfs_rows: Sequence[dict[str, Any]] | None = None,
    hrrr_rows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write the frozen protocol once (refuses to overwrite) plus the local pool file."""
    paths = paths or Phase7Paths()
    if paths.protocol_path.exists():
        raise Phase7Error(f"protocol already registered: {paths.protocol_path}")
    registered_at = (registered_at or utcnow()).astimezone(timezone.utc)
    pre = preflight(registered_at=registered_at, gfs_rows=gfs_rows, hrrr_rows=hrrr_rows)
    pool_sha = write_pool_csv(paths.frozen_pool, pre["pool_rows"])
    source_hashes = {name: sha256_file(p) for name, p in _source_csvs().items()} if gfs_rows is None else {}
    first_cp = min(
        (checkpoint_scheduled_at(pre["start_target_date"], c), c) for c in CHECKPOINT_IDS
    )
    pool_counts: dict[str, int] = {}
    for row in pre["pool_rows"]:
        key = f"{SHORT_MODEL.get(row['model'], row['model'])}|{row['checkpoint_id']}"
        pool_counts[key] = pool_counts.get(key, 0) + 1

    protocol = {
        "schema": PROTOCOL_SCHEMA,
        "protocol_id": PROTOCOL_ID,
        "registered_at": _iso(registered_at),
        "registration_git": git_state(),
        "status": {
            "shadow": "SHADOW ONLY",
            "mode": DECISION_RESEARCH_ONLY,
            "decision": DECISION_NO_BET,
            "incumbent": "calibrated GFS legacy WeatherPrediction (unchanged, stored separately)",
            "no_prices_trades_weights_or_new_models": True,
            "automatic_promotion": False,
        },
        "collection": {
            "start_target_date": pre["start_target_date"],
            "end_target_date": pre["end_target_date"],
            "n_target_dates": N_TARGET_DATES,
            "no_early_stopping": True,
            "start_rule": (
                "first climate date whose climate-day start (00:00 EST) and earliest checkpoint "
                "(dminus1_1800 America/New_York) are both after registered_at"
            ),
            "first_checkpoint": {
                "target_date": pre["start_target_date"],
                "checkpoint_id": first_cp[1],
                "scheduled_at": first_cp[0].isoformat(),
            },
            "checkpoints": list(CHECKPOINT_IDS),
            "capture_window_minutes": CHECKPOINT_CAPTURE_WINDOW_MINUTES,
            "timing": {
                "scheduled_checkpoint_at": "nominal checkpoint (America/New_York)",
                "evidence_cutoff": (
                    "the scheduled checkpoint instant: runs admitted only if init + publication "
                    "latency <= cutoff; observations only if obs_time + 20 min <= cutoff"
                ),
                "prediction_as_of": "actual UTC time the prediction is finalized, after all evidence was received",
                "late_finalization": "finalized at/after scheduled + 30 min -> MISSED; diagnostics kept, never in cohort",
                "no_backfill": "a missed checkpoint is never reconstructed from historical downloads",
            },
        },
        "start_target_date": pre["start_target_date"],
        "end_target_date": pre["end_target_date"],
        "primary_cohort": {
            "events": (
                "live KXHIGHNY events whose settlement rules identify weather_company_clinyc, "
                "with target_date inside the registered 60 dates"
            ),
            "evaluation_target_regime": REGIME_WEATHER_COMPANY_CLINYC,
            "calibration_target_regime": REGIME_NWS_CLI_KNYC,
            "transfer": "experimental KNYC->CLINYC identity transfer, both research components",
            "transfer_status": TRANSFER_STATUS_EXPERIMENTAL,
            "settlement_truth": "confirmed CLINYC settlement (agreeing Kalshi expiration_value + winning bucket)",
            "paired_checkpoint": "valid research GFS, research HRRR and research shadow probabilities",
            "other_regimes": "reported as separate strata; never pooled into the primary cohort",
            "coverage": "every scheduled checkpoint is reported, including MISSED and unavailable predictions",
            "interpretation": INTERPRETATION,
        },
        "primary_comparison": {
            "metric": "multiclass Brier",
            "contrast": "research_shadow minus research_gfs",
            "scope": "pooled intraday checkpoints " + ",".join(INTRADAY_CHECKPOINTS),
            "interval": "95% bootstrap clustered by target date (n=1000, seed=42)",
            "minimum_paired_target_dates": MIN_PAIRED_DATES,
            "below_minimum_label": LABEL_INSUFFICIENT,
            "interim_label": LABEL_INTERIM,
            "final_label": LABEL_FINAL,
            "secondary": "each checkpoint separately; missingness; unique scored dates; log loss",
            "legacy_incumbent": "scored separately for reference; inputs and calibration differ",
        },
        "research_distributions": {
            "research_gfs": "Phase 6 v2.1 version-C calibrated GFS",
            "research_hrrr": "Phase 6 v2.1 version-C calibrated HRRR",
            "research_shadow": "exact 0.5/0.5 combination of those bucket probabilities (SHADOW_GFS_HRRR_EQUAL_V1)",
            "hrrr_policy": (
                "Phase 5 latest_available_by_latency predicate; an incomplete selected run makes "
                "HRRR and shadow UNAVAILABLE; no older run is substituted"
            ),
            "dminus1_1800": "observations NOT_APPLICABLE_PRE_TARGET_DAY; complete climate-day forecast",
        },
        "calibration": {
            "update_policy": (
                "frozen at registration; no prospective KNYC or CLINYC outcome is appended during "
                "the 60-date experiment; new outcomes go to a separate ledger for a future version"
            ),
            "pool_definition": (
                "Phase 6 v2.1 version-C rows: same model, same checkpoint, nws_cli_knyc, "
                "event/checkpoint pairs where BOTH models are FULL_OPERATIONAL_REPLAY, "
                "target_date before the prediction target date"
            ),
            "selection_hierarchy": "month_same_run -> season_same_run -> same_run_global (prediction date month/season)",
            "frozen_pool_file": str(paths.frozen_pool.relative_to(cache.REPO_ROOT))
            if paths.frozen_pool.is_relative_to(cache.REPO_ROOT)
            else str(paths.frozen_pool),
            "frozen_pool_sha256": pool_sha,
            "frozen_pool_rows": len(pre["pool_rows"]),
            "frozen_pool_rows_by_model_checkpoint": pool_counts,
            "source_csv_sha256": source_hashes,
        },
        "calibration_feasibility_preflight": pre["preflight"],
        "hrrr_horizon_preflight": pre["hrrr_horizon_preflight"],
        "method": method.method_fingerprint(),
        "method_change_policy": (
            "Prediction-affecting functions and constants are pinned by source hash; any drift "
            "refuses primary-cohort capture. Operational code (fetching, logging, receipts, "
            "scheduler) is not pinned and is identified by code_git_sha on each record."
        ),
    }
    paths.protocol_path.parent.mkdir(parents=True, exist_ok=True)
    write_once_json(paths.protocol_path, protocol)
    return protocol


def load_protocol(paths: Phase7Paths | None = None) -> dict[str, Any] | None:
    paths = paths or Phase7Paths()
    if not paths.protocol_path.exists():
        return None
    return json.loads(paths.protocol_path.read_text(encoding="utf-8"))


def protocol_sha256(paths: Phase7Paths) -> str | None:
    return sha256_file(paths.protocol_path)


def integrity_status(protocol: dict[str, Any], paths: Phase7Paths) -> dict[str, Any]:
    """Pinned method and calibration hashes only; Git working-tree state is not consulted."""
    method_problems = [f"method_drift:{g}" for g in method.method_drift(protocol["method"])]
    calibration_problems: list[str] = []
    if sha256_file(paths.frozen_pool) != protocol["calibration"]["frozen_pool_sha256"]:
        calibration_problems.append("frozen_pool_hash_mismatch")
    pinned_sources = protocol["calibration"].get("source_csv_sha256") or {}
    csvs = _source_csvs() if pinned_sources else {}
    for name, digest in sorted(pinned_sources.items()):
        if name not in csvs or sha256_file(csvs[name]) != digest:
            calibration_problems.append(f"source_csv_hash_mismatch:{name}")
    return {
        "method_integrity_ok": not method_problems,
        "calibration_integrity_ok": not calibration_problems,
        "problems": method_problems + calibration_problems,
    }


def integrity_problems(protocol: dict[str, Any], paths: Phase7Paths) -> list[str]:
    return integrity_status(protocol, paths)["problems"]


# ---------------------------------------------------------------------------
# Logging and evidence
# ---------------------------------------------------------------------------


def write_evidence_text(path: Path, raw_text: str) -> None:
    """Save decoded response text as UTF-8 bytes: the exact bytes hashed into response_sha256.

    This is the saved UTF-8 response text, not the original HTTP body bytes.
    write_text would translate "\\n" to os.linesep on Windows and break the hash.
    """
    path.write_bytes(raw_text.encode("utf-8"))


def log_fetch(paths: Phase7Paths, entry: dict[str, Any]) -> None:
    cache.append_jsonl(paths.fetch_log, {"logged_at": _iso(utcnow()), **entry})


def fetch_iem_live(
    target_date: str,
    *,
    deadline: datetime | None = None,
    getter: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> dict[str, Any]:
    """Live IEM KNYC fetch for [target_date, target_date + 1) UTC; never cached."""
    getter = getter or requests.get
    d = date.fromisoformat(target_date)
    params = knyc_history._request_params(d, d + timedelta(days=1))
    attempts: list[dict[str, Any]] = []
    for i, backoff in enumerate((0, *IEM_BACKOFF_S)):
        if backoff:
            if deadline is not None and clock() + timedelta(seconds=backoff + IEM_TIMEOUT_S) >= deadline:
                break
            time.sleep(backoff)
        started = clock()
        try:
            response = getter(knyc_history.IEM_ASOS_URL, params=params, timeout=IEM_TIMEOUT_S)
        except requests.RequestException as error:
            attempts.append(
                {
                    "try": i,
                    "outcome": FETCH_OUTCOME_NETWORK_ERROR,
                    "error": f"{type(error).__name__}: {error}",
                    "fetch_started_at": _iso(started),
                    "fetch_completed_at": _iso(clock()),
                }
            )
            continue
        completed = clock()
        text = response.text
        rate_limited = response.status_code in (429, 503) or "too many requests" in text[:300].lower()
        entry = {
            "try": i,
            "http_status": response.status_code,
            "fetch_started_at": _iso(started),
            "fetch_completed_at": _iso(completed),
            "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        if rate_limited or not response.ok:
            attempts.append({**entry, "outcome": FETCH_OUTCOME_HTTP_ERROR, "error": text[:300]})
            continue
        rows = [line for line in text.splitlines() if line and not line.startswith("#")]
        if len(rows) < 2:
            attempts.append({**entry, "outcome": FETCH_OUTCOME_UNUSABLE, "unusable_reason": "no_data_rows"})
            return {"text": None, "raw_text": text, "attempts": attempts, "params": params}
        attempts.append({**entry, "outcome": FETCH_OUTCOME_OK, "row_count": len(rows) - 1})
        return {"text": text, "raw_text": text, "attempts": attempts, "params": params}
    return {"text": None, "raw_text": None, "attempts": attempts, "params": params}


@dataclass
class Fetchers:
    run_hourly: Callable[..., dict[str, Any]] = fetch_run_hourly_logged
    iem: Callable[..., dict[str, Any]] = fetch_iem_live
    clock: Callable[[], datetime] = utcnow
    sleep: Callable[[float], None] = time.sleep


def bucket_dicts_from_markets(markets: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    from research.weather.resolution import parse_temperature_bucket

    out = [b.to_dict() for m in markets if (b := parse_temperature_bucket(m)) is not None]
    out.sort(key=lambda b: (b["lower_bound"] is not None, b["lower_bound"] or 0, b["label"]))
    return out


def _attempt_id(clock: Callable[[], datetime]) -> str:
    return clock().strftime("%Y%m%dT%H%M%S%fZ")


def _fetch_candidate(
    model: str,
    cand: dict[str, Any],
    *,
    ctx: dict[str, Any],
    evidence_dir: Path,
    paths: Phase7Paths,
    fetchers: Fetchers,
) -> dict[str, Any]:
    """Fetch one candidate run (with bounded retries), saving every response."""
    run_init = _parse_dt(cand["run_init"])
    tries: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    for attempt in range(RUN_FETCH_MAX_TRIES):
        result = fetchers.run_hourly(API_MODEL[model], run_init)
        meta = dict(result.get("meta") or {})
        evidence_file = None
        if result.get("raw_text") is not None:
            evidence_file = evidence_dir / f"{SHORT_MODEL[model]}_{run_init:%Y%m%dT%HZ}_try{attempt}.json"
            evidence_file.parent.mkdir(parents=True, exist_ok=True)
            write_evidence_text(evidence_file, result["raw_text"])
        high = None
        if result.get("outcome") == FETCH_OUTCOME_OK:
            high = method.ny_date_high(parse_hourly_series(result.get("payload")), ctx["target_date"])
        entry = {
            "try": attempt,
            "outcome": result.get("outcome"),
            "http_status": meta.get("http_status"),
            "error": meta.get("error"),
            "unusable_reason": meta.get("unusable_reason"),
            "fetch_started_at": meta.get("fetch_started_at"),
            "fetch_completed_at": meta.get("fetch_completed_at"),
            "response_sha256": meta.get("response_sha256"),
            "evidence_file": evidence_file.name if evidence_file else None,
            "target_date_high_f": high,
        }
        tries.append(entry)
        log_fetch(
            paths,
            {
                **ctx,
                "kind": "single_run_hourly",
                "model": model,
                "api_model": API_MODEL[model],
                "run_init": cand["run_init"],
                **entry,
                "selection_eligible": high is not None,
            },
        )
        retryable = result.get("outcome") == FETCH_OUTCOME_NETWORK_ERROR or (
            result.get("outcome") == FETCH_OUTCOME_HTTP_ERROR
            and int(meta.get("http_status") or 0) in (429, 500, 502, 503, 504)
        )
        if not retryable:
            break
        fetchers.sleep(RUN_FETCH_RETRY_SLEEP_S)
    final = tries[-1]
    return {
        "run_init": cand["run_init"],
        "outcome": final["outcome"],
        "payload": result.get("payload"),
        "tries": tries,
        "evidence_file": final["evidence_file"],
    }


def collect_evidence(
    *,
    target_date: str,
    checkpoint_id: str,
    cutoff: datetime,
    deadline: datetime,
    evidence_dir: Path,
    paths: Phase7Paths,
    fetchers: Fetchers,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Fetch observations (intraday only) and candidate runs, saving as received."""
    evidence: dict[str, Any] = {"observations_fetch": None, "runs": {}}
    raw_iem: str | None = None
    if is_intraday_checkpoint(checkpoint_id):
        res = fetchers.iem(target_date, deadline=deadline)
        file_name = None
        if res.get("raw_text") is not None:
            file_name = "iem_knyc.csv"
            (evidence_dir / file_name).parent.mkdir(parents=True, exist_ok=True)
            write_evidence_text(evidence_dir / file_name, res["raw_text"])
        for att in res.get("attempts") or []:
            log_fetch(paths, {**ctx, "kind": "iem_asos", "url": knyc_history.IEM_ASOS_URL, **att})
        raw_iem = res.get("text")
        evidence["observations_fetch"] = {
            "source": knyc_history.SOURCE_ID,
            "url": knyc_history.IEM_ASOS_URL,
            "params": res.get("params"),
            "attempts": res.get("attempts"),
            "evidence_file": file_name if raw_iem is not None else None,
            "raw_response_file": file_name,
        }

    attempts_by_model: dict[str, dict[str, dict[str, Any]]] = {}
    for model in method.COMPONENT_MODELS:
        attempts: dict[str, dict[str, Any]] = {}
        log: list[dict[str, Any]] = []
        for cand in method.candidates_for(model, target_date, cutoff):
            if not cand["latency_eligible"]:
                continue
            got = _fetch_candidate(model, cand, ctx=ctx, evidence_dir=evidence_dir, paths=paths, fetchers=fetchers)
            attempts[cand["run_init"]] = {"outcome": got["outcome"], "payload": got["payload"]}
            log.append({k: v for k, v in got.items() if k != "payload"})
            if got["tries"][-1]["target_date_high_f"] is not None:
                break
        attempts_by_model[model] = attempts
        evidence["runs"][model] = log
    return {"evidence": evidence, "raw_iem": raw_iem, "attempts_by_model": attempts_by_model}


def _evidence_times(evidence: dict[str, Any]) -> list[str]:
    times: list[str] = []
    obs = evidence.get("observations_fetch") or {}
    times += [a["fetch_completed_at"] for a in obs.get("attempts") or [] if a.get("fetch_completed_at")]
    for runs in (evidence.get("runs") or {}).values():
        for run in runs:
            times += [t["fetch_completed_at"] for t in run.get("tries") or [] if t.get("fetch_completed_at")]
    return times


def regime_fields(regime: str) -> dict[str, Any]:
    clinyc = regime == REGIME_WEATHER_COMPANY_CLINYC
    return {
        "evaluation_target_regime": regime,
        "calibration_target_regime": REGIME_NWS_CLI_KNYC,
        "calibration_method": CALIBRATION_METHOD_TRANSFER if clinyc else CALIBRATION_METHOD_NWS,
        "transfer_status": TRANSFER_STATUS_EXPERIMENTAL if clinyc else TRANSFER_STATUS_NONE,
        "primary_cohort_regime_match": clinyc,
    }


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def receipt_path(paths: Phase7Paths, target_date: str, checkpoint_id: str) -> Path:
    return paths.receipts / target_date / f"{checkpoint_id}.json"


def record_path(paths: Phase7Paths, target_date: str, checkpoint_id: str) -> Path:
    return paths.records / target_date / f"{checkpoint_id}.json"


def score_path(paths: Phase7Paths, target_date: str, checkpoint_id: str) -> Path:
    return paths.scores / target_date / f"{checkpoint_id}.json"


def pending_path(paths: Phase7Paths, target_date: str, checkpoint_id: str) -> Path:
    return paths.pending / target_date / f"{checkpoint_id}.json"


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_receipt(paths: Phase7Paths, payload: dict[str, Any]) -> bool:
    """First receipt wins; CAPTURED/MISSED receipts are never overwritten."""
    path = receipt_path(paths, payload["target_date"], payload["checkpoint_id"])
    return write_once_json(path, {**payload, "created_at": _iso(utcnow())}, conflict_check=False)


def capture_checkpoint(
    *,
    protocol: dict[str, Any],
    event: dict[str, Any],
    target_date: str,
    checkpoint_id: str,
    paths: Phase7Paths,
    fetchers: Fetchers | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Capture one checkpoint. Returns {"status", "path", ...}."""
    fetchers = fetchers or Fetchers()
    clock = fetchers.clock
    scheduled = checkpoint_scheduled_at(target_date, checkpoint_id)
    cutoff = scheduled.astimezone(timezone.utc)
    window_end = capture_window_end(scheduled).astimezone(timezone.utc)
    capture_started_at = clock()
    attempt_id = _attempt_id(clock)
    base_root = paths.dry_run if dry_run else paths.root
    evidence_dir = base_root / "evidence" / target_date / checkpoint_id / attempt_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    ctx = {
        "target_date": target_date,
        "checkpoint_id": checkpoint_id,
        "attempt_id": attempt_id,
        "dry_run": dry_run,
    }

    integrity = integrity_status(protocol, paths)
    problems = integrity["problems"]
    regime = str(event.get("regime") or "unknown")
    bucket_dicts = bucket_dicts_from_markets(event.get("markets") or [])
    collected = collect_evidence(
        target_date=target_date,
        checkpoint_id=checkpoint_id,
        cutoff=cutoff,
        deadline=window_end,
        evidence_dir=evidence_dir,
        paths=paths,
        fetchers=fetchers,
        ctx=ctx,
    )
    pool_rows: list[dict[str, Any]] = []
    if "frozen_pool_hash_mismatch" not in problems:
        pool_rows = load_frozen_pool(paths, protocol)
    prediction = method.build_prediction(
        target_date=target_date,
        checkpoint_id=checkpoint_id,
        event_ticker=str(event.get("event_ticker") or ""),
        cutoff=cutoff,
        bucket_dicts=bucket_dicts,
        raw_iem_text=collected["raw_iem"],
        attempts_by_model=collected["attempts_by_model"],
        pool_rows=pool_rows,
    )
    prediction_as_of = clock()
    received = _evidence_times(collected["evidence"])
    latest_received = max(received) if received else None
    if latest_received is not None and _parse_dt(latest_received) > prediction_as_of:
        raise Phase7Error("evidence received after prediction_as_of")
    finalized_in_window = cutoff <= prediction_as_of < window_end
    day_start, day_end = climate_day_bounds_utc(target_date)
    git = git_state()

    record = {
        "schema": RECORD_SCHEMA,
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": protocol_sha256(paths),
        "method_version": method.METHOD_VERSION,
        "method_fingerprint_sha256": method.method_fingerprint()["method_fingerprint_sha256"],
        "pinned_method_fingerprint_sha256": protocol["method"]["method_fingerprint_sha256"],
        "integrity_problems": problems,
        "method_integrity_ok": integrity["method_integrity_ok"],
        "calibration_integrity_ok": integrity["calibration_integrity_ok"],
        **git,
        "attempt_id": attempt_id,
        "dry_run": dry_run,
        "event_ticker": event.get("event_ticker"),
        "target_date": target_date,
        "checkpoint_id": checkpoint_id,
        "in_protocol_window": in_protocol_window(protocol, target_date),
        "scheduled_checkpoint_at": scheduled.isoformat(),
        "scheduled_checkpoint_at_utc": _iso(scheduled),
        "capture_window_end_utc": _iso(window_end),
        "evidence_cutoff_utc": _iso(cutoff),
        "evidence_cutoff_basis": "scheduled checkpoint; frozen publication-latency and observation-availability rules",
        "capture_started_at": _iso(capture_started_at),
        "latest_evidence_received_at": latest_received,
        "prediction_as_of": _iso(prediction_as_of),
        "finalized_within_capture_window": finalized_in_window,
        "climate_day_bounds_utc": [_iso(day_start), _iso(day_end)],
        "buckets": bucket_dicts,
        **regime_fields(regime),
        "resolution_certainty": event.get("resolution_certainty"),
        "evidence_dir": str(evidence_dir.relative_to(paths.root)) if evidence_dir.is_relative_to(paths.root) else str(evidence_dir),
        "evidence": collected["evidence"],
        "frozen_pool": {
            "sha256": protocol["calibration"]["frozen_pool_sha256"],
            "rows_loaded": len(pool_rows),
        },
        "prediction": prediction,
        "status_flags": {
            "shadow": "SHADOW ONLY",
            "mode": DECISION_RESEARCH_ONLY,
            "decision": DECISION_NO_BET,
            "incumbent_unchanged": True,
        },
    }

    if dry_run:
        path = paths.dry_run / "records" / target_date / checkpoint_id / f"{attempt_id}.json"
        write_once_json(path, {**record, "cohort_eligible": False, "note": "DRY RUN — never part of the cohort"})
        return {"status": "DRY_RUN", "path": str(path), "record": record}

    if problems or not finalized_in_window:
        reason = MISSED_METHOD_DRIFT if problems else MISSED_LATE_FINALIZATION
        diag = paths.diagnostics / target_date / checkpoint_id / f"{attempt_id}.json"
        write_once_json(diag, {**record, "diagnostic_reason": reason})
        write_receipt(
            paths,
            {
                "target_date": target_date,
                "checkpoint_id": checkpoint_id,
                "event_ticker": event.get("event_ticker"),
                "status": RECEIPT_MISSED,
                "reason": reason if not problems else f"{reason}:{','.join(problems)}",
                "scheduled_checkpoint_at": scheduled.isoformat(),
                "prediction_as_of": _iso(prediction_as_of),
                "diagnostic_path": str(diag.relative_to(paths.root)),
            },
        )
        return {"status": RECEIPT_MISSED, "reason": reason, "path": str(diag), "record": record}

    path = record_path(paths, target_date, checkpoint_id)
    write_once_json(path, record)
    write_receipt(
        paths,
        {
            "target_date": target_date,
            "checkpoint_id": checkpoint_id,
            "event_ticker": event.get("event_ticker"),
            "status": RECEIPT_CAPTURED,
            "reason": None,
            "scheduled_checkpoint_at": scheduled.isoformat(),
            "prediction_as_of": _iso(prediction_as_of),
            "record_path": str(path.relative_to(paths.root)),
            "paired_valid": prediction["paired_valid"],
        },
    )
    return {"status": RECEIPT_CAPTURED, "path": str(path), "record": record}


def load_open_events(client: Any) -> dict[str, dict[str, Any]]:
    """Open range-bucket KXHIGHNY events by target date, with the settlement regime."""
    from kalshi.client import get_open_markets
    from research.weather.resolution import (
        build_weather_resolution,
        get_event_date,
        group_markets_by_event,
        is_range_bucket_event,
    )

    series_meta: dict[str, Any] = {}
    try:
        series_meta = client.get_series(SERIES_TICKER) or {}
    except Exception:  # noqa: BLE001
        series_meta = {}
    if isinstance(series_meta, dict) and "series" in series_meta:
        series_meta = series_meta["series"]
    out: dict[str, dict[str, Any]] = {}
    for event_ticker, markets in group_markets_by_event(get_open_markets(SERIES_TICKER, client=client)).items():
        target_date = get_event_date(event_ticker)
        if not target_date or not is_range_bucket_event(markets):
            continue
        event_meta: dict[str, Any] = {}
        try:
            event_meta = client.get_event(event_ticker) or {}
            if isinstance(event_meta, dict) and "event" in event_meta:
                event_meta = event_meta["event"]
        except Exception:  # noqa: BLE001
            event_meta = {}
        resolution = build_weather_resolution(
            event_ticker=event_ticker,
            markets=markets,
            series_meta=series_meta if isinstance(series_meta, dict) else None,
            event_meta=event_meta if isinstance(event_meta, dict) else None,
        )
        out[target_date] = {
            "event_ticker": event_ticker,
            "target_date": target_date,
            "markets": markets,
            "regime": resolution.settlement_source_regime,
            "resolution_certainty": resolution.resolution_certainty,
        }
    return out


def due_checkpoints(protocol: dict[str, Any], now: datetime) -> list[tuple[str, str]]:
    now_et = now.astimezone(NYC_TZ).date()
    out: list[tuple[str, str]] = []
    for offset in (0, 1):
        target_date = (now_et + timedelta(days=offset)).isoformat()
        if not in_protocol_window(protocol, target_date):
            continue
        for checkpoint_id in CHECKPOINT_IDS:
            if in_capture_window(now=now, scheduled_at=checkpoint_scheduled_at(target_date, checkpoint_id)):
                out.append((target_date, checkpoint_id))
    return out


def run_capture_stage(
    *,
    now: datetime | None = None,
    client: Any = None,
    paths: Phase7Paths | None = None,
    fetchers: Fetchers | None = None,
    events_loader: Callable[[Any], dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    paths = paths or Phase7Paths()
    fetchers = fetchers or Fetchers()
    protocol = load_protocol(paths)
    if protocol is None:
        return {"status": "not_registered"}
    now = (now or fetchers.clock()).astimezone(timezone.utc)
    due = [
        (d, c) for d, c in due_checkpoints(protocol, now)
        if not receipt_path(paths, d, c).exists()
    ]
    results: list[dict[str, Any]] = []
    if not due:
        return {"status": "ok", "due": 0, "results": results}

    for d, c in due:
        existing = load_json(record_path(paths, d, c))
        if existing is not None:
            write_receipt(
                paths,
                {
                    "target_date": d,
                    "checkpoint_id": c,
                    "event_ticker": existing.get("event_ticker"),
                    "status": RECEIPT_CAPTURED,
                    "reason": "receipt_restored_from_existing_record",
                    "scheduled_checkpoint_at": existing.get("scheduled_checkpoint_at"),
                    "prediction_as_of": existing.get("prediction_as_of"),
                    "record_path": str(record_path(paths, d, c).relative_to(paths.root)),
                    "paired_valid": (existing.get("prediction") or {}).get("paired_valid"),
                },
            )
    due = [(d, c) for d, c in due if not receipt_path(paths, d, c).exists()]
    if not due:
        return {"status": "ok", "due": 0, "results": results}

    events = (events_loader or load_open_events)(client)
    for d, c in due:
        event = events.get(d)
        if event is None or not bucket_dicts_from_markets(event.get("markets") or []):
            results.append({"target_date": d, "checkpoint_id": c, "status": "no_event_yet"})
            continue
        last_error: str | None = None
        for attempt in range(2):
            try:
                out = capture_checkpoint(
                    protocol=protocol, event=event, target_date=d, checkpoint_id=c,
                    paths=paths, fetchers=fetchers,
                )
                results.append(
                    {"target_date": d, "checkpoint_id": c, "status": out["status"],
                     "reason": out.get("reason"), "path": out["path"],
                     "paired_valid": out["record"]["prediction"]["paired_valid"]}
                )
                last_error = None
                break
            except Phase7ConflictError:
                raise
            except Exception as error:  # noqa: BLE001
                last_error = f"{type(error).__name__}: {error}"
                log_fetch(paths, {"kind": "capture_error", "target_date": d, "checkpoint_id": c,
                                  "attempt": attempt, "error": last_error})
                window_end = capture_window_end(checkpoint_scheduled_at(d, c))
                if fetchers.clock() + timedelta(seconds=60) >= window_end:
                    break
                fetchers.sleep(15)
        if last_error is not None:
            results.append({"target_date": d, "checkpoint_id": c, "status": "error", "error": last_error})
    return {"status": "ok", "due": len(due), "results": results}


def dry_run_capture(
    *,
    client: Any = None,
    now: datetime | None = None,
    paths: Phase7Paths | None = None,
    fetchers: Fetchers | None = None,
) -> dict[str, Any]:
    """Live end-to-end exercise for the most recent elapsed checkpoint of an open event.

    Written under dry_run/ only: no receipt, never in the cohort, window not enforced.
    """
    paths = paths or Phase7Paths()
    protocol = load_protocol(paths)
    if protocol is None:
        raise Phase7Error("protocol not registered")
    now = (now or utcnow()).astimezone(timezone.utc)
    events = load_open_events(client)
    best: tuple[datetime, str, str] | None = None
    for target_date in events:
        for checkpoint_id in CHECKPOINT_IDS:
            scheduled = checkpoint_scheduled_at(target_date, checkpoint_id)
            if scheduled <= now and (best is None or scheduled > best[0]):
                best = (scheduled, target_date, checkpoint_id)
    if best is None:
        raise Phase7Error("no open event with an elapsed checkpoint")
    _, target_date, checkpoint_id = best
    return capture_checkpoint(
        protocol=protocol, event=events[target_date], target_date=target_date,
        checkpoint_id=checkpoint_id, paths=paths, fetchers=fetchers, dry_run=True,
    )


def reconcile_missed(
    *,
    now: datetime | None = None,
    paths: Phase7Paths | None = None,
) -> dict[str, Any]:
    """Write MISSED receipts for elapsed protocol checkpoints with no receipt."""
    paths = paths or Phase7Paths()
    protocol = load_protocol(paths)
    if protocol is None:
        return {"status": "not_registered"}
    now = (now or utcnow()).astimezone(timezone.utc)
    missed: list[dict[str, Any]] = []
    for target_date in protocol_target_dates(date.fromisoformat(protocol["start_target_date"])):
        for checkpoint_id in CHECKPOINT_IDS:
            scheduled = checkpoint_scheduled_at(target_date, checkpoint_id)
            if not window_elapsed(now=now, scheduled_at=scheduled):
                continue
            if receipt_path(paths, target_date, checkpoint_id).exists():
                continue
            if record_path(paths, target_date, checkpoint_id).exists():
                rec = load_json(record_path(paths, target_date, checkpoint_id)) or {}
                write_receipt(paths, {
                    "target_date": target_date, "checkpoint_id": checkpoint_id,
                    "event_ticker": rec.get("event_ticker"), "status": RECEIPT_CAPTURED,
                    "reason": "receipt_restored_from_existing_record",
                    "scheduled_checkpoint_at": scheduled.isoformat(),
                    "prediction_as_of": rec.get("prediction_as_of"),
                    "record_path": str(record_path(paths, target_date, checkpoint_id).relative_to(paths.root)),
                    "paired_valid": (rec.get("prediction") or {}).get("paired_valid"),
                })
                continue
            diag_dir = paths.diagnostics / target_date / checkpoint_id
            payload = {
                "target_date": target_date,
                "checkpoint_id": checkpoint_id,
                "event_ticker": None,
                "status": RECEIPT_MISSED,
                "reason": MISSED_WINDOW_ELAPSED,
                "scheduled_checkpoint_at": scheduled.isoformat(),
                "prediction_as_of": None,
                "diagnostics_present": diag_dir.exists() and any(diag_dir.iterdir()),
                "note": "not reconstructed from historical downloads",
            }
            if write_receipt(paths, payload):
                missed.append(payload)
    return {"status": "ok", "missed_this_run": len(missed), "missed": missed}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _incumbent_probs(event_ticker: str, checkpoint_id: str) -> dict[str, Any]:
    from research.weather.checkpoints import load_receipt
    from research.weather.snapshots import load_snapshot

    receipt = load_receipt(event_ticker, checkpoint_id) or {}
    snapshot_id = receipt.get("snapshot_id")
    if not snapshot_id:
        return {"status": "UNAVAILABLE", "reason": f"incumbent_receipt_{receipt.get('status') or 'absent'}"}
    snap = load_snapshot(str(snapshot_id)) or {}
    probs = (snap.get("prediction") or {}).get("bucket_probabilities")
    if not probs:
        return {"status": "UNAVAILABLE", "reason": "incumbent_snapshot_without_probabilities", "snapshot_id": snapshot_id}
    return {
        "status": method.COMPONENT_OK,
        "probabilities": probs,
        "snapshot_id": snapshot_id,
        "prediction_as_of": snap.get("prediction_as_of"),
        "calibration_method": snap.get("calibration_method"),
        "inputs_differ_from_research": True,
    }


def append_outcome_once(paths: Phase7Paths, entry: dict[str, Any]) -> str:
    """Idempotent per event: 'appended', 'duplicate', or 'conflict' (never appended)."""
    existing = [r for r in cache.read_jsonl(paths.outcomes_ledger) if r.get("event_ticker") == entry["event_ticker"]]
    keys = ("event_ticker", "target_date", "regime", "actual_high_f", "winning_bucket", "settlement_source")
    if existing:
        same = all(all(e.get(k) == entry.get(k) for k in keys) for e in existing)
        return "duplicate" if same else "conflict"
    cache.append_jsonl(paths.outcomes_ledger, {**entry, "recorded_at": _iso(utcnow())})
    return "appended"


def score_records(
    *,
    settled_markets: Sequence[dict[str, Any]],
    paths: Phase7Paths | None = None,
    nws_actual_by_date: dict[str, float] | None = None,
    incumbent_loader: Callable[[str, str], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Final immutable scores for confirmed settlements; everything else stays pending."""
    from research.weather.resolution import group_markets_by_event
    from research.weather.service import load_nws_actuals_by_date, resolve_settlement_outcome
    from research.weather.snapshots import SCORE_STATUS_OK

    paths = paths or Phase7Paths()
    now = now or utcnow()
    nws = load_nws_actuals_by_date() if nws_actual_by_date is None else nws_actual_by_date
    incumbent_loader = incumbent_loader or _incumbent_probs
    grouped = group_markets_by_event(list(settled_markets))
    summary = {"final_written": 0, "already_final": 0, "pending": 0, "ledger": {}}
    if not paths.records.exists():
        return summary
    for rec_file in sorted(paths.records.glob("*/*.json")):
        record = json.loads(rec_file.read_text(encoding="utf-8"))
        target_date, checkpoint_id = record["target_date"], record["checkpoint_id"]
        if score_path(paths, target_date, checkpoint_id).exists():
            summary["already_final"] += 1
            continue
        event_ticker = str(record.get("event_ticker") or "")
        regime = str(record.get("evaluation_target_regime") or "unknown")
        outcome = resolve_settlement_outcome(
            grouped.get(event_ticker) or [], regime=regime, target_date=target_date, nws_actual_by_date=nws
        )
        winner = outcome.get("winning_bucket")
        labels = {b["label"] for b in record.get("buckets") or []}
        if outcome["status"] == SCORE_STATUS_OK and winner not in labels:
            outcome = {**outcome, "status": "BUCKET_UNIDENTIFIABLE", "reason": "winner not in recorded buckets"}
        if outcome["status"] != SCORE_STATUS_OK:
            pend = pending_path(paths, target_date, checkpoint_id)
            prev = load_json(pend) or {}
            pend.parent.mkdir(parents=True, exist_ok=True)
            cache.write_json(pend, {
                "target_date": target_date,
                "checkpoint_id": checkpoint_id,
                "event_ticker": event_ticker,
                "status": "PENDING",
                "settlement_status": outcome["status"],
                "reason": outcome["reason"],
                "first_checked_at": prev.get("first_checked_at") or _iso(now),
                "last_checked_at": _iso(now),
                "checks": int(prev.get("checks") or 0) + 1,
            })
            summary["pending"] += 1
            continue

        pred = record["prediction"]
        streams: dict[str, Any] = {}
        for name in ("research_gfs", "research_hrrr", "research_shadow"):
            block = pred[name]
            streams[name] = {
                "status": block.get("status"),
                "reason": block.get("reason"),
                "score": method.score_distribution(block.get("probabilities"), winner)
                if block.get("status") == method.COMPONENT_OK else None,
            }
        inc = incumbent_loader(event_ticker, checkpoint_id)
        streams["legacy_incumbent"] = {
            **{k: v for k, v in inc.items() if k != "probabilities"},
            "score": method.score_distribution(inc.get("probabilities"), winner)
            if inc.get("status") == method.COMPONENT_OK else None,
        }
        receipt = load_json(receipt_path(paths, target_date, checkpoint_id)) or {}
        primary = bool(
            receipt.get("status") == RECEIPT_CAPTURED
            and record.get("in_protocol_window")
            and record.get("finalized_within_capture_window")
            and not record.get("integrity_problems")
            and record.get("primary_cohort_regime_match")
            and pred.get("paired_valid")
        )
        score = {
            "schema": SCORE_SCHEMA,
            "target_date": target_date,
            "checkpoint_id": checkpoint_id,
            "event_ticker": event_ticker,
            "record_path": str(rec_file.relative_to(paths.root)),
            "record_sha256": sha256_file(rec_file),
            "evaluation_target_regime": regime,
            "calibration_target_regime": record.get("calibration_target_regime"),
            "transfer_status": record.get("transfer_status"),
            "actual_high_f": outcome["actual_high_f"],
            "winning_bucket": winner,
            "settlement_source": outcome["settlement_source"],
            "paired_valid": pred.get("paired_valid"),
            "primary_cohort_eligible": primary,
            "streams": streams,
            "scored_at": _iso(now),
        }
        write_once_json(score_path(paths, target_date, checkpoint_id), score)
        pend = pending_path(paths, target_date, checkpoint_id)
        if pend.exists():
            pend.unlink()
        summary["final_written"] += 1
        status = append_outcome_once(paths, {
            "event_ticker": event_ticker,
            "target_date": target_date,
            "regime": regime,
            "actual_high_f": outcome["actual_high_f"],
            "winning_bucket": winner,
            "settlement_source": outcome["settlement_source"],
            "note": "for a future protocol version; never read by Phase 7 v1 calibration",
        })
        summary["ledger"][status] = summary["ledger"].get(status, 0) + 1
    return summary


def run_reconcile_and_score_stage(
    *,
    settled_markets: Sequence[dict[str, Any]],
    now: datetime | None = None,
    paths: Phase7Paths | None = None,
) -> dict[str, Any]:
    paths = paths or Phase7Paths()
    if load_protocol(paths) is None:
        return {"status": "not_registered"}
    return {
        "status": "ok",
        "reconcile": reconcile_missed(now=now, paths=paths),
        "scoring": score_records(settled_markets=settled_markets, paths=paths, now=now),
    }


# ---------------------------------------------------------------------------
# Reproduction
# ---------------------------------------------------------------------------


EVIDENCE_RAW_MATCH = "RAW_MATCH"
EVIDENCE_CRLF_NORMALIZED_MATCH_ONLY = "CRLF_NORMALIZED_MATCH_ONLY"
EVIDENCE_MISMATCH = "MISMATCH"
EVIDENCE_MISSING_FILE = "MISSING_FILE"
EVIDENCE_MISSING_EXPECTED_HASH = "MISSING_EXPECTED_HASH"


def persisted_path(value: str) -> Path:
    """A path string stored in a record, on any OS.

    Records written on Windows store relative paths with backslashes; on POSIX
    Path() would read those as a single file name.
    """
    if "\\" in value:
        win = PureWindowsPath(value)
        if win.is_absolute() or win.drive:
            return Path(value)
        return Path(*win.parts)
    return Path(value)


def _record_evidence_dir(record: dict[str, Any], paths: Phase7Paths) -> Path:
    evidence_dir = persisted_path(record["evidence_dir"])
    return evidence_dir if evidence_dir.is_absolute() else paths.root / evidence_dir


def _referenced_evidence(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Every saved response a record references, with the hash recorded at fetch time."""
    refs: list[dict[str, Any]] = []
    obs = record["evidence"].get("observations_fetch") or {}
    obs_file = obs.get("raw_response_file") or obs.get("evidence_file")
    if obs_file:
        attempts = obs.get("attempts") or []
        refs.append({
            "kind": "observations",
            "evidence_file": obs_file,
            "expected_sha256": attempts[-1].get("response_sha256") if attempts else None,
        })
    for model, runs in sorted((record["evidence"].get("runs") or {}).items()):
        for run in runs:
            for t in run.get("tries") or []:
                if t.get("evidence_file"):
                    refs.append({
                        "kind": "model_run",
                        "model": model,
                        "run_init": run.get("run_init"),
                        "try": t.get("try"),
                        "evidence_file": t["evidence_file"],
                        "expected_sha256": t.get("response_sha256"),
                    })
    return refs


def verify_record_evidence(record: dict[str, Any], *, paths: Phase7Paths | None = None) -> dict[str, Any]:
    """Read-only raw-byte check of each referenced saved response against its recorded hash."""
    paths = paths or Phase7Paths()
    evidence_dir = _record_evidence_dir(record, paths)
    files: list[dict[str, Any]] = []
    for ref in _referenced_evidence(record):
        path = evidence_dir / persisted_path(ref["evidence_file"])
        entry = {**ref, "actual_sha256": None, "raw_bytes_match": None, "crlf_normalized_match": None}
        if not path.is_file():
            entry["status"] = EVIDENCE_MISSING_FILE
        else:
            data = path.read_bytes()
            entry["actual_sha256"] = hashlib.sha256(data).hexdigest()
            if not ref["expected_sha256"]:
                entry["status"] = EVIDENCE_MISSING_EXPECTED_HASH
            else:
                normalized = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
                entry["raw_bytes_match"] = entry["actual_sha256"] == ref["expected_sha256"]
                entry["crlf_normalized_match"] = normalized == ref["expected_sha256"]
                entry["status"] = (
                    EVIDENCE_RAW_MATCH if entry["raw_bytes_match"]
                    else EVIDENCE_CRLF_NORMALIZED_MATCH_ONLY if entry["crlf_normalized_match"]
                    else EVIDENCE_MISMATCH
                )
        files.append(entry)
    counts: dict[str, int] = {}
    for f in files:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    return {
        "all_raw_bytes_match": all(f["status"] == EVIDENCE_RAW_MATCH for f in files),
        "status_counts": counts,
        "files": files,
    }


def reproduce_record(record_file: Path, *, paths: Phase7Paths | None = None) -> dict[str, Any]:
    """Recompute probabilities (and any final score) from saved evidence only."""
    paths = paths or Phase7Paths()
    record = json.loads(Path(record_file).read_text(encoding="utf-8"))
    protocol = load_protocol(paths)
    if protocol is None:
        raise Phase7Error("protocol not registered")
    evidence_dir = _record_evidence_dir(record, paths)
    raw_iem = None
    obs = record["evidence"].get("observations_fetch") or {}
    if obs.get("evidence_file"):
        raw_iem = (evidence_dir / persisted_path(obs["evidence_file"])).read_text(encoding="utf-8")
    attempts_by_model: dict[str, dict[str, dict[str, Any]]] = {}
    for model, runs in (record["evidence"].get("runs") or {}).items():
        attempts: dict[str, dict[str, Any]] = {}
        for run in runs:
            payload = None
            if run.get("evidence_file"):
                raw = (evidence_dir / persisted_path(run["evidence_file"])).read_text(encoding="utf-8")
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = None
            outcome = run["outcome"]
            if outcome == FETCH_OUTCOME_OK and classify_hourly_payload(payload) is not None:
                raise Phase7Error(f"saved payload no longer classifies as usable: {run['evidence_file']}")
            attempts[run["run_init"]] = {"outcome": outcome, "payload": payload}
        attempts_by_model[model] = attempts
    pool_rows = load_frozen_pool(paths, protocol)
    recomputed = method.build_prediction(
        target_date=record["target_date"],
        checkpoint_id=record["checkpoint_id"],
        event_ticker=str(record.get("event_ticker") or ""),
        cutoff=_parse_dt(record["evidence_cutoff_utc"]),
        bucket_dicts=record["buckets"],
        raw_iem_text=raw_iem,
        attempts_by_model=attempts_by_model,
        pool_rows=pool_rows,
    )
    saved = record["prediction"]
    recomputed_rt = json.loads(json.dumps(recomputed, default=str))
    result = {
        "record": str(record_file),
        "prediction_identical": _canonical(recomputed_rt) == _canonical(saved),
        "probabilities_identical": all(
            recomputed_rt[k].get("probabilities") == saved[k].get("probabilities")
            for k in ("research_gfs", "research_hrrr", "research_shadow")
        ),
        "score_identical": None,
        "evidence_integrity": verify_record_evidence(record, paths=paths),
    }
    score = load_json(score_path(paths, record["target_date"], record["checkpoint_id"]))
    if score is not None:
        winner = score["winning_bucket"]
        result["score_identical"] = all(
            score["streams"][k]["score"]
            == (json.loads(json.dumps(method.score_distribution(recomputed_rt[k].get("probabilities"), winner)))
                if recomputed_rt[k].get("status") == method.COMPONENT_OK else None)
            for k in ("research_gfs", "research_hrrr", "research_shadow")
        )
    return result


# ---------------------------------------------------------------------------
# Read-only progress report
# ---------------------------------------------------------------------------


def _read_dir_json(root: Path, pattern: str) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.glob(pattern))]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_progress_report(
    *,
    now: datetime | None = None,
    paths: Phase7Paths | None = None,
) -> dict[str, Any]:
    """Read-only. Writes nothing."""
    paths = paths or Phase7Paths()
    protocol = load_protocol(paths)
    if protocol is None:
        return {"status": "not_registered"}
    now = (now or utcnow()).astimezone(timezone.utc)
    integrity = integrity_status(protocol, paths)
    dates = protocol_target_dates(date.fromisoformat(protocol["start_target_date"]))
    receipts = {(r["target_date"], r["checkpoint_id"]): r for r in _read_dir_json(paths.receipts, "*/*.json")}
    records = {(r["target_date"], r["checkpoint_id"]): r for r in _read_dir_json(paths.records, "*/*.json")}
    scores = {(s["target_date"], s["checkpoint_id"]): s for s in _read_dir_json(paths.scores, "*/*.json")}
    pending = _read_dir_json(paths.pending, "*/*.json")

    coverage: dict[str, Any] = {}
    for checkpoint_id in CHECKPOINT_IDS:
        c = {"scheduled_so_far": 0, "captured": 0, "missed": 0, "missed_reasons": {},
             "awaiting_receipt": 0, "future": 0, "paired_valid": 0,
             "component_status": {"research_gfs": {}, "research_hrrr": {}, "research_shadow": {}},
             "obs_status": {}}
        for d in dates:
            scheduled = checkpoint_scheduled_at(d, checkpoint_id)
            if scheduled > now:
                c["future"] += 1
                continue
            c["scheduled_so_far"] += 1
            rec = receipts.get((d, checkpoint_id))
            if rec is None:
                c["awaiting_receipt"] += 1
            elif rec["status"] == RECEIPT_CAPTURED:
                c["captured"] += 1
            else:
                c["missed"] += 1
                reason = str(rec.get("reason") or "").split(":")[0]
                c["missed_reasons"][reason] = c["missed_reasons"].get(reason, 0) + 1
            record = records.get((d, checkpoint_id))
            if record is not None:
                pred = record["prediction"]
                c["paired_valid"] += int(bool(pred.get("paired_valid")))
                for name in c["component_status"]:
                    key = pred[name].get("status") if pred[name].get("status") == method.COMPONENT_OK else str(pred[name].get("reason"))
                    c["component_status"][name][key] = c["component_status"][name].get(key, 0) + 1
                obs_status = pred["observations"]["summary"].get("status")
                c["obs_status"][obs_status] = c["obs_status"].get(obs_status, 0) + 1
        coverage[checkpoint_id] = c

    fetch_outcomes: dict[str, dict[str, int]] = {}
    for entry in cache.read_jsonl(paths.fetch_log):
        if entry.get("dry_run") or entry.get("target_date") not in dates:
            continue
        kind = f"{entry.get('kind')}:{SHORT_MODEL.get(entry.get('model'), entry.get('model') or 'obs')}"
        bucket = fetch_outcomes.setdefault(kind, {})
        key = str(entry.get("outcome") or entry.get("error") or "unknown")
        bucket[key] = bucket.get(key, 0) + 1

    def _stream_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in ("research_gfs", "research_hrrr", "research_shadow", "legacy_incumbent"):
            vals = [s["streams"][name]["score"] for s in items if s["streams"].get(name, {}).get("score")]
            out[name] = {
                "n": len(vals),
                "brier": _mean([v["brier"] for v in vals]),
                "log_loss": _mean([v["log_loss"] for v in vals]),
            }
        return out

    def _delta(items: list[dict[str, Any]]) -> dict[str, Any]:
        obs = [
            {
                "target_date": s["target_date"],
                "delta_brier": s["streams"]["research_shadow"]["score"]["brier"]
                - s["streams"]["research_gfs"]["score"]["brier"],
            }
            for s in items
        ]
        return bootstrap_clustered_event_date(obs, value_key="delta_brier")

    primary = [s for s in scores.values() if s.get("primary_cohort_eligible")]
    strata: dict[str, list[dict[str, Any]]] = {}
    for s in scores.values():
        strata.setdefault(str(s.get("evaluation_target_regime")), []).append(s)
    primary_intraday = [s for s in primary if s["checkpoint_id"] in INTRADAY_CHECKPOINTS]
    unique_dates = sorted({s["target_date"] for s in primary_intraday})
    end_climate_day = climate_day_bounds_utc(protocol["end_target_date"])[1]
    collection_complete = now >= end_climate_day and not pending and all(
        (d, c) in receipts for d in dates for c in CHECKPOINT_IDS
    )
    if len(unique_dates) < MIN_PAIRED_DATES:
        label = LABEL_INSUFFICIENT
    elif not collection_complete:
        label = LABEL_INTERIM
    else:
        label = LABEL_FINAL
    if collection_complete and len(unique_dates) < MIN_PAIRED_DATES:
        label = f"{LABEL_FINAL}_{LABEL_INSUFFICIENT}"

    return {
        "generated_at": _iso(now),
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": protocol_sha256(paths),
        "registered_at": protocol["registered_at"],
        "collection_window": [protocol["start_target_date"], protocol["end_target_date"]],
        "status": "SHADOW ONLY | RESEARCH_ONLY / NO_BET | no automatic promotion or trading",
        "integrity_problems": integrity["problems"],
        "method_integrity_ok": integrity["method_integrity_ok"],
        "calibration_integrity_ok": integrity["calibration_integrity_ok"],
        "working_tree": git_state(),
        "calibration_unable_to_meet_thresholds": protocol["calibration_feasibility_preflight"][
            "checkpoints_unable_to_meet_thresholds"
        ],
        "coverage_by_checkpoint": coverage,
        "fetch_outcomes": fetch_outcomes,
        "scores": {
            "final": len(scores),
            "pending": len(pending),
            "pending_reasons": {
                r: sum(1 for p in pending if p.get("reason") == r) for r in {p.get("reason") for p in pending}
            },
        },
        "primary_cohort": {
            "evaluation_target_regime": REGIME_WEATHER_COMPANY_CLINYC,
            "paired_checkpoints": len(primary),
            "paired_intraday_checkpoints": len(primary_intraday),
            "unique_paired_intraday_target_dates": len(unique_dates),
            "minimum_required": MIN_PAIRED_DATES,
            "label": label,
            "interval_interpretation": "descriptive only until the fixed collection period ends"
            if label != LABEL_FINAL else "final protocol assessment",
            "pooled_intraday_shadow_minus_gfs_brier": _delta(primary_intraday),
            "by_checkpoint_shadow_minus_gfs_brier": {
                c: _delta([s for s in primary if s["checkpoint_id"] == c]) for c in CHECKPOINT_IDS
            },
            "descriptive_scores": _stream_summary(primary),
            "interpretation": INTERPRETATION,
        },
        "strata_by_regime": {
            regime: {"final_scores": len(items), "descriptive_scores": _stream_summary(items)}
            for regime, items in strata.items()
        },
    }

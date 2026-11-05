"""Phase 7 historical recovery — separate from the frozen prospective protocol.

Read-only against Phase 7 write-once trees. All outputs go under
``data/weather/phase7_historical_replay/``. Missed checkpoints are never
written back into receipts, records, evidence, or primary evaluation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from kalshi import cache
from research.weather import phase7
from research.weather import phase7_method as method
from research.weather.asof_observations import climate_day_bounds_utc
from research.weather.checkpoints import (
    CHECKPOINT_IDS,
    NYC_TZ,
    checkpoint_scheduled_at,
    is_intraday_checkpoint,
)
from research.weather.models import (
    DECISION_NO_BET,
    DECISION_RESEARCH_ONLY,
    MODEL_GFS_OPERATIONAL_LATEST,
    MODEL_HRRR_OPERATIONAL_LATEST,
    REGIME_WEATHER_COMPANY_CLINYC,
    SERIES_TICKER,
)
from research.weather.sources import knyc_history
from research.weather.sources.single_run_hourly import (
    FETCH_OUTCOME_OK,
    WINDOW_STATUS_OK,
    fetch_run_hourly_logged,
    parse_hourly_series,
    remaining_day_high,
)

DATASET_ID = "PHASE7_HISTORICAL_REPLAY_V1"
RECORD_SCHEMA = "phase7_historical_replay_record_v1"
SCORE_SCHEMA = "phase7_historical_replay_score_v1"
HYPOTHESIS_PATH = (
    cache.REPO_ROOT / "research" / "weather" / "hypotheses" / "phase7_historical_replay_v1.json"
)
REPLAY_ROOT = cache.REPO_ROOT / "data" / "weather" / "phase7_historical_replay"

PRIORITY_PRIMARY = ("d0_0600", "d0_1200", "d0_1500")
PRIORITY_SECONDARY = ("dminus1_1800",)
PRIORITY_STRUCTURAL = ("d0_0900",)
STRATUM_MISSED = "MISSED_RECONSTRUCTION"
STRATUM_AGREEMENT = "LIVE_AGREEMENT"

AS_OF_RULE_GATED = "as_of_rule_gated"
AS_OF_ARCHIVE_NOW = "archive_retrieved_now"
AS_OF_POST_SETTLEMENT = "post_settlement_market"
AS_OF_CURRENT_OPEN = "current_open_unverified_as_of"

LIMITATIONS = [
    "Not a Phase 7 record; never enters the prospective cohort or primary evaluation.",
    "reconstructed_at is reconstruction time, not a capture-window finalization.",
    "Open-Meteo payloads are archive_retrieved_now; in-window bytes are unverifiable.",
    "IEM publication time is assumed_fixed_lag_20m; the monthly archive may differ from the live snapshot.",
    "Kalshi buckets from historical settled markets are post_settlement_market, not contemporaneous open state.",
    "d0_0900 remains structurally unpaired under the frozen method.",
    "A reconstructed paired score is HISTORICAL_REPLAY_DIAGNOSTIC only.",
]

RUN_FETCH_MAX_TRIES = 2
RUN_FETCH_RETRY_SLEEP_S = 3.0
PRIMARY_RETRIEVAL_THRESHOLD = 0.8
LIVE_CAPTURE_HOURS_ET = (6, 9, 12, 15, 18)
LIVE_PRE_BUFFER = timedelta(minutes=5)
LIVE_WINDOW = timedelta(minutes=30)
COLLECTOR_MINUTE_ET = 5
COLLECTOR_PAUSE_BEFORE = timedelta(minutes=2)
COLLECTOR_PAUSE_AFTER = timedelta(minutes=3)
CHECKPOINT_TIME_BUDGET = timedelta(seconds=90)
COLLECTOR_WAIT_MAX = timedelta(minutes=6)
WRITE_ONCE_REL_PREFIXES = (
    "data/weather/phase7/records/",
    "data/weather/phase7/receipts/",
    "data/weather/phase7/evidence/",
    "data/weather/phase7/scores/",
    "data/weather/phase7/diagnostics/",
    "data/weather/phase7/dry_run/",
)
APPEND_ONLY_REL = (
    "data/weather/phase7/fetch_log.jsonl",
    "data/weather/phase7/outcomes_ledger.jsonl",
)
PENDING_REL_PREFIX = "data/weather/phase7/pending_scores/"
FROZEN_POOL_REL = "data/weather/phase7/frozen_pools_v1.csv"


class ReplayError(RuntimeError):
    pass


class ReplayIsolationError(ReplayError):
    pass


class ReplayBlockedError(ReplayError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def assert_isolated_write(path: Path, *, phase7_root: Path | None = None) -> None:
    """Refuse any write under the frozen Phase 7 tree or the frozen protocol file."""
    phase7_root = (phase7_root or phase7.PHASE7_ROOT).resolve()
    resolved = path.resolve()
    if _is_under(resolved, phase7_root) or resolved == phase7_root:
        raise ReplayIsolationError(f"refusing to write under Phase 7 root: {path}")
    protocol = phase7.PROTOCOL_PATH.resolve()
    if resolved == protocol:
        raise ReplayIsolationError(f"refusing to modify the frozen protocol: {path}")
    checkpoints = (cache.REPO_ROOT / "data" / "weather" / "checkpoints").resolve()
    if _is_under(resolved, checkpoints):
        raise ReplayIsolationError(f"refusing to write under incumbent checkpoints: {path}")


def priority_band(checkpoint_id: str) -> str:
    if checkpoint_id in PRIORITY_PRIMARY:
        return "primary"
    if checkpoint_id in PRIORITY_SECONDARY:
        return "secondary"
    if checkpoint_id in PRIORITY_STRUCTURAL:
        return "structurally_unavailable"
    return "other"


def structurally_unavailable(checkpoint_id: str) -> bool:
    return checkpoint_id in PRIORITY_STRUCTURAL


@dataclass
class ReplayPaths:
    root: Path = REPLAY_ROOT
    phase7_root: Path = field(default_factory=lambda: phase7.PHASE7_ROOT)

    def __post_init__(self) -> None:
        if _is_under(self.root, self.phase7_root) or self.root.resolve() == self.phase7_root.resolve():
            raise ReplayIsolationError(
                f"replay root must be outside Phase 7: {self.root} vs {self.phase7_root}"
            )

    @property
    def inventory(self) -> Path:
        return self.root / "inventory.json"

    @property
    def probe(self) -> Path:
        return self.root / "probe.json"

    @property
    def evidence(self) -> Path:
        return self.root / "evidence"

    @property
    def records(self) -> Path:
        return self.root / "records"

    @property
    def scores(self) -> Path:
        return self.root / "scores"

    @property
    def coverage_csv(self) -> Path:
        return self.root / "coverage.csv"

    @property
    def agreement_csv(self) -> Path:
        return self.root / "agreement.csv"

    @property
    def report(self) -> Path:
        return self.root / "REPORT.md"

    @property
    def methodology(self) -> Path:
        return self.root / "methodology.json"

    @property
    def fetch_log(self) -> Path:
        return self.root / "fetch_log.jsonl"

    @property
    def pre_run_manifest(self) -> Path:
        return self.root / "pre_run_manifest.json"

    @property
    def integrity(self) -> Path:
        return self.root / "integrity.json"

    @property
    def reconstruction(self) -> Path:
        return self.root / "reconstruction.json"

    def record_path(self, target_date: str, checkpoint_id: str) -> Path:
        return self.records / target_date / f"{checkpoint_id}.json"

    def score_path(self, target_date: str, checkpoint_id: str) -> Path:
        return self.scores / target_date / f"{checkpoint_id}.json"


def write_json(path: Path, payload: dict[str, Any], *, phase7_root: Path | None = None) -> None:
    assert_isolated_write(path, phase7_root=phase7_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_text(path: Path, text: str, *, phase7_root: Path | None = None) -> None:
    assert_isolated_write(path, phase7_root=phase7_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def write_csv(
    path: Path,
    fieldnames: list[str],
    rows: Sequence[dict[str, Any]],
    *,
    phase7_root: Path | None = None,
) -> None:
    assert_isolated_write(path, phase7_root=phase7_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fieldnames})
    path.write_bytes(buf.getvalue().encode("utf-8"))


def append_fetch_log(paths: ReplayPaths, entry: dict[str, Any]) -> None:
    assert_isolated_write(paths.fetch_log, phase7_root=paths.phase7_root)
    paths.fetch_log.parent.mkdir(parents=True, exist_ok=True)
    with paths.fetch_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"logged_at": _iso(utcnow()), **entry}, default=str) + "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _rel_under_phase7(path: Path, phase7_root: Path) -> str:
    return path.resolve().relative_to(phase7_root.resolve()).as_posix()


def _repo_rel(rel: str) -> str:
    return f"data/weather/phase7/{rel}"


def classify_phase7_file(rel: str) -> str:
    repo_rel = rel if rel.startswith("data/weather/phase7/") else _repo_rel(rel)
    if repo_rel == FROZEN_POOL_REL:
        return "frozen"
    if repo_rel.startswith(WRITE_ONCE_REL_PREFIXES):
        return "write_once"
    if repo_rel in APPEND_ONLY_REL:
        return "append_only"
    if repo_rel.startswith(PENDING_REL_PREFIX) or repo_rel == PENDING_REL_PREFIX.rstrip("/"):
        return "pending"
    return "other"


def iter_phase7_files(phase7_root: Path) -> list[Path]:
    files: list[Path] = []
    if not phase7_root.exists():
        return files
    for path in sorted(phase7_root.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            files.append(path)
    return files


def build_phase7_manifest(
    *,
    phase7_paths: phase7.Phase7Paths | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    p7 = phase7_paths or phase7.Phase7Paths()
    files: dict[str, dict[str, Any]] = {}
    for path in iter_phase7_files(p7.root):
        rel = _rel_under_phase7(path, p7.root)
        data = path.read_bytes()
        files[rel] = {
            "sha256": sha256_bytes(data),
            "size": len(data),
            "class": classify_phase7_file(rel),
        }
    protocol_sha = sha256_file(phase7.PROTOCOL_PATH) if phase7.PROTOCOL_PATH.exists() else None
    pool_sha = sha256_file(p7.frozen_pool) if p7.frozen_pool.exists() else None
    return {
        "captured_at": _iso(captured_at or utcnow()),
        "protocol_sha256": protocol_sha,
        "frozen_pool_sha256": pool_sha,
        "file_n": len(files),
        "files": files,
    }


def compare_phase7_integrity(
    before: dict[str, Any],
    after: dict[str, Any] | None = None,
    *,
    phase7_paths: phase7.Phase7Paths | None = None,
) -> dict[str, Any]:
    """Compare a pre-run manifest to the live Phase 7 tree.

    Pre-existing write-once / frozen files must be byte-identical. New collector
    records, receipts, evidence, and scores are allowed. Append-only files may
    grow if the original bytes remain an exact prefix. Pending-score files may
    appear, change, or vanish. Anything else is unexpected.
    """
    p7 = phase7_paths or phase7.Phase7Paths()
    after = after or build_phase7_manifest(phase7_paths=p7)
    before_files = before.get("files") or {}
    after_files = after.get("files") or {}
    unchanged: list[str] = []
    collector_additions: list[str] = []
    append_only_extended: list[dict[str, Any]] = []
    pending_transitions: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []

    protocol_unchanged = before.get("protocol_sha256") == after.get("protocol_sha256")
    pool_unchanged = before.get("frozen_pool_sha256") == after.get("frozen_pool_sha256")
    if not protocol_unchanged:
        unexpected.append({
            "path": "research/weather/hypotheses/phase7_prospective_v1.json",
            "reason": "protocol_hash_changed",
            "before": before.get("protocol_sha256"),
            "after": after.get("protocol_sha256"),
        })
    if not pool_unchanged:
        unexpected.append({
            "path": FROZEN_POOL_REL,
            "reason": "frozen_pool_hash_changed",
            "before": before.get("frozen_pool_sha256"),
            "after": after.get("frozen_pool_sha256"),
        })

    def _live_path(rel: str) -> Path:
        return p7.root / rel

    def _report_path(rel: str) -> str:
        return _repo_rel(rel)

    for rel, meta in before_files.items():
        cls = meta.get("class") or classify_phase7_file(rel)
        current = after_files.get(rel)
        if cls == "pending":
            if current is None:
                pending_transitions.append({"path": _report_path(rel), "change": "removed"})
            elif current.get("sha256") != meta.get("sha256"):
                pending_transitions.append({"path": _report_path(rel), "change": "updated"})
            else:
                unchanged.append(_report_path(rel))
            continue
        if current is None:
            unexpected.append({"path": _report_path(rel), "reason": "deleted", "class": cls})
            continue
        if cls == "append_only":
            live_path = _live_path(rel)
            if not live_path.exists():
                unexpected.append({"path": _report_path(rel), "reason": "deleted", "class": cls})
                continue
            live = live_path.read_bytes()
            size = int(meta.get("size") or 0)
            prefix_ok = (
                len(live) >= size
                and sha256_bytes(live[:size]) == meta.get("sha256")
            )
            if prefix_ok:
                if current.get("sha256") == meta.get("sha256"):
                    unchanged.append(_report_path(rel))
                else:
                    append_only_extended.append({
                        "path": _report_path(rel),
                        "before_size": size,
                        "after_size": current.get("size"),
                    })
            else:
                unexpected.append({
                    "path": _report_path(rel),
                    "reason": "append_only_prefix_mismatch",
                    "class": cls,
                })
            continue
        if current.get("sha256") == meta.get("sha256"):
            unchanged.append(_report_path(rel))
        else:
            unexpected.append({
                "path": _report_path(rel),
                "reason": "content_changed",
                "class": cls,
                "before": meta.get("sha256"),
                "after": current.get("sha256"),
            })

    for rel, meta in after_files.items():
        if rel in before_files:
            continue
        cls = meta.get("class") or classify_phase7_file(rel)
        if cls == "pending":
            pending_transitions.append({"path": _report_path(rel), "change": "added"})
        elif cls in {"write_once", "append_only"}:
            collector_additions.append(_report_path(rel))
        elif cls == "frozen":
            unexpected.append({"path": _report_path(rel), "reason": "frozen_file_appeared", "class": cls})
        else:
            unexpected.append({"path": _report_path(rel), "reason": "unclassified_addition", "class": cls})

    return {
        "ok": not unexpected,
        "compared_at": after.get("captured_at"),
        "protocol_sha256": after.get("protocol_sha256"),
        "frozen_pool_sha256": after.get("frozen_pool_sha256"),
        "protocol_unchanged": protocol_unchanged,
        "frozen_pool_unchanged": pool_unchanged,
        "unchanged_n": len(unchanged),
        "collector_additions_n": len(collector_additions),
        "append_only_extended_n": len(append_only_extended),
        "pending_transitions_n": len(pending_transitions),
        "unexpected_n": len(unexpected),
        "unchanged": unchanged,
        "collector_additions": collector_additions,
        "append_only_extended": append_only_extended,
        "pending_transitions": pending_transitions,
        "unexpected": unexpected,
    }


def _et(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(NYC_TZ)


def capture_protected_bounds(now: datetime) -> list[tuple[datetime, datetime]]:
    """Today and tomorrow capture windows with the 5-minute pre-buffer."""
    local = _et(now)
    bounds: list[tuple[datetime, datetime]] = []
    for day_offset in (0, 1, -1):
        day = (local + timedelta(days=day_offset)).date()
        for hour in LIVE_CAPTURE_HOURS_ET:
            start = datetime(day.year, day.month, day.day, hour, 0, tzinfo=NYC_TZ) - LIVE_PRE_BUFFER
            end = start + LIVE_PRE_BUFFER + LIVE_WINDOW
            bounds.append((start, end))
    bounds.sort()
    return bounds


def in_live_capture_window(now: datetime) -> bool:
    local = _et(now)
    for start, end in capture_protected_bounds(now):
        if start <= local < end:
            return True
    return False


def next_live_capture_start(now: datetime) -> datetime | None:
    local = _et(now)
    for start, end in capture_protected_bounds(now):
        if local < start:
            return start
        if start <= local < end:
            return start
    return None


def seconds_until_live_capture(now: datetime) -> float:
    local = _et(now)
    if in_live_capture_window(now):
        return 0.0
    nxt = next_live_capture_start(now)
    if nxt is None:
        return float("inf")
    return max(0.0, (nxt - local).total_seconds())


def in_hourly_collector_quiet(now: datetime) -> bool:
    local = _et(now)
    collector = local.replace(minute=COLLECTOR_MINUTE_ET, second=0, microsecond=0)
    start = collector - COLLECTOR_PAUSE_BEFORE
    end = collector + COLLECTOR_PAUSE_AFTER
    return start <= local < end


def wait_out_hourly_collector(
    fetchers: ReplayFetchers,
    *,
    log: Callable[[str], None],
    max_wait: timedelta = COLLECTOR_WAIT_MAX,
) -> None:
    started = fetchers.clock()
    if not in_hourly_collector_quiet(started):
        return
    log("pausing for hourly prospective collector")
    deadline = started + max_wait
    while in_hourly_collector_quiet(fetchers.clock()):
        if fetchers.clock() >= deadline:
            break
        fetchers.sleep(5)


def should_stop_for_live_window(
    now: datetime,
    *,
    budget: timedelta = CHECKPOINT_TIME_BUDGET,
) -> bool:
    if in_live_capture_window(now):
        return True
    return seconds_until_live_capture(now) < budget.total_seconds()


def expected_gfs_candidate(target_date: str, cutoff: datetime) -> dict[str, Any] | None:
    for cand in method.gfs_candidates(target_date, cutoff):
        if cand["latency_eligible"]:
            return cand
    return None


def expected_hrrr_candidate(cutoff: datetime) -> dict[str, Any] | None:
    """Newest latency-eligible 3-hourly init (Open-Meteo serving pattern)."""
    fallback = None
    for cand in method.hrrr_candidates(cutoff):
        if not cand["latency_eligible"]:
            continue
        if fallback is None:
            fallback = cand
        if _parse_dt(cand["run_init"]).hour % 3 == 0:
            return cand
    return fallback


def _receipt_row(
    *,
    target_date: str,
    checkpoint_id: str,
    p7: phase7.Phase7Paths,
    receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    scheduled = checkpoint_scheduled_at(target_date, checkpoint_id)
    cutoff = scheduled.astimezone(timezone.utc)
    status = (receipt or {}).get("status")
    if status == phase7.RECEIPT_MISSED:
        stratum = STRATUM_MISSED
    elif status == phase7.RECEIPT_CAPTURED:
        stratum = STRATUM_AGREEMENT
    else:
        stratum = None
    gfs = expected_gfs_candidate(target_date, cutoff)
    hrrr = expected_hrrr_candidate(cutoff)
    return {
        "target_date": target_date,
        "checkpoint_id": checkpoint_id,
        "scheduled_checkpoint_at": scheduled.isoformat(),
        "evidence_cutoff_utc": _iso(cutoff),
        "receipt_status": status,
        "receipt_reason": (receipt or {}).get("reason"),
        "receipt_note": (receipt or {}).get("note"),
        "stratum": stratum,
        "priority_band": priority_band(checkpoint_id),
        "structurally_unavailable": structurally_unavailable(checkpoint_id),
        "phase7_record_present": phase7.record_path(p7, target_date, checkpoint_id).exists(),
        "phase7_score_present": phase7.score_path(p7, target_date, checkpoint_id).exists(),
        "expected_gfs_run_init": None if gfs is None else gfs["run_init"],
        "expected_hrrr_run_init": None if hrrr is None else hrrr["run_init"],
        "intraday": is_intraday_checkpoint(checkpoint_id),
    }


def build_inventory(
    *,
    phase7_paths: phase7.Phase7Paths | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read Phase 7 receipts/records only. Never writes under Phase 7."""
    p7 = phase7_paths or phase7.Phase7Paths()
    protocol = phase7.load_protocol(p7)
    if protocol is None:
        return {"status": "not_registered", "dataset_id": DATASET_ID, "rows": []}
    now = (now or utcnow()).astimezone(timezone.utc)
    start = date.fromisoformat(protocol["start_target_date"])
    rows: list[dict[str, Any]] = []
    for target_date in phase7.protocol_target_dates(start):
        for checkpoint_id in CHECKPOINT_IDS:
            scheduled = checkpoint_scheduled_at(target_date, checkpoint_id)
            if scheduled.astimezone(timezone.utc) > now:
                continue
            receipt = phase7.load_json(phase7.receipt_path(p7, target_date, checkpoint_id))
            if receipt is None and not phase7.window_elapsed(now=now, scheduled_at=scheduled):
                continue
            rows.append(
                _receipt_row(target_date=target_date, checkpoint_id=checkpoint_id, p7=p7, receipt=receipt)
            )
    missed = [r for r in rows if r["receipt_status"] == phase7.RECEIPT_MISSED]
    captured = [r for r in rows if r["receipt_status"] == phase7.RECEIPT_CAPTURED]
    by_cp: dict[str, dict[str, int]] = {}
    for checkpoint_id in CHECKPOINT_IDS:
        subset = [r for r in rows if r["checkpoint_id"] == checkpoint_id]
        by_cp[checkpoint_id] = {
            "elapsed": len(subset),
            "captured": sum(1 for r in subset if r["receipt_status"] == phase7.RECEIPT_CAPTURED),
            "missed": sum(1 for r in subset if r["receipt_status"] == phase7.RECEIPT_MISSED),
            "no_receipt": sum(1 for r in subset if r["receipt_status"] is None),
        }
    integrity = phase7.integrity_status(protocol, p7)
    return {
        "dataset_id": DATASET_ID,
        "status": "ok",
        "inventoried_at": _iso(now),
        "parent_protocol_id": protocol.get("protocol_id"),
        "parent_protocol_sha256": phase7.protocol_sha256(p7),
        "method_integrity_ok": integrity["method_integrity_ok"],
        "calibration_integrity_ok": integrity["calibration_integrity_ok"],
        "integrity_problems": integrity["problems"],
        "collection_window": f"{protocol['start_target_date']} .. {protocol['end_target_date']}",
        "counts": {
            "elapsed": len(rows),
            "captured": len(captured),
            "missed": len(missed),
            "missed_primary": sum(1 for r in missed if r["priority_band"] == "primary"),
            "missed_secondary": sum(1 for r in missed if r["priority_band"] == "secondary"),
            "missed_structurally_unavailable": sum(1 for r in missed if r["structurally_unavailable"]),
        },
        "by_checkpoint": by_cp,
        "rows": rows,
        "limitations": list(LIMITATIONS),
        "note": "Read-only inventory of Phase 7 receipts. Not a backfill.",
    }


def write_inventory(inventory: dict[str, Any], paths: ReplayPaths | None = None) -> Path:
    paths = paths or ReplayPaths()
    write_json(paths.inventory, inventory, phase7_root=paths.phase7_root)
    return paths.inventory


@dataclass
class ReplayFetchers:
    run_hourly: Callable[..., dict[str, Any]] = fetch_run_hourly_logged
    iem_month: Callable[..., tuple[str | None, dict[str, Any]]] = knyc_history.fetch_month
    kalshi_historical: Callable[[], list[dict[str, Any]]] | None = None
    kalshi_settled: Callable[[], list[dict[str, Any]]] | None = None
    kalshi_open: Callable[[], list[dict[str, Any]]] | None = None
    clock: Callable[[], datetime] = utcnow
    sleep: Callable[[float], None] = time.sleep


def _default_kalshi_historical() -> list[dict[str, Any]]:
    from kalshi.client import get_historical_markets_for_series

    return get_historical_markets_for_series(SERIES_TICKER)


def _default_kalshi_open() -> list[dict[str, Any]]:
    from kalshi.client import get_open_markets

    return get_open_markets(SERIES_TICKER)


def _default_kalshi_settled() -> list[dict[str, Any]]:
    """Recent settled KXHIGHNY markets (after /historical/cutoff)."""
    from kalshi.client import KalshiClient

    return KalshiClient().get_markets(
        status="settled",
        series_ticker=SERIES_TICKER,
        mve_filter=None,
    )


def events_from_markets(
    markets: Sequence[dict[str, Any]],
    *,
    as_of_class: str,
) -> dict[str, dict[str, Any]]:
    from research.weather.resolution import get_event_date, group_markets_by_event, is_range_bucket_event

    out: dict[str, dict[str, Any]] = {}
    for event_ticker, grouped in group_markets_by_event(list(markets)).items():
        target_date = get_event_date(event_ticker)
        if not target_date or not is_range_bucket_event(grouped):
            continue
        buckets = phase7.bucket_dicts_from_markets(grouped)
        if not buckets:
            continue
        out[target_date] = {
            "event_ticker": event_ticker,
            "target_date": target_date,
            "markets": grouped,
            "buckets": buckets,
            "kalshi_as_of_class": as_of_class,
        }
    return out


def load_kalshi_events(fetchers: ReplayFetchers) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    historical_fn = fetchers.kalshi_historical or _default_kalshi_historical
    settled_fn = fetchers.kalshi_settled or _default_kalshi_settled
    open_fn = fetchers.kalshi_open or _default_kalshi_open
    meta: dict[str, Any] = {
        "historical_error": None,
        "settled_error": None,
        "open_error": None,
        "historical_n": 0,
        "settled_n": 0,
        "open_n": 0,
        "note": (
            "/historical/markets lags the cutoff; recently settled KXHIGHNY events are on "
            "/markets?status=settled. Neither endpoint is contemporaneous open-book state."
        ),
    }
    events: dict[str, dict[str, Any]] = {}
    try:
        historical = list(historical_fn() or [])
        meta["historical_n"] = len(historical)
        events.update(events_from_markets(historical, as_of_class=AS_OF_POST_SETTLEMENT))
    except Exception as error:  # noqa: BLE001
        meta["historical_error"] = f"{type(error).__name__}: {error}"
    try:
        settled = list(settled_fn() or [])
        meta["settled_n"] = len(settled)
        for target_date, event in events_from_markets(settled, as_of_class=AS_OF_POST_SETTLEMENT).items():
            events.setdefault(target_date, event)
    except Exception as error:  # noqa: BLE001
        meta["settled_error"] = f"{type(error).__name__}: {error}"
    used_open = False
    try:
        opened = list(open_fn() or [])
        meta["open_n"] = len(opened)
        for target_date, event in events_from_markets(opened, as_of_class=AS_OF_CURRENT_OPEN).items():
            if target_date not in events:
                events[target_date] = event
                used_open = True
    except Exception as error:  # noqa: BLE001
        meta["open_error"] = f"{type(error).__name__}: {error}"
    meta["event_dates_n"] = len(events)
    meta["used_current_open_fallback"] = used_open
    meta["ok"] = bool(events) and meta["settled_error"] is None
    return events, meta


class IemArchive:
    """Monthly IEM ASOS archive, fetched once per month chunk."""

    def __init__(self, fetch_month: Callable[..., tuple[str | None, dict[str, Any]]]):
        self.fetch_month = fetch_month
        self._chunks: dict[str, tuple[str | None, dict[str, Any]]] = {}

    def month_for(self, target_date: str) -> tuple[str | None, dict[str, Any]]:
        d = date.fromisoformat(target_date)
        key = f"{d:%Y-%m}"
        if key not in self._chunks:
            start = date(d.year, d.month, 1)
            end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
            self._chunks[key] = self.fetch_month(start, end)
        return self._chunks[key]

    def cover(self, dates: Sequence[str]) -> dict[str, Any]:
        months: dict[str, dict[str, Any]] = {}
        for target_date in dates:
            text, meta = self.month_for(target_date)
            months[f"{date.fromisoformat(target_date):%Y-%m}"] = {
                "status": meta.get("status") or ("ok" if text else "error"),
                "row_count": meta.get("row_count"),
                "http_status": meta.get("http_status"),
                "error": meta.get("error"),
                "retrieved_at": meta.get("retrieved_at"),
                "sha256": meta.get("sha256"),
                "from_cache": meta.get("from_cache"),
                "note": meta.get("note"),
                "as_of_class": AS_OF_ARCHIVE_NOW,
                "has_text": text is not None,
            }
        ok = all(m.get("has_text") for m in months.values()) if months else False
        return {"ok": ok, "months": months, "as_of_class": AS_OF_ARCHIVE_NOW}


def _run_key(api_model: str, run_init: str) -> str:
    return f"{api_model}|{run_init}"


def _fetch_run(
    fetchers: ReplayFetchers,
    api_model: str,
    run_init: str,
    cache_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = _run_key(api_model, run_init)
    if key in cache_map:
        return cache_map[key]
    init = _parse_dt(run_init)
    last: dict[str, Any] = {}
    for attempt in range(RUN_FETCH_MAX_TRIES):
        result = fetchers.run_hourly(api_model, init)
        meta = dict(result.get("meta") or {})
        last = {
            "api_model": api_model,
            "run_init": run_init,
            "outcome": result.get("outcome"),
            "http_status": meta.get("http_status"),
            "error": meta.get("error") or meta.get("unusable_reason"),
            "unusable_reason": meta.get("unusable_reason"),
            "fetch_started_at": meta.get("fetch_started_at"),
            "fetch_completed_at": meta.get("fetch_completed_at") or meta.get("retrieved_at"),
            "response_sha256": meta.get("response_sha256"),
            "payload": result.get("payload"),
            "raw_text": result.get("raw_text"),
            "from_cache": False,
            "as_of_class": AS_OF_ARCHIVE_NOW,
            "try": attempt,
        }
        retryable = result.get("outcome") in {"network_error", "http_error"} and int(
            meta.get("http_status") or 0
        ) in (0, 429, 500, 502, 503, 504)
        if result.get("outcome") == FETCH_OUTCOME_OK or not retryable:
            break
        fetchers.sleep(RUN_FETCH_RETRY_SLEEP_S)
    cache_map[key] = last
    return last


def _window_for(payload: dict[str, Any] | None, *, target_date: str, cutoff: datetime) -> dict[str, Any]:
    series = parse_hourly_series(payload) if payload else None
    high = method.ny_date_high(series, target_date)
    window = remaining_day_high(series or [], target_date=target_date, checkpoint_as_of=cutoff)
    return {
        "target_date_high_f": high,
        "selection_eligible": high is not None,
        "window_status": window.get("status"),
        "window_reason": window.get("reason"),
        "covered_hours": window.get("covered_hours"),
        "expected_hours": window.get("expected_hours"),
    }


def run_probe(
    inventory: dict[str, Any],
    *,
    fetchers: ReplayFetchers | None = None,
    paths: ReplayPaths | None = None,
    include_captured: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Fetch expected GFS/HRRR runs, IEM months, and Kalshi markets. No Phase 7 writes."""
    fetchers = fetchers or ReplayFetchers()
    paths = paths or ReplayPaths()
    log = progress or (lambda _m: None)
    rows = [
        r for r in inventory.get("rows") or []
        if r.get("receipt_status") in {phase7.RECEIPT_MISSED, phase7.RECEIPT_CAPTURED}
        and (include_captured or r.get("receipt_status") == phase7.RECEIPT_MISSED)
    ]
    iem = IemArchive(fetchers.iem_month)
    dates = sorted({r["target_date"] for r in rows})
    log(f"IEM archive for {len(dates)} dates")
    iem_summary = iem.cover(dates) if dates else {"ok": False, "months": {}, "as_of_class": AS_OF_ARCHIVE_NOW}

    log("Kalshi historical markets")
    events, kalshi_meta = load_kalshi_events(fetchers)

    run_cache: dict[str, dict[str, Any]] = {}
    checkpoints: list[dict[str, Any]] = []
    for row in rows:
        cutoff = _parse_dt(row["evidence_cutoff_utc"])
        target_date = row["target_date"]
        per_model: dict[str, Any] = {}
        for model_name, expected in (
            (MODEL_GFS_OPERATIONAL_LATEST, row.get("expected_gfs_run_init")),
            (MODEL_HRRR_OPERATIONAL_LATEST, row.get("expected_hrrr_run_init")),
        ):
            if not expected:
                per_model[model_name] = {"outcome": "no_expected_run", "retrievable": False}
                continue
            fetched = _fetch_run(fetchers, phase7.API_MODEL[model_name], expected, run_cache)
            window = _window_for(fetched.get("payload"), target_date=target_date, cutoff=cutoff)
            per_model[model_name] = {
                "run_init": expected,
                "outcome": fetched.get("outcome"),
                "http_status": fetched.get("http_status"),
                "error": fetched.get("error"),
                "retrievable": fetched.get("outcome") == FETCH_OUTCOME_OK,
                "as_of_class": AS_OF_ARCHIVE_NOW,
                **window,
            }
        kalshi_event = events.get(target_date)
        checkpoints.append({
            "target_date": target_date,
            "checkpoint_id": row["checkpoint_id"],
            "stratum": row["stratum"],
            "priority_band": row["priority_band"],
            "structurally_unavailable": row["structurally_unavailable"],
            "gfs": per_model.get(MODEL_GFS_OPERATIONAL_LATEST),
            "hrrr": per_model.get(MODEL_HRRR_OPERATIONAL_LATEST),
            "iem_ok": bool((iem.month_for(target_date)[0] if row["intraday"] else True)),
            "kalshi_ok": kalshi_event is not None,
            "kalshi_as_of_class": None if kalshi_event is None else kalshi_event["kalshi_as_of_class"],
            "event_ticker": None if kalshi_event is None else kalshi_event["event_ticker"],
        })
        log(f"probed {target_date} {row['checkpoint_id']}")

    def _rate(subset: list[dict[str, Any]], key: str) -> float | None:
        if not subset:
            return None
        return sum(1 for r in subset if (r.get(key) or {}).get("retrievable")) / len(subset)

    missed = [c for c in checkpoints if c["stratum"] == STRATUM_MISSED]
    primary_missed = [c for c in missed if c["priority_band"] == "primary"]
    gfs_primary = _rate(primary_missed, "gfs")
    hrrr_primary = _rate(primary_missed, "hrrr")
    kalshi_ok = kalshi_meta.get("ok") and (
        not primary_missed
        or sum(1 for c in primary_missed if c["kalshi_ok"]) / len(primary_missed) >= PRIMARY_RETRIEVAL_THRESHOLD
    )
    archives_retrievable = bool(
        iem_summary.get("ok")
        and kalshi_ok
        and (gfs_primary is None or gfs_primary >= PRIMARY_RETRIEVAL_THRESHOLD)
        and (hrrr_primary is None or hrrr_primary >= PRIMARY_RETRIEVAL_THRESHOLD)
    )
    coverage = estimate_coverage(inventory, {"checkpoints": checkpoints, "iem": iem_summary, "kalshi": kalshi_meta})
    blockers = []
    if not iem_summary.get("ok"):
        blockers.append("iem_archive_unavailable")
    if not kalshi_ok:
        blockers.append("kalshi_markets_unavailable")
    if gfs_primary is not None and gfs_primary < PRIMARY_RETRIEVAL_THRESHOLD:
        blockers.append("gfs_primary_retrieval_below_threshold")
    if hrrr_primary is not None and hrrr_primary < PRIMARY_RETRIEVAL_THRESHOLD:
        blockers.append("hrrr_primary_retrieval_below_threshold")
    if inventory.get("integrity_problems"):
        blockers.append("phase7_method_or_pool_integrity")

    unique_runs = [
        {k: v for k, v in fetched.items() if k not in {"payload", "raw_text"}}
        for fetched in run_cache.values()
    ]
    probe = {
        "dataset_id": DATASET_ID,
        "status": "ok",
        "probed_at": _iso(fetchers.clock()),
        "archives_retrievable": archives_retrievable,
        "blockers": blockers,
        "include_captured": include_captured,
        "unique_runs_n": len(unique_runs),
        "unique_runs": unique_runs,
        "iem": iem_summary,
        "kalshi": kalshi_meta,
        "rates": {
            "gfs_primary_missed": gfs_primary,
            "hrrr_primary_missed": hrrr_primary,
            "gfs_all_missed": _rate(missed, "gfs"),
            "hrrr_all_missed": _rate(missed, "hrrr"),
        },
        "estimated_coverage": coverage,
        "checkpoints": checkpoints,
        "limitations": list(LIMITATIONS),
        "note": "Archive availability probe. Does not reconstruct probabilities and does not touch Phase 7.",
    }
    write_json(paths.probe, probe, phase7_root=paths.phase7_root)
    return probe


def estimate_coverage(inventory: dict[str, Any], probe_parts: dict[str, Any]) -> dict[str, Any]:
    missed = [r for r in inventory.get("rows") or [] if r.get("receipt_status") == phase7.RECEIPT_MISSED]
    by_key = {(c["target_date"], c["checkpoint_id"]): c for c in probe_parts.get("checkpoints") or []}
    recoverable = []
    unpaired = []
    unrecoverable = []
    for row in missed:
        hit = by_key.get((row["target_date"], row["checkpoint_id"]), {})
        gfs_ok = bool((hit.get("gfs") or {}).get("retrievable"))
        hrrr_ok = bool((hit.get("hrrr") or {}).get("retrievable"))
        iem_ok = bool(hit.get("iem_ok", False)) if row["intraday"] else True
        kalshi_ok = bool(hit.get("kalshi_ok"))
        window_ok = (hit.get("hrrr") or {}).get("window_status") == WINDOW_STATUS_OK
        if not (gfs_ok and iem_ok and kalshi_ok):
            reasons = []
            if not gfs_ok:
                reasons.append("gfs_unavailable")
            if not iem_ok:
                reasons.append("iem_unavailable")
            if not kalshi_ok:
                reasons.append("kalshi_buckets_unavailable")
            unrecoverable.append({**row, "reasons": reasons})
            continue
        if row["structurally_unavailable"] or not (hrrr_ok and window_ok):
            unpaired.append({
                **row,
                "reasons": (
                    ["d0_0900_structurally_unavailable"] if row["structurally_unavailable"]
                    else ["hrrr_window_unavailable"]
                ),
            })
            continue
        recoverable.append(row)
    primary_paired = [r for r in recoverable if r["priority_band"] == "primary"]
    secondary_paired = [r for r in recoverable if r["priority_band"] == "secondary"]
    return {
        "missed_n": len(missed),
        "estimated_reconstructable_paired": len(recoverable),
        "estimated_reconstructable_unpaired": len(unpaired),
        "estimated_unrecoverable": len(unrecoverable),
        "estimated_primary_paired": len(primary_paired),
        "estimated_secondary_paired": len(secondary_paired),
        "unrecoverable_reasons": _count_reasons(unrecoverable),
        "unpaired_reasons": _count_reasons(unpaired),
    }


def _count_reasons(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        for reason in row.get("reasons") or ["unknown"]:
            out[reason] = out.get(reason, 0) + 1
    return out


def _attempt_id(clock: Callable[[], datetime]) -> str:
    return clock().strftime("%Y%m%dT%H%M%S%fZ")


def collect_replay_evidence(
    *,
    target_date: str,
    checkpoint_id: str,
    cutoff: datetime,
    evidence_dir: Path,
    paths: ReplayPaths,
    fetchers: ReplayFetchers,
    iem_archive: IemArchive,
) -> dict[str, Any]:
    """Fetch candidate runs newest-first; observations from the monthly archive."""
    assert_isolated_write(evidence_dir / ".keep", phase7_root=paths.phase7_root)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {"observations_fetch": None, "runs": {}}
    raw_iem: str | None = None
    ctx = {"target_date": target_date, "checkpoint_id": checkpoint_id, "dataset_id": DATASET_ID}
    if is_intraday_checkpoint(checkpoint_id):
        text, meta = iem_archive.month_for(target_date)
        file_name = None
        if text is not None:
            file_name = "iem_knyc.csv"
            write_text(evidence_dir / file_name, text, phase7_root=paths.phase7_root)
            raw_iem = text
        append_fetch_log(paths, {
            **ctx, "kind": "iem_asos_archive", "url": knyc_history.IEM_ASOS_URL,
            "outcome": "ok" if text else "error", **{k: v for k, v in meta.items() if k != "params"},
        })
        evidence["observations_fetch"] = {
            "source": knyc_history.SOURCE_ID,
            "url": knyc_history.IEM_ASOS_URL,
            "as_of_class": AS_OF_ARCHIVE_NOW,
            "attempts": [meta],
            "evidence_file": file_name,
        }

    attempts_by_model: dict[str, dict[str, dict[str, Any]]] = {}
    run_cache: dict[str, dict[str, Any]] = {}
    for model_name in method.COMPONENT_MODELS:
        attempts: dict[str, dict[str, Any]] = {}
        log: list[dict[str, Any]] = []
        for cand in method.candidates_for(model_name, target_date, cutoff):
            if not cand["latency_eligible"]:
                continue
            got = _fetch_run(fetchers, phase7.API_MODEL[model_name], cand["run_init"], run_cache)
            evidence_file = None
            if got.get("raw_text") is not None:
                short = phase7.SHORT_MODEL[model_name]
                init = _parse_dt(cand["run_init"])
                evidence_file = f"{short}_{init:%Y%m%dT%HZ}_try{got.get('try', 0)}.json"
                write_text(evidence_dir / evidence_file, got["raw_text"], phase7_root=paths.phase7_root)
            high = None
            if got.get("outcome") == FETCH_OUTCOME_OK:
                high = method.ny_date_high(parse_hourly_series(got.get("payload")), target_date)
            entry = {
                "run_init": cand["run_init"],
                "outcome": got.get("outcome"),
                "http_status": got.get("http_status"),
                "error": got.get("error"),
                "evidence_file": evidence_file,
                "target_date_high_f": high,
                "as_of_class": AS_OF_ARCHIVE_NOW,
                "fetch_started_at": got.get("fetch_started_at"),
                "fetch_completed_at": got.get("fetch_completed_at"),
                "response_sha256": got.get("response_sha256"),
            }
            attempts[cand["run_init"]] = {"outcome": got.get("outcome"), "payload": got.get("payload")}
            log.append(entry)
            append_fetch_log(paths, {**ctx, "kind": "single_run_hourly", "model": model_name, **entry})
            if high is not None:
                break
        attempts_by_model[model_name] = attempts
        evidence["runs"][model_name] = log
    return {"evidence": evidence, "raw_iem": raw_iem, "attempts_by_model": attempts_by_model}


def reconstruct_checkpoint(
    row: dict[str, Any],
    *,
    protocol: dict[str, Any],
    phase7_paths: phase7.Phase7Paths,
    paths: ReplayPaths,
    fetchers: ReplayFetchers,
    iem_archive: IemArchive,
    events: dict[str, dict[str, Any]],
    pool_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    target_date = row["target_date"]
    checkpoint_id = row["checkpoint_id"]
    existing_path = paths.record_path(target_date, checkpoint_id)
    if existing_path.exists():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        if existing.get("schema") == RECORD_SCHEMA:
            return {
                "status": "RECONSTRUCTED",
                "path": str(existing_path),
                "record": existing,
                "resumed": True,
            }
    cutoff = _parse_dt(row["evidence_cutoff_utc"])
    reconstructed_at = fetchers.clock()
    attempt_id = _attempt_id(fetchers.clock)
    evidence_dir = paths.evidence / target_date / checkpoint_id / attempt_id
    event = events.get(target_date)
    if event is None or not event.get("buckets"):
        return {
            "status": "UNRECOVERABLE",
            "reason": "kalshi_buckets_unavailable",
            "target_date": target_date,
            "checkpoint_id": checkpoint_id,
        }
    collected = collect_replay_evidence(
        target_date=target_date,
        checkpoint_id=checkpoint_id,
        cutoff=cutoff,
        evidence_dir=evidence_dir,
        paths=paths,
        fetchers=fetchers,
        iem_archive=iem_archive,
    )
    prediction = method.build_prediction(
        target_date=target_date,
        checkpoint_id=checkpoint_id,
        event_ticker=str(event.get("event_ticker") or ""),
        cutoff=cutoff,
        bucket_dicts=event["buckets"],
        raw_iem_text=collected["raw_iem"],
        attempts_by_model=collected["attempts_by_model"],
        pool_rows=list(pool_rows),
    )
    day_start, day_end = climate_day_bounds_utc(target_date)
    record = {
        "schema": RECORD_SCHEMA,
        "dataset_id": DATASET_ID,
        "not_a_phase7_record": True,
        "cohort_eligible": False,
        "primary_cohort_eligible": False,
        "stratum": row["stratum"],
        "priority_band": row["priority_band"],
        "structurally_unavailable": row["structurally_unavailable"],
        "phase7_receipt_status": row["receipt_status"],
        "phase7_receipt_reason": row["receipt_reason"],
        "protocol_id": protocol.get("protocol_id"),
        "protocol_sha256": phase7.protocol_sha256(phase7_paths),
        "method_version": method.METHOD_VERSION,
        "method_fingerprint_sha256": method.method_fingerprint()["method_fingerprint_sha256"],
        "attempt_id": attempt_id,
        "event_ticker": event.get("event_ticker"),
        "target_date": target_date,
        "checkpoint_id": checkpoint_id,
        "scheduled_checkpoint_at": row["scheduled_checkpoint_at"],
        "evidence_cutoff_utc": row["evidence_cutoff_utc"],
        "reconstructed_at": _iso(reconstructed_at),
        "climate_day_bounds_utc": [_iso(day_start), _iso(day_end)],
        "buckets": event["buckets"],
        "kalshi_as_of_class": event.get("kalshi_as_of_class"),
        "evaluation_target_regime": REGIME_WEATHER_COMPANY_CLINYC,
        "evidence_dir": str(evidence_dir),
        "evidence": collected["evidence"],
        "prediction": prediction,
        "as_of_classes": {
            "gfs_hrrr": AS_OF_ARCHIVE_NOW,
            "gfs_hrrr_latency_gate": AS_OF_RULE_GATED,
            "observations": AS_OF_ARCHIVE_NOW,
            "obs_lag": AS_OF_RULE_GATED,
            "kalshi": event.get("kalshi_as_of_class"),
        },
        "status_flags": {
            "shadow": "SHADOW ONLY",
            "mode": DECISION_RESEARCH_ONLY,
            "decision": DECISION_NO_BET,
            "label": "HISTORICAL_REPLAY_DIAGNOSTIC",
        },
        "limitations": list(LIMITATIONS),
    }
    write_json(paths.record_path(target_date, checkpoint_id), record, phase7_root=paths.phase7_root)
    return {"status": "RECONSTRUCTED", "path": str(paths.record_path(target_date, checkpoint_id)), "record": record}


def _component_view(block: dict[str, Any] | None) -> dict[str, Any]:
    block = block or {}
    selected = block.get("selected_run") or {}
    window = block.get("window") or {}
    return {
        "status": block.get("status"),
        "reason": block.get("reason"),
        "run_init": selected.get("run_init"),
        "window_status": window.get("status"),
        "window_reason": window.get("reason"),
        "probabilities": block.get("probabilities"),
    }


def compare_agreement(live: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    """Compare a Phase 7 CAPTURED record to a separately reconstructed archive record."""
    out: dict[str, Any] = {
        "target_date": live.get("target_date"),
        "checkpoint_id": live.get("checkpoint_id"),
        "stratum": STRATUM_AGREEMENT,
        "mismatches": [],
    }
    for name in ("research_gfs", "research_hrrr", "research_shadow"):
        live_c = _component_view((live.get("prediction") or {}).get(name))
        replay_c = _component_view((replay.get("prediction") or {}).get(name))
        run_match = live_c.get("run_init") == replay_c.get("run_init")
        status_match = live_c.get("status") == replay_c.get("status")
        delta = _max_abs_prob_delta(live_c.get("probabilities"), replay_c.get("probabilities"))
        block = {
            "live_status": live_c.get("status"),
            "replay_status": replay_c.get("status"),
            "live_reason": live_c.get("reason"),
            "replay_reason": replay_c.get("reason"),
            "live_run_init": live_c.get("run_init"),
            "replay_run_init": replay_c.get("run_init"),
            "selected_run_match": run_match,
            "status_match": status_match,
            "live_window_reason": live_c.get("window_reason"),
            "replay_window_reason": replay_c.get("window_reason"),
            "probability_max_abs_delta": delta,
        }
        out[name] = block
        if not run_match:
            out["mismatches"].append(f"{name}:selected_run")
        if not status_match:
            out["mismatches"].append(f"{name}:status")
        if delta not in (None, 0):
            out["mismatches"].append(f"{name}:probabilities")
    live_obs = ((live.get("prediction") or {}).get("observations") or {}).get("summary") or {}
    replay_obs = ((replay.get("prediction") or {}).get("observations") or {}).get("summary") or {}
    out["observations"] = {
        "live_status": live_obs.get("status"),
        "replay_status": replay_obs.get("status"),
        "live_high_f": live_obs.get("observed_high_so_far_f"),
        "replay_high_f": replay_obs.get("observed_high_so_far_f"),
        "status_match": live_obs.get("status") == replay_obs.get("status"),
        "high_match": live_obs.get("observed_high_so_far_f") == replay_obs.get("observed_high_so_far_f"),
    }
    if not out["observations"]["status_match"] or not out["observations"]["high_match"]:
        out["mismatches"].append("observations")
    live_paired = bool((live.get("prediction") or {}).get("paired_valid"))
    replay_paired = bool((replay.get("prediction") or {}).get("paired_valid"))
    out["paired_valid"] = {"live": live_paired, "replay": replay_paired, "match": live_paired == replay_paired}
    if live_paired != replay_paired:
        out["mismatches"].append("paired_valid")
    out["match"] = not out["mismatches"]
    return out


def _max_abs_prob_delta(left: dict[str, Any] | None, right: dict[str, Any] | None) -> float | None:
    if not left or not right:
        return None if (not left and not right) else 1.0
    keys = set(left) | set(right)
    return max(abs(float(left.get(k) or 0) - float(right.get(k) or 0)) for k in keys)


def score_replay_record(
    record: dict[str, Any],
    *,
    event: dict[str, Any] | None,
    nws_actual_by_date: dict[str, float],
    paths: ReplayPaths,
) -> dict[str, Any] | None:
    from research.weather.service import resolve_settlement_outcome
    from research.weather.snapshots import SCORE_STATUS_OK

    if event is None:
        return None
    existing_path = paths.score_path(record["target_date"], record["checkpoint_id"])
    if existing_path.exists():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        if existing.get("schema") == SCORE_SCHEMA and existing.get("status") != "PENDING":
            return existing
    outcome = resolve_settlement_outcome(
        event.get("markets") or [],
        regime=str(record.get("evaluation_target_regime") or "unknown"),
        target_date=record["target_date"],
        nws_actual_by_date=nws_actual_by_date,
    )
    winner = outcome.get("winning_bucket")
    labels = {b["label"] for b in record.get("buckets") or []}
    if outcome.get("status") == SCORE_STATUS_OK and winner not in labels:
        outcome = {**outcome, "status": "BUCKET_UNIDENTIFIABLE", "reason": "winner not in recorded buckets"}
    if outcome.get("status") != SCORE_STATUS_OK:
        return {
            "schema": SCORE_SCHEMA,
            "dataset_id": DATASET_ID,
            "status": "PENDING",
            "settlement_status": outcome.get("status"),
            "reason": outcome.get("reason"),
            "primary_cohort_eligible": False,
            "label": "HISTORICAL_REPLAY_DIAGNOSTIC",
        }
    pred = record["prediction"]
    streams = {}
    for name in ("research_gfs", "research_hrrr", "research_shadow"):
        block = pred[name]
        streams[name] = {
            "status": block.get("status"),
            "reason": block.get("reason"),
            "score": method.score_distribution(block.get("probabilities"), winner)
            if block.get("status") == method.COMPONENT_OK else None,
        }
    payload = {
        "schema": SCORE_SCHEMA,
        "dataset_id": DATASET_ID,
        "not_a_phase7_score": True,
        "primary_cohort_eligible": False,
        "label": "HISTORICAL_REPLAY_DIAGNOSTIC",
        "target_date": record["target_date"],
        "checkpoint_id": record["checkpoint_id"],
        "event_ticker": record.get("event_ticker"),
        "winning_bucket": winner,
        "actual_high_f": outcome.get("actual_high_f"),
        "settlement_source": outcome.get("settlement_source"),
        "paired_valid": pred.get("paired_valid"),
        "streams": streams,
        "limitations": list(LIMITATIONS),
    }
    write_json(
        paths.score_path(record["target_date"], record["checkpoint_id"]),
        payload,
        phase7_root=paths.phase7_root,
    )
    return payload


def _band_rank(row: dict[str, Any]) -> tuple[int, int, str, str]:
    missed = 0 if row.get("receipt_status") == phase7.RECEIPT_MISSED else 1
    band = {"primary": 0, "secondary": 1, "structurally_unavailable": 2}.get(row.get("priority_band"), 3)
    return (missed, band, row.get("target_date") or "", row.get("checkpoint_id") or "")


def _reconstructed_row(row: dict[str, Any], record: dict[str, Any], *, resumed: bool = False) -> dict[str, Any]:
    pred = record.get("prediction") or {}
    return {
        "target_date": row["target_date"],
        "checkpoint_id": row["checkpoint_id"],
        "stratum": row["stratum"],
        "priority_band": row["priority_band"],
        "paired_valid": pred.get("paired_valid"),
        "gfs": (pred.get("research_gfs") or {}).get("status"),
        "hrrr": (pred.get("research_hrrr") or {}).get("status"),
        "shadow": (pred.get("research_shadow") or {}).get("status"),
        "hrrr_reason": (pred.get("research_hrrr") or {}).get("reason"),
        "structurally_unavailable": row["structurally_unavailable"],
        "resumed": resumed,
        "label": "HISTORICAL_REPLAY_DIAGNOSTIC",
    }


def _stream_score(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    return ((payload.get("streams") or {}).get(name) or {}).get("score")


def diagnostic_accuracy(scores: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Paired diagnostic Brier / log loss. Not combined with the Phase 7 primary cohort."""
    confirmed = [s for s in scores if s.get("status") != "PENDING" and s.get("winning_bucket")]

    def _evaluated(name: str) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for payload in confirmed:
            sc = _stream_score(payload, name)
            if sc and sc.get("brier") is not None:
                rows.append(sc)
        n = len(rows)
        return {
            "n": n,
            "multiclass_brier": (sum(float(r["brier"]) for r in rows) / n) if n else None,
            "log_loss": (sum(float(r["log_loss"]) for r in rows) / n) if n else None,
        }

    paired: list[dict[str, Any]] = []
    for payload in confirmed:
        gfs = _stream_score(payload, "research_gfs")
        hrrr = _stream_score(payload, "research_hrrr")
        shadow = _stream_score(payload, "research_shadow")
        if not (
            gfs and hrrr and shadow
            and gfs.get("brier") is not None
            and hrrr.get("brier") is not None
            and shadow.get("brier") is not None
        ):
            continue
        paired.append({
            "target_date": payload.get("target_date"),
            "checkpoint_id": payload.get("checkpoint_id"),
            "gfs_brier": float(gfs["brier"]),
            "hrrr_brier": float(hrrr["brier"]),
            "shadow_brier": float(shadow["brier"]),
            "gfs_log_loss": float(gfs["log_loss"]),
            "hrrr_log_loss": float(hrrr["log_loss"]),
            "shadow_log_loss": float(shadow["log_loss"]),
        })
    n = len(paired)

    def _mean(key: str) -> float | None:
        return (sum(p[key] for p in paired) / n) if n else None

    def _mean_diff(left: str, right: str) -> float | None:
        return (sum(p[left] - p[right] for p in paired) / n) if n else None

    return {
        "label": "HISTORICAL_REPLAY_DIAGNOSTIC",
        "not_combined_with_phase7_primary_cohort": True,
        "confirmed_settlements_n": len(confirmed),
        "gfs": _evaluated("research_gfs"),
        "hrrr": _evaluated("research_hrrr"),
        "shadow": _evaluated("research_shadow"),
        "paired_all_three_n": n,
        "paired_gfs_brier": _mean("gfs_brier"),
        "paired_hrrr_brier": _mean("hrrr_brier"),
        "paired_shadow_brier": _mean("shadow_brier"),
        "paired_gfs_log_loss": _mean("gfs_log_loss"),
        "paired_hrrr_log_loss": _mean("hrrr_log_loss"),
        "paired_shadow_log_loss": _mean("shadow_log_loss"),
        "paired_shadow_minus_gfs_brier": _mean_diff("shadow_brier", "gfs_brier"),
        "paired_shadow_minus_hrrr_brier": _mean_diff("shadow_brier", "hrrr_brier"),
    }


def run_reconstruction(
    inventory: dict[str, Any],
    *,
    phase7_paths: phase7.Phase7Paths | None = None,
    paths: ReplayPaths | None = None,
    fetchers: ReplayFetchers | None = None,
    require_probe: bool = True,
    include_captured: bool = True,
    nws_actual_by_date: dict[str, float] | None = None,
    progress: Callable[[str], None] | None = None,
    respect_live_windows: bool = True,
    time_budget: timedelta = CHECKPOINT_TIME_BUDGET,
) -> dict[str, Any]:
    """Reconstruct missed (and optionally captured) checkpoints into the replay tree only."""
    fetchers = fetchers or ReplayFetchers()
    paths = paths or ReplayPaths()
    p7 = phase7_paths or phase7.Phase7Paths()
    log = progress or (lambda _m: None)
    if require_probe:
        if not paths.probe.exists():
            raise ReplayBlockedError("archive probe has not been run")
        probe = json.loads(paths.probe.read_text(encoding="utf-8"))
        if not probe.get("archives_retrievable"):
            raise ReplayBlockedError(
                "archive probe did not confirm retrieval: " + ",".join(probe.get("blockers") or [])
            )
    protocol = phase7.load_protocol(p7)
    if protocol is None:
        raise ReplayError("Phase 7 protocol is not registered")
    integrity = phase7.integrity_status(protocol, p7)
    if integrity["problems"]:
        raise ReplayBlockedError("method or pool integrity: " + ",".join(integrity["problems"]))
    pool_rows = phase7.load_frozen_pool(p7, protocol)
    events, _kalshi_meta = load_kalshi_events(fetchers)
    iem = IemArchive(fetchers.iem_month)
    rows = [
        r for r in inventory.get("rows") or []
        if r.get("receipt_status") == phase7.RECEIPT_MISSED
        or (include_captured and r.get("receipt_status") == phase7.RECEIPT_CAPTURED)
    ]
    rows.sort(key=_band_rank)
    reconstructed: list[dict[str, Any]] = []
    unrecoverable: list[dict[str, Any]] = []
    agreements: list[dict[str, Any]] = []
    scores: list[dict[str, Any]] = []
    nws = nws_actual_by_date if nws_actual_by_date is not None else {}
    stopped_reason: str | None = None
    remaining = list(rows)

    def _persist(summary: dict[str, Any]) -> dict[str, Any]:
        coverage_rows = [
            _coverage_row(r, reconstructed, unrecoverable)
            for r in (inventory.get("rows") or [])
            if r.get("receipt_status") == phase7.RECEIPT_MISSED
        ]
        write_csv(
            paths.coverage_csv,
            ["target_date", "checkpoint_id", "priority_band", "status", "paired_valid",
             "gfs", "hrrr", "shadow", "reason"],
            coverage_rows,
            phase7_root=paths.phase7_root,
        )
        write_csv(
            paths.agreement_csv,
            ["target_date", "checkpoint_id", "match", "mismatches",
             "gfs_run_match", "hrrr_run_match", "gfs_prob_delta", "hrrr_prob_delta", "shadow_prob_delta"],
            [
                {
                    "target_date": a["target_date"],
                    "checkpoint_id": a["checkpoint_id"],
                    "match": a["match"],
                    "mismatches": ";".join(a["mismatches"]),
                    "gfs_run_match": a["research_gfs"]["selected_run_match"],
                    "hrrr_run_match": a["research_hrrr"]["selected_run_match"],
                    "gfs_prob_delta": a["research_gfs"]["probability_max_abs_delta"],
                    "hrrr_prob_delta": a["research_hrrr"]["probability_max_abs_delta"],
                    "shadow_prob_delta": a["research_shadow"]["probability_max_abs_delta"],
                }
                for a in agreements
            ],
            phase7_root=paths.phase7_root,
        )
        write_json(paths.reconstruction, summary, phase7_root=paths.phase7_root)
        return summary

    for index, row in enumerate(rows):
        if respect_live_windows:
            wait_out_hourly_collector(fetchers, log=log)
            now = fetchers.clock()
            if should_stop_for_live_window(now, budget=time_budget):
                stopped_reason = "live_capture_window"
                remaining = rows[index:]
                log(
                    f"stopping before {row['target_date']} {row['checkpoint_id']}: "
                    f"live capture window (remaining={len(remaining)})"
                )
                break
        log(f"reconstruct {row['target_date']} {row['checkpoint_id']}")
        result = reconstruct_checkpoint(
            row,
            protocol=protocol,
            phase7_paths=p7,
            paths=paths,
            fetchers=fetchers,
            iem_archive=iem,
            events=events,
            pool_rows=pool_rows,
        )
        if result.get("status") != "RECONSTRUCTED":
            unrecoverable.append(result)
            continue
        record = result["record"]
        reconstructed.append(_reconstructed_row(row, record, resumed=bool(result.get("resumed"))))
        scored = score_replay_record(
            record, event=events.get(row["target_date"]), nws_actual_by_date=nws, paths=paths,
        )
        if scored is not None:
            scores.append(scored)
        if row["stratum"] == STRATUM_AGREEMENT and row.get("phase7_record_present"):
            live = phase7.load_json(phase7.record_path(p7, row["target_date"], row["checkpoint_id"]))
            if live:
                agreements.append(compare_agreement(live, record))
    else:
        remaining = []

    accuracy = diagnostic_accuracy(scores)
    summary = {
        "dataset_id": DATASET_ID,
        "label": "HISTORICAL_REPLAY_DIAGNOSTIC",
        "status": "stopped_for_live_window" if stopped_reason else "ok",
        "stopped_reason": stopped_reason,
        "remaining_n": len(remaining),
        "remaining": [
            {"target_date": r["target_date"], "checkpoint_id": r["checkpoint_id"]}
            for r in remaining
        ],
        "reconstructed_n": len(reconstructed),
        "resumed_n": sum(1 for r in reconstructed if r.get("resumed")),
        "unrecoverable_n": len(unrecoverable),
        "agreement_n": len(agreements),
        "agreement_mismatches_n": sum(1 for a in agreements if not a["match"]),
        "scores_n": len(scores),
        "accuracy": accuracy,
        "reconstructed": reconstructed,
        "unrecoverable": unrecoverable,
        "agreements": agreements,
        "scores": [
            {
                "target_date": s.get("target_date"),
                "checkpoint_id": s.get("checkpoint_id"),
                "status": s.get("status"),
                "paired_valid": s.get("paired_valid"),
                "label": s.get("label") or "HISTORICAL_REPLAY_DIAGNOSTIC",
                "gfs_brier": (_stream_score(s, "research_gfs") or {}).get("brier"),
                "hrrr_brier": (_stream_score(s, "research_hrrr") or {}).get("brier"),
                "shadow_brier": (_stream_score(s, "research_shadow") or {}).get("brier"),
                "gfs_log_loss": (_stream_score(s, "research_gfs") or {}).get("log_loss"),
                "hrrr_log_loss": (_stream_score(s, "research_hrrr") or {}).get("log_loss"),
                "shadow_log_loss": (_stream_score(s, "research_shadow") or {}).get("log_loss"),
            }
            for s in scores if s.get("status") != "PENDING"
        ],
    }
    return _persist(summary)


def _coverage_row(
    row: dict[str, Any],
    reconstructed: Sequence[dict[str, Any]],
    unrecoverable: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    key = (row["target_date"], row["checkpoint_id"])
    rec = next((r for r in reconstructed if (r["target_date"], r["checkpoint_id"]) == key), None)
    fail = next((r for r in unrecoverable if (r.get("target_date"), r.get("checkpoint_id")) == key), None)
    if rec:
        return {
            "target_date": row["target_date"],
            "checkpoint_id": row["checkpoint_id"],
            "priority_band": row["priority_band"],
            "status": "RECONSTRUCTED",
            "paired_valid": rec["paired_valid"],
            "gfs": rec["gfs"],
            "hrrr": rec["hrrr"],
            "shadow": rec["shadow"],
            "reason": rec.get("hrrr_reason") if row["structurally_unavailable"] else "",
        }
    return {
        "target_date": row["target_date"],
        "checkpoint_id": row["checkpoint_id"],
        "priority_band": row["priority_band"],
        "status": "UNRECOVERABLE",
        "paired_valid": False,
        "gfs": "",
        "hrrr": "",
        "shadow": "",
        "reason": (fail or {}).get("reason") or "not_reconstructed",
    }


def build_report(
    inventory: dict[str, Any],
    probe: dict[str, Any] | None = None,
    reconstruction: dict[str, Any] | None = None,
    integrity: dict[str, Any] | None = None,
) -> str:
    counts = inventory.get("counts") or {}
    lines = [
        "# Phase 7 historical recovery report",
        "",
        "SHADOW ONLY / RESEARCH_ONLY / NO_BET. This dataset is **not** the Phase 7 prospective cohort.",
        "",
        f"Inventoried at: {inventory.get('inventoried_at')}",
        f"Collection window: {inventory.get('collection_window')}",
        "",
        "## Missed checkpoints",
        "",
        f"- Elapsed receipts: {counts.get('elapsed')}",
        f"- CAPTURED: {counts.get('captured')}",
        f"- **MISSED: {counts.get('missed')}**",
        f"- Missed primary (`d0_0600`/`d0_1200`/`d0_1500`): {counts.get('missed_primary')}",
        f"- Missed secondary (`dminus1_1800`): {counts.get('missed_secondary')}",
        f"- Missed structurally unavailable (`d0_0900`): {counts.get('missed_structurally_unavailable')}",
        "",
        "By checkpoint:",
        "",
        "| Checkpoint | Elapsed | Captured | Missed |",
        "|---|---:|---:|---:|",
    ]
    for checkpoint_id in CHECKPOINT_IDS:
        c = (inventory.get("by_checkpoint") or {}).get(checkpoint_id) or {}
        lines.append(
            f"| `{checkpoint_id}` | {c.get('elapsed', 0)} | {c.get('captured', 0)} | {c.get('missed', 0)} |"
        )
    if probe:
        cov = probe.get("estimated_coverage") or {}
        lines += [
            "",
            "## Archive probe",
            "",
            f"- Archives retrievable: **{probe.get('archives_retrievable')}**",
            f"- Blockers: {', '.join(probe.get('blockers') or []) or 'none'}",
            f"- IEM ok: {(probe.get('iem') or {}).get('ok')}",
            f"- Kalshi ok: {(probe.get('kalshi') or {}).get('ok')} "
            f"(historical_n={(probe.get('kalshi') or {}).get('historical_n')}, "
            f"event_dates={(probe.get('kalshi') or {}).get('event_dates_n')})",
            f"- GFS retrieval rate, primary missed: {(probe.get('rates') or {}).get('gfs_primary_missed')}",
            f"- HRRR retrieval rate, primary missed: {(probe.get('rates') or {}).get('hrrr_primary_missed')}",
            "",
            "## Estimated recovery coverage (from probe, before reconstruction)",
            "",
            f"- Missed: {cov.get('missed_n')}",
            f"- Estimated reconstructable paired: {cov.get('estimated_reconstructable_paired')}",
            f"- Estimated reconstructable unpaired (includes `d0_0900`): {cov.get('estimated_reconstructable_unpaired')}",
            f"- Estimated unrecoverable: {cov.get('estimated_unrecoverable')}",
            f"- Estimated primary paired: {cov.get('estimated_primary_paired')}",
            f"- Estimated secondary paired: {cov.get('estimated_secondary_paired')}",
            f"- Unrecoverable reasons: {cov.get('unrecoverable_reasons') or '{}'}",
            f"- Unpaired reasons: {cov.get('unpaired_reasons') or '{}'}",
        ]
    if reconstruction:
        rec = reconstruction.get("reconstructed") or []
        paired = [r for r in rec if r.get("paired_valid")]
        unpaired = [r for r in rec if not r.get("paired_valid")]
        missed_rec = [r for r in rec if r.get("stratum") == STRATUM_MISSED]
        lines += [
            "",
            "## Reconstruction",
            "",
            f"- Successfully reconstructed: {reconstruction.get('reconstructed_n')}",
            f"- Of those, missed-stratum: {len(missed_rec)}",
            f"- Unrecoverable: {reconstruction.get('unrecoverable_n')}",
            f"- Paired GFS/HRRR/shadow: {len(paired)}",
            f"- Unpaired (including `d0_0900`): {len(unpaired)}",
        ]
        unrec = reconstruction.get("unrecoverable") or []
        if unrec:
            lines += ["", "Unrecoverable checkpoints:", ""]
            for item in unrec:
                lines.append(
                    f"- {item.get('target_date')} `{item.get('checkpoint_id')}`: {item.get('reason')}"
                )
        if reconstruction.get("stopped_reason"):
            lines += [
                "",
                f"- **Stopped early:** `{reconstruction.get('stopped_reason')}` "
                f"({reconstruction.get('remaining_n')} checkpoints remaining; resume is safe)",
            ]
        acc = reconstruction.get("accuracy") or {}
        if acc:
            def _fmt(value: Any) -> str:
                return f"{value:.4f}" if isinstance(value, (int, float)) else "n/a"

            gfs = acc.get("gfs") or {}
            hrrr = acc.get("hrrr") or {}
            shadow = acc.get("shadow") or {}
            lines += [
                "",
                "## Historical prediction accuracy (HISTORICAL_REPLAY_DIAGNOSTIC)",
                "",
                "Not combined with the Phase 7 prospective primary cohort. "
                "Direct model comparisons use the same checkpoints (all three models valid).",
                "",
                f"- Confirmed settlements scored: {acc.get('confirmed_settlements_n')}",
                f"- GFS: n={gfs.get('n')}  Brier={_fmt(gfs.get('multiclass_brier'))}  "
                f"log loss={_fmt(gfs.get('log_loss'))}",
                f"- HRRR: n={hrrr.get('n')}  Brier={_fmt(hrrr.get('multiclass_brier'))}  "
                f"log loss={_fmt(hrrr.get('log_loss'))}",
                f"- Shadow: n={shadow.get('n')}  Brier={_fmt(shadow.get('multiclass_brier'))}  "
                f"log loss={_fmt(shadow.get('log_loss'))}",
                f"- Checkpoints where all three models are valid: {acc.get('paired_all_three_n')}",
                f"- Paired GFS Brier: {_fmt(acc.get('paired_gfs_brier'))}  "
                f"log loss={_fmt(acc.get('paired_gfs_log_loss'))}",
                f"- Paired HRRR Brier: {_fmt(acc.get('paired_hrrr_brier'))}  "
                f"log loss={_fmt(acc.get('paired_hrrr_log_loss'))}",
                f"- Paired shadow Brier: {_fmt(acc.get('paired_shadow_brier'))}  "
                f"log loss={_fmt(acc.get('paired_shadow_log_loss'))}",
                f"- Paired shadow−GFS Brier: {_fmt(acc.get('paired_shadow_minus_gfs_brier'))}",
                f"- Paired shadow−HRRR Brier: {_fmt(acc.get('paired_shadow_minus_hrrr_brier'))}",
            ]
        agreements = reconstruction.get("agreements") or []
        if agreements:
            mismatch = [a for a in agreements if not a.get("match")]
            lines += [
                "",
                "## Agreement with previously CAPTURED checkpoints",
                "",
                f"- Compared: {len(agreements)}",
                f"- Exact match: {len(agreements) - len(mismatch)}",
                f"- **Mismatches: {len(mismatch)}**",
            ]
            if mismatch:
                lines += ["", "Mismatched captured checkpoints:", ""]
                for item in mismatch:
                    lines.append(
                        f"- {item.get('target_date')} `{item.get('checkpoint_id')}`: "
                        + ", ".join(item.get("mismatches") or [])
                    )
    else:
        lines += [
            "",
            "## Reconstruction",
            "",
            "Full reconstruction has not been run.",
        ]
    if integrity:
        unexpected = integrity.get("unexpected") or []
        additions = integrity.get("collector_additions") or []
        pending = integrity.get("pending_transitions") or []
        extended = integrity.get("append_only_extended") or []
        lines += [
            "",
            "## Phase 7 integrity (collector-aware)",
            "",
            f"- Comparison ok: **{integrity.get('ok')}**",
            f"- Protocol unchanged: {integrity.get('protocol_unchanged')} "
            f"(`{integrity.get('protocol_sha256')}`)",
            f"- Frozen pool unchanged: {integrity.get('frozen_pool_unchanged')} "
            f"(`{integrity.get('frozen_pool_sha256')}`)",
            f"- Original files verified unchanged: {integrity.get('unchanged_n')}",
            f"- Legitimate collector additions: {integrity.get('collector_additions_n')}",
            f"- Append-only files extended (original prefix retained): "
            f"{integrity.get('append_only_extended_n')}",
            f"- Pending-score transitions: {integrity.get('pending_transitions_n')}",
            f"- **Unexpected modifications: {integrity.get('unexpected_n')}**",
        ]
        if additions:
            lines += ["", "Collector additions (sample):", ""]
            for rel in additions[:20]:
                lines.append(f"- `{rel}`")
            if len(additions) > 20:
                lines.append(f"- … {len(additions) - 20} more")
        if pending:
            lines += ["", "Pending-score transitions:", ""]
            for item in pending[:20]:
                lines.append(f"- `{item.get('path')}` ({item.get('change')})")
        if extended:
            lines += ["", "Append-only extensions:", ""]
            for item in extended:
                lines.append(
                    f"- `{item.get('path')}` {item.get('before_size')} → {item.get('after_size')} bytes"
                )
        if unexpected:
            lines += ["", "Unexpected modifications:", ""]
            for item in unexpected:
                lines.append(
                    f"- `{item.get('path')}`: {item.get('reason')} (class={item.get('class')})"
                )
    lines += ["", "## Limitations", ""]
    for item in LIMITATIONS:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def write_report(
    inventory: dict[str, Any],
    *,
    probe: dict[str, Any] | None = None,
    reconstruction: dict[str, Any] | None = None,
    integrity: dict[str, Any] | None = None,
    paths: ReplayPaths | None = None,
) -> Path:
    paths = paths or ReplayPaths()
    text = build_report(inventory, probe, reconstruction, integrity)
    write_text(paths.report, text, phase7_root=paths.phase7_root)
    write_json(
        paths.methodology,
        {
            "dataset_id": DATASET_ID,
            "generated_at": _iso(utcnow()),
            "parent_protocol_id": inventory.get("parent_protocol_id"),
            "limitations": list(LIMITATIONS),
            "priority": {
                "primary": list(PRIORITY_PRIMARY),
                "secondary": list(PRIORITY_SECONDARY),
                "structurally_unavailable": list(PRIORITY_STRUCTURAL),
            },
            "as_of_classes": {
                AS_OF_RULE_GATED: "Frozen latency / observation-lag gates.",
                AS_OF_ARCHIVE_NOW: "Archive payload retrieved at reconstruction time.",
                AS_OF_POST_SETTLEMENT: "Kalshi historical settled markets.",
                AS_OF_CURRENT_OPEN: "Current open markets; not as-of HH:00.",
            },
        },
        phase7_root=paths.phase7_root,
    )
    return paths.report


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_pre_run_manifest(
    paths: ReplayPaths,
    *,
    phase7_paths: phase7.Phase7Paths | None = None,
) -> dict[str, Any]:
    existing = load_json(paths.pre_run_manifest)
    if existing and existing.get("files"):
        return existing
    manifest = build_phase7_manifest(phase7_paths=phase7_paths)
    write_json(paths.pre_run_manifest, manifest, phase7_root=paths.phase7_root)
    return manifest


def wait_until_live_capture_closed(
    fetchers: ReplayFetchers,
    *,
    log: Callable[[str], None],
) -> None:
    while in_live_capture_window(fetchers.clock()):
        log("waiting for live capture window to close before reconstruction")
        fetchers.sleep(30)

"""Path classification for research storage (immutable vs disposable).

Classification is policy only: it never deletes files. Immutable evidence includes
every sha256-pinned capture/settlement body and every write-once record/score.
Reconstructable HTTP caches may be expired by the cleanup command after dry-run
confirmation. Frozen protocols and prediction journals are never cleanup targets.
"""

from __future__ import annotations

from pathlib import Path

from research.sports.core import REPO_ROOT

CLASS_IMMUTABLE = "immutable_evidence"
CLASS_APPEND_ONLY = "append_only_logs"
CLASS_HTTP_CACHE = "reconstructable_http_cache"
CLASS_TEMP = "temporary"
CLASS_REPORT = "generated_report"
CLASS_UNKNOWN = "unknown"

# Prefixes relative to the repo root, POSIX, no trailing slash required on match.
IMMUTABLE_PREFIXES = (
    "data/cache/sports/raw_capture_v2/",
    "data/cache/sports/raw_settlement_v1/",
    "data/cache/sports/raw_challenger_itf_v1/",
    "data/cache/sports/evidence_blobs_v1/",
    "data/sports/phase2/capture_v2/",
    "data/sports/phase2/capture_v2_scores/",
    "data/sports/normalized/",
    "data/sports/crosswalk/",
    "data/sports/predictions/",
    "data/weather/phase7/records/",
    "data/weather/phase7/receipts/",
    "data/weather/phase7/evidence/",
    "data/weather/phase7/scores/",
    "data/weather/phase7/pending_scores/",
    "data/weather/phase7/diagnostics/",
    "data/weather/phase7_historical_replay/",
    "data/weather/checkpoints/",
    "data/weather/snapshots/",
    "data/weather/snapshot_scores/",
    "data/weather/calibration/",
    "research/sports/protocols/",
    "research/weather/protocols/",
)

APPEND_ONLY_PREFIXES = (
    "data/logs/sports/",
    "data/logs/prospective/",
)

APPEND_ONLY_FILES = {
    "data/weather/phase7/fetch_log.jsonl",
    "data/weather/phase7/outcomes_ledger.jsonl",
}

# Disposable / regenerable HTTP response caches (never pinned as live evidence).
HTTP_CACHE_PREFIXES = (
    "data/cache/sports/raw/",
    "data/cache/sports/raw_capture_v2_dev/",
    "data/cache/sports/kalshi_discovery/",
    "data/cache/weather/",
    "data/cache/inventory/",
    "data/cache/events/",
    "data/cache/series/",
    "data/cache/candles/",
    "data/cache/fees/",
    "data/cache/logs/",
)

REPORT_PREFIXES = (
    "data/results/",
    "data/weather/phase7/dry_run/",
)

TEMP_NAME_MARKERS = (".kelchunks", ".backup_staging")
TEMP_DIR_NAMES = {".backup_staging", "__pycache__", ".pytest_cache"}
# Host temp used by pytest; only applied when the classification root is not itself under it
# (otherwise every test path would look temporary).
EXTERNAL_TEMP_DIR_NAMES = {"pytest-of-kyleb"}
TEMP_SUFFIXES = (".tmp", ".pyc", ".swp", ".lock.tmp")

CLASS_LABELS = {
    CLASS_IMMUTABLE: "Immutable research evidence (write-once / sha256-pinned)",
    CLASS_APPEND_ONLY: "Append-only logs and receipts",
    CLASS_HTTP_CACHE: "Reconstructable HTTP caches",
    CLASS_TEMP: "Temporary processing files",
    CLASS_REPORT: "Generated reports and regenerable outputs",
    CLASS_UNKNOWN: "Unclassified (protected by default)",
}


def rel_posix(path: Path, root: Path = REPO_ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _under(rel: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel == p.rstrip("/") or rel.startswith(p) for p in prefixes)


def classify(path: Path | str, *, root: Path = REPO_ROOT) -> str:
    """Return the storage class for ``path`` (absolute or repo-relative)."""
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    name = p.name
    rel = rel_posix(p, root)
    root_resolved = root.resolve()
    root_under_external_temp = any(part in EXTERNAL_TEMP_DIR_NAMES for part in root_resolved.parts)

    if name in TEMP_DIR_NAMES or name.endswith(TEMP_SUFFIXES):
        return CLASS_TEMP
    if any(m in rel for m in TEMP_NAME_MARKERS):
        return CLASS_TEMP
    if any(part in TEMP_DIR_NAMES for part in p.parts):
        return CLASS_TEMP
    if not root_under_external_temp and any(part in EXTERNAL_TEMP_DIR_NAMES for part in p.parts):
        return CLASS_TEMP
    if rel in APPEND_ONLY_FILES or _under(rel, APPEND_ONLY_PREFIXES):
        # Locks and local config under log dirs are operational, not evidence, but still not
        # HTTP-cache cleanup targets. Keep them append/ops protected.
        return CLASS_APPEND_ONLY
    if _under(rel, IMMUTABLE_PREFIXES):
        return CLASS_IMMUTABLE
    if _under(rel, HTTP_CACHE_PREFIXES):
        return CLASS_HTTP_CACHE
    if _under(rel, REPORT_PREFIXES):
        return CLASS_REPORT
    return CLASS_UNKNOWN


def is_protected(path: Path | str, *, root: Path = REPO_ROOT) -> bool:
    """True if cleanup must never delete this path (immutable, append-only, unknown)."""
    return classify(path, root=root) in (CLASS_IMMUTABLE, CLASS_APPEND_ONLY, CLASS_UNKNOWN)


def http_cache_roots(root: Path = REPO_ROOT) -> list[Path]:
    return [root / p.rstrip("/") for p in HTTP_CACHE_PREFIXES]


def immutable_roots(root: Path = REPO_ROOT) -> list[Path]:
    return [root / p.rstrip("/") for p in IMMUTABLE_PREFIXES]

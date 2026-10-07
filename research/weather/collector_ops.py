"""Collector operations: ownership marker, state backup/restore, archives, alerts.

Operational code only. Nothing here is pinned by the Phase 7 protocol, and
nothing here creates, edits or reconstructs a prediction, receipt or score.

* Ownership marker: OWNER.json on a ref in a control repository. Every
  collector entry point fetches it fresh before each cycle; if it cannot be
  fetched or names another collector, the cycle is skipped and an alert is
  sent. No cached ownership is ever used.
* State backup: an explicit allowlist of state files mirrored byte-for-byte
  (`* -text`, autocrlf off) into a separate backup ref. Backups never touch
  the ownership ref, never force-push and never merge or rebase.
* Restore / archive: hash-verified, refusing to overwrite or drop
  write-once captures and append-only logs.

Run ``python -m research.weather.collector_ops --help``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "collector.local.json"
CONFIG_ENV = "KEL_COLLECTOR_CONFIG"

EXIT_OK = 0
EXIT_NOT_OWNER = 20
EXIT_OWNERSHIP_UNVERIFIED = 21
EXIT_CONFIG = 22
EXIT_BACKUP_FAILED = 30
EXIT_UNSAFE = 32
EXIT_VERIFY_FAILED = 33

OWNER_FILE = "OWNER.json"
OWNER_SCHEMA = "kel_collector_owner_v1"
STOP_STATUS_SCHEMA = "kel_collector_stop_status_v1"
STOP_STATUS_MAX_AGE = timedelta(hours=6)
BACKUP_STATE_DIR = "state"
BACKUP_MANIFEST = "MANIFEST.json"
BINARY_ATTRIBUTES = "* -text\n"

WINDOWS_TASK_NAME = "KalshiEdgeLab-WeatherProspective"
SYSTEMD_TIMER = "kalshi-weather-prospective.timer"
SYSTEMD_SERVICE = "kalshi-weather-prospective.service"
CYCLE_COMMAND_MARKER = "--prospective-cycle"

# Byte-for-byte deviations audited in docs/research/phase7_evidence_line_endings_note.md.
# Any other non-RAW_MATCH evidence file fails verification.
AUDITED_EVIDENCE_EXCEPTIONS: dict[tuple[str, str, str], dict[str, str]] = {
    ("2026-10-03", "d0_0600", "iem_knyc.csv"): {
        "status": "CRLF_NORMALIZED_MATCH_ONLY",
        "expected_sha256": "c8ef0d009eae8f8c788f802984ef107cbcb2379d06dfbc26ff840c5d7e575aea",
        "actual_sha256": "c38533afb014bc66d6b8a23564baf9018bfbf855564ccf3493d21e4798b5904d",
    },
    ("2026-10-03", "d0_0900", "iem_knyc.csv"): {
        "status": "CRLF_NORMALIZED_MATCH_ONLY",
        "expected_sha256": "62c16a1240dc6b241f8aed0da44578c099679e47476c2ce0eca1c94d3c4dbf99",
        "actual_sha256": "520f42a3ed4ba008430bfd6debbca1c21f803886bae1c9ed6e08e7a7fc22be6f",
    },
}

REGISTERED_PINS = {
    "protocol_sha256": "cc33e71db90d0d6c069791892872f8e74d6d9dc7453e6fa5b9e1b3e79f948ae2",
    "frozen_pool_sha256": "eb4b6e716c8b5d24ece9a2597d22a2876175648f2e5584adefe86b71b633c4bb",
    "method_fingerprint_sha256": "95b0d991f47f97068b291b93a953a8f6a4eb84099d9288fd2ae6770228e78e6d",
    "source_csv_sha256": {
        "gfs_v2_1_csv": "a5ed82b4487aabb053cbb7bf02bb7f528a552cebd3fbd4b11ebd02200b17028a",
        "hrrr_v2_1_csv": "43e73fba55922a3e3cbef2cdcf8ed45d3a72bc00ff4494bf1d7d9473a55f45f5",
    },
}


class OpsError(RuntimeError):
    pass


class ConfigError(OpsError):
    pass


class GitError(OpsError):
    pass


class OwnershipUnverified(OpsError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollectorConfig:
    collector_id: str
    control_remote: str
    ownership_ref: str = "refs/heads/ownership"
    backup_remote: str | None = None
    backup_ref: str = "refs/heads/state"
    backup_dir: Path | None = None
    collector_ping_url: str | None = None
    backup_ping_url: str | None = None
    ssh_command: str | None = None
    git_timeout_s: int = 60


def config_path() -> Path:
    env = os.environ.get(CONFIG_ENV)
    return Path(env) if env else DEFAULT_CONFIG_PATH


def load_config(path: Path) -> CollectorConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"unreadable collector config {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"collector config {path} is not a JSON object")
    known = set(CollectorConfig.__dataclass_fields__)
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"unknown collector config keys: {unknown}")
    for key in ("collector_id", "control_remote"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ConfigError(f"collector config requires a non-empty {key!r}")
    if raw.get("backup_dir"):
        raw["backup_dir"] = Path(raw["backup_dir"])
    cfg = CollectorConfig(**raw)
    for ref in (cfg.ownership_ref, cfg.backup_ref):
        if not ref.startswith("refs/heads/"):
            raise ConfigError(f"refs must be full branch refs (refs/heads/...): {ref}")
    if cfg.backup_remote and cfg.backup_remote == cfg.control_remote and cfg.backup_ref == cfg.ownership_ref:
        raise ConfigError("backup_ref must differ from ownership_ref on the same remote")
    if bool(cfg.backup_remote) != bool(cfg.backup_dir):
        raise ConfigError("backup_remote and backup_dir must be set together")
    return cfg


# ---------------------------------------------------------------------------
# Git plumbing (text conversion always off)
# ---------------------------------------------------------------------------

GIT_SAFE_CONFIG = ("-c", "core.autocrlf=false", "-c", "core.safecrlf=false")


def _git(
    args: list[str],
    *,
    cwd: Path | None = None,
    ssh_command: str | None = None,
    timeout: int = 60,
    extra_config: tuple[str, ...] = GIT_SAFE_CONFIG,
    identity: str | None = None,
) -> subprocess.CompletedProcess:
    cmd = ["git", *extra_config]
    if identity:
        local = re.sub(r"[^A-Za-z0-9._-]+", "-", identity).strip("-") or "collector"
        cmd += ["-c", f"user.name={identity}", "-c", f"user.email={local}@collector.invalid"]
    cmd += args
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if ssh_command:
        env["GIT_SSH_COMMAND"] = ssh_command
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, timeout=timeout,
            text=True, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(f"git {' '.join(args[:2])} failed: {type(error).__name__}: {error}") from error
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} exit {proc.returncode}: {proc.stderr.strip()[:500]}")
    return proc


def _branch(ref: str) -> str:
    return ref.removeprefix("refs/heads/")


def remote_ref_sha(remote: str, ref: str, *, ssh_command: str | None = None, timeout: int = 60) -> str | None:
    """SHA of ``ref`` on ``remote``; None if the ref does not exist. Raises GitError if unreachable."""
    out = _git(["ls-remote", "--refs", remote, ref], ssh_command=ssh_command, timeout=timeout).stdout
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name == ref:
            return sha
    return None


@contextmanager
def _scratch_dir(prefix: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True) as tmp:
        yield Path(tmp)


# ---------------------------------------------------------------------------
# Ownership marker
# ---------------------------------------------------------------------------


def fetch_owner(remote: str, ref: str, *, ssh_command: str | None = None, timeout: int = 60) -> dict[str, Any]:
    """Fresh read of OWNER.json from ``remote``/``ref``. Never cached."""
    with _scratch_dir("kel-owner-") as tmp:
        try:
            _git(["init", "-q", "--bare", str(tmp)], timeout=timeout)
            _git(["fetch", "-q", remote, ref], cwd=tmp, ssh_command=ssh_command, timeout=timeout)
            commit = _git(["rev-parse", "FETCH_HEAD"], cwd=tmp).stdout.strip()
            raw = _git(["show", f"FETCH_HEAD:{OWNER_FILE}"], cwd=tmp).stdout
        except GitError as error:
            raise OwnershipUnverified(f"ownership marker unavailable: {error}") from error
    try:
        marker = json.loads(raw)
    except ValueError as error:
        raise OwnershipUnverified(f"ownership marker is not JSON: {error}") from error
    if not isinstance(marker, dict) or marker.get("schema") != OWNER_SCHEMA or not marker.get("owner_id"):
        raise OwnershipUnverified("ownership marker has an unexpected schema")
    return {**marker, "marker_commit": commit}


Pinger = Callable[[str | None, bool, str], None]


def http_ping(url: str | None, fail: bool, body: str) -> None:
    """Healthchecks.io-style ping: URL for success, URL + '/fail' for failure."""
    if not url:
        return
    import requests

    try:
        requests.post(url.rstrip("/") + ("/fail" if fail else ""), data=body.encode("utf-8")[:10000], timeout=10)
    except requests.RequestException as error:
        print(f"alert ping failed: {type(error).__name__}: {error}", file=sys.stderr)


def guard(
    *,
    cfg_path: Path | None = None,
    require_config: bool = False,
    pinger: Pinger = http_ping,
) -> tuple[int, dict[str, Any]]:
    """May this host run a collection cycle now?"""
    path = cfg_path or config_path()
    if not path.exists():
        if require_config:
            return EXIT_CONFIG, {"ownership": "config_missing", "config": str(path)}
        return EXIT_OK, {"ownership": "not_configured", "mode": "pre_migration_standalone", "config": str(path)}
    try:
        cfg = load_config(path)
    except ConfigError as error:
        return EXIT_CONFIG, {"ownership": "config_invalid", "error": str(error)}
    try:
        marker = fetch_owner(cfg.control_remote, cfg.ownership_ref, ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s)
    except OwnershipUnverified as error:
        result = {"ownership": "unverified", "collector_id": cfg.collector_id, "error": str(error),
                  "action": "cycle skipped"}
        pinger(cfg.collector_ping_url, True, json.dumps(result))
        return EXIT_OWNERSHIP_UNVERIFIED, result
    if marker["owner_id"] != cfg.collector_id:
        result = {"ownership": "not_owner", "collector_id": cfg.collector_id, "owner_id": marker["owner_id"],
                  "marker_commit": marker["marker_commit"], "action": "cycle skipped"}
        pinger(cfg.collector_ping_url, True, json.dumps(result))
        return EXIT_NOT_OWNER, result
    return EXIT_OK, {"ownership": "owner", "collector_id": cfg.collector_id, "marker_commit": marker["marker_commit"]}


def validate_stop_status(status: dict[str, Any], *, outgoing: str, now: datetime) -> list[str]:
    problems: list[str] = []
    if status.get("schema") != STOP_STATUS_SCHEMA:
        problems.append("stop status has an unexpected schema")
    if status.get("collector_id") != outgoing:
        problems.append(f"stop status is for {status.get('collector_id')!r}, not the outgoing owner {outgoing!r}")
    if status.get("confirmed_stopped") is not True:
        problems.append("outgoing collector is not confirmed stopped")
    try:
        checked = datetime.fromisoformat(str(status.get("checked_at")))
        if now - checked > STOP_STATUS_MAX_AGE:
            problems.append("stop status is older than 6 hours")
    except ValueError:
        problems.append("stop status has no valid checked_at")
    return problems


def write_owner(
    *,
    remote: str,
    ref: str,
    new_owner: str,
    expected_current: str | None,
    reason: str,
    actor: str,
    outgoing_stop_status: dict[str, Any] | None = None,
    outgoing_unconfirmed_reason: str | None = None,
    unsynced_blocked_reason: str | None = None,
    allow_unconfirmed: bool = False,
    adopt_running: bool = False,
    ssh_command: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare-and-swap the ownership marker. Never force-pushes.

    ``expected_current=None`` initializes a marker and fails if one exists.
    The outgoing collector must be confirmed stopped with no unsynced captures,
    unless a human records why that could not be confirmed (never automatic).
    ``adopt_running`` (init only) names the collector that is already running
    as the first owner, so no other collector has to be stopped.
    """
    now = now or utcnow()
    if not reason.strip():
        raise OpsError("a reason is required")
    if adopt_running and expected_current is not None:
        raise OpsError("adopt_running applies only to init")
    outgoing = expected_current or (outgoing_stop_status or {}).get("collector_id")
    if outgoing_stop_status is not None and not adopt_running:
        problems = validate_stop_status(outgoing_stop_status, outgoing=str(outgoing), now=now)
        if problems:
            raise OpsError("refusing ownership change: " + "; ".join(problems))
        unsynced = (outgoing_stop_status.get("unsynced") or {}).get("status")
        if unsynced not in ("none", "not_configured") and not unsynced_blocked_reason:
            raise OpsError(f"refusing ownership change: outgoing unsynced state status is {unsynced!r}; "
                           "recover it first or record --unsynced-blocked-reason")
    elif not adopt_running:
        if not (allow_unconfirmed and outgoing_unconfirmed_reason and unsynced_blocked_reason):
            raise OpsError("refusing ownership change without a confirmed stop of the outgoing collector")
    with _scratch_dir("kel-owner-write-") as tmp:
        _git(["init", "-q", str(tmp)])
        current = remote_ref_sha(remote, ref, ssh_command=ssh_command)
        if current is None:
            if expected_current is not None:
                raise OpsError(f"no ownership marker at {ref}; use init")
            _git(["checkout", "-q", "--orphan", "owner"], cwd=tmp)
        else:
            if expected_current is None:
                raise OpsError(f"ownership marker already exists at {ref}; use transfer")
            _git(["fetch", "-q", remote, ref], cwd=tmp, ssh_command=ssh_command)
            _git(["checkout", "-q", "-b", "owner", "FETCH_HEAD"], cwd=tmp)
            existing = json.loads((tmp / OWNER_FILE).read_text(encoding="utf-8"))
            if existing.get("owner_id") != expected_current:
                raise OpsError(f"current owner is {existing.get('owner_id')!r}, not {expected_current!r}")
        marker = {
            "schema": OWNER_SCHEMA,
            "owner_id": new_owner,
            "previous_owner_id": expected_current,
            "set_at": _iso(now),
            "set_by": actor,
            "reason": reason,
            "adopted_running_collector": adopt_running,
            "outgoing_stop_status": outgoing_stop_status,
            "outgoing_unconfirmed_reason": outgoing_unconfirmed_reason,
            "unsynced_blocked_reason": unsynced_blocked_reason,
        }
        (tmp / ".gitattributes").write_bytes(BINARY_ATTRIBUTES.encode("ascii"))
        (tmp / OWNER_FILE).write_bytes((json.dumps(marker, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        _git(["add", "-A"], cwd=tmp)
        _git(["commit", "-q", "-m", f"ownership: {expected_current} -> {new_owner} ({reason})"], cwd=tmp, identity=actor)
        _git(["push", "-q", remote, f"HEAD:{ref}"], cwd=tmp, ssh_command=ssh_command)
        marker["marker_commit"] = _git(["rev-parse", "HEAD"], cwd=tmp).stdout.strip()
    return marker


# ---------------------------------------------------------------------------
# State allowlist and file classes
# ---------------------------------------------------------------------------

PHASE7 = "data/weather/phase7"
STATE_DIRS = (
    f"{PHASE7}/records",
    f"{PHASE7}/receipts",
    f"{PHASE7}/evidence",
    f"{PHASE7}/scores",
    f"{PHASE7}/pending_scores",
    f"{PHASE7}/diagnostics",
    f"{PHASE7}/dry_run",
    "data/weather/checkpoints",
    "data/weather/snapshots",
    "data/weather/snapshot_scores",
    "data/results",
)
STATE_FILES = (
    f"{PHASE7}/fetch_log.jsonl",
    f"{PHASE7}/outcomes_ledger.jsonl",
    "data/weather/calibration/knyc_clinyc_pairs.csv",
)
STATE_LOG_DIR = "data/logs/prospective"
MIGRATION_DIRS = ("data/cache/weather",)
MIGRATION_FILES = ("gfs_run_cache.json",)
PROFILES = ("backup", "migration")

EXCLUDED_NAMES = {"cycle.lock", "cycle.flock", "collector.local.json", ".env", "__pycache__"}
EXCLUDED_SUFFIXES = (".tmp", ".lock", ".flock", ".pyc", ".swp")

WRITE_ONCE = "write_once"
APPEND_ONLY = "append_only"
MUTABLE = "mutable"

WRITE_ONCE_PREFIXES = tuple(
    f"{d}/" for d in (
        f"{PHASE7}/records", f"{PHASE7}/receipts", f"{PHASE7}/evidence", f"{PHASE7}/scores",
        f"{PHASE7}/diagnostics", f"{PHASE7}/dry_run", "data/weather/checkpoints",
    )
)
APPEND_ONLY_FILES = {f"{PHASE7}/fetch_log.jsonl", f"{PHASE7}/outcomes_ledger.jsonl"}


def is_excluded(name: str) -> bool:
    return name in EXCLUDED_NAMES or name.startswith(".") or name.endswith(EXCLUDED_SUFFIXES)


def classify(rel: str) -> str:
    if rel.startswith(WRITE_ONCE_PREFIXES):
        return WRITE_ONCE
    if rel.startswith("data/weather/snapshots/") and not rel.endswith("/latest.json"):
        return WRITE_ONCE
    if rel in APPEND_ONLY_FILES or (rel.startswith(f"{STATE_LOG_DIR}/") and rel.endswith(".log")):
        return APPEND_ONLY
    return MUTABLE


def _walk(root: Path, rel_dir: str) -> Iterator[str]:
    base = root / rel_dir
    if not base.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not is_excluded(d))
        for name in sorted(filenames):
            if not is_excluded(name):
                yield (Path(dirpath) / name).relative_to(root).as_posix()


def collect_state(root: Path, profile: str = "backup") -> dict[str, Path]:
    """Allowlisted state files under ``root`` keyed by POSIX relative path."""
    if profile not in PROFILES:
        raise OpsError(f"unknown profile {profile!r}")
    rels: list[str] = []
    for d in STATE_DIRS + (MIGRATION_DIRS if profile == "migration" else ()):
        rels.extend(_walk(root, d))
    for f in STATE_FILES + (MIGRATION_FILES if profile == "migration" else ()):
        if (root / f).is_file():
            rels.append(f)
    log_dir = root / STATE_LOG_DIR
    if log_dir.is_dir():
        rels.extend(f"{STATE_LOG_DIR}/{p.name}" for p in sorted(log_dir.glob("*.log")) if not is_excluded(p.name))
    return {rel: root / rel for rel in sorted(set(rels))}


def build_manifest(files: dict[str, Path]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rel, path in sorted(files.items()):
        data = path.read_bytes()
        out[rel] = {"sha256": sha256_bytes(data), "size": len(data)}
    return out


def _safe_rel(rel: str) -> str:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts or "\\" in rel or ":" in p.parts[0]:
        raise OpsError(f"unsafe path in state source: {rel!r}")
    return p.as_posix()


# ---------------------------------------------------------------------------
# Restore planning (shared by archive extraction, backup restore, unsynced check)
# ---------------------------------------------------------------------------


@dataclass
class Source:
    """Verified state: rel path -> bytes."""

    files: dict[str, bytes]
    origin: str
    manifest: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class RestorePlan:
    copy: list[str] = field(default_factory=list)
    overwrite: list[str] = field(default_factory=list)
    delete: list[str] = field(default_factory=list)
    unchanged: int = 0
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    target_only_captures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.conflicts and not self.target_only_captures

    def summary(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "copy": len(self.copy),
            "overwrite": len(self.overwrite),
            "delete": len(self.delete),
            "unchanged": self.unchanged,
            "conflicts": self.conflicts,
            "target_only_captures": self.target_only_captures,
            "overwrite_paths": self.overwrite,
            "delete_paths": self.delete,
        }


def plan_restore(source: Source, target_root: Path, profile: str) -> RestorePlan:
    """What restoring ``source`` into ``target_root`` would do; never writes.

    write-once: target copy must be absent or identical; a target-only
      write-once file is a capture missing from the source.
    append-only: target must be a byte prefix of the source; a longer target
      holds entries missing from the source.
    mutable: overwritten; target-only mutable files are deleted.
    """
    plan = RestorePlan()
    target = collect_state(target_root, profile)
    for rel, data in sorted(source.files.items()):
        kind = classify(rel)
        path = target.get(rel)
        if path is None:
            plan.copy.append(rel)
            continue
        current = path.read_bytes()
        if current == data:
            plan.unchanged += 1
        elif kind == WRITE_ONCE:
            plan.conflicts.append({"path": rel, "class": kind, "reason": "write-once file differs",
                                   "target_sha256": sha256_bytes(current), "source_sha256": sha256_bytes(data)})
        elif kind == APPEND_ONLY:
            if data.startswith(current):
                plan.overwrite.append(rel)
            elif current.startswith(data):
                plan.target_only_captures.append({"path": rel, "class": kind,
                                                  "reason": "target has entries the source lacks",
                                                  "extra_bytes": len(current) - len(data)})
            else:
                plan.conflicts.append({"path": rel, "class": kind, "reason": "append-only histories diverge"})
        else:
            plan.overwrite.append(rel)
    for rel in sorted(set(target) - set(source.files)):
        kind = classify(rel)
        if kind == MUTABLE:
            plan.delete.append(rel)
        else:
            plan.target_only_captures.append({"path": rel, "class": kind, "reason": "present only in target"})
    return plan


def apply_restore(source: Source, target_root: Path, plan: RestorePlan, profile: str) -> dict[str, Any]:
    if not plan.safe:
        raise OpsError("refusing to apply an unsafe restore plan")
    for rel in plan.copy + plan.overwrite:
        dest = target_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.restore.tmp")
        tmp.write_bytes(source.files[rel])
        os.replace(tmp, dest)
    for rel in plan.delete:
        (target_root / rel).unlink()
    after = build_manifest(collect_state(target_root, profile))
    expected = {rel: {"sha256": sha256_bytes(b), "size": len(b)} for rel, b in source.files.items()}
    if after != expected:
        bad = sorted(k for k in set(after) | set(expected) if after.get(k) != expected.get(k))[:20]
        raise OpsError(f"post-restore verification failed: {bad}")
    return {"verified_files": len(after)}


def _source_from_dir(state_dir: Path, manifest: dict[str, dict[str, Any]], origin: str) -> Source:
    on_disk: dict[str, bytes] = {}
    if state_dir.is_dir():
        for path in sorted(p for p in state_dir.rglob("*") if p.is_file()):
            on_disk[path.relative_to(state_dir).as_posix()] = path.read_bytes()
    problems = []
    for rel in sorted(set(manifest) | set(on_disk)):
        if rel not in on_disk:
            problems.append(f"missing {rel}")
        elif rel not in manifest:
            problems.append(f"unlisted {rel}")
        elif sha256_bytes(on_disk[rel]) != manifest[rel]["sha256"] or len(on_disk[rel]) != manifest[rel]["size"]:
            problems.append(f"hash mismatch {rel}")
    if problems:
        raise OpsError(f"{origin}: manifest verification failed: {problems[:20]}")
    return Source(files={_safe_rel(r): b for r, b in on_disk.items()}, origin=origin, manifest=manifest)


# ---------------------------------------------------------------------------
# Cycle lock / stop confirmation
# ---------------------------------------------------------------------------


def _lock_paths(repo_root: Path) -> tuple[Path, Path]:
    log_dir = repo_root / STATE_LOG_DIR
    return log_dir / "cycle.lock", log_dir / "cycle.flock"


@contextmanager
def hold_cycle_lock(repo_root: Path) -> Iterator[None]:
    """Block new cycles while state is read (flock on POSIX; refuse if the Windows lock exists)."""
    win_lock, flock_path = _lock_paths(repo_root)
    if win_lock.exists():
        raise OpsError(f"a cycle holds {win_lock}; wait for it to finish")
    if os.name != "posix":
        yield
        if win_lock.exists():
            raise OpsError(f"a cycle started during the operation ({win_lock})")
        return
    import fcntl

    flock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(flock_path, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OpsError(f"a cycle holds {flock_path}; wait for it to finish") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _run_quiet(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return -1, f"{type(error).__name__}: {error}"
    return p.returncode, (p.stdout or "").strip()


@dataclass
class PlatformChecks:
    """Each check returns (ok, detail); ok is None when it could not be determined."""

    triggers_disabled: Callable[[], tuple[bool | None, str]]
    no_active_cycle: Callable[[Path], tuple[bool | None, str]]
    no_cycle_process: Callable[[], tuple[bool | None, str]]


def _posix_triggers_disabled() -> tuple[bool | None, str]:
    rc, enabled = _run_quiet(["systemctl", "is-enabled", SYSTEMD_TIMER])
    rc2, active = _run_quiet(["systemctl", "is-active", SYSTEMD_TIMER])
    if rc == -1 or rc2 == -1:
        return None, f"systemctl unavailable: {enabled or active}"
    ok = enabled in ("disabled", "masked", "not-found", "") and active in ("inactive", "failed", "unknown")
    return ok, f"timer is-enabled={enabled or 'not-found'} is-active={active}"


def _posix_no_active_cycle(repo_root: Path) -> tuple[bool | None, str]:
    rc, active = _run_quiet(["systemctl", "is-active", SYSTEMD_SERVICE])
    try:
        with hold_cycle_lock(repo_root):
            pass
    except OpsError as error:
        return False, str(error)
    if active in ("active", "activating", "deactivating"):
        return False, f"service is-active={active}"
    return True, f"cycle lock free; service is-active={active or 'unknown'}"


def _posix_no_cycle_process() -> tuple[bool | None, str]:
    rc, out = _run_quiet(["pgrep", "-af", f"weather_model.py {CYCLE_COMMAND_MARKER}"])
    if rc == 1:
        return True, "no prospective-cycle process"
    if rc == 0:
        return False, f"running: {out[:300]}"
    return None, f"pgrep failed: {out}"


def _ps(command: str) -> tuple[int, str]:
    return _run_quiet(["powershell", "-NoProfile", "-NonInteractive", "-Command", command])


def _windows_triggers_disabled() -> tuple[bool | None, str]:
    rc, state = _ps(f"(Get-ScheduledTask -TaskName '{WINDOWS_TASK_NAME}' -ErrorAction Stop).State")
    if rc != 0:
        return None, f"could not read task state: {state}"
    return state == "Disabled", f"task State={state}"


def _windows_no_active_cycle(repo_root: Path) -> tuple[bool | None, str]:
    win_lock, _ = _lock_paths(repo_root)
    if win_lock.exists():
        return False, f"{win_lock} present"
    return True, "no cycle.lock"


def _windows_no_cycle_process() -> tuple[bool | None, str]:
    rc, out = _ps(
        "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*weather_model.py*{CYCLE_COMMAND_MARKER}*' }}).Count"
    )
    if rc != 0:
        return None, f"process query failed: {out}"
    return out == "0", f"matching python processes={out}"


def default_platform_checks() -> PlatformChecks:
    if os.name == "nt":
        return PlatformChecks(_windows_triggers_disabled, _windows_no_active_cycle, _windows_no_cycle_process)
    return PlatformChecks(_posix_triggers_disabled, _posix_no_active_cycle, _posix_no_cycle_process)


def stop_status(
    *,
    repo_root: Path = REPO_ROOT,
    cfg: CollectorConfig | None,
    checks: PlatformChecks | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run on the outgoing host after disabling its trigger and letting any cycle finish."""
    checks = checks or default_platform_checks()
    trig = checks.triggers_disabled()
    cyc = checks.no_active_cycle(repo_root)
    proc = checks.no_cycle_process()
    confirmed = trig[0] is True and cyc[0] is True and proc[0] is True
    if cfg is not None and cfg.backup_remote:
        unsynced = unsynced_report(cfg, repo_root=repo_root)
    else:
        unsynced = {"status": "not_configured",
                    "note": "no backup configured; the final state sync is the hash-verified archive"}
    return {
        "schema": STOP_STATUS_SCHEMA,
        "collector_id": cfg.collector_id if cfg else f"unconfigured:{socket.gethostname()}",
        "host": socket.gethostname(),
        "checked_at": _iso(now or utcnow()),
        "triggers_disabled": {"ok": trig[0], "detail": trig[1]},
        "no_active_cycle": {"ok": cyc[0], "detail": cyc[1]},
        "no_cycle_process": {"ok": proc[0], "detail": proc[1]},
        "confirmed_stopped": confirmed,
        "unsynced": unsynced,
    }


# ---------------------------------------------------------------------------
# Byte-exact state backup
# ---------------------------------------------------------------------------


def _backup_git(cfg: CollectorConfig, args: list[str], **kw: Any) -> subprocess.CompletedProcess:
    return _git(args, cwd=cfg.backup_dir, ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s, **kw)


def backup_init(cfg: CollectorConfig, *, create_if_missing: bool = False) -> dict[str, Any]:
    """Clone the backup ref (or, explicitly, start a new one). Never adopts a divergent history."""
    if not cfg.backup_remote or not cfg.backup_dir:
        raise ConfigError("backup_remote/backup_dir not configured")
    if (cfg.backup_dir / ".git").exists():
        raise OpsError(f"{cfg.backup_dir} is already initialized")
    remote_sha = remote_ref_sha(cfg.backup_remote, cfg.backup_ref, ssh_command=cfg.ssh_command)
    cfg.backup_dir.parent.mkdir(parents=True, exist_ok=True)
    if remote_sha is not None:
        _git(["clone", "-q", "--branch", _branch(cfg.backup_ref), "--single-branch", cfg.backup_remote,
              str(cfg.backup_dir)], ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s * 5)
    elif create_if_missing:
        _git(["init", "-q", "-b", _branch(cfg.backup_ref), str(cfg.backup_dir)])
        _backup_git(cfg, ["remote", "add", "origin", cfg.backup_remote])
        (cfg.backup_dir / ".gitattributes").write_bytes(BINARY_ATTRIBUTES.encode("ascii"))
        _backup_git(cfg, ["add", ".gitattributes"])
        _backup_git(cfg, ["commit", "-q", "-m", "state backup: binary attributes"], identity=cfg.collector_id)
    else:
        raise OpsError(f"{cfg.backup_ref} does not exist on the backup remote; pass --create to start it")
    for key, value in (("core.autocrlf", "false"), ("core.safecrlf", "false")):
        _backup_git(cfg, ["config", key, value])
    attrs = (cfg.backup_dir / ".gitattributes")
    if not attrs.exists() or attrs.read_bytes() != BINARY_ATTRIBUTES.encode("ascii"):
        raise OpsError("backup repository lacks the '* -text' attributes; refusing to use it")
    return {"backup_dir": str(cfg.backup_dir), "remote_sha": remote_sha}


def _mirror_state(cfg: CollectorConfig, files: dict[str, Path]) -> dict[str, dict[str, Any]]:
    assert cfg.backup_dir is not None
    state_dir = cfg.backup_dir / BACKUP_STATE_DIR
    manifest: dict[str, dict[str, Any]] = {}
    for rel, src in files.items():
        data = src.read_bytes()
        manifest[rel] = {"sha256": sha256_bytes(data), "size": len(data)}
        dest = state_dir / rel
        if not dest.exists() or dest.read_bytes() != data:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    if state_dir.is_dir():
        for path in sorted(state_dir.rglob("*"), reverse=True):
            rel = path.relative_to(state_dir).as_posix()
            if path.is_file() and rel not in manifest:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    (cfg.backup_dir / BACKUP_MANIFEST).write_bytes(
        (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return manifest


def run_backup(
    cfg: CollectorConfig,
    *,
    repo_root: Path = REPO_ROOT,
    pinger: Pinger = http_ping,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mirror allowlisted state into the backup clone, commit, push without force.

    Local data is never modified. A failed or rejected push leaves the commit in
    the local backup clone (unsynced) and alerts on the backup health check.
    """
    now = now or utcnow()
    result: dict[str, Any] = {"backup": "failed", "collector_id": cfg.collector_id, "at": _iso(now)}

    def finish(status: str, **extra: Any) -> dict[str, Any]:
        result.update(backup=status, **extra)
        pinger(cfg.backup_ping_url, status not in ("pushed", "up_to_date"), json.dumps(result, default=str))
        return result

    if not cfg.backup_remote or not cfg.backup_dir:
        return finish("not_configured")
    if not (cfg.backup_dir / ".git").exists():
        return finish("failed", error=f"backup clone missing at {cfg.backup_dir}; run backup-init")
    attrs = cfg.backup_dir / ".gitattributes"
    if not attrs.exists() or attrs.read_bytes() != BINARY_ATTRIBUTES.encode("ascii"):
        return finish("failed", error="backup clone lacks '* -text' attributes")

    try:
        remote_sha = remote_ref_sha(cfg.backup_remote, cfg.backup_ref, ssh_command=cfg.ssh_command,
                                    timeout=cfg.git_timeout_s)
        remote_reachable = True
    except GitError as error:
        remote_sha, remote_reachable = None, False
        result["remote_error"] = str(error)

    previous: dict[str, Any] = {}
    manifest_path = cfg.backup_dir / BACKUP_MANIFEST
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = collect_state(repo_root, "backup")
    vanished = sorted(rel for rel in previous if rel not in files and classify(rel) != MUTABLE)
    if vanished:
        return finish("failed", error="write-once/append-only files vanished locally; refusing to mirror",
                      vanished=vanished[:50])
    shrunk = [rel for rel, meta in previous.items()
              if classify(rel) == APPEND_ONLY and rel in files and files[rel].stat().st_size < meta["size"]]
    if shrunk:
        return finish("failed", error="append-only files shrank locally; refusing to mirror", shrunk=shrunk)

    try:
        with _null() if _cycle_lock_held_by_caller() else hold_cycle_lock(repo_root):
            manifest = _mirror_state(cfg, files)
        _backup_git(cfg, ["add", "-A"])
        if _git_rc(cfg.backup_dir, ["diff", "--cached", "--quiet"]) != 0:
            _backup_git(cfg, ["commit", "-q", "-m", f"state backup {cfg.collector_id} {_iso(now)}"],
                        identity=cfg.collector_id)
        head = _backup_git(cfg, ["rev-parse", "HEAD"]).stdout.strip()
    except (OpsError, OSError) as error:
        return finish("failed", error=f"local commit failed: {error}")
    result.update(local_head=head, files=len(manifest))

    if not remote_reachable:
        return finish("push_failed", unsynced_commits=_count_unsynced(cfg, None))
    if remote_sha == head:
        return finish("up_to_date", unsynced_commits=0)
    if remote_sha is not None:
        try:
            _backup_git(cfg, ["fetch", "-q", "origin", cfg.backup_ref])
            known = _git_rc(cfg.backup_dir, ["merge-base", "--is-ancestor", remote_sha, head]) == 0
        except GitError as error:
            return finish("push_failed", error=str(error), unsynced_commits=_count_unsynced(cfg, None))
        if not known:
            return finish("conflict", remote_sha=remote_sha,
                          error="remote backup has commits not in this host's history; not merging, not forcing",
                          unsynced_commits=_count_unsynced(cfg, remote_sha))
    try:
        _backup_git(cfg, ["push", "-q", "origin", f"HEAD:{cfg.backup_ref}"])
    except GitError as error:
        status = "conflict" if "rejected" in str(error) or "non-fast-forward" in str(error) else "push_failed"
        return finish(status, error=str(error), unsynced_commits=_count_unsynced(cfg, remote_sha))
    return finish("pushed", unsynced_commits=0)


@contextmanager
def _null() -> Iterator[None]:
    yield


def _cycle_lock_held_by_caller() -> bool:
    """The runners set KEL_CYCLE_LOCK_HELD=1 while they hold the cycle lock themselves."""
    return os.environ.get("KEL_CYCLE_LOCK_HELD") == "1"


def _git_rc(cwd: Path | None, args: list[str]) -> int:
    return subprocess.run(["git", *GIT_SAFE_CONFIG, *args], cwd=cwd, capture_output=True).returncode


def _count_unsynced(cfg: CollectorConfig, remote_sha: str | None) -> int | None:
    try:
        spec = f"{remote_sha}..HEAD" if remote_sha else "HEAD"
        if remote_sha is None:
            tracking = f"refs/remotes/origin/{_branch(cfg.backup_ref)}"
            if _git_rc(cfg.backup_dir, ["rev-parse", "--verify", "-q", tracking]) == 0:
                spec = f"{tracking}..HEAD"
        return int(_backup_git(cfg, ["rev-list", "--count", spec]).stdout.strip())
    except (GitError, ValueError):
        return None


def clone_backup(remote: str, ref: str, dest: Path, *, ssh_command: str | None = None,
                 hostile: bool = True, timeout: int = 300) -> Source:
    """Fresh clone of the backup ref, verified file-by-file against its manifest.

    ``hostile`` clones with autocrlf=true / eol=crlf to prove the stored bytes
    do not depend on the restoring host's Git settings.
    """
    cfg_args = ("-c", "core.autocrlf=true", "-c", "core.eol=crlf") if hostile else GIT_SAFE_CONFIG
    _git(["clone", "-q", "--branch", _branch(ref), "--single-branch", remote, str(dest)],
         ssh_command=ssh_command, timeout=timeout, extra_config=cfg_args)
    manifest = json.loads((dest / BACKUP_MANIFEST).read_text(encoding="utf-8"))
    return _source_from_dir(dest / BACKUP_STATE_DIR, manifest, origin=f"{remote}@{ref}")


def unsynced_report(cfg: CollectorConfig, *, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Local captures missing from the remote backup. 'blocked' if the remote can't be read."""
    assert cfg.backup_remote
    with _scratch_dir("kel-unsynced-") as tmp:
        try:
            source = clone_backup(cfg.backup_remote, cfg.backup_ref, tmp / "clone", ssh_command=cfg.ssh_command)
        except (OpsError, OSError, ValueError) as error:
            return {"status": "blocked", "error": str(error)}
    plan = plan_restore(source, repo_root, "backup")
    items = plan.target_only_captures + plan.conflicts
    return {
        "status": "none" if not items else "unsynced",
        "items": items[:200],
        "mutable_differences": len(plan.overwrite) + len(plan.delete),
    }


# ---------------------------------------------------------------------------
# Consistent archives (initial migration / manual transfers)
# ---------------------------------------------------------------------------


def create_archive(repo_root: Path, out_path: Path, *, profile: str = "migration",
                   during: Callable[[], None] | None = None) -> dict[str, Any]:
    with hold_cycle_lock(repo_root):
        files = collect_state(repo_root, profile)
        before = build_manifest(files)
        manifest: dict[str, dict[str, Any]] = {}
        tmp_out = out_path.with_name(out_path.name + ".partial")
        with tarfile.open(tmp_out, "w", format=tarfile.PAX_FORMAT) as tar:
            for rel, path in files.items():
                data = path.read_bytes()
                manifest[rel] = {"sha256": sha256_bytes(data), "size": len(data)}
                info = tarfile.TarInfo(f"{BACKUP_STATE_DIR}/{rel}")
                info.size = len(data)
                info.mtime = int(path.stat().st_mtime)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
            if during:
                during()
            body = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8")
            info = tarfile.TarInfo(BACKUP_MANIFEST)
            info.size = len(body)
            info.mtime = int(utcnow().timestamp())
            tar.addfile(info, io.BytesIO(body))
        after = build_manifest(collect_state(repo_root, profile))
        if not (before == manifest == after):
            tmp_out.unlink(missing_ok=True)
            changed = sorted(set(map(str, before.items())) ^ set(map(str, after.items())))[:20]
            raise OpsError(f"state changed while archiving; archive discarded: {changed}")
        os.replace(tmp_out, out_path)
    archive_sha = sha256_bytes(out_path.read_bytes())
    sidecar = {"archive": out_path.name, "archive_sha256": archive_sha, "profile": profile,
               "files": len(manifest), "created_at": _iso(utcnow()), "host": socket.gethostname(),
               "manifest_sha256": sha256_bytes(body)}
    out_path.with_name(out_path.name + ".json").write_bytes(
        (json.dumps(sidecar, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return sidecar


def read_archive(archive: Path, *, expected_sha256: str | None = None) -> Source:
    data = archive.read_bytes()
    if expected_sha256 and sha256_bytes(data) != expected_sha256:
        raise OpsError("archive SHA-256 does not match the expected value")
    files: dict[str, bytes] = {}
    manifest: dict[str, Any] | None = None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                raise OpsError(f"unexpected non-file archive member {member.name!r}")
            content = tar.extractfile(member).read()  # type: ignore[union-attr]
            if member.name == BACKUP_MANIFEST:
                manifest = json.loads(content.decode("utf-8"))
            elif member.name.startswith(f"{BACKUP_STATE_DIR}/"):
                files[_safe_rel(member.name[len(BACKUP_STATE_DIR) + 1:])] = content
            else:
                raise OpsError(f"unexpected archive member {member.name!r}")
    if manifest is None:
        raise OpsError("archive has no manifest")
    with _scratch_dir("kel-archive-") as tmp:
        for rel, content in files.items():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp / rel).write_bytes(content)
        return _source_from_dir(tmp, manifest, origin=str(archive))


# ---------------------------------------------------------------------------
# Verification against the registered protocol
# ---------------------------------------------------------------------------


def verify_pins(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    from research.weather import phase7
    from research.weather import phase7_method as method

    paths = phase7.Phase7Paths()
    protocol = phase7.load_protocol(paths)
    if protocol is None:
        return {"ok": False, "problems": ["protocol not registered"]}
    problems: list[str] = []
    actual_protocol = phase7.protocol_sha256(paths)
    if actual_protocol != REGISTERED_PINS["protocol_sha256"]:
        problems.append(f"protocol file sha256 {actual_protocol}")
    cal = protocol["calibration"]
    if cal["frozen_pool_sha256"] != REGISTERED_PINS["frozen_pool_sha256"]:
        problems.append("protocol frozen_pool_sha256 changed")
    if cal.get("source_csv_sha256") != REGISTERED_PINS["source_csv_sha256"]:
        problems.append("protocol source_csv_sha256 changed")
    if protocol["method"]["method_fingerprint_sha256"] != REGISTERED_PINS["method_fingerprint_sha256"]:
        problems.append("protocol method fingerprint changed")
    current_fp = method.method_fingerprint()["method_fingerprint_sha256"]
    if current_fp != REGISTERED_PINS["method_fingerprint_sha256"]:
        problems.append(f"current method fingerprint {current_fp}")
    integrity = phase7.integrity_status(protocol, paths)
    problems += integrity["problems"]
    record_protocol_hashes = sorted({
        json.loads(p.read_text(encoding="utf-8")).get("protocol_sha256")
        for p in paths.records.glob("*/*.json")
    } - {None}) if paths.records.exists() else []
    if record_protocol_hashes and record_protocol_hashes != [REGISTERED_PINS["protocol_sha256"]]:
        problems.append(f"records reference protocol hashes {record_protocol_hashes}")
    return {
        "ok": not problems,
        "problems": problems,
        "protocol_sha256": actual_protocol,
        "method_fingerprint_sha256": current_fp,
        "method_integrity_ok": integrity["method_integrity_ok"],
        "calibration_integrity_ok": integrity["calibration_integrity_ok"],
        "record_protocol_sha256_values": record_protocol_hashes,
    }


def reproduce_all() -> dict[str, Any]:
    """--phase7-reproduce for every record, with only the audited evidence exceptions allowed."""
    from research.weather import phase7

    paths = phase7.Phase7Paths()
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    seen_exceptions: set[tuple[str, str, str]] = set()
    for rec_file in sorted(paths.records.glob("*/*.json")) if paths.records.exists() else []:
        key = f"{rec_file.parent.name}/{rec_file.stem}"
        try:
            r = phase7.reproduce_record(rec_file, paths=paths)
        except (OSError, phase7.Phase7Error, ValueError, KeyError) as error:
            failures.append(f"{key}: reproduction error {type(error).__name__}: {error}")
            continue
        row = {"record": key, "prediction_identical": r["prediction_identical"],
               "probabilities_identical": r["probabilities_identical"], "score_identical": r["score_identical"],
               "evidence_status_counts": r["evidence_integrity"]["status_counts"]}
        results.append(row)
        if r["prediction_identical"] is not True or r["probabilities_identical"] is not True:
            failures.append(f"{key}: prediction not identical")
        if r["score_identical"] is False:
            failures.append(f"{key}: score not identical")
        for f in r["evidence_integrity"]["files"]:
            if f["status"] == phase7.EVIDENCE_RAW_MATCH:
                continue
            ek = (rec_file.parent.name, rec_file.stem, f["evidence_file"])
            audited = AUDITED_EVIDENCE_EXCEPTIONS.get(ek)
            if audited and all(f.get(k) == v for k, v in audited.items()):
                seen_exceptions.add(ek)
                continue
            failures.append(f"{key}: {f['evidence_file']} {f['status']} (not an audited exception)")
    return {
        "ok": not failures,
        "records": len(results),
        "failures": failures,
        "audited_exceptions_seen": sorted("/".join(k) for k in seen_exceptions),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Runner hook
# ---------------------------------------------------------------------------


def post_cycle(cycle_exit: int, *, cfg_path: Path | None = None, pinger: Pinger = http_ping,
               repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """After a cycle: collector health ping, then backup (separate health check)."""
    path = cfg_path or config_path()
    if not path.exists():
        return {"post": "not_configured"}
    cfg = load_config(path)
    pinger(cfg.collector_ping_url, cycle_exit != 0,
           json.dumps({"collector_id": cfg.collector_id, "cycle_exit": cycle_exit}))
    if not cfg.backup_remote:
        return {"post": "ok", "backup": "not_configured"}
    return {"post": "ok", **run_backup(cfg, repo_root=repo_root, pinger=pinger)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _cfg_required() -> CollectorConfig:
    path = config_path()
    if not path.exists():
        raise ConfigError(f"collector config not found: {path}")
    return load_config(path)


def _remote_args(args: argparse.Namespace) -> tuple[str, str, str | None]:
    if args.remote:
        return args.remote, args.ref, None
    cfg = _cfg_required()
    return cfg.control_remote, cfg.ownership_ref, cfg.ssh_command


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m research.weather.collector_ops", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("guard", help="ownership check before a cycle (exit 0 = run)")
    g.add_argument("--require-config", action="store_true")

    po = sub.add_parser("post", help="after a cycle: health ping + backup")
    po.add_argument("--cycle-exit", type=int, required=True)

    own = sub.add_parser("ownership", help="show / init / transfer the ownership marker")
    own_sub = own.add_subparsers(dest="action", required=True)
    for name in ("show", "init", "transfer"):
        a = own_sub.add_parser(name)
        a.add_argument("--remote", help="control repo URL (default: collector config)")
        a.add_argument("--ref", default="refs/heads/ownership")
        if name in ("init", "transfer"):
            a.add_argument("--to", required=True, help="new owner collector_id")
            a.add_argument("--reason", required=True)
            a.add_argument("--actor", default=os.environ.get("USERNAME") or os.environ.get("USER") or "operator")
            a.add_argument("--stop-status", type=Path, help="stop-status JSON from the outgoing host")
            a.add_argument("--outgoing-unconfirmed-reason")
            a.add_argument("--unsynced-blocked-reason")
            a.add_argument("--i-confirm-outgoing-cannot-run", action="store_true")
        if name == "init":
            a.add_argument("--adopt-running-collector", action="store_true",
                           help="the new owner is the collector already running (first marker only)")
        if name == "transfer":
            a.add_argument("--from", dest="from_", required=True, help="expected current owner")

    st = sub.add_parser("stop-status", help="confirm this host's collector is stopped (run on outgoing host)")
    st.add_argument("--out", type=Path)

    sub.add_parser("unsynced", help="local captures missing from the remote backup")

    bi = sub.add_parser("backup-init")
    bi.add_argument("--create", action="store_true", help="start a new backup ref if none exists")
    sub.add_parser("backup", help="mirror state to the backup ref (owner only)")

    vb = sub.add_parser("verify-backup", help="fresh hostile-settings clone + manifest verification")
    vb.add_argument("--remote")
    vb.add_argument("--ref", default="refs/heads/state")

    ar = sub.add_parser("archive", help="consistent hash-manifested tar of state")
    ar.add_argument("--out", type=Path, required=True)
    ar.add_argument("--profile", choices=PROFILES, default="migration")

    rs = sub.add_parser("restore", help="plan (default) or --apply a verified restore")
    src = rs.add_mutually_exclusive_group(required=True)
    src.add_argument("--archive", type=Path)
    src.add_argument("--from-backup", action="store_true")
    rs.add_argument("--archive-sha256")
    rs.add_argument("--remote")
    rs.add_argument("--ref", default="refs/heads/state")
    rs.add_argument("--dest", type=Path, default=REPO_ROOT)
    rs.add_argument("--profile", choices=PROFILES)
    rs.add_argument("--apply", action="store_true")

    sub.add_parser("verify-pins", help="registered protocol/calibration/method hashes")
    sub.add_parser("reproduce-all", help="offline reproduction of every record")

    args = p.parse_args(argv)
    try:
        if args.cmd == "guard":
            code, result = guard(require_config=args.require_config)
            _print(result)
            return code
        if args.cmd == "post":
            try:
                _print(post_cycle(args.cycle_exit))
            except Exception as error:  # noqa: BLE001 - post must never change the cycle outcome
                _print({"post": "error", "error": f"{type(error).__name__}: {error}"})
            return EXIT_OK
        if args.cmd == "ownership":
            remote, ref, ssh = _remote_args(args)
            if args.action == "show":
                _print(fetch_owner(remote, ref, ssh_command=ssh))
                return EXIT_OK
            stop = json.loads(args.stop_status.read_text(encoding="utf-8")) if args.stop_status else None
            marker = write_owner(
                remote=remote, ref=ref, new_owner=args.to,
                expected_current=getattr(args, "from_", None), reason=args.reason, actor=args.actor,
                outgoing_stop_status=stop, outgoing_unconfirmed_reason=args.outgoing_unconfirmed_reason,
                unsynced_blocked_reason=args.unsynced_blocked_reason,
                allow_unconfirmed=args.i_confirm_outgoing_cannot_run,
                adopt_running=getattr(args, "adopt_running_collector", False), ssh_command=ssh,
            )
            _print(marker)
            return EXIT_OK
        if args.cmd == "stop-status":
            path = config_path()
            status = stop_status(cfg=load_config(path) if path.exists() else None)
            if args.out:
                args.out.write_bytes((json.dumps(status, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            _print(status)
            return EXIT_OK if status["confirmed_stopped"] else EXIT_UNSAFE
        if args.cmd == "unsynced":
            report = unsynced_report(_cfg_required())
            _print(report)
            return EXIT_OK if report["status"] == "none" else EXIT_UNSAFE
        if args.cmd == "backup-init":
            _print(backup_init(_cfg_required(), create_if_missing=args.create))
            return EXIT_OK
        if args.cmd == "backup":
            code, owner = guard(require_config=True)
            if code != EXIT_OK:
                _print({"backup": "refused", **owner})
                return code
            result = run_backup(_cfg_required())
            _print(result)
            return EXIT_OK if result["backup"] in ("pushed", "up_to_date") else EXIT_BACKUP_FAILED
        if args.cmd == "verify-backup":
            cfg = None if args.remote else _cfg_required()
            remote = args.remote or cfg.backup_remote  # type: ignore[union-attr]
            ref = args.ref if args.remote else cfg.backup_ref  # type: ignore[union-attr]
            with _scratch_dir("kel-verify-") as tmp:
                source = clone_backup(remote, ref, tmp / "clone", ssh_command=cfg.ssh_command if cfg else None)
            _print({"ok": True, "files": len(source.files), "origin": source.origin})
            return EXIT_OK
        if args.cmd == "archive":
            _print(create_archive(REPO_ROOT, args.out, profile=args.profile))
            return EXIT_OK
        if args.cmd == "restore":
            profile = args.profile or ("migration" if args.archive else "backup")
            with _scratch_dir("kel-restore-") as tmp:
                if args.archive:
                    source = read_archive(args.archive, expected_sha256=args.archive_sha256)
                else:
                    cfg = None if args.remote else _cfg_required()
                    remote = args.remote or cfg.backup_remote  # type: ignore[union-attr]
                    ref = args.ref if args.remote else cfg.backup_ref  # type: ignore[union-attr]
                    source = clone_backup(remote, ref, tmp / "clone", ssh_command=cfg.ssh_command if cfg else None)
                plan = plan_restore(source, args.dest, profile)
                out: dict[str, Any] = {"origin": source.origin, "dest": str(args.dest), "profile": profile,
                                       "plan": plan.summary(), "applied": False}
                if args.apply:
                    if not plan.safe:
                        _print(out)
                        return EXIT_UNSAFE
                    with hold_cycle_lock(args.dest):
                        out["verification"] = apply_restore(source, args.dest, plan, profile)
                    out["applied"] = True
                _print(out)
                return EXIT_OK if plan.safe else EXIT_UNSAFE
        if args.cmd == "verify-pins":
            result = verify_pins()
            _print(result)
            return EXIT_OK if result["ok"] else EXIT_VERIFY_FAILED
        if args.cmd == "reproduce-all":
            result = reproduce_all()
            _print({k: v for k, v in result.items() if k != "results"} | {"results": result["results"]})
            return EXIT_OK if result["ok"] else EXIT_VERIFY_FAILED
    except ConfigError as error:
        _print({"error": str(error)})
        return EXIT_CONFIG
    except OwnershipUnverified as error:
        _print({"error": str(error)})
        return EXIT_OWNERSHIP_UNVERIFIED
    except OpsError as error:
        _print({"error": str(error)})
        return EXIT_UNSAFE
    return EXIT_UNSAFE


if __name__ == "__main__":
    sys.exit(main())

"""Private backup of sports research state (separate from, and never altering, the weather backup).

Profile ``sports`` (explicit allowlist, nothing else):

* capture records: ``data/sports/phase2/capture_v2/{accepted,attempts,runs}``
* scores: ``data/sports/phase2/capture_v2_scores/{finalized,runs}``
* protocols: ``research/sports/protocols/*``
* every dependency file named by a capture record (normalized history, crosswalk, inventory),
  checked against the sha256 the record pinned
* exactly the raw evidence the records and scores reference: ``<run_id>/<source>/<kk>/<key>.body``
  (and its ``.meta.json``) under ``raw_capture_v2`` and ``raw_settlement_v1``
* collector provenance logs: ``collector_cycles.jsonl``, ``collector_*.log``, ``task_*.log``,
  ``collector_status.json``, ``collector_state.json``

Never included: credentials and config (``collector.local.json``, ``.env``), locks, dot/temporary files,
dry runs, unreferenced cache bodies.

Destination: the private state remote and SSH command already configured in ``collector.local.json``
(read only), but a separate ref (``refs/heads/sports-state-v2``) and a separate local clone. The weather
backup's ref, clone, allowlist and ownership marker are not touched. ``sports-state-v2`` starts from the last
pushed ``sports-state`` (v1) commit; the v1 clone and its unpushed commits are preserved but no longer written.

Storage (manifest ``kel_sports_state_backup_v2``): files under 32 MiB are stored byte-for-byte at
``state/<rel>``; larger files as gzip pieces of 16 MiB raw bytes at ``state/<rel>.kelchunks/<index>.gz``, with
the sha256 of every stored piece, every raw piece and the whole file in the manifest. Restore reassembles and
verifies all three. Before pushing, every blob the push would upload is checked against a 50 MiB limit.

Scheduling: the backup has its own task (``KalshiEdgeLab-SportsBackup``, every 15 minutes, never inside the
capture task). ``run --if-due`` takes a new snapshot at most hourly (30 minutes after a failed snapshot; at the
next check after a busy lock or an inconsistent snapshot) and, at every check, pushes a commit that an earlier
run could not upload. Scheduled runs skip while ``data/logs/sports/backup_paused.json`` exists (``pause`` /
``resume``). ``backup.lock`` keeps two backup runs from overlapping.

Snapshot consistency. Capture and settlement write only while holding the sports lock, and they write
records, scores, run summaries and evidence by exclusive create followed by an in-place write, never
rewriting them afterwards; collector status files and normalized dependencies are replaced atomically;
logs are appended in place. The sports lock is held only to:

* list the allowlisted record/score/protocol/log files with their size and modification time,
* copy the two small collector status files (refused above 1 MiB, never large files),
* hard-link the known dependency files into a staging directory on the same volume (a link keeps the
  lock-time bytes if the file is later replaced atomically; it does not protect against in-place writes).

Everything else (reading, hashing, compression, Git, upload) happens after the lock is released. Each file
is read with its identity and metadata checked before and after reading against the lock-time listing
(append-only logs: only their lock-time length, whose earlier prefix must still match the previous
backup), and evidence is checked against the sha256 its record pins. Any detected change rejects the
snapshot (``inconsistent_snapshot``) without committing. If hard links cannot be created, linking stops at
the first failure (the reason is reported) and dependencies are read outside the lock with the same checks.
Before committing, every stored file written by the run is re-read and checked against the new manifest.
Research state recorded in the previous backup that has vanished locally refuses the snapshot; the
mirror's delete pass never removes such state.

Pushes wait for the weather cycle lock to be free (the weather backup runs inside that lock), are never
forced, and a remote that has commits this host does not know is reported as a conflict. A failed upload
keeps its commit; the next check retries it without taking the sports lock.
"""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research.weather import collector_ops as CO

from ..core import REPO_ROOT
from ..live import config as LC
from ..scoring import config as SCC
from . import lock as LK

SCHEMA = "kel_sports_state_backup_v2"
READABLE_SCHEMAS = ("kel_sports_state_backup_v1", SCHEMA)
PROFILE = "sports"
SPORTS_REF = "refs/heads/sports-state-v2"
DEFAULT_DIR = Path.home() / ".kel" / "sports-state-backup-v2"
# v1 history: its unpushed local commits embed a >100 MB blob, so v2 starts from the last pushed v1 commit.
LEGACY_REF = "refs/heads/sports-state"
LEGACY_DIR = Path.home() / ".kel" / "sports-state-backup"
STATE_DIR = "state"
MANIFEST = "MANIFEST.json"
INTERVAL = timedelta(hours=1)
FAILURE_BACKOFF = timedelta(minutes=30)
CHECK_INTERVAL = timedelta(minutes=15)
BACKUP_TASK_NAME = "KalshiEdgeLab-SportsBackup"
# Snapshot outcomes retried at the next 15-minute check rather than after FAILURE_BACKOFF.
SNAPSHOT_RETRY_NEXT_CHECK = ("skipped_busy", "inconsistent_snapshot")
SNAPSHOT_FAILURES = ("failed", "skipped_busy", "inconsistent_snapshot")
SMALL_COPY_MAX = 1024 * 1024
DEFAULT_MBPS = 40.0

# Files at or above CHUNK_THRESHOLD are stored as gzip-compressed pieces of CHUNK_RAW_BYTES raw bytes under
# ``state/<rel>.kelchunks/<index>.gz``; the manifest pins the sha256 of every piece and of the whole file.
CHUNK_FORMAT = "kel_gzip_chunks_v1"
CHUNK_SUFFIX = ".kelchunks"
CHUNK_THRESHOLD = 32 * 1024 * 1024
CHUNK_RAW_BYTES = 16 * 1024 * 1024
# Refuse to push any pending blob above this (GitHub rejects blobs over 100 MB).
MAX_BLOB_BYTES = 50 * 1024 * 1024
GIT_ADD_TIMEOUT_S = 3600

WRITE_ONCE = CO.WRITE_ONCE
APPEND_ONLY = CO.APPEND_ONLY
MUTABLE = CO.MUTABLE

CAPTURE_REL = "data/sports/phase2/capture_v2"
SCORE_REL = "data/sports/phase2/capture_v2_scores"
PROTOCOL_REL = "research/sports/protocols"
CAPTURE_EVIDENCE_REL = "data/cache/sports/raw_capture_v2"
SETTLE_EVIDENCE_REL = "data/cache/sports/raw_settlement_v1"
LOG_REL = "data/logs/sports"
STAGING_REL = f"{LOG_REL}/.backup_staging"
STATE_SUBDIRS = (f"{CAPTURE_REL}/accepted", f"{CAPTURE_REL}/attempts", f"{CAPTURE_REL}/runs",
                 f"{SCORE_REL}/finalized", f"{SCORE_REL}/runs", PROTOCOL_REL)
LOG_PATTERNS = ("collector_cycles.jsonl", "collector_*.log", "task_*.log",
                "collector_status.json", "collector_state.json")
MUTABLE_LOGS = {f"{LOG_REL}/collector_status.json", f"{LOG_REL}/collector_state.json"}
EXCLUDED_NAMES = {"collector.local.json", ".env", "cycle.lock", "backup.lock"}
EXCLUDED_SUFFIXES = (".tmp", ".lock", ".flock", ".pyc", ".swp")

EXIT_OK = 0
EXIT_FAILED = 30
EXIT_CONFLICT = 31
EXIT_VERIFY_FAILED = 33


STATUS_PATH = REPO_ROOT / LOG_REL / "backup_status.json"


def status_path(repo_root: Path | None = None) -> Path:
    return STATUS_PATH if repo_root is None else repo_root / LOG_REL / "backup_status.json"


PAUSE_PATH = REPO_ROOT / LOG_REL / "backup_paused.json"


def pause_path(repo_root: Path | None = None) -> Path:
    return PAUSE_PATH if repo_root is None else repo_root / LOG_REL / "backup_paused.json"


def read_pause(repo_root: Path | None = None) -> dict | None:
    """Operator marker that makes scheduled (``--if-due``) backups skip; manual runs are unaffected."""
    try:
        body = json.loads(pause_path(repo_root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"reason": "unreadable pause marker"}
    return body if isinstance(body, dict) else {"reason": "malformed pause marker"}


def set_pause(reason: str, repo_root: Path | None = None, now: datetime | None = None) -> dict:
    body = {"paused_at": _iso(now or datetime.now(timezone.utc)), "reason": reason}
    path = pause_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return body


def clear_pause(repo_root: Path | None = None) -> bool:
    try:
        pause_path(repo_root).unlink()
        return True
    except FileNotFoundError:
        return False


def backup_lock_path(repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / LOG_REL / "backup.lock"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Configuration (reads the weather collector config; never writes it)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SportsBackupConfig:
    collector_id: str
    remote: str
    ref: str
    backup_dir: Path
    ssh_command: str | None
    git_timeout_s: int = 60


def load_config(path: Path | None = None, backup_dir: Path | None = None) -> SportsBackupConfig:
    cfg = CO.load_config(path or CO.config_path())
    if not cfg.backup_remote:
        raise CO.ConfigError("collector config has no backup_remote; sports backup needs the private state remote")
    out = SportsBackupConfig(collector_id=cfg.collector_id, remote=cfg.backup_remote, ref=SPORTS_REF,
                             backup_dir=Path(backup_dir or DEFAULT_DIR), ssh_command=cfg.ssh_command,
                             git_timeout_s=cfg.git_timeout_s)
    if out.ref in (cfg.backup_ref, cfg.ownership_ref):
        raise CO.ConfigError(f"sports backup ref {out.ref} collides with a weather ref")
    if cfg.backup_dir and Path(cfg.backup_dir).resolve() == out.backup_dir.resolve():
        raise CO.ConfigError("sports backup clone must not share the weather backup clone")
    if out.backup_dir.resolve() == LEGACY_DIR.resolve():
        raise CO.ConfigError(f"{LEGACY_DIR} holds the preserved v1 history and is not written to")
    return out


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

def is_excluded(name: str) -> bool:
    return name in EXCLUDED_NAMES or name.startswith(".") or name.endswith(EXCLUDED_SUFFIXES)


def classify(rel: str) -> str:
    if rel in MUTABLE_LOGS:
        return MUTABLE
    if rel.startswith(f"{LOG_REL}/"):
        return APPEND_ONLY
    return WRITE_ONCE


def is_dependency(rel: str) -> bool:
    return not rel.startswith(tuple(f"{d}/" for d in (CAPTURE_REL, SCORE_REL, PROTOCOL_REL, CAPTURE_EVIDENCE_REL,
                                                       SETTLE_EVIDENCE_REL, LOG_REL)))


@dataclass(frozen=True)
class Stat:
    size: int
    mtime_ns: int
    ino: int = 0  # 0 when the listing did not provide a file identity


def _stat_of(st: os.stat_result) -> Stat:
    return Stat(st.st_size, st.st_mtime_ns, st.st_ino)


def _scan(root: Path, rel_dir: str, out: dict[str, Stat]) -> None:
    """Files under ``rel_dir`` with size and mtime from the directory scan itself (no per-file open)."""
    stack = [(str(root / rel_dir), rel_dir)]
    while stack:
        path, rel = stack.pop()
        try:
            it = os.scandir(path)
        except (FileNotFoundError, NotADirectoryError):
            continue
        with it:
            for e in it:
                if is_excluded(e.name):
                    continue
                child = f"{rel}/{e.name}"
                if e.is_dir(follow_symlinks=False):
                    stack.append((e.path, child))
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    out[child] = Stat(st.st_size, st.st_mtime_ns)


def list_state(root: Path = REPO_ROOT) -> dict[str, Stat]:
    """Allowlisted records, scores, protocols and provenance logs (not evidence or dependencies, which the
    records name). Logs get a full stat so their file identity is known."""
    out: dict[str, Stat] = {}
    for d in STATE_SUBDIRS:
        _scan(root, d, out)
    log_dir = root / LOG_REL
    if log_dir.is_dir():
        with os.scandir(log_dir) as it:
            for e in it:
                if (e.is_file(follow_symlinks=False) and not is_excluded(e.name)
                        and any(fnmatch.fnmatch(e.name, p) for p in LOG_PATTERNS)):
                    out[f"{LOG_REL}/{e.name}"] = _stat_of(os.stat(e.path))
    return out


def _evidence_refs(root_rel: str, run_id: str, evidence: list[dict]) -> tuple[dict[str, str | None], list[str]]:
    bodies: dict[str, str | None] = {}
    metas = []
    for e in evidence:
        stem = f"{root_rel}/{run_id}/{e['source']}/{e['key'][:2]}/{e['key']}"
        bodies[f"{stem}.body"] = e.get("sha256")
        metas.append(f"{stem}.meta.json")
    return bodies, metas


def _names_refs(rel: str) -> bool:
    return rel.endswith(".json") and rel.startswith((f"{CAPTURE_REL}/accepted/", f"{CAPTURE_REL}/attempts/",
                                                    f"{SCORE_REL}/finalized/"))


def _refs_of(rel: str, data: bytes) -> dict | None:
    """Files a record or finalized score names: dependencies (with pinned sha256), evidence bodies (with
    pinned sha256), evidence meta files and a score's source record."""
    if rel.startswith(f"{SCORE_REL}/finalized/"):
        sc = json.loads(data)
        bodies, metas = _evidence_refs(SETTLE_EVIDENCE_REL, sc["settlement_run_id"], sc.get("settlement_evidence") or [])
        return {"kind": "score", "deps": {}, "bodies": bodies, "metas": metas, "source_record": sc.get("source_record")}
    rec = json.loads(data)
    bodies, metas = _evidence_refs(CAPTURE_EVIDENCE_REL, rec["run_id"], rec.get("evidence") or [])
    return {"kind": "record", "deps": rec.get("dependencies") or {}, "bodies": bodies, "metas": metas}


@dataclass
class Collected:
    files: dict[str, Path]
    missing: list[str]
    dependency_drift: list[str]
    counts: dict[str, int]
    pins: dict[str, str] = field(default_factory=dict)  # evidence body -> sha256 pinned by its record or score
    deps: dict[str, str | None] = field(default_factory=dict)  # dependency -> first pinned sha256


class Hasher:
    """sha256 per file, reused while (size, mtime_ns) is unchanged. The cache lives inside the clone's
    ``.git`` directory (never committed); ``verify-restore`` always rehashes everything."""

    def __init__(self, cache_path: Path | None = None):
        self.path = cache_path
        self.cache: dict[str, list] = {}
        if cache_path and cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.cache = {}

    def sha(self, path: Path, key: str | None = None) -> str:
        st = path.stat()
        key = key or str(path)
        hit = self.cache.get(key)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return hit[2]
        digest = CO.sha256_bytes(path.read_bytes())
        self.cache[key] = [st.st_size, st.st_mtime_ns, digest]
        return digest

    def lookup(self, key: str, st: Stat) -> str | None:
        hit = self.cache.get(key)
        return hit[2] if hit and hit[0] == st.size and hit[1] == st.mtime_ns else None

    def store(self, key: str, st: Stat, digest: str) -> None:
        self.cache[key] = [st.size, st.mtime_ns, digest]

    def save(self) -> None:
        if self.path:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.cache), encoding="utf-8")
            os.replace(tmp, self.path)


def resolve_state(root: Path, listing: dict[str, Stat], refs_of: Callable[[str], dict | None]) -> Collected:
    """The allowlist from a listing plus the files its records and scores name. ``refs_of(rel)`` returns the
    references of a record or finalized score (None for other files)."""
    rels = set(listing)
    counts = {"records": 0, "scores": 0, "dependencies": 0, "capture_evidence": 0, "settlement_evidence": 0}
    referenced: set[str] = set()
    optional: set[str] = set()
    deps: dict[str, str | None] = {}
    pins: dict[str, str] = {}
    drift: set[str] = set()
    for rel in sorted(listing):
        refs = refs_of(rel) if _names_refs(rel) else None
        if refs is None:
            continue
        if refs["kind"] == "record":
            counts["records"] += 1
            counts["capture_evidence"] += len(refs["bodies"])
            for path, sha in refs["deps"].items():
                if path in deps and deps[path] != sha:
                    drift.add(path)
                deps.setdefault(path, sha)
        else:
            counts["scores"] += 1
            counts["settlement_evidence"] += len(refs["bodies"])
            src = refs.get("source_record")
            if src and src not in rels:
                referenced.add(src)
        referenced.update(refs["bodies"])
        optional.update(refs["metas"])
        pins.update((k, v) for k, v in refs["bodies"].items() if v)
    referenced.update(deps)
    counts["dependencies"] = len(deps)
    missing = sorted(r for r in referenced if not (root / r).is_file())
    rels.update(r for r in referenced if (root / r).is_file())
    rels.update(r for r in optional if (root / r).is_file())
    bad = sorted(r for r in rels if any(is_excluded(part) or part.endswith(CHUNK_SUFFIX) for part in r.split("/"))
                 or r.startswith("/") or ".." in r.split("/"))
    if bad:
        raise CO.OpsError(f"refusing excluded or unsafe paths in sports backup: {bad[:10]}")
    return Collected(files={r: root / r for r in sorted(rels)}, missing=missing, dependency_drift=sorted(drift),
                     counts=counts, pins=pins, deps=deps)


def collect_state(root: Path = REPO_ROOT, hasher: Hasher | None = None) -> Collected:
    """Allowlisted sports state under ``root`` read live (no lock): rel path -> absolute path, plus integrity
    findings. Used by ``plan``; ``run_backup`` resolves the same allowlist from a locked listing."""
    col = resolve_state(root, list_state(root), lambda rel: _refs_of(rel, (root / rel).read_bytes()))
    hasher = hasher or Hasher()
    drift = set(col.dependency_drift)
    for path, sha in col.deps.items():
        p = root / path
        if sha is not None and p.is_file() and hasher.sha(p, f"src:{path}") != sha:
            drift.add(path)
    col.dependency_drift = sorted(drift)
    return col


# ---------------------------------------------------------------------------
# Backup run
# ---------------------------------------------------------------------------

def _bgit(cfg: SportsBackupConfig, args: list[str], **kw) -> subprocess.CompletedProcess:
    return CO._git(args, cwd=cfg.backup_dir, ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s, **kw)


def backup_init(cfg: SportsBackupConfig, *, create_if_missing: bool = False, base_ref: str | None = None) -> dict:
    """Clone the sports ref, or explicitly start it (optionally on top of ``base_ref``'s pushed tip, so only
    new content is uploaded). Never adopts a divergent history."""
    if (cfg.backup_dir / ".git").exists():
        raise CO.OpsError(f"{cfg.backup_dir} is already initialized")
    remote_sha = CO.remote_ref_sha(cfg.remote, cfg.ref, ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s)
    cfg.backup_dir.parent.mkdir(parents=True, exist_ok=True)
    base_sha = None
    if remote_sha is not None:
        CO._git(["clone", "-q", "--branch", CO._branch(cfg.ref), "--single-branch", cfg.remote, str(cfg.backup_dir)],
                ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s * 10)
    elif base_ref and create_if_missing:
        if base_ref in (cfg.ref,):
            raise CO.OpsError("base ref must differ from the backup ref")
        base_sha = CO.remote_ref_sha(cfg.remote, base_ref, ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s)
        if base_sha is None:
            raise CO.OpsError(f"base ref {base_ref} does not exist on the backup remote")
        CO._git(["clone", "-q", "--branch", CO._branch(base_ref), "--single-branch", cfg.remote, str(cfg.backup_dir)],
                ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s * 30)
        _bgit(cfg, ["checkout", "-q", "-b", CO._branch(cfg.ref)])
    elif create_if_missing:
        CO._git(["init", "-q", "-b", CO._branch(cfg.ref), str(cfg.backup_dir)])
        _bgit(cfg, ["remote", "add", "origin", cfg.remote])
        (cfg.backup_dir / ".gitattributes").write_bytes(CO.BINARY_ATTRIBUTES.encode("ascii"))
        _bgit(cfg, ["add", ".gitattributes"])
        _bgit(cfg, ["commit", "-q", "-m", "sports state backup: binary attributes"], identity=cfg.collector_id)
    else:
        raise CO.OpsError(f"{cfg.ref} does not exist on the backup remote; pass --create to start it")
    configure_clone(cfg)
    _check_attributes(cfg)
    return {"backup_dir": str(cfg.backup_dir), "ref": cfg.ref, "remote_sha": remote_sha,
            "base_ref": base_ref if base_sha else None, "base_sha": base_sha}


CLONE_CONFIG = (("core.autocrlf", "false"), ("core.safecrlf", "false"),
                ("pack.threads", "1"), ("core.compression", "1"))


def configure_clone(cfg: SportsBackupConfig) -> None:
    """Byte-exact storage, and packing light enough not to starve the 5-minute collector."""
    for key, value in CLONE_CONFIG:
        _bgit(cfg, ["config", key, value])


def _check_attributes(cfg: SportsBackupConfig) -> None:
    attrs = cfg.backup_dir / ".gitattributes"
    if not attrs.exists() or attrs.read_bytes() != CO.BINARY_ATTRIBUTES.encode("ascii"):
        raise CO.OpsError("sports backup clone lacks the '* -text' attributes; refusing to use it")


def chunk_name(rel: str, index: int) -> str:
    return f"{rel}{CHUNK_SUFFIX}/{index:05d}.gz"


def stored_paths(rel: str, meta: dict) -> list[str]:
    """Paths under ``state/`` that hold ``rel`` (one plain file, or its chunk files)."""
    storage = meta.get("storage")
    if not storage:
        return [rel]
    return [chunk_name(rel, i) for i in range(len(storage["chunks"]))]


def _chunks_intact(state_dir: Path, rel: str, storage: dict, hasher: Hasher) -> bool:
    for i, c in enumerate(storage["chunks"]):
        p = state_dir / chunk_name(rel, i)
        if not p.is_file() or p.stat().st_size != c["stored_size"] or hasher.sha(p, f"dst:{chunk_name(rel, i)}") != c["stored_sha256"]:
            return False
    return True


def _write_chunked(state_dir: Path, rel: str, data: bytes, old: dict | None, hasher: Hasher) -> dict:
    """Store ``data`` as gzip pieces; pieces whose raw bytes are unchanged since ``old`` are kept as they are."""
    prior = (old or {}).get("chunks") or []
    chunks = []
    for i, offset in enumerate(range(0, len(data), CHUNK_RAW_BYTES)):
        piece = data[offset:offset + CHUNK_RAW_BYTES]
        sha = CO.sha256_bytes(piece)
        name = chunk_name(rel, i)
        dest = state_dir / name
        prev = prior[i] if i < len(prior) else None
        if (prev and prev["sha256"] == sha and dest.is_file() and dest.stat().st_size == prev["stored_size"]
                and hasher.sha(dest, f"dst:{name}") == prev["stored_sha256"]):
            chunks.append(prev)
            continue
        blob = gzip.compress(piece, compresslevel=6, mtime=0)
        if len(blob) > MAX_BLOB_BYTES:
            raise CO.OpsError(f"{rel}: chunk {i} is {len(blob)} bytes after compression (limit {MAX_BLOB_BYTES})")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        hasher.sha(dest, f"dst:{name}")
        chunks.append({"sha256": sha, "size": len(piece), "stored_sha256": CO.sha256_bytes(blob),
                       "stored_size": len(blob)})
    return {"format": CHUNK_FORMAT, "raw_chunk_bytes": CHUNK_RAW_BYTES, "chunks": chunks}


class Inconsistent(CO.OpsError):
    """A file changed between the locked listing and the read, or the staged clone does not match the
    manifest: the snapshot is rejected and nothing is committed."""


class SnapshotRefused(CO.OpsError):
    pass


class Throttle:
    """Caps the rate at which the unlocked phase reads source bytes (``mbps`` <= 0 or None: unlimited)."""

    def __init__(self, mbps: float | None = None):
        self.rate = mbps * 1e6 if mbps and mbps > 0 else None
        self.t0 = time.monotonic()
        self.done = 0

    def consume(self, n: int) -> None:
        if not self.rate:
            return
        self.done += n
        ahead = self.done / self.rate - (time.monotonic() - self.t0)
        if ahead > 0:
            time.sleep(min(ahead, 5.0))


@dataclass
class Item:
    """How to read one file of the snapshot. ``data``: bytes copied under the lock. ``expect``: the lock-time
    stat the file must still have (for ``limit`` items only the identity, and at least ``limit`` bytes).
    ``limit``: append-only log, read only its lock-time length. ``staged``: ``path`` is a hard link made
    under the lock."""
    path: Path | None = None
    data: bytes | None = None
    expect: Stat | None = None
    limit: int | None = None
    staged: bool = False


def _same(a: Stat, b: Stat) -> bool:
    return a.size == b.size and a.mtime_ns == b.mtime_ns and (not a.ino or not b.ino or a.ino == b.ino)


def _check_expect(rel: str, item: Item, st: Stat) -> None:
    e = item.expect
    if e is None:
        return
    if item.limit is not None:
        if st.size < item.limit or (e.ino and st.ino and st.ino != e.ino):
            raise Inconsistent(f"{rel}: append-only file shrank or was replaced after the snapshot listing")
    elif not _same(st, e):
        raise Inconsistent(f"{rel}: changed after the snapshot listing (size, mtime or file identity)")


def _read_item(rel: str, item: Item, throttle: Throttle | None = None) -> tuple[bytes, Stat | None]:
    """Bytes of ``item``, with identity and metadata checked before and after reading."""
    if item.data is not None:
        return item.data, None
    assert item.path is not None
    try:
        with open(item.path, "rb") as f:
            before = _stat_of(os.fstat(f.fileno()))
            _check_expect(rel, item, before)
            data = f.read() if item.limit is None else f.read(item.limit)
            after = _stat_of(os.fstat(f.fileno()))
        now = _stat_of(os.stat(item.path))
    except FileNotFoundError:
        raise Inconsistent(f"{rel}: disappeared during the snapshot") from None
    if item.limit is None:
        if not (_same(before, after) and _same(after, now)) or len(data) != before.size:
            raise Inconsistent(f"{rel}: changed while being read")
    elif len(data) != item.limit or not (before.ino == after.ino == now.ino) or now.size < item.limit:
        raise Inconsistent(f"{rel}: append-only file changed identity or shrank while being read")
    if throttle:
        throttle.consume(len(data))
    return data, after


def _item_meta(rel: str, item: Item, hasher: Hasher, throttle: Throttle | None) -> tuple[str, int]:
    """(sha256, size) of the snapshot version of ``rel``; reuses the hash cache when the file's current stat
    matches the lock-time stat (and therefore the bytes hashed before)."""
    if item.data is not None:
        return CO.sha256_bytes(item.data), len(item.data)
    if item.limit is not None:
        data, _ = _read_item(rel, item, throttle)
        return CO.sha256_bytes(data), len(data)
    assert item.path is not None
    try:
        st = _stat_of(os.stat(item.path))
    except FileNotFoundError:
        raise Inconsistent(f"{rel}: disappeared during the snapshot") from None
    _check_expect(rel, item, st)
    key = f"src:{rel}"
    digest = hasher.lookup(key, st)
    if digest is None:
        data, after = _read_item(rel, item, throttle)
        digest = CO.sha256_bytes(data)
        hasher.store(key, after or st, digest)
    return digest, st.size


def _owner_rel(stored: str) -> str:
    marker = f"{CHUNK_SUFFIX}/"
    return stored.split(marker, 1)[0] if marker in stored else stored


def _mirror(cfg: SportsBackupConfig, items: dict[str, Item], previous: dict, hasher: Hasher,
            throttle: Throttle | None = None, vanished: list[str] | None = None) -> tuple[dict, set[str]]:
    """Write the snapshot into the clone; returns the manifest and the files whose stored bytes were written.
    Unchanged files are not read (hash cache keyed by the lock-time size and mtime). Refuses, before writing
    anything, changed write-once files, shrunk or rewritten logs and ``vanished`` backed-up state."""
    state_dir = cfg.backup_dir / STATE_DIR
    manifest: dict[str, dict] = {}
    changed_once = []
    for rel, item in items.items():
        sha, size = _item_meta(rel, item, hasher, throttle)
        meta = {"sha256": sha, "size": size, "class": classify(rel)}
        old = previous.get(rel)
        if old and ((meta["class"] == WRITE_ONCE and old["sha256"] != meta["sha256"])
                    or (meta["class"] == APPEND_ONLY and meta["size"] < old["size"])):
            changed_once.append(rel)
        manifest[rel] = meta
    if changed_once:
        raise CO.OpsError(f"write-once files changed or append-only files shrank locally: {changed_once[:20]}")
    if vanished:
        raise CO.OpsError(f"backed-up research state vanished locally {_vanish_summary(vanished)}; refusing to mirror")
    rewritten = []
    for rel, meta in manifest.items():
        old = previous.get(rel)
        if meta["class"] == APPEND_ONLY and old and old["size"] and meta["sha256"] != old["sha256"]:
            data, _ = _read_item(rel, items[rel], throttle)
            if CO.sha256_bytes(data[:old["size"]]) != old["sha256"]:
                rewritten.append(rel)
    if rewritten:
        raise CO.OpsError(f"append-only files were rewritten, not appended, since the previous backup: {rewritten[:20]}")
    written: set[str] = set()

    def snapshot_bytes(rel: str, meta: dict) -> bytes:
        data, _ = _read_item(rel, items[rel], throttle)
        if CO.sha256_bytes(data) != meta["sha256"] or len(data) != meta["size"]:
            raise Inconsistent(f"{rel}: bytes differ from the hash taken earlier in this snapshot")
        return data

    for rel, meta in manifest.items():
        if meta["size"] >= CHUNK_THRESHOLD:
            old = previous.get(rel)
            known = old["storage"] if old and (old.get("storage") or {}).get("format") == CHUNK_FORMAT else None
            if known and old["sha256"] == meta["sha256"] and _chunks_intact(state_dir, rel, known, hasher):
                meta["storage"] = known
                continue
            meta["storage"] = _write_chunked(state_dir, rel, snapshot_bytes(rel, meta), known, hasher)
            written.add(rel)
            continue
        dest = state_dir / rel
        if dest.exists() and dest.stat().st_size == meta["size"] and hasher.sha(dest, f"dst:{rel}") == meta["sha256"]:
            continue
        data = snapshot_bytes(rel, meta)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        hasher.sha(dest, f"dst:{rel}")
        written.add(rel)
    keep = {p for rel, meta in manifest.items() for p in stored_paths(rel, meta)}
    if state_dir.is_dir():
        for path in sorted(state_dir.rglob("*"), reverse=True):
            stored = path.relative_to(state_dir).as_posix()
            if path.is_file() and stored not in keep:
                owner = _owner_rel(stored)
                if owner not in manifest and (previous.get(owner) or {}).get("class") not in (None, MUTABLE):
                    raise CO.OpsError(f"refusing to delete backed-up research state {owner} from the clone")
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    body = {"schema": SCHEMA, "profile": PROFILE, "files": manifest}
    (cfg.backup_dir / MANIFEST).write_bytes((json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return manifest, written


def _verify_clone(cfg: SportsBackupConfig, manifest: dict, written: set[str]) -> None:
    """The staged clone holds exactly the manifest's stored files, and every file written by this run
    reassembles to the manifest's bytes."""
    state_dir = cfg.backup_dir / STATE_DIR
    expected = {p for rel, meta in manifest.items() for p in stored_paths(rel, meta)}
    on_disk = ({p.relative_to(state_dir).as_posix() for p in state_dir.rglob("*") if p.is_file()}
               if state_dir.is_dir() else set())
    if on_disk != expected:
        raise Inconsistent(f"staged clone does not match the manifest: unlisted {sorted(on_disk - expected)[:5]}, "
                           f"missing {sorted(expected - on_disk)[:5]}")
    for rel in sorted(written):
        try:
            read_stored(state_dir, rel, manifest[rel])
        except (CO.OpsError, OSError, EOFError, zlib.error) as error:
            raise Inconsistent(f"staged bytes do not match the manifest: {error}") from None
    body = json.loads((cfg.backup_dir / MANIFEST).read_text(encoding="utf-8"))
    if body.get("files") != manifest:
        raise Inconsistent("written MANIFEST.json differs from the snapshot manifest")


@dataclass
class Snapshot:
    listing: dict[str, Stat]
    small: dict[str, bytes]
    staged: dict[str, tuple[Path, Stat]]
    staging: str
    staging_fallback_reason: str | None
    timings: dict


# Test seams: called after the sports lock is released and before the staged clone is verified.
HOOKS: dict[str, Callable] = {}


def _hook(name: str, *args) -> None:
    fn = HOOKS.get(name)
    if fn:
        fn(*args)


def take_snapshot(repo_root: Path, staging_dir: Path, known_deps: list[str], lock_path: Path) -> Snapshot:
    """The only work done under the sports lock: list, copy the small status files, hard-link dependencies."""
    t_req = time.monotonic()
    with LK.sports_lock("backup:snapshot", lock_path):
        t0 = time.monotonic()
        listing = list_state(repo_root)
        t1 = time.monotonic()
        small: dict[str, bytes] = {}
        for rel in sorted(MUTABLE_LOGS & listing.keys()):
            if listing[rel].size > SMALL_COPY_MAX:
                raise SnapshotRefused(f"{rel} is {listing[rel].size} bytes; only small status files are copied "
                                      "under the sports lock")
            small[rel] = (repo_root / rel).read_bytes()
        t2 = time.monotonic()
        staged: dict[str, tuple[Path, Stat]] = {}
        mode, reason = "hardlink", None
        for rel in known_deps:
            src = repo_root / rel
            dest = staging_dir / rel
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.link(src, dest)
            except FileNotFoundError:
                if not src.exists():
                    continue
                mode, reason = "verified_read", f"hard link failed for {rel}: FileNotFoundError"
                break
            except OSError as error:
                mode, reason = "verified_read", f"hard link failed for {rel}: {type(error).__name__}: {error}"
                break
            staged[rel] = (dest, _stat_of(os.stat(dest)))
        t3 = time.monotonic()
    held = time.monotonic() - t0
    if mode != "hardlink":
        staged = {}
        shutil.rmtree(staging_dir, ignore_errors=True)
    timings = {"lock_wait_s": round(t0 - t_req, 3), "lock_held_s": round(held, 3), "list_s": round(t1 - t0, 3),
               "copy_status_s": round(t2 - t1, 3), "link_s": round(t3 - t2, 3), "listed_files": len(listing),
               "hard_links": len(staged)}
    return Snapshot(listing=listing, small=small, staged=staged, staging=mode, staging_fallback_reason=reason,
                    timings=timings)


def snapshot_items(repo_root: Path, snap: Snapshot, files: dict[str, Path]) -> dict[str, Item]:
    items: dict[str, Item] = {}
    for rel, path in files.items():
        if rel in snap.small:
            items[rel] = Item(data=snap.small[rel])
        elif rel in snap.staged:
            staged_path, st = snap.staged[rel]
            items[rel] = Item(path=staged_path, expect=st, staged=True)
        elif rel in snap.listing and classify(rel) == APPEND_ONLY:
            items[rel] = Item(path=path, expect=snap.listing[rel], limit=snap.listing[rel].size)
        elif rel in snap.listing:
            items[rel] = Item(path=path, expect=snap.listing[rel])
        else:
            items[rel] = Item(path=path)  # evidence or a dependency: immutable; checked by stat around the read
    return items


class RefsCache:
    """References parsed from records and scores, keyed by (size, mtime); kept in the clone's ``.git``."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data: dict[str, list] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, rel: str, st: Stat) -> dict | None:
        hit = self.data.get(rel)
        return hit[2] if hit and hit[0] == st.size and hit[1] == st.mtime_ns else None

    def put(self, rel: str, st: Stat, refs: dict) -> None:
        self.data[rel] = [st.size, st.mtime_ns, refs]

    def deps(self) -> set[str]:
        return {p for _, _, refs in self.data.values() for p in refs.get("deps", {})}

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(tmp, self.path)


def _head_manifest(cfg: SportsBackupConfig) -> dict:
    """The committed manifest (not the working tree, which a rejected run may have left half-written)."""
    if CO._git_rc(cfg.backup_dir, ["cat-file", "-e", "HEAD:" + MANIFEST]) != 0:
        return {}
    out = CO._git(["show", "HEAD:" + MANIFEST], cwd=cfg.backup_dir, timeout=GIT_ADD_TIMEOUT_S).stdout
    return json.loads(out)["files"]


def _reset_clone(cfg: SportsBackupConfig) -> None:
    """Return the clone's working tree to its last commit; commits (pushed or not) are never touched."""
    try:
        CO._git(["reset", "-q", "--hard", "HEAD"], cwd=cfg.backup_dir, timeout=GIT_ADD_TIMEOUT_S)
        CO._git(["clean", "-fdq", "--", STATE_DIR, MANIFEST], cwd=cfg.backup_dir, timeout=GIT_ADD_TIMEOUT_S)
    except CO.OpsError:
        pass


_VANISH_KINDS = ((f"{CAPTURE_REL}/accepted/", "accepted records"), (f"{CAPTURE_REL}/attempts/", "attempt records"),
                 (f"{SCORE_REL}/finalized/", "finalized scores"), (CAPTURE_EVIDENCE_REL + "/", "capture evidence"),
                 (SETTLE_EVIDENCE_REL + "/", "settlement evidence"))


def _vanish_summary(vanished: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for rel in vanished:
        kind = next((k for prefix, k in _VANISH_KINDS if rel.startswith(prefix)), "other")
        out[kind] = out.get(kind, 0) + 1
    return out


def _weather_lock_held(repo_root: Path) -> bool:
    return (repo_root / CO.STATE_LOG_DIR / "cycle.lock").exists()


def _write_status(repo_root: Path, result: dict) -> None:
    path = status_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_status(repo_root: Path | None = None) -> dict | None:
    try:
        return json.loads(status_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def is_due(repo_root: Path | None = None, now: datetime | None = None, interval: timedelta = INTERVAL,
           backoff: timedelta = FAILURE_BACKOFF) -> bool:
    """Whether a new snapshot is due: hourly after the last committed snapshot; at the next check after a busy
    sports lock or an inconsistent snapshot; ``backoff`` after any other snapshot failure. Upload retries of an
    existing commit are decided separately (``has_unpushed``)."""
    st = read_status(repo_root)
    now = now or datetime.now(timezone.utc)
    if not st or not st.get("at"):
        return True
    snap = st.get("snapshot")
    if snap in SNAPSHOT_RETRY_NEXT_CHECK:
        return True
    if snap in SNAPSHOT_FAILURES:
        return now - _parse(st["at"]) >= backoff
    last = st.get("last_snapshot_at") or st["at"]
    return now - _parse(last) >= interval


def local_head(cfg: SportsBackupConfig) -> str | None:
    try:
        return CO._git(["rev-parse", "HEAD"], cwd=cfg.backup_dir, timeout=cfg.git_timeout_s).stdout.strip()
    except CO.OpsError:
        return None


def has_unpushed(cfg: SportsBackupConfig, repo_root: Path | None = None) -> bool:
    """Local check (no network): the clone's HEAD is not the last commit this host saw on the remote."""
    head = local_head(cfg)
    return head is not None and head != (read_status(repo_root) or {}).get("last_pushed_head")


def pending_blobs(cfg: SportsBackupConfig, remote_sha: str | None) -> list[dict]:
    """Every blob a push of HEAD would have to upload (not reachable from the remote ref or any
    remote-tracking ref), with its size and a path naming it."""
    args = ["rev-list", "--objects", "HEAD", "--not", "--remotes=origin"] + ([remote_sha] if remote_sha else [])
    listing = CO._git(args, cwd=cfg.backup_dir, timeout=GIT_ADD_TIMEOUT_S).stdout
    names: dict[str, str] = {}
    for line in listing.splitlines():
        sha, _, name = line.partition(" ")
        if sha:
            names.setdefault(sha, name)
    if not names:
        return []
    proc = subprocess.run(["git", *CO.GIT_SAFE_CONFIG, "cat-file", "--batch-check=%(objecttype) %(objectname) %(objectsize)"],
                          cwd=cfg.backup_dir, input="\n".join(names) + "\n", capture_output=True, text=True,
                          encoding="utf-8", timeout=GIT_ADD_TIMEOUT_S, check=True)
    out = []
    for line in proc.stdout.splitlines():
        kind, sha, size = line.split(" ")
        if kind == "blob":
            out.append({"sha": sha, "size": int(size), "path": names.get(sha, "")})
    return out


def _push_ref(cfg: SportsBackupConfig, head: str) -> None:
    CO._git(["push", "-q", "origin", f"{head}:{cfg.ref}"], cwd=cfg.backup_dir, ssh_command=cfg.ssh_command,
            timeout=GIT_ADD_TIMEOUT_S)


def _push_stage(cfg: SportsBackupConfig, repo_root: Path, head: str, result: dict) -> str:
    """Upload ``head`` without force and without any sports lock; returns the push status."""
    if _weather_lock_held(repo_root):
        result["push_reason"] = "weather cycle/backup lock held; commit kept locally"
        return "push_deferred"
    try:
        remote_sha = CO.remote_ref_sha(cfg.remote, cfg.ref, ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s)
    except CO.GitError as error:
        result["push_error"] = str(error)
        return "push_failed"
    if remote_sha == head:
        return "up_to_date"
    if remote_sha is not None:
        try:
            _bgit(cfg, ["fetch", "-q", "origin", cfg.ref])
            known = CO._git_rc(cfg.backup_dir, ["merge-base", "--is-ancestor", remote_sha, head]) == 0
        except CO.GitError as error:
            result["push_error"] = str(error)
            return "push_failed"
        if not known:
            result.update(remote_sha=remote_sha, push_error="remote sports backup has commits not in this host's "
                                                            "history; not merging, not forcing")
            return "conflict"
    try:
        pending = pending_blobs(cfg, remote_sha)
    except (CO.OpsError, OSError, subprocess.SubprocessError, ValueError) as error:
        result["push_error"] = f"could not list pending blobs: {error}"
        return "push_failed"
    oversized = sorted((b for b in pending if b["size"] > MAX_BLOB_BYTES), key=lambda b: -b["size"])
    result.update(pending_blobs=len(pending), pending_blob_bytes=sum(b["size"] for b in pending),
                  largest_pending_blob=max((b["size"] for b in pending), default=0))
    if oversized:
        result.update(oversized=oversized[:20],
                      push_error=f"{len(oversized)} pending blob(s) exceed {MAX_BLOB_BYTES} bytes; not pushing")
        return "blocked_oversized"
    t0 = time.monotonic()
    try:
        _push_ref(cfg, head)
    except CO.GitError as error:
        result["push_error"] = str(error)
        return "conflict" if "rejected" in str(error) or "non-fast-forward" in str(error) else "push_failed"
    finally:
        result["upload_s"] = round(time.monotonic() - t0, 1)
    return "pushed"


CARRIED = ("last_snapshot_at", "last_snapshot_lock_s", "last_snapshot_files", "last_snapshot_bytes",
           "last_pushed_at", "last_pushed_head")


def _lower_priority() -> str:
    """Below-normal CPU priority (inherited by Git child processes) plus Windows background mode (low I/O
    and memory priority) for this process. Applied only after the sports lock is released."""
    if sys.platform != "win32":
        try:
            os.nice(5)
        except OSError:
            return "unchanged"
        return "nice+5"
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.SetPriorityClass.restype = wintypes.BOOL
    handle = k32.GetCurrentProcess()
    below = bool(k32.SetPriorityClass(handle, 0x00004000))
    background = bool(k32.SetPriorityClass(handle, 0x00100000))
    return "+".join(n for n, ok in (("below_normal", below), ("background", background)) if ok) or "unchanged"


def run_backup(cfg: SportsBackupConfig, *, repo_root: Path = REPO_ROOT, now: datetime | None = None,
               purpose: str = "manual", lock_path: Path | None = None, snapshot: bool = True,
               throttle_mbps: float | None = None, low_priority: bool = False) -> dict:
    """One backup run, never overlapping another (``backup.lock``): optionally a snapshot (sports lock held
    only for the listing, status copy and hard links), then commit and a no-force upload of any commit the
    remote lacks. Local research data is never modified."""
    now = now or datetime.now(timezone.utc)
    guard_path = backup_lock_path(repo_root)
    try:
        guard = LK.acquire(f"backup-run:{purpose}", guard_path)
    except LK.LockBusy as busy:
        owner = busy.owner or {}
        return {"schema": SCHEMA, "backup": "skipped_overlap", "purpose": purpose, "at": _iso(now),
                "error": (f"another backup run holds backup.lock (pid {owner.get('pid')}, {owner.get('purpose')}, "
                          f"since {owner.get('acquired_utc')})") if owner else str(busy)}
    try:
        return _run_backup(cfg, repo_root, now, purpose, lock_path or (repo_root / LOG_REL / "cycle.lock"),
                           snapshot, Throttle(throttle_mbps), low_priority)
    finally:
        LK.release(guard, guard_path)


def _run_backup(cfg: SportsBackupConfig, repo_root: Path, now: datetime, purpose: str, lock_path: Path,
                snapshot: bool, throttle: Throttle, low_priority: bool) -> dict:
    prev = read_status(repo_root) or {}
    result: dict = {"schema": SCHEMA, "backup": "failed", "purpose": purpose, "at": _iso(now),
                    "ref": cfg.ref, "collector_id": cfg.collector_id}
    result.update({k: prev[k] for k in CARRIED if k in prev})
    started = time.monotonic()

    def finish(status: str, **extra) -> dict:
        result.update(backup=status, **extra)
        result["finished_at"] = _iso(datetime.now(timezone.utc))
        result["duration_s"] = round(time.monotonic() - started, 1)
        _write_status(repo_root, result)
        return result

    if not (cfg.backup_dir / ".git").exists():
        return finish("failed", error=f"sports backup clone missing at {cfg.backup_dir}; run init")
    try:
        _check_attributes(cfg)
        configure_clone(cfg)
    except CO.OpsError as error:
        return finish("failed", error=str(error))

    snap_status = None
    if snapshot:
        snap_status = _snapshot_and_commit(cfg, repo_root, now, lock_path, throttle, low_priority, result)
        result["snapshot"] = snap_status or "committed"
    elif low_priority:
        result["priority"] = _lower_priority()
    head = local_head(cfg)
    if head is None:
        return finish(snap_status or "failed", error=result.get("error") or "clone has no HEAD")
    result["local_head"] = head
    push = "up_to_date" if head == result.get("last_pushed_head") else _push_stage(cfg, repo_root, head, result)
    if push in ("pushed", "up_to_date"):
        result.update(last_pushed_head=head, last_pushed_at=_iso(datetime.now(timezone.utc)))
    result["push"] = push
    result["unpushed"] = result.get("last_pushed_head") != head
    if snap_status is None and "push_error" in result:
        result["error"] = result["push_error"]
    return finish(snap_status or push)


def _snapshot_and_commit(cfg: SportsBackupConfig, repo_root: Path, now: datetime, lock_path: Path,
                         throttle: Throttle, low_priority: bool, result: dict) -> str | None:
    """Returns None after a consistent snapshot was committed (or matched HEAD), else the failure status."""
    try:
        previous = _head_manifest(cfg)
    except (CO.OpsError, ValueError, KeyError) as error:
        result["error"] = f"cannot read the committed manifest: {error}"
        return "failed"
    hasher = Hasher(cfg.backup_dir / ".git" / "kel_sports_hash_cache.json")
    refs = RefsCache(cfg.backup_dir / ".git" / "kel_sports_refs.json")
    staging_root = repo_root / STAGING_REL
    shutil.rmtree(staging_root, ignore_errors=True)
    staging = staging_root / now.strftime("%Y%m%dT%H%M%S%fZ")
    staging.mkdir(parents=True, exist_ok=True)
    known_deps = sorted({r for r in previous if is_dependency(r)} | refs.deps())
    try:
        try:
            snap = take_snapshot(repo_root, staging, known_deps, lock_path)
        except LK.LockBusy as busy:
            result["error"] = str(busy)
            return "skipped_busy"
        except (SnapshotRefused, OSError) as error:
            result["error"] = f"snapshot refused: {error}"
            return "failed"
        result.update(sports_lock_held_s=snap.timings["lock_held_s"], snapshot_timings=snap.timings,
                      staging=snap.staging, staging_fallback_reason=snap.staging_fallback_reason)
        if low_priority:
            result["priority"] = _lower_priority()
        _hook("after_lock", snap)
        t0 = time.monotonic()

        def refs_of(rel: str) -> dict | None:
            st = snap.listing[rel]
            hit = refs.get(rel, st)
            if hit is not None:
                return hit
            data, _ = _read_item(rel, Item(path=repo_root / rel, expect=st), throttle)
            out = _refs_of(rel, data)
            refs.put(rel, st, out)
            return out

        try:
            col = resolve_state(repo_root, snap.listing, refs_of)
            vanished = sorted(r for r in previous if r not in col.files and previous[r].get("class") != MUTABLE)
            if vanished:
                result.update(vanished=vanished[:50], vanished_by_kind=_vanish_summary(vanished))
            items = snapshot_items(repo_root, snap, col.files)
            manifest, written = _mirror(cfg, items, previous, hasher, throttle, vanished=vanished)
            bad_pins = sorted(r for r, sha in col.pins.items() if r in manifest and manifest[r]["sha256"] != sha)
            if bad_pins:
                raise CO.OpsError(f"evidence does not match the sha256 pinned by its record or score: {bad_pins[:10]}")
            drift = set(col.dependency_drift) | {p for p, sha in col.deps.items()
                                                 if sha and p in manifest and manifest[p]["sha256"] != sha}
            _hook("before_verify", cfg, manifest, written)
            _verify_clone(cfg, manifest, written)
            result["verified_written_files"] = len(written)
            CO._git(["add", "-A"], cwd=cfg.backup_dir, timeout=GIT_ADD_TIMEOUT_S)
            if CO._git_rc(cfg.backup_dir, ["diff", "--cached", "--quiet"]) != 0:
                CO._git(["commit", "-q", "-m", f"sports state backup {cfg.collector_id} {_iso(now)}"],
                        cwd=cfg.backup_dir, timeout=GIT_ADD_TIMEOUT_S, identity=cfg.collector_id)
        except Inconsistent as error:
            _reset_clone(cfg)
            result["error"] = str(error)
            return "inconsistent_snapshot"
        except (CO.OpsError, OSError, ValueError, KeyError) as error:
            _reset_clone(cfg)
            result["error"] = f"snapshot failed: {error}"
            return "failed"
        result["build_s"] = round(time.monotonic() - t0, 1)
        result.update(files=len(manifest), bytes=sum(m["size"] for m in manifest.values()), counts=col.counts,
                      chunked_files=sum(1 for m in manifest.values() if m.get("storage")),
                      missing=col.missing[:50], missing_count=len(col.missing), dependency_drift=sorted(drift),
                      incomplete=bool(col.missing or drift), last_snapshot_at=_iso(now),
                      last_snapshot_lock_s=snap.timings["lock_held_s"], last_snapshot_files=len(manifest),
                      last_snapshot_bytes=sum(m["size"] for m in manifest.values()))
        return None
    finally:
        hasher.save()
        refs.save()
        shutil.rmtree(staging_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Isolated restore + offline replay
# ---------------------------------------------------------------------------

def _tracked_code(repo_root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo_root, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def _offline_env(target: Path) -> dict:
    env = os.environ.copy()
    env.update(PYTHONPATH=str(target), PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", NO_PROXY="",
               http_proxy="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9")
    return env


def _run_in(target: Path, args: list[str], timeout: int = 3600) -> dict:
    proc = subprocess.run([sys.executable, *args], cwd=target, env=_offline_env(target), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
    return {"command": " ".join(args), "exit": proc.returncode, "tail": lines[-3:]}


def _hostile_clone(cfg: SportsBackupConfig, dest: Path) -> None:
    """Fresh clone with autocrlf=true / eol=crlf: stored bytes must not depend on the restoring host's Git settings."""
    CO._git(["clone", "-q", "--branch", CO._branch(cfg.ref), "--single-branch", cfg.remote, str(dest)],
            ssh_command=cfg.ssh_command, timeout=GIT_ADD_TIMEOUT_S,
            extra_config=("-c", "core.autocrlf=true", "-c", "core.eol=crlf"))


def load_manifest(clone: Path) -> dict[str, dict]:
    body = json.loads((clone / MANIFEST).read_text(encoding="utf-8"))
    if body.get("schema") not in READABLE_SCHEMAS:
        raise CO.OpsError(f"{clone / MANIFEST} is not a sports state backup manifest ({body.get('schema')!r})")
    return body["files"]


def read_stored(state_dir: Path, rel: str, meta: dict) -> bytes:
    """Original bytes of ``rel``: plain, or reassembled from verified gzip chunks; checked against the manifest."""
    storage = meta.get("storage")
    if not storage:
        data = (state_dir / rel).read_bytes()
    else:
        if storage.get("format") != CHUNK_FORMAT:
            raise CO.OpsError(f"{rel}: unknown storage format {storage.get('format')!r}")
        parts = []
        for i, c in enumerate(storage["chunks"]):
            blob = (state_dir / chunk_name(rel, i)).read_bytes()
            if CO.sha256_bytes(blob) != c["stored_sha256"]:
                raise CO.OpsError(f"{rel}: stored chunk {i} hash mismatch")
            piece = gzip.decompress(blob)
            if len(piece) != c["size"] or CO.sha256_bytes(piece) != c["sha256"]:
                raise CO.OpsError(f"{rel}: chunk {i} content hash mismatch")
            parts.append(piece)
        data = b"".join(parts)
    if len(data) != meta["size"] or CO.sha256_bytes(data) != meta["sha256"]:
        raise CO.OpsError(f"hash mismatch {rel}")
    return data


def iter_backup(clone: Path) -> Iterator[tuple[str, bytes, dict]]:
    """(rel, original bytes, manifest entry) for every file in a backup checkout, one at a time. Refuses
    unlisted or missing stored files before yielding anything."""
    manifest = load_manifest(clone)
    state_dir = clone / STATE_DIR
    expected = {p for rel, meta in manifest.items() for p in stored_paths(rel, meta)}
    on_disk = ({p.relative_to(state_dir).as_posix() for p in state_dir.rglob("*") if p.is_file()}
               if state_dir.is_dir() else set())
    problems = ([f"unlisted {p}" for p in sorted(on_disk - expected)][:20]
                + [f"missing {p}" for p in sorted(expected - on_disk)][:20])
    if problems:
        raise CO.OpsError(f"{clone}: manifest verification failed: {problems}")
    for rel in sorted(manifest):
        yield CO._safe_rel(rel), read_stored(state_dir, rel, manifest[rel]), manifest[rel]


def clone_and_verify(cfg: SportsBackupConfig, dest: Path) -> CO.Source:
    """Hostile fresh clone, verified file-by-file against the manifest (loads every file into memory)."""
    _hostile_clone(cfg, dest)
    files = {rel: data for rel, data, _ in iter_backup(dest)}
    return CO.Source(files=files, origin=f"{cfg.remote}@{cfg.ref}", manifest=load_manifest(dest))


def verify_restore(cfg: SportsBackupConfig, *, repo_root: Path = REPO_ROOT, work: Path | None = None,
                   keep: bool = False, replay: bool = True) -> dict:
    """Hostile fresh clone -> manifest check -> restore into an empty isolated tree -> byte comparison
    with this repo -> offline replay and score verification inside the isolated tree."""
    tmp = Path(tempfile.mkdtemp(prefix="kel-sports-restore-", dir=work))
    out: dict = {"ok": False, "work_dir": str(tmp), "ref": cfg.ref}
    try:
        clone = tmp / "clone"
        try:
            _hostile_clone(cfg, clone)
            manifest = load_manifest(clone)
        except (CO.OpsError, OSError, ValueError, KeyError) as error:
            out["error"] = str(error)
            return out
        out["head"] = CO._git(["rev-parse", "HEAD"], cwd=clone).stdout.strip()
        out["files"] = len(manifest)
        out["chunked_files"] = sorted(r for r, m in manifest.items() if m.get("storage"))
        target = tmp / "root"
        for rel in _tracked_code(repo_root):
            if rel in manifest or rel.startswith(("data/", "frontend/")):
                continue
            src = repo_root / rel
            if src.is_file():
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
        bad_manifest: list[str] = []
        identical, prefix, mutable, differs, gone = 0, 0, [], [], []
        try:
            for rel, data, meta in iter_backup(clone):
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                if CO.sha256_bytes(dest.read_bytes()) != meta["sha256"]:
                    bad_manifest.append(rel)
                live = repo_root / rel
                if not live.is_file():
                    gone.append(rel)
                    continue
                cur = live.read_bytes()
                if cur == data:
                    identical += 1
                elif classify(rel) == APPEND_ONLY and cur.startswith(data):
                    prefix += 1
                elif classify(rel) == MUTABLE:
                    mutable.append(rel)
                else:
                    differs.append(rel)
        except (CO.OpsError, OSError, ValueError, KeyError, EOFError, zlib.error) as error:
            out["error"] = str(error)
            return out
        out.update(manifest_mismatch=bad_manifest, byte_identical=identical, live_grew_since_backup=prefix,
                   mutable_changed_since_backup=mutable, differs_from_live=differs[:20], missing_in_live=gone[:20])
        checks = []
        if replay:
            checks.append(_run_in(target, ["-c", "import research,sys; print(research.__file__)"], 60))
            checks.append(_run_in(target, ["-m", "research.sports.live.run", "replay", "--all"]))
            checks.append(_run_in(target, ["-m", "research.sports.scoring.run", "verify-scores"]))
        out["checks"] = checks
        imported_from = checks[0]["tail"][-1] if checks and checks[0]["tail"] else ""
        out["isolated_import"] = imported_from.startswith(str(target))
        out["ok"] = (not bad_manifest and not differs and not gone
                     and all(c["exit"] == 0 for c in checks) and (not replay or out["isolated_import"]))
        return out
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research.sports.ops.backup", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="clone (or, with --create, start) the sports backup ref")
    p_init.add_argument("--create", action="store_true")
    p_init.add_argument("--base-ref", help=f"with --create, start on this remote ref's tip (e.g. {LEGACY_REF})")
    p_run = sub.add_parser("run", help="snapshot, commit and push")
    p_run.add_argument("--if-due", action="store_true",
                       help="snapshot only if due (hourly); always retry uploading an unpushed commit")
    p_run.add_argument("--purpose", default="manual")
    p_push = sub.add_parser("push-pending", help="upload an existing unpushed commit; no snapshot, no sports lock")
    p_push.add_argument("--purpose", default="manual")
    for p in (p_run, p_push):
        p.add_argument("--low-priority", action="store_true",
                       help="after the sports lock is released, run at below-normal CPU and background I/O priority")
    sub.add_parser("plan", help="list what the sports profile would back up (no writes)")
    p_ver = sub.add_parser("verify-restore", help="hostile clone, isolated restore, offline replay")
    p_ver.add_argument("--keep", action="store_true")
    p_ver.add_argument("--no-replay", action="store_true")
    sub.add_parser("status")
    p_pause = sub.add_parser("pause", help="make scheduled (--if-due) runs skip until `resume`")
    p_pause.add_argument("--reason", required=True)
    sub.add_parser("resume", help="remove the pause marker")
    for p in (p_init, p_run, p_push, p_ver):
        p.add_argument("--backup-dir", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps({"last_run": read_status(), "paused": read_pause()}, indent=1))
        return EXIT_OK
    if args.cmd == "pause":
        print(json.dumps({"paused": set_pause(args.reason)}))
        return EXIT_OK
    if args.cmd == "resume":
        print(json.dumps({"resumed": clear_pause()}))
        return EXIT_OK
    if args.cmd == "plan":
        col = collect_state()
        print(json.dumps({"files": len(col.files), "bytes": sum(p.stat().st_size for p in col.files.values()),
                          "counts": col.counts, "missing": col.missing, "dependency_drift": col.dependency_drift},
                         indent=1))
        return EXIT_OK if not col.missing and not col.dependency_drift else EXIT_FAILED
    if args.cmd == "run" and args.if_due and (paused := read_pause()):
        print(json.dumps({"backup": "paused", **paused}))
        return EXIT_OK
    try:
        cfg = load_config(backup_dir=args.backup_dir)
    except CO.OpsError as error:
        print(f"config error: {error}", file=sys.stderr)
        return EXIT_FAILED
    if args.cmd == "init":
        print(json.dumps(backup_init(cfg, create_if_missing=args.create, base_ref=args.base_ref), indent=1))
        return EXIT_OK
    if args.cmd in ("run", "push-pending"):
        snapshot = args.cmd == "run"
        if args.cmd == "run" and args.if_due:
            snapshot = is_due()
            if not snapshot and not has_unpushed(cfg):
                print(json.dumps({"backup": "not_due", "last_snapshot_at": (read_status() or {}).get("last_snapshot_at")}))
                return EXIT_OK
        mbps = float(os.environ.get("KEL_SPORTS_BACKUP_MBPS", DEFAULT_MBPS))
        res = run_backup(cfg, purpose=args.purpose, snapshot=snapshot, throttle_mbps=mbps,
                         low_priority=args.low_priority)
        print(json.dumps({k: v for k, v in res.items() if k not in ("missing", "vanished")}, default=str))
        if res["backup"] in ("pushed", "up_to_date", "push_deferred", "skipped_busy", "skipped_overlap"):
            return EXIT_FAILED if res.get("incomplete") else EXIT_OK
        return EXIT_CONFLICT if res["backup"] == "conflict" else EXIT_FAILED
    if args.cmd == "verify-restore":
        res = verify_restore(cfg, keep=args.keep, replay=not args.no_replay)
        print(json.dumps(res, indent=1, default=str))
        return EXIT_OK if res["ok"] else EXIT_VERIFY_FAILED
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())

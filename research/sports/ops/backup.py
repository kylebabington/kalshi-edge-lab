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
(read only), but a separate ref (``refs/heads/sports-state``) and a separate local clone. The weather
backup's ref, clone, allowlist and ownership marker are not touched.

Coordination: the snapshot is copied while holding the sports lock (no capture or settlement can write
mid-copy). Pushes wait for the weather cycle lock to be free (the weather backup runs inside that lock),
are never forced, and a remote that has commits this host does not know is reported as a conflict.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research.weather import collector_ops as CO

from ..core import REPO_ROOT
from ..live import config as LC
from ..scoring import config as SCC
from . import lock as LK

SCHEMA = "kel_sports_state_backup_v1"
PROFILE = "sports"
SPORTS_REF = "refs/heads/sports-state"
DEFAULT_DIR = Path.home() / ".kel" / "sports-state-backup"
STATE_DIR = "state"
MANIFEST = "MANIFEST.json"
INTERVAL = timedelta(hours=1)

WRITE_ONCE = CO.WRITE_ONCE
APPEND_ONLY = CO.APPEND_ONLY
MUTABLE = CO.MUTABLE

CAPTURE_REL = "data/sports/phase2/capture_v2"
SCORE_REL = "data/sports/phase2/capture_v2_scores"
PROTOCOL_REL = "research/sports/protocols"
CAPTURE_EVIDENCE_REL = "data/cache/sports/raw_capture_v2"
SETTLE_EVIDENCE_REL = "data/cache/sports/raw_settlement_v1"
LOG_REL = "data/logs/sports"
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


def _walk(root: Path, rel_dir: str) -> list[str]:
    base = root / rel_dir
    if not base.is_dir():
        return []
    out = []
    for path in base.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not any(is_excluded(part) for part in rel.split("/")):
            out.append(rel)
    return out


def _evidence_rels(root_rel: str, run_id: str, evidence: list[dict]) -> list[str]:
    out = []
    for e in evidence:
        stem = f"{root_rel}/{run_id}/{e['source']}/{e['key'][:2]}/{e['key']}"
        out.append(f"{stem}.body")
        out.append(f"{stem}.meta.json")
    return out


@dataclass
class Collected:
    files: dict[str, Path]
    missing: list[str]
    dependency_drift: list[str]
    counts: dict[str, int]


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

    def save(self) -> None:
        if self.path:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.cache), encoding="utf-8")
            os.replace(tmp, self.path)


def collect_state(root: Path = REPO_ROOT, hasher: Hasher | None = None) -> Collected:
    """Allowlisted sports state under ``root``: rel path -> absolute path, plus integrity findings."""
    rels: set[str] = set()
    for d in STATE_SUBDIRS:
        rels.update(_walk(root, d))
    log_dir = root / LOG_REL
    if log_dir.is_dir():
        for pattern in LOG_PATTERNS:
            rels.update(f"{LOG_REL}/{p.name}" for p in log_dir.glob(pattern) if p.is_file() and not is_excluded(p.name))
    counts = {"records": 0, "scores": 0, "dependencies": 0, "capture_evidence": 0, "settlement_evidence": 0}
    referenced: set[str] = set()
    optional: set[str] = set()
    deps: dict[str, str | None] = {}
    drift: set[str] = set()
    for rel in sorted(rels):
        if not rel.endswith(".json"):
            continue
        if rel.startswith((f"{CAPTURE_REL}/accepted/", f"{CAPTURE_REL}/attempts/")):
            rec = json.loads((root / rel).read_text(encoding="utf-8"))
            counts["records"] += 1
            for path, sha in (rec.get("dependencies") or {}).items():
                if path in deps and deps[path] != sha:
                    drift.add(path)
                deps.setdefault(path, sha)
            ev = _evidence_rels(CAPTURE_EVIDENCE_REL, rec["run_id"], rec.get("evidence") or [])
            counts["capture_evidence"] += len(ev) // 2
            referenced.update(ev[0::2])
            optional.update(ev[1::2])
        elif rel.startswith(f"{SCORE_REL}/finalized/"):
            sc = json.loads((root / rel).read_text(encoding="utf-8"))
            counts["scores"] += 1
            ev = _evidence_rels(SETTLE_EVIDENCE_REL, sc["settlement_run_id"], sc.get("settlement_evidence") or [])
            counts["settlement_evidence"] += len(ev) // 2
            referenced.update(ev[0::2])
            optional.update(ev[1::2])
            src = sc.get("source_record")
            if src and src not in rels:
                referenced.add(src)
    hasher = hasher or Hasher()
    for path, sha in deps.items():
        referenced.add(path)
        p = root / path
        if sha is not None and p.exists() and hasher.sha(p, f"src:{path}") != sha:
            drift.add(path)
    counts["dependencies"] = len(deps)
    missing = sorted(r for r in referenced if not (root / r).is_file())
    rels.update(r for r in referenced if (root / r).is_file())
    rels.update(r for r in optional if (root / r).is_file())
    bad = sorted(r for r in rels if any(is_excluded(part) for part in r.split("/")) or r.startswith("/") or ".." in r.split("/"))
    if bad:
        raise CO.OpsError(f"refusing excluded or unsafe paths in sports backup: {bad[:10]}")
    return Collected(files={r: root / r for r in sorted(rels)}, missing=missing,
                     dependency_drift=sorted(drift), counts=counts)


# ---------------------------------------------------------------------------
# Backup run
# ---------------------------------------------------------------------------

def _bgit(cfg: SportsBackupConfig, args: list[str], **kw) -> subprocess.CompletedProcess:
    return CO._git(args, cwd=cfg.backup_dir, ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s, **kw)


def backup_init(cfg: SportsBackupConfig, *, create_if_missing: bool = False) -> dict:
    """Clone the sports ref, or explicitly start it. Never adopts a divergent history."""
    if (cfg.backup_dir / ".git").exists():
        raise CO.OpsError(f"{cfg.backup_dir} is already initialized")
    remote_sha = CO.remote_ref_sha(cfg.remote, cfg.ref, ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s)
    cfg.backup_dir.parent.mkdir(parents=True, exist_ok=True)
    if remote_sha is not None:
        CO._git(["clone", "-q", "--branch", CO._branch(cfg.ref), "--single-branch", cfg.remote, str(cfg.backup_dir)],
                ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s * 10)
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
    return {"backup_dir": str(cfg.backup_dir), "ref": cfg.ref, "remote_sha": remote_sha}


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


def _mirror(cfg: SportsBackupConfig, files: dict[str, Path], previous: dict, hasher: Hasher,
            final: bool = True) -> dict:
    """Copy changed files into the clone. ``final=False`` is the unlocked warm-up pass: it copies and caches
    hashes but neither deletes nor writes the manifest; the locked final pass then handles only deltas."""
    state_dir = cfg.backup_dir / STATE_DIR
    manifest: dict[str, dict] = {}
    changed_once = []
    for rel, src in files.items():
        meta = {"sha256": hasher.sha(src, f"src:{rel}"), "size": src.stat().st_size, "class": classify(rel)}
        old = previous.get(rel)
        if old and ((meta["class"] == WRITE_ONCE and old["sha256"] != meta["sha256"])
                    or (meta["class"] == APPEND_ONLY and meta["size"] < old["size"])):
            changed_once.append(rel)
        manifest[rel] = meta
    if changed_once:
        raise CO.OpsError(f"write-once files changed or append-only files shrank locally: {changed_once[:20]}")
    for rel, meta in manifest.items():
        dest = state_dir / rel
        if dest.exists() and dest.stat().st_size == meta["size"] and hasher.sha(dest, f"dst:{rel}") == meta["sha256"]:
            continue
        data = files[rel].read_bytes()
        if CO.sha256_bytes(data) != meta["sha256"]:
            raise CO.OpsError(f"{rel} changed during the snapshot")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        hasher.sha(dest, f"dst:{rel}")
    if not final:
        return manifest
    if state_dir.is_dir():
        for path in sorted(state_dir.rglob("*"), reverse=True):
            rel = path.relative_to(state_dir).as_posix()
            if path.is_file() and rel not in manifest:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    body = {"schema": SCHEMA, "profile": PROFILE, "files": manifest}
    (cfg.backup_dir / MANIFEST).write_bytes((json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return manifest


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


def is_due(repo_root: Path | None = None, now: datetime | None = None, interval: timedelta = INTERVAL) -> bool:
    st = read_status(repo_root)
    if not st or st.get("backup") not in ("pushed", "up_to_date") or not st.get("at"):
        return True
    last = datetime.fromisoformat(st["at"].replace("Z", "+00:00"))
    return (now or datetime.now(timezone.utc)) - last >= interval


def run_backup(cfg: SportsBackupConfig, *, repo_root: Path = REPO_ROOT, now: datetime | None = None,
               purpose: str = "manual", lock_path: Path | None = None) -> dict:
    """Snapshot (under the sports lock), commit, push without force. Local data is never modified."""
    now = now or datetime.now(timezone.utc)
    result: dict = {"schema": SCHEMA, "backup": "failed", "purpose": purpose, "at": _iso(now),
                    "ref": cfg.ref, "collector_id": cfg.collector_id}

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
    manifest_path = cfg.backup_dir / MANIFEST
    previous = json.loads(manifest_path.read_text(encoding="utf-8"))["files"] if manifest_path.exists() else {}
    hasher = Hasher(cfg.backup_dir / ".git" / "kel_sports_hash_cache.json")
    try:
        _mirror(cfg, collect_state(repo_root, hasher).files, previous, hasher, final=False)
    except (CO.OpsError, OSError, ValueError, KeyError):
        pass  # warm-up only (files may be mid-write); the locked pass below decides
    t0 = time.monotonic()
    try:
        with LK.sports_lock(f"backup:{purpose}", lock_path or (repo_root / LOG_REL / "cycle.lock")):
            col = collect_state(repo_root, hasher)
            vanished = sorted(r for r in previous if r not in col.files and previous[r].get("class") != MUTABLE)
            if vanished:
                return finish("failed", error="write-once/append-only files vanished locally; refusing to mirror",
                              vanished=vanished[:50])
            manifest = _mirror(cfg, col.files, previous, hasher)
    except LK.LockBusy as busy:
        return finish("skipped_busy", error=str(busy))
    except (CO.OpsError, OSError, ValueError, KeyError) as error:
        return finish("failed", error=f"snapshot failed: {error}")
    finally:
        hasher.save()
    result["sports_lock_held_s"] = round(time.monotonic() - t0, 1)
    result.update(files=len(manifest), bytes=sum(m["size"] for m in manifest.values()), counts=col.counts,
                  missing=col.missing[:50], missing_count=len(col.missing),
                  dependency_drift=col.dependency_drift)
    try:
        _bgit(cfg, ["add", "-A"])
        if CO._git_rc(cfg.backup_dir, ["diff", "--cached", "--quiet"]) != 0:
            _bgit(cfg, ["commit", "-q", "-m", f"sports state backup {cfg.collector_id} {_iso(now)}"],
                  identity=cfg.collector_id)
        head = _bgit(cfg, ["rev-parse", "HEAD"]).stdout.strip()
    except (CO.OpsError, OSError) as error:
        return finish("failed", error=f"local commit failed: {error}")
    result["local_head"] = head
    problem = bool(col.missing or col.dependency_drift)

    if _weather_lock_held(repo_root):
        return finish("push_deferred", reason="weather cycle/backup lock held; commit kept locally",
                      incomplete=problem)
    try:
        remote_sha = CO.remote_ref_sha(cfg.remote, cfg.ref, ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s)
    except CO.GitError as error:
        return finish("push_failed", error=str(error), incomplete=problem)
    if remote_sha == head:
        return finish("up_to_date", incomplete=problem)
    if remote_sha is not None:
        try:
            _bgit(cfg, ["fetch", "-q", "origin", cfg.ref])
            known = CO._git_rc(cfg.backup_dir, ["merge-base", "--is-ancestor", remote_sha, head]) == 0
        except CO.GitError as error:
            return finish("push_failed", error=str(error), incomplete=problem)
        if not known:
            return finish("conflict", remote_sha=remote_sha, incomplete=problem,
                          error="remote sports backup has commits not in this host's history; not merging, not forcing")
    try:
        CO._git(["push", "-q", "origin", f"HEAD:{cfg.ref}"], cwd=cfg.backup_dir, ssh_command=cfg.ssh_command,
                timeout=cfg.git_timeout_s * 10)
    except CO.GitError as error:
        status = "conflict" if "rejected" in str(error) or "non-fast-forward" in str(error) else "push_failed"
        return finish(status, error=str(error), incomplete=problem)
    return finish("pushed", incomplete=problem)


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


def clone_and_verify(cfg: SportsBackupConfig, dest: Path) -> CO.Source:
    """Fresh clone with autocrlf=true / eol=crlf (stored bytes must not depend on the restoring host's
    Git settings), verified file-by-file against the manifest."""
    CO._git(["clone", "-q", "--branch", CO._branch(cfg.ref), "--single-branch", cfg.remote, str(dest)],
            ssh_command=cfg.ssh_command, timeout=cfg.git_timeout_s * 10,
            extra_config=("-c", "core.autocrlf=true", "-c", "core.eol=crlf"))
    body = json.loads((dest / MANIFEST).read_text(encoding="utf-8"))
    if body.get("schema") != SCHEMA:
        raise CO.OpsError(f"{dest / MANIFEST} is not a {SCHEMA} manifest")
    return CO._source_from_dir(dest / STATE_DIR, body["files"], origin=f"{cfg.remote}@{cfg.ref}")


def verify_restore(cfg: SportsBackupConfig, *, repo_root: Path = REPO_ROOT, work: Path | None = None,
                   keep: bool = False, replay: bool = True) -> dict:
    """Hostile fresh clone -> manifest check -> restore into an empty isolated tree -> byte comparison
    with this repo -> offline replay and score verification inside the isolated tree."""
    tmp = Path(tempfile.mkdtemp(prefix="kel-sports-restore-", dir=work))
    out: dict = {"ok": False, "work_dir": str(tmp)}
    try:
        clone = tmp / "clone"
        source = clone_and_verify(cfg, clone)
        out["head"] = CO._git(["rev-parse", "HEAD"], cwd=clone).stdout.strip()
        out["files"] = len(source.files)
        target = tmp / "root"
        state = set(source.files)
        for rel in _tracked_code(repo_root):
            if rel in state or rel.startswith(("data/", "frontend/")):
                continue
            src = repo_root / rel
            if src.is_file():
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
        for rel, data in source.files.items():
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        restored = {rel: CO.sha256_bytes((target / rel).read_bytes()) for rel in source.files}
        bad_manifest = sorted(r for r, sha in restored.items() if sha != source.manifest[r]["sha256"])
        identical, prefix, mutable, differs, gone = 0, 0, [], [], []
        for rel, data in source.files.items():
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
    p_run = sub.add_parser("run", help="snapshot, commit and push")
    p_run.add_argument("--if-due", action="store_true", help="skip unless the last successful push is >= 1 h old")
    p_run.add_argument("--purpose", default="manual")
    sub.add_parser("plan", help="list what the sports profile would back up (no writes)")
    p_ver = sub.add_parser("verify-restore", help="hostile clone, isolated restore, offline replay")
    p_ver.add_argument("--keep", action="store_true")
    p_ver.add_argument("--no-replay", action="store_true")
    sub.add_parser("status")
    for p in (p_init, p_run, p_ver):
        p.add_argument("--backup-dir", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps(read_status(), indent=1))
        return EXIT_OK
    if args.cmd == "plan":
        col = collect_state()
        print(json.dumps({"files": len(col.files), "bytes": sum(p.stat().st_size for p in col.files.values()),
                          "counts": col.counts, "missing": col.missing, "dependency_drift": col.dependency_drift},
                         indent=1))
        return EXIT_OK if not col.missing and not col.dependency_drift else EXIT_FAILED
    try:
        cfg = load_config(backup_dir=args.backup_dir)
    except CO.OpsError as error:
        print(f"config error: {error}", file=sys.stderr)
        return EXIT_FAILED
    if args.cmd == "init":
        print(json.dumps(backup_init(cfg, create_if_missing=args.create), indent=1))
        return EXIT_OK
    if args.cmd == "run":
        if args.if_due and not is_due():
            print(json.dumps({"backup": "not_due", "last": (read_status() or {}).get("at")}))
            return EXIT_OK
        try:
            with LK.sports_lock(f"backup-run:{args.purpose}", backup_lock_path()):
                res = run_backup(cfg, purpose=args.purpose)
        except LK.LockBusy as busy:
            print(json.dumps({"backup": "skipped_busy", "error": str(busy)}))
            return EXIT_OK
        print(json.dumps({k: v for k, v in res.items() if k != "missing"}, default=str))
        if res["backup"] in ("pushed", "up_to_date", "push_deferred", "skipped_busy"):
            return EXIT_FAILED if res.get("incomplete") else EXIT_OK
        return EXIT_CONFLICT if res["backup"] == "conflict" else EXIT_FAILED
    if args.cmd == "verify-restore":
        res = verify_restore(cfg, keep=args.keep, replay=not args.no_replay)
        print(json.dumps(res, indent=1, default=str))
        return EXIT_OK if res["ok"] else EXIT_VERIFY_FAILED
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())

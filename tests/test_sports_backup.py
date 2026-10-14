"""Sports state backup: allowlist, write-once protection, lock/weather coordination, no-force push, restore."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from research.sports.core import request_key, sha256_bytes
from research.sports.ops import backup as BK
from research.sports.ops import lock as LK
from research.weather import collector_ops as CO


def _write(root: Path, rel: str, data: bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _evidence(root: Path, base: str, run_id: str, source: str, url: str, body: bytes) -> dict:
    key = request_key(url, {})
    stem = f"{base}/{run_id}/{source}/{key[:2]}/{key}"
    _write(root, f"{stem}.body", body)
    _write(root, f"{stem}.meta.json", b'{"status": 200}\r\n')
    return {"source": source, "key": key, "url": url, "params": {}, "sha256": sha256_bytes(body)}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    dep = b"line1\r\nline2\n"
    _write(root, "data/sports/normalized/nfl/events.jsonl", dep)
    ev = _evidence(root, BK.CAPTURE_EVIDENCE_REL, "R1", "kalshi", "https://x/markets", b"\x00\x01binary\r\n")
    _evidence(root, BK.CAPTURE_EVIDENCE_REL, "R1", "kalshi", "https://x/unreferenced", b"junk")
    rec = {"run_id": "R1", "dependencies": {"data/sports/normalized/nfl/events.jsonl": sha256_bytes(dep)},
           "evidence": [ev]}
    rec_rel = f"{BK.CAPTURE_REL}/accepted/nfl/E1/c.json"
    _write(root, rec_rel, json.dumps(rec).encode())
    sev = _evidence(root, BK.SETTLE_EVIDENCE_REL, "S1", "espn", "https://espn/summary", b"{}")
    _write(root, f"{BK.SCORE_REL}/finalized/nfl/E1/T.json",
           json.dumps({"settlement_run_id": "S1", "settlement_evidence": [sev], "source_record": rec_rel}).encode())
    _write(root, f"{BK.CAPTURE_REL}/runs/R1.json", b"{}")
    _write(root, f"{BK.PROTOCOL_REL}/p.json", b"{}\n")
    _write(root, f"{BK.LOG_REL}/collector_cycles.jsonl", b'{"a":1}\n')
    _write(root, f"{BK.LOG_REL}/collector_status.json", b"{}")
    _write(root, f"{BK.LOG_REL}/.task_x.out", b"tmp")
    _write(root, f"{BK.LOG_REL}/dry_runs/d.json", b"{}")
    _write(root, f"{BK.LOG_REL}/benchmark.log", b"x")
    _write(root, "collector.local.json", b"{}")
    _write(root, f"{BK.CAPTURE_REL}/accepted/nfl/E1/.c.json.tmp", b"partial")
    return root


@pytest.fixture()
def cfg(tmp_path: Path) -> BK.SportsBackupConfig:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    c = BK.SportsBackupConfig(collector_id="test-host", remote=str(remote), ref=BK.SPORTS_REF,
                              backup_dir=tmp_path / "clone", ssh_command=None)
    BK.backup_init(c, create_if_missing=True)
    return c


def test_collect_state_allowlist_and_exclusions(repo: Path):
    _write(repo, f"{BK.LOG_REL}/cycle.lock", b"{}")
    col = BK.collect_state(repo)
    files = set(col.files)
    assert col.missing == [] and col.dependency_drift == []
    assert "data/sports/normalized/nfl/events.jsonl" in files
    assert any(f.endswith(".body") and "/R1/" in f for f in files)
    assert any(f.endswith(".body") and "/S1/" in f for f in files)
    assert sum(1 for f in files if f.startswith(BK.CAPTURE_EVIDENCE_REL) and f.endswith(".body")) == 1
    assert f"{BK.LOG_REL}/collector_cycles.jsonl" in files
    for bad in ("cycle.lock", ".task_x.out", "dry_runs", "benchmark.log", "collector.local.json", ".tmp"):
        assert not any(bad in f for f in files), bad
    assert BK.classify(f"{BK.LOG_REL}/collector_cycles.jsonl") == BK.APPEND_ONLY
    assert BK.classify(f"{BK.LOG_REL}/collector_status.json") == BK.MUTABLE
    assert BK.classify(f"{BK.CAPTURE_REL}/accepted/nfl/E1/c.json") == BK.WRITE_ONCE


def test_missing_evidence_and_dependency_drift_are_reported(repo: Path):
    (repo / "data/sports/normalized/nfl/events.jsonl").write_bytes(b"changed")
    body = next(p for p in (repo / BK.SETTLE_EVIDENCE_REL).rglob("*.body"))
    body.unlink()
    col = BK.collect_state(repo)
    assert col.dependency_drift == ["data/sports/normalized/nfl/events.jsonl"]
    assert len(col.missing) == 1 and col.missing[0].endswith(".body")


def test_backup_push_and_hostile_clone_is_byte_exact(repo: Path, cfg, tmp_path: Path):
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "pushed", res
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "up_to_date"
    source = BK.clone_and_verify(cfg, tmp_path / "restore")
    col = BK.collect_state(repo)
    assert set(source.files) == set(col.files)
    for rel, data in source.files.items():
        assert data == (repo / rel).read_bytes(), rel
    assert BK.read_status(repo)["backup"] == "up_to_date"
    assert not (repo / BK.LOG_REL / "cycle.lock").exists()


def test_write_once_change_and_vanish_refused(repo: Path, cfg):
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    rec = repo / BK.CAPTURE_REL / "accepted/nfl/E1/c.json"
    original = rec.read_bytes()
    rec.write_bytes(original.replace(b"R1", b"R1 "))
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "failed" and "write-once" in res["error"]
    rec.write_bytes(original)
    (repo / BK.CAPTURE_REL / "runs/R1.json").unlink()
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "failed" and "vanished" in res["error"]


def test_append_only_log_may_grow_but_not_shrink(repo: Path, cfg):
    log = repo / BK.LOG_REL / "collector_cycles.jsonl"
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    log.write_bytes(log.read_bytes() + b'{"b":2}\n')
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    log.write_bytes(b"")
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "failed"


def test_busy_sports_lock_skips_without_touching_state(repo: Path, cfg):
    with LK.sports_lock("capture", repo / BK.LOG_REL / "cycle.lock"):
        res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "skipped_busy"
    assert not (cfg.backup_dir / BK.MANIFEST).exists()


def test_weather_lock_defers_push(repo: Path, cfg):
    _write(repo, f"{CO.STATE_LOG_DIR}/cycle.lock", b"weather")
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "push_deferred"
    assert CO.remote_ref_sha(cfg.remote, cfg.ref) != res["local_head"]
    (repo / CO.STATE_LOG_DIR / "cycle.lock").unlink()
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"


def test_divergent_remote_is_a_conflict_never_forced(repo: Path, cfg, tmp_path: Path):
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "--branch", CO._branch(BK.SPORTS_REF), cfg.remote, str(other)], check=True)
    (other / "x").write_text("foreign")
    subprocess.run(["git", "-C", str(other), "add", "x"], check=True)
    subprocess.run(["git", "-C", str(other), "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qm", "x"],
                   check=True)
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", f"HEAD:{BK.SPORTS_REF}"], check=True)
    foreign = CO.remote_ref_sha(cfg.remote, cfg.ref)
    log = repo / BK.LOG_REL / "collector_cycles.jsonl"
    log.write_bytes(log.read_bytes() + b"{}\n")
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "conflict"
    assert CO.remote_ref_sha(cfg.remote, cfg.ref) == foreign


def test_config_refuses_weather_ref_and_clone(tmp_path: Path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"collector_id": "h", "control_remote": "r", "backup_remote": "s",
                             "backup_ref": BK.SPORTS_REF, "backup_dir": str(tmp_path / "w")}))
    with pytest.raises(CO.ConfigError):
        BK.load_config(p)
    p.write_text(json.dumps({"collector_id": "h", "control_remote": "r", "backup_remote": "s",
                             "backup_dir": str(tmp_path / "w")}))
    with pytest.raises(CO.ConfigError):
        BK.load_config(p, backup_dir=tmp_path / "w")
    assert BK.load_config(p).ref == BK.SPORTS_REF


def test_is_due(repo: Path):
    from datetime import datetime, timedelta, timezone
    assert BK.is_due(repo)
    now = datetime.now(timezone.utc)
    ago = lambda m: BK._iso(now - timedelta(minutes=m))  # noqa: E731
    BK._write_status(repo, {"backup": "pushed", "snapshot": "committed", "at": ago(10), "last_snapshot_at": ago(10)})
    assert not BK.is_due(repo, now)
    BK._write_status(repo, {"backup": "pushed", "snapshot": "committed", "at": ago(61), "last_snapshot_at": ago(61)})
    assert BK.is_due(repo, now)
    # An upload failure does not make a new snapshot due (the commit is retried by has_unpushed instead).
    BK._write_status(repo, {"backup": "push_failed", "snapshot": "committed", "at": ago(20), "last_snapshot_at": ago(20)})
    assert not BK.is_due(repo, now)
    BK._write_status(repo, {"backup": "failed", "snapshot": "failed", "at": ago(5), "last_snapshot_at": ago(90)})
    assert not BK.is_due(repo, now)
    assert BK.is_due(repo, now + BK.FAILURE_BACKOFF)
    for retry in BK.SNAPSHOT_RETRY_NEXT_CHECK:
        BK._write_status(repo, {"backup": retry, "snapshot": retry, "at": ago(1), "last_snapshot_at": ago(5)})
        assert BK.is_due(repo, now)
    BK._write_status(repo, {"backup": "pushed", "at": ago(30)})  # status written before last_snapshot_at existed
    assert not BK.is_due(repo, now)


@pytest.fixture()
def small_chunks(monkeypatch):
    monkeypatch.setattr(BK, "CHUNK_THRESHOLD", 1000)
    monkeypatch.setattr(BK, "CHUNK_RAW_BYTES", 400)


def _stored_chunks(cfg, rel: str) -> dict[str, bytes]:
    d = cfg.backup_dir / BK.STATE_DIR / f"{rel}{BK.CHUNK_SUFFIX}"
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())}


def test_large_files_are_chunked_and_restore_byte_exact(repo: Path, cfg, tmp_path: Path, small_chunks):
    log_rel = f"{BK.LOG_REL}/collector_cycles.jsonl"
    log = repo / log_rel
    log.write_bytes(os.urandom(1500) + b"\r\n" + b'{"a":1}\n' * 100)
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    assert not (cfg.backup_dir / BK.STATE_DIR / log_rel).exists()
    manifest = BK.load_manifest(cfg.backup_dir)
    storage = manifest[log_rel]["storage"]
    assert storage["format"] == BK.CHUNK_FORMAT and len(storage["chunks"]) == 6
    assert sum(c["size"] for c in storage["chunks"]) == log.stat().st_size
    before = _stored_chunks(cfg, log_rel)

    log.write_bytes(log.read_bytes() + b'{"b":2}\n' * 20)
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "pushed" and res["chunked_files"] == 1
    after = _stored_chunks(cfg, log_rel)
    assert len(after) == 7
    for name in sorted(before)[:5]:
        assert after[name] == before[name], "full chunks of an appended file are not rewritten"

    source = BK.clone_and_verify(cfg, tmp_path / "restore")
    assert source.files[log_rel] == log.read_bytes()
    for rel, data in source.files.items():
        assert data == (repo / rel).read_bytes(), rel


def test_corrupt_or_unlisted_chunks_are_refused(repo: Path, cfg, tmp_path: Path, small_chunks):
    log_rel = f"{BK.LOG_REL}/collector_cycles.jsonl"
    (repo / log_rel).write_bytes(os.urandom(2500))
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    BK._hostile_clone(cfg, tmp_path / "c1")
    chunk = tmp_path / "c1" / BK.STATE_DIR / BK.chunk_name(log_rel, 1)
    chunk.write_bytes(gzip.compress(b"tampered", mtime=0))
    with pytest.raises(CO.OpsError, match="chunk 1"):
        list(BK.iter_backup(tmp_path / "c1"))
    BK._hostile_clone(cfg, tmp_path / "c2")
    (tmp_path / "c2" / BK.STATE_DIR / BK.chunk_name(log_rel, 99)).write_bytes(b"x")
    with pytest.raises(CO.OpsError, match="unlisted"):
        list(BK.iter_backup(tmp_path / "c2"))


def test_oversized_blob_in_tree_or_unpushed_history_blocks_push(repo: Path, cfg, monkeypatch):
    monkeypatch.setattr(BK, "MAX_BLOB_BYTES", 5000)
    status = repo / BK.LOG_REL / "collector_status.json"
    status.write_bytes(b"x" * 6000)
    _write(repo, f"{CO.STATE_LOG_DIR}/cycle.lock", b"weather")
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "push_deferred"
    (repo / CO.STATE_LOG_DIR / "cycle.lock").unlink()
    remote_before = CO.remote_ref_sha(cfg.remote, cfg.ref)
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "blocked_oversized"
    assert res["oversized"][0]["path"].endswith("collector_status.json")
    status.write_bytes(b"{}")
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "blocked_oversized", "the old blob is still in the unpushed history"
    assert CO.remote_ref_sha(cfg.remote, cfg.ref) == remote_before


def test_clean_branch_on_pushed_base_uploads_only_new_content(repo: Path, tmp_path: Path, small_chunks):
    remote = tmp_path / "remote2.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    legacy = BK.SportsBackupConfig(collector_id="test-host", remote=str(remote), ref=BK.LEGACY_REF,
                                   backup_dir=tmp_path / "legacy", ssh_command=None)
    BK.backup_init(legacy, create_if_missing=True)
    assert BK.run_backup(legacy, repo_root=repo)["backup"] == "pushed"
    legacy_sha = CO.remote_ref_sha(str(remote), BK.LEGACY_REF)

    big_rel = f"{BK.LOG_REL}/collector_cycles.jsonl"
    (repo / big_rel).write_bytes((repo / big_rel).read_bytes() + os.urandom(3000))
    v2 = BK.SportsBackupConfig(collector_id="test-host", remote=str(remote), ref=BK.SPORTS_REF,
                               backup_dir=tmp_path / "v2", ssh_command=None)
    info = BK.backup_init(v2, create_if_missing=True, base_ref=BK.LEGACY_REF)
    assert info["base_sha"] == legacy_sha
    res = BK.run_backup(v2, repo_root=repo)
    assert res["backup"] == "pushed", res
    chunks = BK.load_manifest(v2.backup_dir)[big_rel]["storage"]["chunks"]
    assert res["pending_blobs"] == len(chunks) + 1, "only the new chunks and the manifest are uploaded"
    assert CO.remote_ref_sha(str(remote), BK.LEGACY_REF) == legacy_sha
    head = CO.remote_ref_sha(str(remote), BK.SPORTS_REF)
    assert subprocess.run(["git", "-C", str(v2.backup_dir), "merge-base", "--is-ancestor", legacy_sha, head]).returncode == 0
    source = BK.clone_and_verify(v2, tmp_path / "restore")
    assert source.files[big_rel] == (repo / big_rel).read_bytes()
    with pytest.raises(CO.OpsError):
        BK.backup_init(v2, create_if_missing=True, base_ref=BK.LEGACY_REF)


def test_legacy_v1_manifest_is_readable(tmp_path: Path):
    clone = tmp_path / "v1"
    _write(clone, f"{BK.STATE_DIR}/a/b.json", b"{}\r\n")
    (clone / BK.MANIFEST).write_text(json.dumps({"schema": "kel_sports_state_backup_v1", "profile": "sports", "files": {
        "a/b.json": {"sha256": sha256_bytes(b"{}\r\n"), "size": 4, "class": BK.WRITE_ONCE}}}))
    assert [(r, d) for r, d, _ in BK.iter_backup(clone)] == [("a/b.json", b"{}\r\n")]


def test_pause_skips_only_scheduled_runs(repo: Path, monkeypatch, capsys):
    monkeypatch.setattr(BK, "PAUSE_PATH", BK.pause_path(repo))
    monkeypatch.setattr(BK, "STATUS_PATH", BK.status_path(repo))
    assert BK.main(["pause", "--reason", "repair"]) == 0
    capsys.readouterr()
    assert BK.main(["run", "--if-due", "--purpose", "scheduled"]) == 0
    assert json.loads(capsys.readouterr().out)["backup"] == "paused"
    assert BK.read_status(repo) is None
    assert BK.main(["resume"]) == 0
    assert BK.read_pause(repo) is None


def test_config_refuses_the_preserved_legacy_clone(tmp_path: Path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"collector_id": "h", "control_remote": "r", "backup_remote": "s",
                             "backup_dir": str(tmp_path / "w")}))
    with pytest.raises(CO.ConfigError, match="preserved"):
        BK.load_config(p, backup_dir=BK.LEGACY_DIR)


# ---------------------------------------------------------------- decoupled, short-lock snapshots

DEP = "data/sports/normalized/nfl/events.jsonl"
LOG = f"{BK.LOG_REL}/collector_cycles.jsonl"
CYCLE_LOCK = f"{BK.LOG_REL}/cycle.lock"


def _hashes(root: Path, rel_dir: str) -> dict[str, str]:
    base = root / rel_dir
    return {p.relative_to(root).as_posix(): sha256_bytes(p.read_bytes()) for p in base.rglob("*") if p.is_file()}


def _committed_manifest(cfg) -> dict:
    out = subprocess.run(["git", "-C", str(cfg.backup_dir), "show", f"HEAD:{BK.MANIFEST}"], capture_output=True,
                         check=True).stdout
    return json.loads(out)["files"]


def _head(cfg) -> str:
    return subprocess.run(["git", "-C", str(cfg.backup_dir), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def _commits(cfg) -> int:
    return int(subprocess.run(["git", "-C", str(cfg.backup_dir), "rev-list", "--count", "HEAD"], capture_output=True,
                              text=True, check=True).stdout)


def _new_record(repo: Path, name: str, run_id: str) -> str:
    ev = _evidence(repo, BK.CAPTURE_EVIDENCE_REL, run_id, "kalshi", f"https://x/{name}", f"body {name}".encode())
    rel = f"{BK.CAPTURE_REL}/accepted/nfl/{name}/c.json"
    _write(repo, rel, json.dumps({"run_id": run_id, "dependencies": {}, "evidence": [ev]}).encode())
    return rel


@pytest.fixture()
def hooks(monkeypatch):
    monkeypatch.setattr(BK, "HOOKS", {})
    return BK.HOOKS


def test_snapshot_lock_covers_only_listing_status_copy_and_links(repo: Path, cfg):
    first = BK.run_backup(cfg, repo_root=repo)
    assert first["backup"] == "pushed" and first["snapshot"] == "committed"
    res = BK.run_backup(cfg, repo_root=repo)
    t = res["snapshot_timings"]
    assert res["staging"] == "hardlink" and res["staging_fallback_reason"] is None
    assert t["hard_links"] == 1 and t["listed_files"] >= 5
    assert res["sports_lock_held_s"] == t["lock_held_s"] < 5
    assert not (repo / BK.STAGING_REL).exists(), "staging links are removed after the run"
    assert not (repo / CYCLE_LOCK).exists() and not BK.backup_lock_path(repo).exists()


def test_slow_upload_runs_alongside_capture_and_overlapping_trigger_is_skipped(repo: Path, cfg, monkeypatch):
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    accepted_before = _hashes(repo, f"{BK.CAPTURE_REL}/accepted")
    log = repo / LOG
    with open(log, "ab") as f:
        f.write(b'{"cycle":2}\n')
    log_at_snapshot = log.read_bytes()
    started, release = threading.Event(), threading.Event()
    real_push = BK._push_ref

    def slow_push(c, head):
        started.set()
        assert release.wait(60)
        real_push(c, head)

    monkeypatch.setattr(BK, "_push_ref", slow_push)
    out: dict = {}
    worker = threading.Thread(target=lambda: out.update(BK.run_backup(cfg, repo_root=repo)))
    worker.start()
    try:
        assert started.wait(60), "upload never started"
        t0 = time.monotonic()
        with LK.sports_lock("collector cycle", repo / CYCLE_LOCK):  # a capture cycle while the upload is running
            waited = time.monotonic() - t0
            new_rel = _new_record(repo, "E2", "R2")
            with open(log, "ab") as f:
                f.write(b'{"cycle":3}\n')
        assert waited < 1.0
        overlap = BK.run_backup(cfg, repo_root=repo)
        assert overlap["backup"] == "skipped_overlap" and "backup-run" in overlap["error"]
    finally:
        release.set()
        worker.join(120)
    assert out["backup"] == "pushed", out
    manifest = _committed_manifest(cfg)
    assert new_rel not in manifest, "files created after the locked listing belong to the next snapshot"
    assert manifest[LOG]["size"] == len(log_at_snapshot)
    assert manifest[LOG]["sha256"] == sha256_bytes(log_at_snapshot)
    assert BK.read_status(repo)["backup"] == "pushed", "the overlapping trigger did not overwrite the status"
    after = _hashes(repo, f"{BK.CAPTURE_REL}/accepted")
    assert all(after[r] == h for r, h in accepted_before.items()) and new_rel in after
    nxt = BK.run_backup(cfg, repo_root=repo)
    assert nxt["backup"] == "pushed" and new_rel in _committed_manifest(cfg)
    source = BK.clone_and_verify(cfg, repo.parent / "restore")
    for rel, data in source.files.items():
        assert data == (repo / rel).read_bytes(), rel


def test_upload_failure_keeps_commit_and_retries_without_the_sports_lock(repo: Path, cfg, monkeypatch):
    real_push = BK._push_ref

    def down(c, head):
        raise CO.GitError("push failed: network unreachable")

    monkeypatch.setattr(BK, "_push_ref", down)
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "push_failed" and res["snapshot"] == "committed" and res["unpushed"] is True
    head, commits = res["local_head"], _commits(cfg)
    assert CO.remote_ref_sha(cfg.remote, cfg.ref) != head
    assert BK.has_unpushed(cfg, repo) and not BK.is_due(repo), "retry uploads; it does not take a new snapshot"
    monkeypatch.setattr(BK, "_push_ref", real_push)
    taken: list[str] = []
    real_lock = LK.sports_lock

    def spy(purpose, path=None, log=None):
        taken.append(purpose)
        return real_lock(purpose, path, log)

    monkeypatch.setattr(LK, "sports_lock", spy)
    retry = BK.run_backup(cfg, repo_root=repo, snapshot=False)
    assert retry["backup"] == "pushed" and retry["local_head"] == head and taken == []
    assert CO.remote_ref_sha(cfg.remote, cfg.ref) == head and _commits(cfg) == commits
    assert not BK.has_unpushed(cfg, repo)


def test_atomic_replacement_after_listing_keeps_lock_time_bytes(repo: Path, cfg, hooks):
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    dep = repo / DEP
    old = dep.read_bytes()

    def replace(snap):
        assert DEP in snap.staged
        tmp = dep.with_name(dep.name + ".tmp")
        tmp.write_bytes(b"rebuilt history\n")
        os.replace(tmp, dep)

    hooks["after_lock"] = replace
    _new_record(repo, "E3", "R3")  # something new so a commit is made
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "pushed", res
    assert _committed_manifest(cfg)[DEP]["sha256"] == sha256_bytes(old)
    assert BK.clone_and_verify(cfg, repo.parent / "restore").files[DEP] == old


def test_in_place_write_to_hard_linked_file_is_rejected(repo: Path, cfg, hooks):
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    head, manifest = _head(cfg), _committed_manifest(cfg)
    dep = repo / DEP

    def mutate(snap):
        staged_path, _ = snap.staged[DEP]
        assert os.stat(staged_path).st_ino == os.stat(dep).st_ino
        time.sleep(0.05)
        with open(dep, "r+b") as f:  # in place: the hard link sees the same change
            f.write(b"LINE1")

    hooks["after_lock"] = mutate
    _new_record(repo, "E4", "R4")
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "inconsistent_snapshot" and DEP in res["error"], res
    assert _head(cfg) == head and BK.load_manifest(cfg.backup_dir) == manifest, "nothing committed, tree reset"
    assert CO.remote_ref_sha(cfg.remote, cfg.ref) == head
    assert BK.is_due(repo), "an inconsistent snapshot is retried at the next check"


def test_in_place_change_to_listed_record_after_listing_is_rejected(repo: Path, cfg, hooks):
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    rel = _new_record(repo, "E5", "R5")

    def mutate(snap):
        assert rel in snap.listing
        time.sleep(0.05)
        with open(repo / rel, "r+b") as f:
            f.seek(0, os.SEEK_END)
            f.write(b" ")

    hooks["after_lock"] = mutate
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "inconsistent_snapshot" and rel in res["error"]
    assert rel not in _committed_manifest(cfg)


def test_rewritten_append_only_log_is_refused(repo: Path, cfg):
    log = repo / LOG
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    log.write_bytes(b'{"A":1}\n' + b'{"b":2}\n')  # longer, but the old prefix changed
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "failed" and "rewritten" in res["error"]


def test_corrupt_staged_clone_is_rejected_before_commit(repo: Path, cfg, hooks):
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    head = _head(cfg)
    rel = _new_record(repo, "E6", "R6")

    def corrupt(c, manifest, written):
        assert rel in written
        (c.backup_dir / BK.STATE_DIR / rel).write_bytes(b"corrupted")

    hooks["before_verify"] = corrupt
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "inconsistent_snapshot" and "manifest" in res["error"]
    assert _head(cfg) == head and not (cfg.backup_dir / BK.STATE_DIR / rel).exists()


def test_vanished_backed_up_state_is_refused_never_deleted(repo: Path, cfg):
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    head, manifest = _head(cfg), _committed_manifest(cfg)
    body = next(r for r in manifest if r.startswith(BK.CAPTURE_EVIDENCE_REL) and r.endswith(".body"))
    data = (repo / body).read_bytes()
    (repo / body).unlink()
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "failed" and "vanished" in res["error"]
    assert res["vanished_by_kind"] == {"capture evidence": 1}
    assert _head(cfg) == head and (cfg.backup_dir / BK.STATE_DIR / body).read_bytes() == data
    score = f"{BK.SCORE_REL}/finalized/nfl/E1/T.json"
    items = {r: BK.Item(path=repo / r) for r in manifest if r != score and (repo / r).is_file()}
    with pytest.raises(CO.OpsError, match="refusing to delete"):
        BK._mirror(cfg, items, manifest, BK.Hasher())


def test_evidence_not_matching_its_pin_is_refused(repo: Path, cfg):
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"
    ev = _evidence(repo, BK.CAPTURE_EVIDENCE_REL, "R7", "kalshi", "https://x/pinned", b"actual")
    ev["sha256"] = sha256_bytes(b"what the record saw")
    _write(repo, f"{BK.CAPTURE_REL}/accepted/nfl/E7/c.json",
           json.dumps({"run_id": "R7", "dependencies": {}, "evidence": [ev]}).encode())
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "failed" and "pinned" in res["error"]


def test_hard_link_failure_falls_back_to_verified_reads_outside_the_lock(repo: Path, cfg, monkeypatch):
    assert BK.run_backup(cfg, repo_root=repo)["backup"] == "pushed"

    def no_links(src, dst):
        raise OSError(1, "hard links not supported on this volume")

    monkeypatch.setattr(BK.os, "link", no_links)
    _new_record(repo, "E8", "R8")
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "pushed", res
    assert res["staging"] == "verified_read" and "hard links not supported" in res["staging_fallback_reason"]
    assert res["snapshot_timings"]["hard_links"] == 0


def test_status_file_too_large_to_copy_under_the_lock_is_refused(repo: Path, cfg, monkeypatch):
    monkeypatch.setattr(BK, "SMALL_COPY_MAX", 10)
    (repo / BK.LOG_REL / "collector_status.json").write_bytes(b"{" + b" " * 50 + b"}")
    res = BK.run_backup(cfg, repo_root=repo)
    assert res["backup"] == "failed" and "only small status files" in res["error"]
    assert not (repo / CYCLE_LOCK).exists()


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_capture_task_no_longer_runs_the_backup():
    text = (SCRIPTS / "sports_prospective_task.ps1").read_text(encoding="utf-8")
    assert "research.sports.ops.backup" not in text and "research.sports.ops.collector" in text


def test_backup_task_registration_is_separate_and_bounded():
    reg = (SCRIPTS / "register_sports_backup_task.ps1").read_text(encoding="utf-8")
    runner = (SCRIPTS / "sports_backup_task.ps1").read_text(encoding="utf-8")
    assert "KalshiEdgeLab-SportsBackup" in reg and BK.BACKUP_TASK_NAME == "KalshiEdgeLab-SportsBackup"
    assert "IgnoreNew" in reg and "-Priority 7" in reg and "-Minutes 15" in reg
    assert "Weather" in reg and "KalshiEdgeLab-SportsProspective" in reg  # both refused as targets
    assert "'run', '--if-due', '--purpose', 'scheduled', '--low-priority'" in runner


def test_lower_priority_takes_effect_in_a_child_process():
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-c", "from research.sports.ops import backup as BK; print(BK._lower_priority())"],
                         capture_output=True, text=True, cwd=BK.REPO_ROOT, timeout=60).stdout.strip()
    assert out in ("below_normal+background", "nice+5"), out

"""Sports state backup: allowlist, write-once protection, lock/weather coordination, no-force push, restore."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
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
    BK._write_status(repo, {"backup": "pushed", "at": BK._iso(now - timedelta(minutes=10))})
    assert not BK.is_due(repo, now)
    BK._write_status(repo, {"backup": "pushed", "at": BK._iso(now - timedelta(minutes=61))})
    assert BK.is_due(repo, now)
    BK._write_status(repo, {"backup": "push_failed", "at": BK._iso(now - timedelta(minutes=5))})
    assert not BK.is_due(repo, now)
    assert BK.is_due(repo, now + BK.FAILURE_BACKOFF)
    BK._write_status(repo, {"backup": "push_deferred", "at": BK._iso(now)})
    assert BK.is_due(repo, now)


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

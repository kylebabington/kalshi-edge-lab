"""Storage policy, safe cleanup, cache write safety, and disk-full guards.

RESEARCH_ONLY / NO_BET. Never deletes real research evidence; all paths are under tmp_path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from research.storage import cleanup as CL
from research.storage import config as CFG
from research.storage import health as H
from research.storage import policy as P
from research.storage import refs as R
from research.storage import write as W
from research.storage.run import main as storage_main
from research.sports.core import Fetcher, sha256_bytes


# ------------------------------------------------------------------ classification

def test_classify_immutable_capture_evidence(tmp_path):
    p = tmp_path / "data/cache/sports/raw_capture_v2/20261102T000000Z/espn/ab/abc.body"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    assert P.classify(p, root=tmp_path) == P.CLASS_IMMUTABLE
    assert P.is_protected(p, root=tmp_path)


def test_classify_http_cache_and_temp(tmp_path):
    cache = tmp_path / "data/cache/sports/raw/espn/ab/abc.body"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"y")
    assert P.classify(cache, root=tmp_path) == P.CLASS_HTTP_CACHE
    tmp = tmp_path / "data/cache/sports/raw/espn/ab/.abc.body.tmp"
    tmp.write_bytes(b"z")
    assert P.classify(tmp, root=tmp_path) == P.CLASS_TEMP


def test_classify_append_only_logs(tmp_path):
    log = tmp_path / "data/logs/sports/collector_cycles.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("{}\n", encoding="utf-8")
    assert P.classify(log, root=tmp_path) == P.CLASS_APPEND_ONLY
    assert P.is_protected(log, root=tmp_path)


# ------------------------------------------------------------------ write safety

def test_write_cache_pair_atomic_and_readable(tmp_path):
    body_p = tmp_path / "a.body"
    meta_p = tmp_path / "a.meta.json"
    body = b'{"ok":true}'
    meta = {"status": 200, "sha256": sha256_bytes(body)}
    W.write_cache_pair(body_p, meta_p, body, meta, write_once=False)
    assert body_p.read_bytes() == body
    assert json.loads(meta_p.read_text(encoding="utf-8"))["sha256"] == sha256_bytes(body)


def test_write_once_refuses_different_bytes(tmp_path):
    body_p = tmp_path / "e.body"
    meta_p = tmp_path / "e.meta.json"
    first = b"evidence-one"
    W.write_cache_pair(body_p, meta_p, first, {"status": 200}, write_once=True)
    with pytest.raises(W.EvidenceWriteError, match="refusing to overwrite"):
        W.write_cache_pair(body_p, meta_p, b"evidence-two", {"status": 200}, write_once=True)
    assert body_p.read_bytes() == first


def test_write_once_identical_bytes_noop(tmp_path):
    body_p = tmp_path / "e.body"
    meta_p = tmp_path / "e.meta.json"
    data = b"same"
    W.write_cache_pair(body_p, meta_p, data, {"status": 200}, write_once=True)
    W.write_cache_pair(body_p, meta_p, data, {"status": 200, "extra": 1}, write_once=True)
    assert body_p.read_bytes() == data


def test_fetcher_uses_atomic_cache_write(tmp_path, monkeypatch):
    calls = []

    def fake_pair(body_p, meta_p, body, meta, *, write_once=False):
        calls.append({"body_p": body_p, "write_once": write_once, "n": len(body)})
        body_p.parent.mkdir(parents=True, exist_ok=True)
        body_p.write_bytes(body)
        meta_p.write_text(json.dumps(meta), encoding="utf-8")

    monkeypatch.setattr("research.storage.write.write_cache_pair", fake_pair)

    class Resp:
        status_code = 200
        content = b'{"hi":1}'
        headers = {"content-type": "application/json"}

    class Sess:
        def get(self, *a, **k):
            return Resp()

    # Point classify so reconstructable root is not write-once
    root = tmp_path / "data/cache/sports/raw"
    f = Fetcher(root=root, min_interval=0.0, max_retries=0, session=Sess())
    raw = f.get("espn", "https://example.test/x", {})
    assert raw.status == 200
    assert calls and calls[0]["write_once"] is False


def test_fetcher_disk_full_does_not_leave_partial_body(tmp_path, monkeypatch):
    root = tmp_path / "data/cache/sports/raw_capture_v2" / "run1"
    root.mkdir(parents=True)

    def boom(*a, **k):
        raise W.DiskFullError("disk full writing test")

    monkeypatch.setattr("research.storage.cas.write_evidence_payload", boom)

    class Resp:
        status_code = 200
        content = b"payload"
        headers = {}

    class Sess:
        def get(self, *a, **k):
            return Resp()

    f = Fetcher(root=root, min_interval=0.0, max_retries=0, session=Sess())
    with pytest.raises(W.DiskFullError):
        f.get("kalshi", "https://example.test/m", {"cursor": "1"})
    bodies = list(root.rglob("*.body"))
    assert bodies == []


# ------------------------------------------------------------------ CAS evidence

def test_cas_dedupes_identical_payloads_preserves_receipts(tmp_path):
    from research.storage import cas as CAS

    blob_root = tmp_path  # store blobs under tmp_path/data/cache/sports/evidence_blobs_v1
    run_a = tmp_path / "data/cache/sports/raw_capture_v2/RUN_A/espn/ab"
    run_b = tmp_path / "data/cache/sports/raw_capture_v2/RUN_B/espn/ab"
    run_a.mkdir(parents=True)
    run_b.mkdir(parents=True)
    body = b'{"scoreboard":true,"games":[1,2,3]}' * 50
    meta_a = {"url": "https://ex/a", "status": 200, "retrieved_at": "2026-11-04T01:00:00Z"}
    meta_b = {"url": "https://ex/a", "status": 200, "retrieved_at": "2026-11-04T01:05:00Z"}
    key = "ab" * 32
    body_a = run_a / f"{key}.body"
    meta_a_p = run_a / f"{key}.meta.json"
    body_b = run_b / f"{key}.body"
    meta_b_p = run_b / f"{key}.meta.json"
    s1 = CAS.write_evidence_payload(body_a, meta_a_p, body, meta_a, blob_repo_root=tmp_path)
    s2 = CAS.write_evidence_payload(body_b, meta_b_p, body, meta_b, blob_repo_root=tmp_path)
    assert s1["sha256"] == s2["sha256"]
    assert s2["deduped"] is True
    assert not body_a.exists() and not body_b.exists()
    got_a = CAS.read_evidence_payload(body_a, meta_a_p, blob_repo_root=tmp_path)
    got_b = CAS.read_evidence_payload(body_b, meta_b_p, blob_repo_root=tmp_path)
    assert got_a == body == got_b
    ma = json.loads(meta_a_p.read_text(encoding="utf-8"))
    mb = json.loads(meta_b_p.read_text(encoding="utf-8"))
    assert ma["retrieved_at"] != mb["retrieved_at"]
    assert ma["sha256"] == mb["sha256"] == s1["sha256"]


def test_cas_legacy_body_still_readable(tmp_path):
    from research.storage import cas as CAS

    body_p = tmp_path / "k.body"
    meta_p = tmp_path / "k.meta.json"
    data = b"legacy-bytes"
    body_p.write_bytes(data)
    meta_p.write_text(json.dumps({"sha256": sha256_bytes(data), "status": 200}), encoding="utf-8")
    assert CAS.read_evidence_payload(body_p, meta_p) == data


def test_fetcher_evidence_root_uses_cas(tmp_path, monkeypatch):
    import research.storage.cas as CAS

    monkeypatch.setattr(CAS, "DEFAULT_BLOB_ROOT", tmp_path / CAS.BLOBS_REL)
    evidence = tmp_path / "data/cache/sports/raw_capture_v2" / "runX"

    class Resp:
        status_code = 200
        content = b'{"ok":1}'
        headers = {"content-type": "application/json"}

    class Sess:
        def get(self, *a, **k):
            return Resp()

    f = Fetcher(root=evidence, min_interval=0.0, max_retries=0, session=Sess())
    raw = f.get("kalshi", "https://example.test/cas", {"p": "1"})
    assert raw.status == 200
    evidence2 = tmp_path / "data/cache/sports/raw_capture_v2" / "runY"
    f2 = Fetcher(root=evidence2, min_interval=0.0, max_retries=0, session=Sess())
    raw2 = f2.get("kalshi", "https://example.test/cas", {"p": "1"})
    assert raw2.body == raw.body
    blob_files = [
        p for p in (tmp_path / CAS.BLOBS_REL).rglob("*")
        if p.is_file() and not p.name.endswith(".tmp")
    ]
    assert len(blob_files) == 1
    hit = f2.cached("kalshi", "https://example.test/cas", {"p": "1"})
    assert hit is not None and hit.body == raw.body


def test_cleanup_finds_inventory_tmp(tmp_path):
    inv = tmp_path / "data/cache/inventory"
    inv.mkdir(parents=True)
    orphan = inv / ".usable_inventory.abc123.jsonl.tmp"
    orphan.write_bytes(b"x" * 1000)
    evidence = tmp_path / "data/cache/sports/raw_capture_v2/RUN/espn/aa/aa.body"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"pin")
    cfg = CFG.StorageConfig(refuse_cleanup_when_collectors_busy=False, cache_limits=[])
    plan = CL.run_cleanup(root=tmp_path, cfg=cfg, dry_run=True, include_http_cache=False,
                          required_refs=set())
    paths = {c["path"] for c in plan.candidates}
    assert any(p.endswith(".usable_inventory.abc123.jsonl.tmp") for p in paths)
    assert plan.bytes_eligible >= 1000


# ------------------------------------------------------------------ cleanup safety

def _seed_cache_tree(root: Path):
    old = root / "data/cache/sports/raw/espn/aa/old.body"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old-cache")
    os.utime(old, (1_000_000_000, 1_000_000_000))  # old mtime
    fresh = root / "data/cache/sports/raw/espn/bb/fresh.body"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"fresh-cache")
    evidence = root / "data/cache/sports/raw_capture_v2/RUN1/espn/cc/cc.body"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"pinned")
    meta = evidence.with_suffix(".meta.json")
    # sidecar naming is .body -> need .meta.json alongside
    meta = evidence.parent / "cc.meta.json"
    meta.write_text("{}", encoding="utf-8")
    tmp = root / "data/cache/sports/raw/espn/aa/.old.body.tmp"
    tmp.write_bytes(b"tmp")
    return old, fresh, evidence, tmp


def test_cleanup_dry_run_lists_only_disposable(tmp_path, monkeypatch):
    old, fresh, evidence, tmp = _seed_cache_tree(tmp_path)
    cfg = CFG.StorageConfig(
        cache_limits=[CFG.CacheLimit("data/cache/sports/raw/", max_age_days=0.0001, max_bytes=10**15)],
        refuse_cleanup_when_collectors_busy=False,
    )
    monkeypatch.setattr(CL, "collect_required_evidence_paths", lambda **k: {evidence.resolve()})
    # Force "now" far in the future relative to old mtime so age filter hits old only when age days tiny
    plan = CL.run_cleanup(root=tmp_path, cfg=cfg, dry_run=True, required_refs={evidence.resolve()})
    paths = {c["path"] for c in plan.candidates}
    assert any(p.endswith("old.body") for p in paths) or any(p.endswith(".tmp") for p in paths)
    assert not any("raw_capture_v2" in p for p in paths)
    assert plan.bytes_deleted == 0
    assert evidence.exists()


def test_cleanup_refuses_without_confirm(tmp_path):
    _seed_cache_tree(tmp_path)
    cfg = CFG.StorageConfig(refuse_cleanup_when_collectors_busy=False,
                            cache_limits=[CFG.CacheLimit("data/cache/sports/raw/", max_age_days=0, max_bytes=1)])
    plan = CL.run_cleanup(root=tmp_path, cfg=cfg, dry_run=False, confirm=None,
                          required_refs=set())
    assert plan.refused
    assert (tmp_path / "data/cache/sports/raw_capture_v2/RUN1/espn/cc/cc.body").exists()


def test_cleanup_refuses_when_collector_busy(tmp_path, monkeypatch):
    _seed_cache_tree(tmp_path)
    monkeypatch.setattr(CL, "collector_lock_snapshot",
                        lambda root=None: {"sports": {"held": True}, "weather": {"held": False}, "any_busy": True})
    cfg = CFG.StorageConfig(refuse_cleanup_when_collectors_busy=True)
    plan = CL.run_cleanup(root=tmp_path, cfg=cfg, dry_run=False, confirm=CFG.CONFIRM_TOKEN,
                          required_refs=set())
    assert any("collector lock" in r for r in plan.refused)


def test_cleanup_never_deletes_referenced_path(tmp_path):
    root = tmp_path
    # Place a file under HTTP cache that is also in required refs (defense in depth).
    target = root / "data/cache/sports/raw/espn/zz/secret.body"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"secret")
    os.utime(target, (1, 1))
    cfg = CFG.StorageConfig(
        refuse_cleanup_when_collectors_busy=False,
        cache_limits=[CFG.CacheLimit("data/cache/sports/raw/", max_age_days=0, max_bytes=1)],
    )
    plan = CL.run_cleanup(root=root, cfg=cfg, dry_run=False, confirm=CFG.CONFIRM_TOKEN,
                          required_refs={target.resolve()})
    assert target.exists()
    assert plan.skipped_referenced >= 1 or target.as_posix() not in {d["path"] for d in plan.deleted}


def test_cleanup_execute_deletes_temp_only(tmp_path):
    _seed_cache_tree(tmp_path)
    tmp = tmp_path / "data/cache/sports/raw/espn/aa/.old.body.tmp"
    assert tmp.exists()
    cfg = CFG.StorageConfig(refuse_cleanup_when_collectors_busy=False)
    plan = CL.run_cleanup(root=tmp_path, cfg=cfg, dry_run=False, confirm=CFG.CONFIRM_TOKEN,
                          include_http_cache=False, include_temp=True, required_refs=set())
    assert not tmp.exists()
    assert (tmp_path / "data/cache/sports/raw_capture_v2/RUN1/espn/cc/cc.body").exists()
    assert plan.bytes_deleted >= 1


# ------------------------------------------------------------------ refs

def test_collect_capture_refs(tmp_path):
    cap = tmp_path / "data/sports/phase2/capture_v2"
    ev = tmp_path / "data/cache/sports/raw_capture_v2"
    rec_dir = cap / "accepted" / "checkpoint" / "nfl"
    rec_dir.mkdir(parents=True)
    key = "abcd" * 16
    rec = {
        "run_id": "RUNX",
        "evidence": [{"key": key, "source": "espn", "url": "u", "params": {}, "sha256": "0" * 64}],
    }
    (rec_dir / "espn_1.json").write_text(json.dumps(rec), encoding="utf-8")
    refs = R.collect_sports_capture_refs(root=tmp_path)
    assert any(p.name == f"{key}.body" for p in refs)


# ------------------------------------------------------------------ health + CLI

def test_assess_disk_levels(tmp_path, monkeypatch):
    class Usage:
        total = 1000
        used = 950
        free = 50

    monkeypatch.setattr(H.shutil, "disk_usage", lambda p: Usage())
    cfg = CFG.StorageConfig(free_warning_bytes=200, free_critical_bytes=100)
    health = H.assess_disk(root=tmp_path, cfg=cfg, record_sample=False, budget_s_per_dir=0.1)
    assert health.level == "critical"
    Usage.free = 150
    health = H.assess_disk(root=tmp_path, cfg=cfg, record_sample=False, budget_s_per_dir=0.1)
    assert health.level == "warning"
    Usage.free = 500
    health = H.assess_disk(root=tmp_path, cfg=cfg, record_sample=False, budget_s_per_dir=0.1)
    assert health.level == "ok"


def test_cli_policy_and_cleanup_dry_run(tmp_path, capsys):
    assert storage_main(["--root", str(tmp_path), "policy"]) == 0
    out = capsys.readouterr().out
    assert "kel_storage_policy_v1" in out
    assert "immutable_evidence" in out
    assert storage_main(["--root", str(tmp_path), "cleanup"]) == 0
    out = capsys.readouterr().out
    assert "kel_storage_cleanup_plan_v1" in out
    assert '"dry_run": true' in out or '"dry_run": true'.replace(" ", "") in out.replace(" ", "")


def test_load_config_override(tmp_path):
    cfg_path = tmp_path / "data/logs/storage/storage_policy.local.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({
        "free_critical_bytes": 123,
        "cache_limits": [{"prefix": "data/cache/weather/", "max_age_days": 3, "max_bytes": 99}],
    }), encoding="utf-8")
    cfg = CFG.load_config(path=cfg_path, root=tmp_path)
    assert cfg.free_critical_bytes == 123
    assert cfg.cache_limits[0].max_age_days == 3


# ------------------------------------------------------------------ migrate + archive-aware read

def test_migrate_batch_to_archive_keeps_source_body(tmp_path, monkeypatch):
    from research.storage import migrate_evidence as ME
    from research.storage import cas as CAS

    archive = tmp_path / "archive"
    archive.mkdir()
    # Two runs, identical payload → dedupe on archive
    payload = b'{"markets":[1,2,3]}' * 20
    for run in ("RUNA", "RUNB"):
        body = tmp_path / f"data/cache/sports/raw_capture_v2/{run}/kalshi/aa/aabb.body"
        meta = body.parent / "aabb.meta.json"
        body.parent.mkdir(parents=True)
        body.write_bytes(payload)
        meta.write_text(json.dumps({"sha256": sha256_bytes(payload), "status": 200}), encoding="utf-8")

    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(archive))
    result = ME.migrate_batch(
        repo_root=tmp_path,
        archive_dir=archive,
        batch_size=10,
        dry_run=False,
        checkpoint_path=tmp_path / "data/logs/storage/migrate_evidence_checkpoint.json",
    )
    assert not result.refused, result.refused
    assert result.files_migrated >= 2
    assert result.verified >= 2
    # Sources kept
    assert (tmp_path / "data/cache/sports/raw_capture_v2/RUNA/kalshi/aa/aabb.body").is_file()
    assert (tmp_path / "data/cache/sports/raw_capture_v2/RUNB/kalshi/aa/aabb.body").is_file()
    blobs = list((archive / "sports/evidence_blobs_v1").rglob("*"))
    blob_files = [p for p in blobs if p.is_file() and not p.name.endswith(".tmp")]
    assert len(blob_files) == 1
    # Meta points at archive
    meta_a = json.loads(
        (tmp_path / "data/cache/sports/raw_capture_v2/RUNA/kalshi/aa/aabb.meta.json").read_text(encoding="utf-8")
    )
    assert meta_a["storage"]["location"] == CAS.LOCATION_ARCHIVE
    # Resume: second call migrates nothing new
    result2 = ME.migrate_batch(
        repo_root=tmp_path,
        archive_dir=archive,
        batch_size=10,
        dry_run=False,
        checkpoint_path=tmp_path / "data/logs/storage/migrate_evidence_checkpoint.json",
    )
    assert result2.files_migrated == 0 or result2.refused  # completed runs skipped
    rep = ME.retirement_report(
        repo_root=tmp_path,
        archive_dir=archive,
        checkpoint_path=tmp_path / "data/logs/storage/migrate_evidence_checkpoint.json",
    )
    assert rep["deletion_status"].startswith("NOT_EXECUTED")
    assert rep["original_bytes"] > 0


def test_read_evidence_prefers_legacy_then_archive(tmp_path, monkeypatch):
    from research.storage import cas as CAS

    archive = tmp_path / "archive"
    body_p = tmp_path / "run/kalshi/aa/k.body"
    meta_p = tmp_path / "run/kalshi/aa/k.meta.json"
    body_p.parent.mkdir(parents=True)
    data = b"archive-only-payload"
    storage = CAS.store_blob(
        data, absolute_blob_root=CAS.archive_blobs_dir(archive), location=CAS.LOCATION_ARCHIVE
    )
    meta_p.write_text(
        json.dumps({"sha256": sha256_bytes(data), "storage": storage}),
        encoding="utf-8",
    )
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(archive))
    # No legacy body → read from archive
    assert CAS.read_evidence_payload(body_p, meta_p) == data
    # Legacy body wins even if archive present
    body_p.write_bytes(data)
    assert CAS.read_evidence_payload(body_p, meta_p) == data
    # Missing archive → clear error when no local body
    body_p.unlink()
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(tmp_path / "missing-archive-root-xyz"))
    # Volume path may still "exist" as missing folder on same drive — force unavailable
    monkeypatch.setattr(
        "research.storage.roots.archive_status",
        lambda roots=None: __import__("research.storage.roots", fromlist=["ArchiveStatus"]).ArchiveStatus(
            True, False, "X:/nope", None, "archive volume missing"
        ),
    )
    with pytest.raises(CAS.EvidenceMissing, match="archive"):
        CAS.read_evidence_payload(body_p, meta_p)


def test_saved_evidence_archive_only_isolated_root(tmp_path, monkeypatch):
    """Actual SavedEvidence consumer with .body absent; fail if it could see a local body."""
    from research.storage import cas as CAS
    from research.sports.live import discover as D
    from research.sports.core import request_key

    archive = tmp_path / "archive"
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(archive))
    evidence_root = tmp_path / "evidence_run"
    url, params = "https://example.test/scoreboard", {"dates": "20261101"}
    key = request_key(url, params)
    source = "espn"
    body = b'{"events":[{"id":1}]}'
    storage = CAS.store_blob(
        body, absolute_blob_root=CAS.archive_blobs_dir(archive), location=CAS.LOCATION_ARCHIVE
    )
    meta_dir = evidence_root / source / key[:2]
    meta_dir.mkdir(parents=True)
    meta_p = meta_dir / f"{key}.meta.json"
    body_p = meta_dir / f"{key}.body"
    meta_p.write_text(
        json.dumps({
            "sha256": sha256_bytes(body),
            "status": 200,
            "storage": storage,
            "url": url,
            "params": params,
        }),
        encoding="utf-8",
    )
    assert not body_p.exists()
    # Place a trap body elsewhere that must not be used
    trap = tmp_path / "trap" / source / key[:2] / f"{key}.body"
    trap.parent.mkdir(parents=True)
    trap.write_bytes(b"WRONG")

    entries = [{
        "key": key, "source": source, "url": url, "params": params,
        "status": 200, "sha256": sha256_bytes(body), "bytes": len(body),
        "requested_at": "2026-11-01T00:00:00.000000Z",
        "received_at": "2026-11-01T00:00:00.100000Z",
    }]
    saved = D.SavedEvidence(evidence_root, entries)
    raw = saved.get(source, url, params)
    assert raw.body == body
    assert raw.body != b"WRONG"


def test_verify_archive_mode_b_no_empty_source_success(tmp_path, monkeypatch):
    from research.storage import inventory as INV
    from research.storage import migrate_evidence as ME
    from research.storage import verify_archive as VA
    from research.storage import cas as CAS

    archive = tmp_path / "archive"
    archive.mkdir()
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(archive))
    payload = b'{"ok":true}'
    run = "RUN1"
    body = tmp_path / f"data/cache/sports/raw_capture_v2/{run}/kalshi/aa/aabb.body"
    meta = body.parent / "aabb.meta.json"
    body.parent.mkdir(parents=True)
    body.write_bytes(payload)
    digest = sha256_bytes(payload)
    meta.write_text(json.dumps({"sha256": digest, "bytes": len(payload), "status": 200}), encoding="utf-8")
    ME.migrate_batch(
        repo_root=tmp_path, archive_dir=archive, batch_size=1, dry_run=False,
        checkpoint_path=tmp_path / "data/logs/storage/migrate_evidence_checkpoint.json",
    )
    INV.build_inventory(repo_root=tmp_path, archive_dir=archive, run_ids=[run])
    # Hide bodies without deleting inventory: move aside
    hidden = tmp_path / "hidden_bodies"
    hidden.mkdir()
    body.rename(hidden / "aabb.body")
    report = VA.verify_archive(repo_root=tmp_path, archive_dir=archive, mode="b")
    assert report["mode_b"]["verified_refs"] >= 1
    assert report["mode_b"]["missing_refs"] == 0
    # Empty source Mode A must not succeed as empty OK
    report_a = VA.verify_archive(repo_root=tmp_path, archive_dir=archive, mode="a")
    assert report_a["blocking_issues"] or report_a.get("mode_a") is None


def test_persistent_archive_dir_precedence(tmp_path, monkeypatch):
    from research.storage import roots as R

    monkeypatch.delenv("KALSHI_ARCHIVE_DIR", raising=False)
    R.write_persistent_archive_dir(tmp_path / "from_file", repo_root=tmp_path)
    roots = R.load_roots(tmp_path)
    assert roots.archive_dir == (tmp_path / "from_file").resolve()
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(tmp_path / "from_env"))
    roots2 = R.load_roots(tmp_path)
    assert roots2.archive_dir == (tmp_path / "from_env").resolve()
    roots3 = R.load_roots(tmp_path, archive_dir_override=tmp_path / "from_cli")
    assert roots3.archive_dir == (tmp_path / "from_cli").resolve()


def _archive_only_fixture(tmp_path, monkeypatch, payload: bytes = b'{"events":[{"id":"x"}]}'):
    """Isolated evidence root with archive CAS only — no local .body reachable."""
    from research.storage import cas as CAS
    from research.sports.core import request_key

    archive = tmp_path / "archive"
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(archive))
    evidence_root = tmp_path / "isolated_evidence" / "RUNARCHIVE"
    # Trap: real-looking body under a different root that must never be read.
    trap_root = tmp_path / "trap_original_bodies"
    url, params = "https://example.test/archive-only", {"dates": "20261104"}
    key = request_key(url, params)
    source = "espn"
    storage = CAS.store_blob(
        payload, absolute_blob_root=CAS.archive_blobs_dir(archive), location=CAS.LOCATION_ARCHIVE
    )
    digest = sha256_bytes(payload)
    for root in (evidence_root, trap_root):
        d = root / source / key[:2]
        d.mkdir(parents=True)
        (d / f"{key}.meta.json").write_text(
            json.dumps({
                "sha256": digest,
                "status": 200,
                "storage": storage,
                "url": url,
                "params": params,
                "bytes": len(payload),
            }),
            encoding="utf-8",
        )
    # Only the trap has a .body; isolated evidence_root must not.
    (trap_root / source / key[:2] / f"{key}.body").write_bytes(b"TRAP_MUST_NOT_BE_USED")
    assert not (evidence_root / source / key[:2] / f"{key}.body").exists()
    entry = {
        "key": key, "source": source, "url": url, "params": params,
        "status": 200, "sha256": digest, "bytes": len(payload),
        "requested_at": "2026-11-04T00:00:00.000000Z",
        "received_at": "2026-11-04T00:00:00.100000Z",
    }
    return {
        "archive": archive,
        "evidence_root": evidence_root,
        "trap_root": trap_root,
        "payload": payload,
        "digest": digest,
        "entry": entry,
        "body_path": evidence_root / source / key[:2] / f"{key}.body",
        "meta_path": evidence_root / source / key[:2] / f"{key}.meta.json",
        "storage": storage,
    }


def test_replay_check_evidence_archive_only_isolated(tmp_path, monkeypatch):
    from research.sports.live import replay as RP

    fx = _archive_only_fixture(tmp_path, monkeypatch)
    rec = {"evidence": [fx["entry"]]}
    RP.check_evidence(rec, fx["evidence_root"])
    # If consumer fell back to trap, hash would still match only if trap had same bytes —
    # trap is WRONG bytes; ensure reading trap path is not how we succeeded.
    assert fx["body_path"].exists() is False
    assert (fx["trap_root"] / "espn").exists()


def test_saved_evidence_fails_if_fallback_to_trap_body(tmp_path, monkeypatch):
    """Guard: pointing SavedEvidence at trap root with wrong body must fail hash check."""
    from research.sports.live import discover as D

    fx = _archive_only_fixture(tmp_path, monkeypatch)
    # Wrong body at trap — reading trap without archive should fail expected sha
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(tmp_path / "no-archive-xyz"))
    monkeypatch.setattr(
        "research.storage.roots.archive_status",
        lambda roots=None: __import__("research.storage.roots", fromlist=["ArchiveStatus"]).ArchiveStatus(
            True, False, "X:/nope", None, "archive volume missing"
        ),
    )
    from research.storage import cas as CAS
    with pytest.raises((D.EvidenceError, CAS.EvidenceMissing, FileNotFoundError)):
        D.SavedEvidence(fx["trap_root"], [fx["entry"]]).get(
            fx["entry"]["source"], fx["entry"]["url"], fx["entry"]["params"]
        )


def test_verify_detects_corrupt_gzip_and_wrong_hash(tmp_path, monkeypatch):
    from research.storage import cas as CAS
    from research.storage import inventory as INV
    from research.storage import migrate_evidence as ME
    from research.storage import verify_archive as VA

    archive = tmp_path / "archive"
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(archive))
    run = "CORRUPT1"
    payload = b'{"good":true}'
    body = tmp_path / f"data/cache/sports/raw_capture_v2/{run}/kalshi/aa/good.body"
    meta = body.with_name("good.meta.json")
    body.parent.mkdir(parents=True)
    body.write_bytes(payload)
    digest = sha256_bytes(payload)
    meta.write_text(json.dumps({"sha256": digest, "bytes": len(payload), "status": 200}), encoding="utf-8")
    ME.migrate_batch(
        repo_root=tmp_path, archive_dir=archive, batch_size=1, dry_run=False,
        checkpoint_path=tmp_path / "data/logs/storage/migrate_evidence_checkpoint.json",
    )
    INV.build_inventory(repo_root=tmp_path, archive_dir=archive, run_ids=[run])
    # Corrupt whichever encoding was stored (small payloads may stay identity)
    paths = CAS.blob_paths(digest, absolute_blob_root=CAS.archive_blobs_dir(archive))
    blob = paths["gzip"] if paths["gzip"].is_file() else paths["identity"]
    assert blob.is_file()
    blob.write_bytes(b"\xff\x00not-valid-payload-xxxxx")
    report = VA.verify_archive(repo_root=tmp_path, archive_dir=archive, mode="b")
    assert report["mode_b"]["corrupt_payloads"] + report["mode_b"]["missing_refs"] >= 1
    assert report["blocking_issues"]


def test_verify_detects_missing_archive_blob(tmp_path, monkeypatch):
    from research.storage import cas as CAS
    from research.storage import inventory as INV
    from research.storage import migrate_evidence as ME
    from research.storage import verify_archive as VA

    archive = tmp_path / "archive"
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(archive))
    run = "MISS1"
    payload = b'{"gone":true}'
    body = tmp_path / f"data/cache/sports/raw_capture_v2/{run}/kalshi/bb/miss.body"
    meta = body.with_name("miss.meta.json")
    body.parent.mkdir(parents=True)
    body.write_bytes(payload)
    digest = sha256_bytes(payload)
    meta.write_text(json.dumps({"sha256": digest, "bytes": len(payload), "status": 200}), encoding="utf-8")
    ME.migrate_batch(
        repo_root=tmp_path, archive_dir=archive, batch_size=1, dry_run=False,
        checkpoint_path=tmp_path / "data/logs/storage/migrate_evidence_checkpoint.json",
    )
    INV.build_inventory(repo_root=tmp_path, archive_dir=archive, run_ids=[run])
    for p in CAS.blob_paths(digest, absolute_blob_root=CAS.archive_blobs_dir(archive)).values():
        if p.is_file():
            p.unlink()
    report = VA.verify_archive(repo_root=tmp_path, archive_dir=archive, mode="b")
    assert report["mode_b"]["missing_refs"] >= 1


def test_verify_incomplete_inventory_manifest(tmp_path, monkeypatch):
    import hashlib

    from research.storage import verify_archive as VA

    archive = tmp_path / "archive"
    archive.mkdir()
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(archive))
    inv = tmp_path / "data/logs/storage/historical_evidence_inventory_v1.jsonl"
    meta = tmp_path / "data/logs/storage/historical_evidence_inventory_v1.meta.json"
    inv.parent.mkdir(parents=True)
    inv.write_text("", encoding="utf-8")
    digest = hashlib.sha256(inv.read_bytes()).hexdigest()
    meta.write_text(json.dumps({"inventory_sha256": digest}), encoding="utf-8")
    report = VA.verify_archive(repo_root=tmp_path, archive_dir=archive, mode="b")
    assert report["mode_b"].get("error") or report["blocking_issues"]


def test_migrate_interrupted_resume(tmp_path, monkeypatch):
    from research.storage import migrate_evidence as ME
    from research.storage import cas as CAS

    archive = tmp_path / "archive"
    monkeypatch.setenv("KALSHI_ARCHIVE_DIR", str(archive))
    run = "RESUME1"
    bodies = []
    for i in range(4):
        body = tmp_path / f"data/cache/sports/raw_capture_v2/{run}/kalshi/r{i}/p{i}.body"
        body.parent.mkdir(parents=True)
        data = f'{{"i":{i}}}'.encode()
        body.write_bytes(data)
        body.with_name(f"p{i}.meta.json").write_text(
            json.dumps({"sha256": sha256_bytes(data), "bytes": len(data), "status": 200}),
            encoding="utf-8",
        )
        bodies.append(body)
    ckpt = tmp_path / "data/logs/storage/migrate_evidence_checkpoint.json"
    # First pass: migrate then pretend interrupted by clearing completed_runs but keeping keys
    r1 = ME.migrate_batch(
        repo_root=tmp_path, archive_dir=archive, batch_size=1, dry_run=False, checkpoint_path=ckpt,
    )
    assert r1.files_migrated == 4
    data = json.loads(ckpt.read_text(encoding="utf-8"))
    # Simulate partial: remove run from completed_runs, drop last key
    keys = data["completed_keys"][run]
    data["completed_runs"] = []
    data["completed_keys"][run] = keys[:2]
    ckpt.write_text(json.dumps(data), encoding="utf-8")
    r2 = ME.migrate_batch(
        repo_root=tmp_path, archive_dir=archive, batch_size=1, dry_run=False, checkpoint_path=ckpt,
    )
    assert r2.files_skipped == 2
    assert r2.files_migrated == 2
    assert run in json.loads(ckpt.read_text(encoding="utf-8"))["completed_runs"]
    # Blobs still readable
    for body in bodies:
        digest = sha256_bytes(body.read_bytes())
        assert CAS.read_blob(digest, absolute_blob_root=CAS.archive_blobs_dir(archive)) == body.read_bytes()


def test_archive_drive_disconnect_refuses_read(tmp_path, monkeypatch):
    from research.storage import cas as CAS

    fx = _archive_only_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "research.storage.roots.archive_status",
        lambda roots=None: __import__("research.storage.roots", fromlist=["ArchiveStatus"]).ArchiveStatus(
            True, False, "D:/disconnected", None, "archive volume missing"
        ),
    )
    with pytest.raises(CAS.EvidenceMissing, match="archive"):
        CAS.read_evidence_payload(fx["body_path"], fx["meta_path"])


def test_settlement_style_evidence_archive_only(tmp_path, monkeypatch):
    """Settlement consumer path: read_evidence_payload via meta storage, no local body."""
    from research.storage import cas as CAS

    fx = _archive_only_fixture(tmp_path, monkeypatch, payload=b'{"markets":[]}')
    got = CAS.read_evidence_payload(fx["body_path"], fx["meta_path"], expected_sha256=fx["digest"])
    assert got == fx["payload"]
    assert not fx["body_path"].exists()

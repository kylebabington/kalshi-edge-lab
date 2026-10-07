"""Ownership marker, byte-exact backup, restore/rollback and archive safeguards.

Remotes are local bare repositories; health-check pings and platform checks are
injected, so nothing here touches the network or the live collector.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from research.weather import collector_ops as ops
from research.weather import phase7

# Bytes chosen to break under any text conversion.
CRLF_EVIDENCE = b"station,valid,tmpf\r\nNYC,2026-10-07 12:51,66.0\r\n"
LF_RECORD = b'{\n  "schema": "x",\n  "evidence_dir": "evidence\\\\2026-10-07\\\\d0_1200"\n}\n'
BINARY = bytes(range(256)) + b"\r\n\n\r\x00\x1a"
LONE_CR = b"a\rb\rc"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def bare(tmp: Path, name: str) -> str:
    path = tmp / f"{name}.git"
    git("init", "-q", "--bare", str(path))
    return path.as_posix()


def remote_sha(remote: str, ref: str) -> str | None:
    return ops.remote_ref_sha(remote, ref)


class Pings:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, bool, dict]] = []

    def __call__(self, url: str | None, fail: bool, body: str) -> None:
        self.calls.append((url, fail, json.loads(body)))

    def for_url(self, url: str) -> list[tuple[bool, dict]]:
        return [(fail, body) for u, fail, body in self.calls if u == url]


@pytest.fixture(autouse=True)
def hostile_git_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every git call in these tests runs under autocrlf=true global settings."""
    cfg = tmp_path / "hostile.gitconfig"
    cfg.write_text("[core]\n\tautocrlf = true\n\teol = crlf\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("KEL_CYCLE_LOCK_HELD", raising=False)


def write(root: Path, rel: str, data: bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def seed_state(root: Path) -> None:
    write(root, "data/weather/phase7/records/2026-10-07/d0_1200.json", LF_RECORD)
    write(root, "data/weather/phase7/evidence/2026-10-07/d0_1200/iem_knyc.csv", CRLF_EVIDENCE)
    write(root, "data/weather/phase7/evidence/2026-10-07/d0_1200/blob.bin", BINARY)
    write(root, "data/weather/phase7/evidence/2026-10-07/d0_1200/lone_cr.txt", LONE_CR)
    write(root, "data/weather/phase7/fetch_log.jsonl", b'{"n":1}\n')
    write(root, "data/weather/phase7/outcomes_ledger.jsonl", b'{"d":"2026-10-06"}\r\n')
    write(root, "data/results/prospective_validation_summary.json", b'{"v":1}\n')
    write(root, "data/logs/prospective/2026-10-07.log", b"2026-10-07T16:05:00Z cycle start\r\n")


def stop_ok(collector_id: str, unsynced: str = "none") -> dict:
    return {
        "schema": ops.STOP_STATUS_SCHEMA,
        "collector_id": collector_id,
        "checked_at": ops._iso(ops.utcnow()),
        "confirmed_stopped": True,
        "unsynced": {"status": unsynced},
    }


def make_config(tmp: Path, name: str, *, control: str, backup: str | None = None,
                ref: str = "refs/heads/state", **extra) -> tuple[Path, ops.CollectorConfig]:
    raw = {
        "collector_id": name,
        "control_remote": control,
        "collector_ping_url": f"https://hc.invalid/{name}-collector",
        "backup_ping_url": f"https://hc.invalid/{name}-backup",
        "git_timeout_s": 30,
        **extra,
    }
    if backup:
        raw.update(backup_remote=backup, backup_ref=ref, backup_dir=str(tmp / f"{name}-backup"))
    path = tmp / f"{name}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path, ops.load_config(path)


@pytest.fixture
def control(tmp_path: Path) -> str:
    remote = bare(tmp_path, "control")
    ops.write_owner(remote=remote, ref="refs/heads/ownership", new_owner="windows-laptop", expected_current=None,
                    reason="initial", actor="test", adopt_running=True)
    return remote


# ---------------------------------------------------------------------------
# Ownership marker
# ---------------------------------------------------------------------------


def test_owner_runs_and_fetch_failure_skips_without_cached_ownership(tmp_path, control):
    cfg_path, cfg = make_config(tmp_path, "windows-laptop", control=control)
    pings = Pings()
    code, result = ops.guard(cfg_path=cfg_path, require_config=True, pinger=pings)
    assert code == ops.EXIT_OK and result["ownership"] == "owner"
    assert pings.calls == []

    shutil.move(control, str(tmp_path / "control-offline.git"))
    code, result = ops.guard(cfg_path=cfg_path, require_config=True, pinger=pings)
    assert code == ops.EXIT_OWNERSHIP_UNVERIFIED
    assert result["ownership"] == "unverified" and result["action"] == "cycle skipped"
    [(fail, body)] = pings.for_url(cfg.collector_ping_url)
    assert fail is True and body["ownership"] == "unverified"
    assert not list(tmp_path.glob("**/*owner*cache*"))


def test_corrupt_or_missing_marker_is_unverified(tmp_path):
    remote = bare(tmp_path, "control")
    cfg_path, _ = make_config(tmp_path, "oracle-micro-1", control=remote)
    code, _ = ops.guard(cfg_path=cfg_path, pinger=Pings())
    assert code == ops.EXIT_OWNERSHIP_UNVERIFIED  # ref does not exist yet

    work = tmp_path / "w"
    git("init", "-q", str(work))
    (work / "OWNER.json").write_text('{"owner_id": "oracle-micro-1"}', encoding="utf-8")  # wrong schema
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "x", cwd=work)
    git("push", "-q", remote, "HEAD:refs/heads/ownership", cwd=work)
    code, result = ops.guard(cfg_path=cfg_path, pinger=Pings())
    assert code == ops.EXIT_OWNERSHIP_UNVERIFIED and "schema" in result["error"]


def test_stale_host_restart_after_transfer_does_not_collect(tmp_path, control):
    laptop_cfg, laptop = make_config(tmp_path, "windows-laptop", control=control)
    oracle_cfg, _ = make_config(tmp_path, "oracle-micro-1", control=control)
    ops.write_owner(remote=control, ref="refs/heads/ownership", new_owner="oracle-micro-1",
                    expected_current="windows-laptop", reason="handoff", actor="test",
                    outgoing_stop_status=stop_ok("windows-laptop"))
    pings = Pings()
    code, result = ops.guard(cfg_path=laptop_cfg, require_config=True, pinger=pings)
    assert code == ops.EXIT_NOT_OWNER and result["owner_id"] == "oracle-micro-1"
    assert pings.for_url(laptop.collector_ping_url)[0][0] is True
    assert ops.guard(cfg_path=oracle_cfg, require_config=True, pinger=Pings())[0] == ops.EXIT_OK


def test_config_modes(tmp_path):
    missing = tmp_path / "nope.json"
    assert ops.guard(cfg_path=missing, require_config=True, pinger=Pings())[0] == ops.EXIT_CONFIG
    code, result = ops.guard(cfg_path=missing, require_config=False, pinger=Pings())
    assert code == ops.EXIT_OK and result["mode"] == "pre_migration_standalone"
    bad = tmp_path / "bad.json"
    bad.write_text('{"collector_id": "x"}', encoding="utf-8")
    assert ops.guard(cfg_path=bad, pinger=Pings())[0] == ops.EXIT_CONFIG
    with pytest.raises(ops.ConfigError):
        make_config(tmp_path, "x", control="r", backup="r", ref="refs/heads/ownership")


def test_ownership_change_requires_confirmed_stop_and_compare_and_swap(tmp_path, control):
    ref = "refs/heads/ownership"
    kw = dict(remote=control, ref=ref, new_owner="oracle-micro-1", reason="handoff", actor="test")
    before = remote_sha(control, ref)

    with pytest.raises(ops.OpsError, match="confirmed stop"):
        ops.write_owner(expected_current="windows-laptop", **kw)
    not_stopped = {**stop_ok("windows-laptop"), "confirmed_stopped": False}
    with pytest.raises(ops.OpsError, match="not confirmed stopped"):
        ops.write_owner(expected_current="windows-laptop", outgoing_stop_status=not_stopped, **kw)
    with pytest.raises(ops.OpsError, match="not the outgoing owner"):
        ops.write_owner(expected_current="windows-laptop", outgoing_stop_status=stop_ok("someone-else"), **kw)
    old = {**stop_ok("windows-laptop"), "checked_at": ops._iso(ops.utcnow() - timedelta(hours=7))}
    with pytest.raises(ops.OpsError, match="older than 6 hours"):
        ops.write_owner(expected_current="windows-laptop", outgoing_stop_status=old, **kw)
    for status in ("unsynced", "blocked"):
        with pytest.raises(ops.OpsError, match="unsynced"):
            ops.write_owner(expected_current="windows-laptop", outgoing_stop_status=stop_ok("windows-laptop", status), **kw)
    with pytest.raises(ops.OpsError, match="current owner is"):
        ops.write_owner(expected_current="oracle-micro-1", outgoing_stop_status=stop_ok("oracle-micro-1"), **kw)
    with pytest.raises(ops.OpsError, match="already exists"):
        ops.write_owner(expected_current=None, outgoing_stop_status=stop_ok("windows-laptop"), **kw)
    assert remote_sha(control, ref) == before

    marker = ops.write_owner(expected_current="windows-laptop",
                             outgoing_stop_status=stop_ok("windows-laptop", "blocked"),
                             unsynced_blocked_reason="laptop disk failed; captures after 2026-10-07T16:05Z lost", **kw)
    assert marker["unsynced_blocked_reason"].startswith("laptop disk failed")
    after = remote_sha(control, ref)
    clone = tmp_path / "ctl"
    git("clone", "-q", "--branch", "ownership", control, str(clone))
    assert git("merge-base", "--is-ancestor", before, after, cwd=clone) == ""  # fast-forward, never forced


def test_adopting_the_running_collector_is_init_only(tmp_path, control):
    with pytest.raises(ops.OpsError, match="init"):
        ops.write_owner(remote=control, ref="refs/heads/ownership", new_owner="oracle-micro-1",
                        expected_current="windows-laptop", reason="x", actor="test", adopt_running=True)
    with pytest.raises(ops.OpsError, match="confirmed stop"):
        ops.write_owner(remote=bare(tmp_path, "c2"), ref="refs/heads/ownership", new_owner="oracle-micro-1",
                        expected_current=None, reason="x", actor="test")
    assert ops.fetch_owner(control, "refs/heads/ownership")["adopted_running_collector"] is True


def test_unconfirmed_failover_needs_explicit_human_reasons(tmp_path, control):
    kw = dict(remote=control, ref="refs/heads/ownership", new_owner="oracle-micro-1",
              expected_current="windows-laptop", reason="laptop lost", actor="test")
    with pytest.raises(ops.OpsError):
        ops.write_owner(allow_unconfirmed=True, outgoing_unconfirmed_reason="laptop stolen", **kw)
    with pytest.raises(ops.OpsError):
        ops.write_owner(outgoing_unconfirmed_reason="x", unsynced_blocked_reason="y", **kw)
    marker = ops.write_owner(allow_unconfirmed=True, outgoing_unconfirmed_reason="laptop stolen",
                             unsynced_blocked_reason="captures since last backup are unrecoverable", **kw)
    assert marker["outgoing_stop_status"] is None and marker["outgoing_unconfirmed_reason"] == "laptop stolen"


def test_stop_status_requires_every_check_positive(tmp_path):
    def checks(t, c, p):
        return ops.PlatformChecks(lambda: t, lambda root: c, lambda: p)

    ok = (True, "ok")
    status = ops.stop_status(repo_root=tmp_path, cfg=None, checks=checks(ok, ok, ok))
    assert status["confirmed_stopped"] is True and status["unsynced"]["status"] == "not_configured"
    for bad in ((False, "enabled"), (None, "unknown")):
        assert ops.stop_status(repo_root=tmp_path, cfg=None, checks=checks(bad, ok, ok))["confirmed_stopped"] is False
        assert ops.stop_status(repo_root=tmp_path, cfg=None, checks=checks(ok, bad, ok))["confirmed_stopped"] is False
        assert ops.stop_status(repo_root=tmp_path, cfg=None, checks=checks(ok, ok, bad))["confirmed_stopped"] is False


def test_windows_active_cycle_check_sees_lock(tmp_path):
    assert ops._windows_no_active_cycle(tmp_path)[0] is True
    write(tmp_path, "data/logs/prospective/cycle.lock", b"{}")
    assert ops._windows_no_active_cycle(tmp_path)[0] is False


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def backup_host(tmp: Path, name: str, control: str, backup: str, *, create: bool) -> tuple[Path, ops.CollectorConfig]:
    root = tmp / f"{name}-repo"
    root.mkdir()
    _, cfg = make_config(tmp, name, control=control, backup=backup)
    ops.backup_init(cfg, create_if_missing=create)
    return root, cfg


def test_backup_is_byte_exact_after_hostile_fresh_clone(tmp_path, control):
    backup = bare(tmp_path, "state")
    root, cfg = backup_host(tmp_path, "windows-laptop", control, backup, create=True)
    seed_state(root)
    write(root, "data/logs/prospective/cycle.lock", b"{}")
    write(root, "data/weather/phase7/records/2026-10-07/.d0_1500.json.abc.tmp", b"partial")
    write(root, "data/cache/weather/hrrr/live.json", b"{}")
    write(root, "collector.local.json", b'{"secret": true}')
    write(root, ".env", b"KALSHI_KEY=x")
    pings = Pings()

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KEL_CYCLE_LOCK_HELD", "1")  # the runner holds cycle.lock while post runs
        result = ops.run_backup(cfg, repo_root=root, pinger=pings)
    assert result["backup"] == "pushed", result
    assert pings.for_url(cfg.backup_ping_url) == [(False, result)]

    source = ops.clone_backup(backup, "refs/heads/state", tmp_path / "fresh", hostile=True)
    expected = {rel: p.read_bytes() for rel, p in ops.collect_state(root, "backup").items()}
    assert source.files == expected
    assert source.files["data/weather/phase7/evidence/2026-10-07/d0_1200/iem_knyc.csv"] == CRLF_EVIDENCE
    assert source.files["data/weather/phase7/evidence/2026-10-07/d0_1200/blob.bin"] == BINARY
    assert source.files["data/weather/phase7/evidence/2026-10-07/d0_1200/lone_cr.txt"] == LONE_CR
    assert source.files["data/weather/phase7/records/2026-10-07/d0_1200.json"] == LF_RECORD
    for path in (tmp_path / "fresh" / "state").rglob("*"):
        if path.is_file():
            rel = path.relative_to(tmp_path / "fresh" / "state").as_posix()
            assert sha(path.read_bytes()) == sha(expected[rel])
    names = set(source.files)
    assert not any(n.endswith((".lock", ".tmp")) or "cache" in n or "collector.local" in n or ".env" in n
                   for n in names)

    assert ops.run_backup(cfg, repo_root=root, pinger=pings)["backup"] == "failed"  # cycle.lock, not held
    write(root, "data/weather/phase7/fetch_log.jsonl", b'{"n":1}\n{"n":2}\n')
    (root / "data/logs/prospective/cycle.lock").unlink()
    assert ops.run_backup(cfg, repo_root=root, pinger=pings)["backup"] == "pushed"
    assert ops.run_backup(cfg, repo_root=root, pinger=pings)["backup"] == "up_to_date"


def test_backup_never_touches_the_ownership_ref(tmp_path, control):
    root, cfg = backup_host(tmp_path, "windows-laptop", control, control, create=True)  # same repo, other ref
    seed_state(root)
    before = remote_sha(control, "refs/heads/ownership")
    assert ops.run_backup(cfg, repo_root=root, pinger=Pings())["backup"] == "pushed"
    assert remote_sha(control, "refs/heads/ownership") == before
    assert ops.fetch_owner(control, "refs/heads/ownership")["owner_id"] == "windows-laptop"


def test_push_failure_alerts_backup_only_and_keeps_unsynced_commit(tmp_path, control):
    backup = bare(tmp_path, "state")
    root, cfg = backup_host(tmp_path, "oracle-micro-1", control, backup, create=True)
    cfg_path = tmp_path / "oracle-micro-1.json"
    seed_state(root)
    assert ops.run_backup(cfg, repo_root=root, pinger=Pings())["backup"] == "pushed"
    pushed = remote_sha(backup, "refs/heads/state")

    new_capture = "data/weather/phase7/records/2026-10-08/d0_0600.json"
    write(root, new_capture, b'{"captured": true}\n')
    offline = tmp_path / "state-offline.git"
    shutil.move(backup, str(offline))
    pings = Pings()
    result = ops.post_cycle(0, cfg_path=cfg_path, pinger=pings, repo_root=root)
    assert result["backup"] == "push_failed" and result["unsynced_commits"] == 1
    assert pings.for_url(cfg.collector_ping_url)[0][0] is False  # collector healthy
    assert pings.for_url(cfg.backup_ping_url)[0][0] is True      # backup alerts separately
    assert (cfg.backup_dir / "state" / new_capture).read_bytes() == b'{"captured": true}\n'
    assert (root / new_capture).exists()

    shutil.move(str(offline), backup)
    assert remote_sha(backup, "refs/heads/state") == pushed
    result = ops.run_backup(cfg, repo_root=root, pinger=Pings())
    assert result["backup"] == "pushed" and result["unsynced_commits"] == 0
    source = ops.clone_backup(backup, "refs/heads/state", tmp_path / "fresh")
    assert source.files[new_capture] == b'{"captured": true}\n'


def test_conflicting_backup_histories_are_not_merged_or_forced(tmp_path, control):
    backup = bare(tmp_path, "state")
    root_a, cfg_a = backup_host(tmp_path, "windows-laptop", control, backup, create=True)
    seed_state(root_a)
    assert ops.run_backup(cfg_a, repo_root=root_a, pinger=Pings())["backup"] == "pushed"
    root_b, cfg_b = backup_host(tmp_path, "oracle-micro-1", control, backup, create=False)
    seed_state(root_b)

    write(root_a, "data/weather/phase7/records/2026-10-08/d0_0600.json", b'{"host": "a"}\n')
    assert ops.run_backup(cfg_a, repo_root=root_a, pinger=Pings())["backup"] == "pushed"
    remote_after_a = remote_sha(backup, "refs/heads/state")

    write(root_b, "data/weather/phase7/records/2026-10-08/d0_0600.json", b'{"host": "b"}\n')
    pings = Pings()
    result = ops.run_backup(cfg_b, repo_root=root_b, pinger=pings)
    assert result["backup"] == "conflict" and result["unsynced_commits"] >= 1
    assert pings.for_url(cfg_b.backup_ping_url)[0][0] is True
    assert remote_sha(backup, "refs/heads/state") == remote_after_a
    assert (root_b / "data/weather/phase7/records/2026-10-08/d0_0600.json").read_bytes() == b'{"host": "b"}\n'
    assert (cfg_b.backup_dir / "state/data/weather/phase7/records/2026-10-08/d0_0600.json").read_bytes() == b'{"host": "b"}\n'


def test_backup_refuses_when_captures_vanish_or_logs_shrink(tmp_path, control):
    backup = bare(tmp_path, "state")
    root, cfg = backup_host(tmp_path, "oracle-micro-1", control, backup, create=True)
    seed_state(root)
    assert ops.run_backup(cfg, repo_root=root, pinger=Pings())["backup"] == "pushed"
    pushed = remote_sha(backup, "refs/heads/state")
    (root / "data/weather/phase7/records/2026-10-07/d0_1200.json").unlink()
    assert ops.run_backup(cfg, repo_root=root, pinger=Pings())["backup"] == "failed"
    seed_state(root)
    write(root, "data/weather/phase7/fetch_log.jsonl", b"")
    assert ops.run_backup(cfg, repo_root=root, pinger=Pings())["backup"] == "failed"
    assert remote_sha(backup, "refs/heads/state") == pushed


def test_unsynced_report_detects_missing_captures_and_blocked_remote(tmp_path, control):
    backup = bare(tmp_path, "state")
    root, cfg = backup_host(tmp_path, "windows-laptop", control, backup, create=True)
    seed_state(root)
    ops.run_backup(cfg, repo_root=root, pinger=Pings())
    assert ops.unsynced_report(cfg, repo_root=root)["status"] == "none"
    write(root, "data/weather/phase7/records/2026-10-08/d0_0600.json", b"{}")
    report = ops.unsynced_report(cfg, repo_root=root)
    assert report["status"] == "unsynced"
    assert report["items"][0]["path"] == "data/weather/phase7/records/2026-10-08/d0_0600.json"
    shutil.move(backup, str(tmp_path / "gone.git"))
    assert ops.unsynced_report(cfg, repo_root=root)["status"] == "blocked"


# ---------------------------------------------------------------------------
# Restore / rollback
# ---------------------------------------------------------------------------


def source_of(root: Path, profile: str = "backup") -> ops.Source:
    files = ops.collect_state(root, profile)
    return ops.Source(files={r: p.read_bytes() for r, p in files.items()}, origin=str(root))


def test_restore_plan_protects_write_once_and_append_only(tmp_path):
    src_root, dst_root = tmp_path / "src", tmp_path / "dst"
    seed_state(src_root)
    seed_state(dst_root)
    write(src_root, "data/weather/phase7/records/2026-10-08/d0_0600.json", b"new")
    write(src_root, "data/weather/phase7/fetch_log.jsonl", b'{"n":1}\n{"n":2}\n')
    write(src_root, "data/results/prospective_validation_summary.json", b'{"v":2}\n')
    write(dst_root, "data/results/stale.json", b"{}")
    plan = ops.plan_restore(source_of(src_root), dst_root, "backup")
    assert plan.safe
    assert plan.copy == ["data/weather/phase7/records/2026-10-08/d0_0600.json"]
    assert set(plan.overwrite) == {"data/weather/phase7/fetch_log.jsonl",
                                   "data/results/prospective_validation_summary.json"}
    assert plan.delete == ["data/results/stale.json"]
    assert ops.apply_restore(source_of(src_root), dst_root, plan, "backup")["verified_files"] == len(source_of(src_root).files)
    assert ops.build_manifest(ops.collect_state(dst_root)) == ops.build_manifest(ops.collect_state(src_root))

    write(dst_root, "data/weather/phase7/records/2026-10-08/d0_0900.json", b"only here")
    write(dst_root, "data/weather/phase7/records/2026-10-08/d0_0600.json", b"different")
    write(dst_root, "data/weather/phase7/fetch_log.jsonl", b'{"n":1}\n{"n":2}\n{"n":3}\n')
    write(dst_root, "data/weather/phase7/outcomes_ledger.jsonl", b"diverged\n")
    plan = ops.plan_restore(source_of(src_root), dst_root, "backup")
    assert not plan.safe
    assert {c["path"] for c in plan.conflicts} == {"data/weather/phase7/records/2026-10-08/d0_0600.json",
                                                   "data/weather/phase7/outcomes_ledger.jsonl"}
    assert {c["path"] for c in plan.target_only_captures} == {"data/weather/phase7/records/2026-10-08/d0_0900.json",
                                                              "data/weather/phase7/fetch_log.jsonl"}
    before = ops.build_manifest(ops.collect_state(dst_root))
    with pytest.raises(ops.OpsError):
        ops.apply_restore(source_of(src_root), dst_root, plan, "backup")
    assert ops.build_manifest(ops.collect_state(dst_root)) == before


def test_safe_rollback_to_windows_from_backup(tmp_path, control):
    """Oracle collected after handoff; the stopped laptop is rolled forward from the backup."""
    backup = bare(tmp_path, "state")
    laptop = tmp_path / "laptop"
    seed_state(laptop)
    oracle, cfg = backup_host(tmp_path, "oracle-micro-1", control, backup, create=True)
    seed_state(oracle)
    write(oracle, "data/weather/phase7/records/2026-10-08/d0_0600.json", b'{"by": "oracle"}\n')
    write(oracle, "data/weather/phase7/fetch_log.jsonl", b'{"n":1}\n{"n":2}\n')
    assert ops.run_backup(cfg, repo_root=oracle, pinger=Pings())["backup"] == "pushed"

    source = ops.clone_backup(backup, "refs/heads/state", tmp_path / "fresh")
    plan = ops.plan_restore(source, laptop, "backup")
    assert plan.safe
    ops.apply_restore(source, laptop, plan, "backup")
    assert (laptop / "data/weather/phase7/records/2026-10-08/d0_0600.json").read_bytes() == b'{"by": "oracle"}\n'
    assert (laptop / "data/weather/phase7/evidence/2026-10-07/d0_1200/iem_knyc.csv").read_bytes() == CRLF_EVIDENCE

    write(laptop, "data/weather/phase7/records/2026-10-08/d0_0900.json", b"laptop ran while not owner")
    assert not ops.plan_restore(source, laptop, "backup").safe

    write(laptop, "data/logs/prospective/cycle.lock", b"{}")
    with pytest.raises(ops.OpsError, match="cycle holds"):
        with ops.hold_cycle_lock(laptop):
            pass


# ---------------------------------------------------------------------------
# Archives and allowlist
# ---------------------------------------------------------------------------


def test_archive_roundtrip_is_hash_verified_and_consistent(tmp_path):
    root = tmp_path / "repo"
    seed_state(root)
    write(root, "data/cache/weather/hrrr/live.json", b'{"cache": 1}\r\n')
    write(root, "gfs_run_cache.json", b"{}")
    out = tmp_path / "state.tar"
    sidecar = ops.create_archive(root, out, profile="migration")
    assert sidecar["archive_sha256"] == sha(out.read_bytes())
    source = ops.read_archive(out, expected_sha256=sidecar["archive_sha256"])
    assert source.files == {r: p.read_bytes() for r, p in ops.collect_state(root, "migration").items()}
    assert "data/cache/weather/hrrr/live.json" in source.files and "gfs_run_cache.json" in source.files
    with pytest.raises(ops.OpsError, match="SHA-256"):
        ops.read_archive(out, expected_sha256="0" * 64)

    target = tmp_path / "server"
    plan = ops.plan_restore(source, target, "migration")
    ops.apply_restore(source, target, plan, "migration")
    assert ops.build_manifest(ops.collect_state(target, "migration")) == source.manifest


def test_archive_refuses_during_cycle_and_discards_on_change(tmp_path):
    root = tmp_path / "repo"
    seed_state(root)
    out = tmp_path / "state.tar"
    write(root, "data/logs/prospective/cycle.lock", b"{}")
    with pytest.raises(ops.OpsError):
        ops.create_archive(root, out)
    (root / "data/logs/prospective/cycle.lock").unlink()

    def mutate() -> None:
        write(root, "data/weather/phase7/fetch_log.jsonl", b'{"n":1}\n{"n":2}\n')

    with pytest.raises(ops.OpsError, match="archive discarded"):
        ops.create_archive(root, out, during=mutate)
    assert not out.exists() and not list(tmp_path.glob("state.tar*"))


def test_archive_rejects_path_traversal(tmp_path):
    import io
    import tarfile

    evil = tmp_path / "evil.tar"
    with tarfile.open(evil, "w") as tar:
        info = tarfile.TarInfo("state/../../outside.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ops.OpsError, match="unsafe path"):
        ops.read_archive(evil)


def test_allowlist_classes_and_exclusions(tmp_path):
    root = tmp_path / "repo"
    seed_state(root)
    write(root, "data/weather/phase7/evidence/2026-10-07/d0_1200/.iem.csv.x.tmp", b"")
    write(root, "data/logs/prospective/.cycle_20261007T155038851Z.out", b"")
    write(root, "data/logs/prospective/cycle.lock", b"{}")
    write(root, "data/logs/prospective/cycle.flock", b"")
    write(root, "data/cache/weather/nws/forecast.json", b"{}")
    write(root, "data/weather/snapshots/KXHIGHNY-26OCT07/latest.json", b"{}")
    write(root, "data/weather/snapshots/KXHIGHNY-26OCT07/20261007T160500Z.json", b"{}")
    backup = set(ops.collect_state(root, "backup"))
    assert not any(".tmp" in r or ".out" in r or r.endswith((".lock", ".flock")) or "cache" in r for r in backup)
    assert "data/cache/weather/nws/forecast.json" in ops.collect_state(root, "migration")
    assert ops.classify("data/weather/snapshots/KXHIGHNY-26OCT07/latest.json") == ops.MUTABLE
    assert ops.classify("data/weather/snapshots/KXHIGHNY-26OCT07/20261007T160500Z.json") == ops.WRITE_ONCE
    assert ops.classify("data/weather/phase7/receipts/2026-10-07/d0_1200.json") == ops.WRITE_ONCE
    assert ops.classify("data/weather/phase7/pending_scores/x.json") == ops.MUTABLE
    assert ops.classify("data/logs/prospective/2026-10-07.log") == ops.APPEND_ONLY
    assert ops.classify("data/weather/phase7/outcomes_ledger.jsonl") == ops.APPEND_ONLY


# ---------------------------------------------------------------------------
# Registered hashes
# ---------------------------------------------------------------------------


def test_tracked_write_once_state_has_a_fixed_checkout_encoding():
    """A Linux clone must reproduce the collector's bytes for tracked state, or restores conflict."""
    out = git("ls-files", "--eol", "--", *ops.STATE_DIRS, *ops.STATE_FILES, cwd=ops.REPO_ROOT)
    unpinned = []
    for line in out.splitlines():
        meta, _, rel = line.partition("\t")
        attr = meta.split("attr/", 1)[-1]
        if ops.classify(rel) != ops.MUTABLE and "eol=" not in attr and "-text" not in attr:
            unpinned.append(rel)
    assert unpinned == []


def test_registered_protocol_calibration_and_method_hashes_unchanged():
    result = ops.verify_pins()
    assert result["ok"], result["problems"]
    paths = phase7.Phase7Paths()
    protocol = phase7.load_protocol(paths)
    assert phase7.protocol_sha256(paths) == ops.REGISTERED_PINS["protocol_sha256"]
    assert phase7.sha256_file(paths.frozen_pool) == ops.REGISTERED_PINS["frozen_pool_sha256"]
    assert protocol["calibration"]["source_csv_sha256"] == ops.REGISTERED_PINS["source_csv_sha256"]
    for name, path in phase7._source_csvs().items():
        assert phase7.sha256_file(path) == ops.REGISTERED_PINS["source_csv_sha256"][name]

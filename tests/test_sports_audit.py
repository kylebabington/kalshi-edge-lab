"""Collector health audit: wrapper log parsing and per-trigger outcome classification (read only)."""

from __future__ import annotations

from datetime import datetime, timezone

from research.sports.ops import audit as AU
from research.sports.ops import collector as CO


def _t(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


def test_task_runs_and_trigger_outcomes(tmp_path, monkeypatch):
    monkeypatch.setattr(CO, "LOG_DIR", tmp_path)
    (tmp_path / "task_2026-10-11.log").write_text(
        "\ufeff2026-10-11T14:27:44.7Z [pid 1] task start; repo=x\n"
        "2026-10-11T14:37:17.1Z [pid 1] task end (exit 0)\n"
        "2026-10-11T14:41:41.3Z [pid 2] task start; repo=x\n"
        "2026-10-11T14:41:42.0Z [pid 2]   stderr: noise\n"
        "2026-10-11T14:44:49.5Z [pid 2] task end (exit 2)\n", encoding="utf-8")
    runs = AU.task_runs()
    assert [(r["pid"], r["exit"]) for r in runs] == [(1, 0), (2, 2)]
    trig = {"start_boundary": "2026-10-11T10:31:00-04:00", "interval_min": 5, "multiple_instances": "IgnoreNew"}
    res = AU.classify_triggers(trig, runs, _t("2026-10-11T14:52:00"))
    outcomes = [(r["trigger"], r["outcome"]) for r in res["not_started"]]
    assert outcomes == [("2026-10-11T14:31:00Z", "not_started_instance_running"),
                        ("2026-10-11T14:36:00Z", "not_started_instance_running"),
                        ("2026-10-11T14:46:00Z", "not_started_unexplained"),
                        ("2026-10-11T14:51:00Z", "not_started_unexplained")]
    assert res["outcomes"]["started"] == 1 and res["launch_delay_s"]["max"] == 41.3
    assert AU.classify_triggers({}, runs, 0) == {"available": False}

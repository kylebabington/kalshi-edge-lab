"""Activity Center: write-free GETs, stable ids, explore deltas, backup limits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from research.activity import derive, explore_emit, index
from research.activity import paths as AP
from research.explore import paths as P
from research.explore import store as ST
from research.weather import checkpoints as CK
from research.weather import snapshots as SN


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate explore + activity + weather/sports artifact roots."""
    explore = tmp_path / "explore"
    activity = tmp_path / "activity"
    weather_snap = tmp_path / "weather" / "snapshots"
    weather_score = tmp_path / "weather" / "scores"
    weather_receipt = tmp_path / "weather" / "checkpoints"
    weather_log = tmp_path / "logs" / "prospective"
    sports_cycles = tmp_path / "logs" / "sports" / "collector_cycles.jsonl"
    sports_backup = tmp_path / "logs" / "sports" / "backup_status.json"
    for d in (explore, activity, weather_snap, weather_score, weather_receipt, weather_log, sports_cycles.parent):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(P, "EXPLORE_ROOT", explore)
    monkeypatch.setattr(P, "RUNS_DIR", explore / "runs")
    monkeypatch.setattr(P, "EVENTS_DIR", explore / "events")
    monkeypatch.setattr(P, "SERIES_PATH", explore / "series.json")
    monkeypatch.setattr(P, "TAGS_PATH", explore / "tags_by_categories.json")
    monkeypatch.setattr(P, "EVENTS_INDEX_PATH", explore / "events_index.json")
    monkeypatch.setattr(P, "REFRESH_STATUS_PATH", explore / "refresh_status.json")
    monkeypatch.setattr(P, "LOOKUP_ROTATION_PATH", explore / "lookup_rotation.json")
    monkeypatch.setattr(P, "LATEST_MANIFEST_PATH", explore / "latest_manifest.json")
    monkeypatch.setattr(P, "LOG_DIR", tmp_path / "logs" / "explore")
    monkeypatch.setattr(P, "LOCK_PATH", tmp_path / "logs" / "explore" / "refresh.lock")
    P.ensure_explore_dirs()

    monkeypatch.setattr(AP, "ACTIVITY_ROOT", activity)
    monkeypatch.setattr(AP, "EXPLORE_DIR", activity / "explore")
    monkeypatch.setattr(AP, "PENDING_DIR", activity / "explore_pending")
    AP.ensure_activity_dirs()

    monkeypatch.setattr(SN, "SNAPSHOT_ROOT", weather_snap)
    monkeypatch.setattr(SN, "SCORE_ROOT", weather_score)
    monkeypatch.setattr(CK, "CHECKPOINT_ROOT", weather_receipt)
    monkeypatch.setattr(derive, "WEATHER_LOG_DIR", weather_log)
    monkeypatch.setattr(derive, "SPORTS_CYCLES", sports_cycles)
    monkeypatch.setattr(derive, "SPORTS_BACKUP_STATUS", sports_backup)

    from research.sports.live import config as LC
    from research.sports.scoring import config as SCC

    sports_capture = tmp_path / "sports" / "capture_v2"
    sports_scores = tmp_path / "sports" / "scores"
    (sports_capture / "accepted" / "checkpoint").mkdir(parents=True)
    (sports_scores / "finalized").mkdir(parents=True)
    monkeypatch.setattr(LC, "CAPTURE_DIR", sports_capture)
    monkeypatch.setattr(SCC, "SCORE_DIR", sports_scores)

    index.reset_memory()
    yield {
        "tmp": tmp_path,
        "weather_snap": weather_snap,
        "weather_score": weather_score,
        "weather_receipt": weather_receipt,
        "weather_log": weather_log,
        "sports_cycles": sports_cycles,
        "sports_backup": sports_backup,
        "sports_capture": sports_capture,
        "sports_scores": sports_scores,
        "activity": activity,
        "overrides": {
            "snapshot_root": weather_snap,
            "score_root": weather_score,
            "receipt_root": weather_receipt,
            "weather_log_dir": weather_log,
            "sports_cycles": sports_cycles,
            "sports_backup": sports_backup,
            "sports_capture_dir": sports_capture,
            "sports_score_dir": sports_scores,
        },
    }
    index.reset_memory()


def _market(ticker: str, **kw: Any) -> dict:
    base = {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0] if "-" in ticker else ticker,
        "title": ticker,
        "status": "active",
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.45",
        "no_bid_dollars": "0.55",
        "no_ask_dollars": "0.60",
        "last_price_dollars": "0.42",
        "yes_bid_size_fp": "10",
        "yes_ask_size_fp": "12",
        "volume_fp": "100",
        "volume_24h_fp": "50",
        "open_interest_fp": "20",
    }
    base.update(kw)
    return base


def _event(ticker: str, markets: list[dict] | None = None, **kw: Any) -> dict:
    body = {
        "event_ticker": ticker,
        "series_ticker": "KXHIGHNY",
        "title": f"Title {ticker}",
        "markets": markets or [_market(f"{ticker}-T70")],
    }
    body.update(kw)
    return body


def test_explore_discovery_not_quote_or_status(env):
    entry = ST.upsert_event(
        _event("KXHIGHNY-A", [_market("KXHIGHNY-A-T70", yes_bid_dollars="0.40")]),
        retrieved_at="2026-10-17T12:00:00Z",
        source="test",
        run_id="run1",
        markets_complete=True,
        markets_complete_basis="test",
    )
    assert entry["first_seen_at"] == "2026-10-17T12:00:00Z"
    items = explore_emit.load_explore_activity_files()
    types = {i["type"] for i in items}
    assert types == {"explore_discovered"}
    assert items[0]["id"] == "explore.discovered:KXHIGHNY-A:2026-10-17T12:00:00Z"


def test_explore_quote_aggregates_fields_and_both_retrieval_times(env):
    ST.upsert_event(
        _event("KXHIGHNY-B", [_market("KXHIGHNY-B-T70", yes_bid_dollars="0.40", yes_ask_dollars="0.45")]),
        retrieved_at="2026-10-17T12:00:00Z",
        source="test",
        run_id="run1",
        markets_complete=True,
        markets_complete_basis="test",
    )
    ST.upsert_event(
        _event(
            "KXHIGHNY-B",
            [_market("KXHIGHNY-B-T70", yes_bid_dollars="0.42", yes_ask_dollars="0.48", status="active")],
        ),
        retrieved_at="2026-10-17T14:00:00Z",
        source="test",
        run_id="run2",
        markets_complete=True,
        markets_complete_basis="test",
    )
    items = [i for i in explore_emit.load_explore_activity_files() if i["type"] == "explore_quote_change"]
    assert len(items) == 1
    it = items[0]
    assert it["id"] == "explore.quote:KXHIGHNY-B:KXHIGHNY-B-T70:2026-10-17T14:00:00Z"
    assert it["payload"]["old_retrieved_at"] == "2026-10-17T12:00:00Z"
    assert it["payload"]["new_retrieved_at"] == "2026-10-17T14:00:00Z"
    fields = {c["field"] for c in it["payload"]["changes"]}
    assert "yes_bid" in fields and "yes_ask" in fields
    # One item per market observation, not one per field.
    assert len(items) == 1


def test_explore_status_includes_market_ticker(env):
    ST.upsert_event(
        _event("KXHIGHNY-C", [_market("KXHIGHNY-C-T70", status="active")]),
        retrieved_at="2026-10-17T12:00:00Z",
        source="test",
        run_id="run1",
        markets_complete=True,
        markets_complete_basis="test",
    )
    ST.upsert_event(
        _event("KXHIGHNY-C", [_market("KXHIGHNY-C-T70", status="determined")]),
        retrieved_at="2026-10-17T15:00:00Z",
        source="test",
        run_id="run2",
        markets_complete=True,
        markets_complete_basis="test",
    )
    status_items = [i for i in explore_emit.load_explore_activity_files() if i["type"] == "explore_status_change"]
    assert any("KXHIGHNY-C-T70" in i["id"] for i in status_items)


def test_explore_duplicate_suppression_on_retry(env):
    raw = _event("KXHIGHNY-D", [_market("KXHIGHNY-D-T70")])
    ST.upsert_event(raw, retrieved_at="2026-10-17T12:00:00Z", source="test", run_id="run1", markets_complete=True)
    # Simulate crash recovery: pending already written, upsert again after clearing shard only.
    # Re-upserting identical observation after deleting activity files but keeping shard should not
    # re-emit discovery (is_new=False). Re-emitting pending for same quote change uses create-exclusive.
    items1 = explore_emit.load_explore_activity_files()
    ids1 = sorted(i["id"] for i in items1)
    # Fold again with same ids — pending gone; rewrite pending then fold should not duplicate in jsonl after dedupe.
    explore_emit.persist_pending(items1)
    explore_emit.fold_pending_into_run("run1", [i["id"] for i in items1])
    loaded = explore_emit.load_explore_activity_files()
    ids2 = sorted(i["id"] for i in loaded)
    assert ids2 == ids1
    # Index dedupes
    index.reset_memory()
    result = index.list_activity(path_overrides=env["overrides"])
    feed_ids = [i["id"] for i in result["items"] if i["id"] in ids1]
    assert len(feed_ids) == len(set(feed_ids))


def test_partial_crawl_no_closure_from_absence(env):
    ST.upsert_event(
        _event("KXHIGHNY-E", [_market("KXHIGHNY-E-T70"), _market("KXHIGHNY-E-T71")]),
        retrieved_at="2026-10-17T12:00:00Z",
        source="test",
        run_id="run1",
        markets_complete=True,
        markets_complete_basis="test",
    )
    # Partial: only one market returned; mark_missing marks not_in_latest_response but must not emit closed.
    ST.upsert_event(
        _event("KXHIGHNY-E", [_market("KXHIGHNY-E-T70", yes_bid_dollars="0.41")]),
        retrieved_at="2026-10-17T13:00:00Z",
        source="test",
        run_id="run2",
        markets_complete=False,
        markets_complete_basis="partial",
        mark_missing_markets=True,
    )
    items = explore_emit.load_explore_activity_files()
    assert not any("closed" in (i.get("summary") or "").lower() and "absence" in (i.get("summary") or "").lower() for i in items)
    assert not any(
        i["type"] == "explore_status_change" and i.get("payload", {}).get("to") == "closed" and i.get("payload", {}).get("from") is None
        for i in items
    )


def test_weather_forecast_dedupes_receipt_and_snapshot(env):
    snap_root = env["weather_snap"]
    receipt_root = env["weather_receipt"]
    ticker = "KXHIGHNY-F"
    sid = f"{ticker}__20261017120000Z"
    (snap_root / ticker).mkdir(parents=True)
    (receipt_root / ticker).mkdir(parents=True)
    snap = {
        "snapshot_id": sid,
        "event_ticker": ticker,
        "prediction_as_of": "2026-10-17T12:00:00Z",
        "created_at": "2026-10-17T12:01:00Z",
        "checkpoint_id": "d0_1200",
    }
    (snap_root / ticker / "20261017120000Z.json").write_text(json.dumps(snap), encoding="utf-8")
    receipt = {
        "event_ticker": ticker,
        "checkpoint_id": "d0_1200",
        "status": CK.RECEIPT_STATUS_CAPTURED,
        "snapshot_id": sid,
        "created_at": "2026-10-17T12:01:00Z",
        "actual_prediction_as_of": "2026-10-17T12:00:00Z",
    }
    (receipt_root / ticker / "d0_1200.json").write_text(json.dumps(receipt), encoding="utf-8")
    items = derive.derive_weather_forecasts(snapshot_root=snap_root, receipt_root=receipt_root)
    ids = [i["id"] for i in items]
    assert ids.count(f"weather.forecast:{sid}") == 1
    assert not any(i["id"].startswith("weather.forecast.receipt:") for i in items)


def test_collector_failure_then_recovery_and_skipped_separate(env):
    log = env["weather_log"] / "task.log"
    log.write_text(
        "\n".join(
            [
                "2026-10-16T08:00:00Z [pid 1] cycle end (exit 1)",
                "2026-10-16T09:00:00Z [pid 1] cycle end (exit 0)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    weather = derive.derive_weather_collector(log_dir=env["weather_log"])
    assert any(i["type"] == "collector_failure" for i in weather)
    assert any(i["type"] == "collector_recovery" for i in weather)

    cycles = env["sports_cycles"]
    cycles.write_text(
        "\n".join(
            [
                json.dumps({"finished_at": "2026-10-16T10:00:00Z", "result": "skipped_lock_busy", "exit_code": 0}),
                json.dumps({"finished_at": "2026-10-16T11:00:00Z", "result": "error", "exit_code": 1, "detail": "boom"}),
                json.dumps({"finished_at": "2026-10-16T12:00:00Z", "result": "ok", "exit_code": 0}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    sports = derive.derive_sports_collector(cycles_path=cycles)
    assert any(i["type"] == "collector_skipped" for i in sports)
    assert any(i["type"] == "collector_failure" for i in sports)
    assert any(i["type"] == "collector_recovery" for i in sports)
    # Skipped is not a failure
    assert not any(i["type"] == "collector_failure" and "skipped" in (i.get("summary") or "") for i in sports)


def test_backup_current_status_only_no_invented_recovery(env):
    path = env["sports_backup"]
    path.write_text(
        json.dumps({"backup": "pushed", "at": "2026-10-17T10:00:00Z", "finished_at": "2026-10-17T10:00:05Z"}),
        encoding="utf-8",
    )
    items = derive.derive_sports_backup(status_path=path)
    assert len(items) == 1
    assert items[0]["type"] == "backup_ok"
    assert items[0]["payload"]["history"] == "current_status_only"
    assert items[0]["type"] != "backup_recovery"

    path.write_text(
        json.dumps({"backup": "skipped_busy", "snapshot": "skipped_busy", "at": "2026-10-17T11:00:00Z"}),
        encoding="utf-8",
    )
    skipped = derive.derive_sports_backup(status_path=path)
    assert skipped[0]["type"] == "backup_skipped"


def test_get_write_free_and_network_free(env, monkeypatch):
    """GETs must not write activity cache files or touch the network, including first request."""

    def boom(*_a, **_k):
        raise AssertionError("network call during activity GET")

    monkeypatch.setattr("kalshi.client.KalshiClient.get", boom, raising=False)
    try:
        import kalshi.client as kc

        monkeypatch.setattr(kc, "KalshiClient", type("NoNet", (), {"get": staticmethod(boom), "__init__": lambda self, *a, **k: None}))
    except Exception:
        pass

    def snapshot(root: Path) -> dict[str, tuple[int, int] | None]:
        out: dict[str, tuple[int, int] | None] = {}
        if not root.exists():
            return out
        for p in root.rglob("*"):
            if p.is_file():
                st = p.stat()
                out[str(p)] = (st.st_mtime_ns, st.st_size)
        return out

    before = snapshot(env["activity"])
    index.reset_memory()
    # First request after startup (empty memory).
    r1 = index.list_activity(path_overrides=env["overrides"])
    assert "items" in r1
    s1 = index.summary(path_overrides=env["overrides"])
    assert "total" in s1
    # Second request with unchanged filesystem.
    r2 = index.list_activity(path_overrides=env["overrides"])
    assert r2["total"] == r1["total"]
    after = snapshot(env["activity"])
    assert before == after
    assert not (env["activity"] / "derived.jsonl").exists()
    assert not (env["activity"] / "index.json").exists()
    assert not (env["activity"] / "sources_state.json").exists()

def test_unchanged_filesystem_skips_full_rescan(env):
    log = env["weather_log"] / "task.log"
    log.write_text("2026-10-16T08:00:00Z [pid 1] cycle end (exit 1)\n", encoding="utf-8")
    index.reset_memory()
    index.ensure_index(path_overrides=env["overrides"])
    built = index._built_at
    sigs = list(index._source_sigs or [])
    index.ensure_index(path_overrides=env["overrides"])
    assert index._built_at == built
    assert index._source_sigs == sigs


def test_stable_ids_across_reindex(env):
    log = env["weather_log"] / "task.log"
    log.write_text("2026-10-16T08:00:00Z [pid 1] cycle end (exit 1)\n", encoding="utf-8")
    index.reset_memory()
    a = index.list_activity(path_overrides=env["overrides"])
    ids_a = sorted(i["id"] for i in a["items"])
    times_a = {i["id"]: (i["observed_at"], i["source_at"]) for i in a["items"]}
    index.reset_memory()
    b = index.list_activity(path_overrides=env["overrides"])
    ids_b = sorted(i["id"] for i in b["items"])
    times_b = {i["id"]: (i["observed_at"], i["source_at"]) for i in b["items"]}
    assert ids_a == ids_b
    assert times_a == times_b
    # Missing observation stays null — never indexing time
    for it in b["items"]:
        assert it.get("observed_at") is None or "T" in it["observed_at"]


def test_api_watched_filter_aliases(env):
    ST.upsert_event(
        _event("KXHIGHNY-G", [_market("KXHIGHNY-G-T70")]),
        retrieved_at="2026-10-17T12:00:00Z",
        source="test",
        run_id="run1",
        markets_complete=True,
    )
    index.reset_memory()
    result = index.list_activity(
        watched=["weather:KXHIGHNY-G"],
        path_overrides=env["overrides"],
    )
    assert any(i["id"].startswith("explore.discovered:KXHIGHNY-G") for i in result["items"])
    empty = index.list_activity(watched=["weather:OTHER"], path_overrides=env["overrides"])
    assert not any(i["id"].startswith("explore.discovered:KXHIGHNY-G") for i in empty["items"])


def test_http_activity_routes_passive(env, monkeypatch):
    """HTTP GET /api/activity* must succeed without writing activity cache files."""
    index.reset_memory()
    # Route handlers call ensure_index without overrides; monkeypatched roots apply.
    before = sorted(str(p) for p in env["activity"].rglob("*") if p.is_file())
    client = TestClient(__import__("api.app", fromlist=["app"]).app)
    assert client.get("/api/activity/summary").status_code == 200
    assert client.get("/api/activity").status_code == 200
    assert client.get("/api/activity/ids").status_code == 200
    after = sorted(str(p) for p in env["activity"].rglob("*") if p.is_file())
    assert before == after
    assert not (env["activity"] / "derived.jsonl").exists()


def test_pending_before_shard_survives_crash_simulation(env):
    """Pending files written before shard; fold after — retry uses same ids."""
    prev = None
    entry = {
        "event_ticker": "KXHIGHNY-H",
        "title": "H",
        "markets": [],
        "first_seen_at": "2026-10-17T12:00:00Z",
        "lifecycle": {"state": "open"},
        "markets_complete": True,
    }
    items = explore_emit.emit_and_stage(
        event_ticker="KXHIGHNY-H",
        prev=prev,
        entry=entry,
        retrieved_at="2026-10-17T12:00:00Z",
        run_id="runX",
        is_new=True,
    )
    assert items
    pending = list(AP.PENDING_DIR.glob("*/*.json"))
    assert pending
    # Crash before shard: pending remains. Retry emit_and_stage should not create duplicate pending.
    items2 = explore_emit.emit_and_stage(
        event_ticker="KXHIGHNY-H",
        prev=prev,
        entry=entry,
        retrieved_at="2026-10-17T12:00:00Z",
        run_id="runX",
        is_new=True,
    )
    assert [i["id"] for i in items2] == [i["id"] for i in items]
    assert len(list(AP.PENDING_DIR.glob("*/*.json"))) == len(pending)
    explore_emit.fold_pending_into_run("runX", [i["id"] for i in items])
    loaded = explore_emit.load_explore_activity_files()
    assert sum(1 for i in loaded if i["id"] == items[0]["id"]) == 1

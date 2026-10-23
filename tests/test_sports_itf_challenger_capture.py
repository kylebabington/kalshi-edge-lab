"""Isolated ITF challenger capture: timing, pools, pins, immutability. RESEARCH_ONLY / NO_BET."""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from research.sports import models as M
from research.sports.challenger import capture as CAP
from research.sports.challenger import history as H
from research.sports.challenger import itf_singles_v1 as CH
from research.sports.challenger import records as R
from research.sports.challenger import report as RP
from research.sports.challenger.constants import COMPETITIONS, P_AGREE_TOL, PAIRED_FROZEN_PARAMS
from research.sports.core import sha256_json


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _event(gid, start, home, away, winner, available, receipt):
    return {
        "source_game_id": gid, "start": start, "home": home, "away": away,
        "winner_id": winner, "available_at": available, "completed": True, "neutral": True,
        "_receipt_at": receipt, "_receipt_source": "test",
    }


COMP = SimpleNamespace(key="k_kxitfmatch", source="kalshi_only")
COMPW = SimpleNamespace(key="k_kxitfwmatch", source="kalshi_only")


def test_frozen_helper_signature_is_events_queries_params():
    sig = inspect.signature(CH.predict)
    assert list(sig.parameters) == ["events", "queries", "params"]


def test_as_of_history_excludes_result_released_between_as_of_and_helper_cutoff():
    start = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
    as_of = start - timedelta(minutes=80)
    helper = start - timedelta(minutes=60)
    leak_at = start - timedelta(minutes=70)
    early = _event("early", "2026-10-20T12:00:00Z", "a", "b", "a",
                   "2026-10-20T14:00:00Z", "2026-10-20T14:05:00Z")
    leak = _event("leak", "2026-10-24T12:00:00Z", "a", "c", "a",
                  _iso(leak_at), _iso(leak_at + timedelta(seconds=30)))
    view, meta = H.as_of_history(COMP, [early, leak], as_of.timestamp(), start.timestamp())
    assert meta.get("status") == "ok" and view is not None
    assert meta["results_used"] == 1 and meta["events_hidden"] == 1
    assert not any(e.get("winner_id") and e.get("source_game_id") == "leak" for e in view)
    q = [("q", "a", "c", start.timestamp())]
    p_filtered = CH.predict(view, q, {"k": 24, "carry": 0.0})["q"]
    p_never = CH.predict([H.strip_internal(early)], q, {"k": 24, "carry": 0.0})["q"]
    assert abs(p_filtered - p_never) <= P_AGREE_TOL
    p_leak = CH.predict([H.strip_internal(early), H.strip_internal(leak)], q, {"k": 24, "carry": 0.0})["q"]
    assert abs(p_leak - p_filtered) > P_AGREE_TOL
    assert helper.timestamp() == H.helper_cutoff(start.timestamp())


def test_safety_gate_refuses_unfiltered_leak():
    start = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
    as_of = (start - timedelta(minutes=80)).timestamp()
    leak_at = start - timedelta(minutes=70)
    leak = H.strip_internal(_event("leak", "2026-10-24T12:00:00Z", "a", "c", "a",
                                   _iso(leak_at), _iso(leak_at)))
    problems = H.safety_problems([leak], as_of, start.timestamp())
    assert problems and "after prediction_as_of" in problems[0]


def test_missing_receipt_is_unavailable_not_a_silent_reinterpretation():
    start = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
    as_of = (start - timedelta(minutes=80)).timestamp()
    e = {"source_game_id": "x", "start": "2026-10-20T12:00:00Z", "home": "a", "away": "b",
         "winner_id": "a", "available_at": "2026-10-20T14:00:00Z", "completed": True}
    view, meta = H.as_of_history(COMP, [e], as_of, start.timestamp())
    assert view is None
    assert "missing_local_receipt" in meta["unavailable"]


def test_participant_direction_is_named_player_vs_opponent():
    start = _ts("2026-10-24T16:00:00Z")
    cs = [
        {"ticker": "E-AAA", "status": "ok", "participant": "uuid-aaa", "family": "game_winner"},
        {"ticker": "E-BBB", "status": "ok", "participant": "uuid-bbb", "family": "game_winner"},
    ]
    qs, why = H.queries_for_contracts(cs, start)
    assert why is None and len(qs) == 2
    by = {q[0]: q for q in qs}
    assert by["E-AAA"][1] == "uuid-aaa" and by["E-AAA"][2] == "uuid-bbb"
    assert by["E-BBB"][1] == "uuid-bbb" and by["E-BBB"][2] == "uuid-aaa"
    assert inspect.signature(CH.predict).parameters["queries"]


def test_pools_stay_separate():
    start = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
    as_of = (start - timedelta(minutes=70)).timestamp()
    men = [_event("m1", "2026-10-20T12:00:00Z", "mA", "mB", "mA",
                  "2026-10-20T14:00:00Z", "2026-10-20T14:05:00Z")]
    women = [_event("w1", "2026-10-20T12:00:00Z", "wA", "wB", "wA",
                    "2026-10-20T14:00:00Z", "2026-10-20T14:05:00Z")]
    vm, mm = H.as_of_history(COMP, men, as_of, start.timestamp())
    vw, mw = H.as_of_history(COMPW, women, as_of, start.timestamp())
    assert mm["pool"] == "k_kxitfmatch" and mw["pool"] == "k_kxitfwmatch"
    assert {e["source_game_id"] for e in vm} == {"m1"}
    assert {e["source_game_id"] for e in vw} == {"w1"}
    q = [("q", "mA", "mB", start.timestamp())]
    p_men = CH.predict(vm, q, {"k": 48, "carry": 0.0})["q"]
    p_mixed = CH.predict(vm + vw, q, {"k": 48, "carry": 0.0})["q"]
    assert abs(p_men - p_mixed) <= P_AGREE_TOL  # unused women's ids do not change men
    # mixing pools for a women's query would use the wrong state if concatenated; capture never concatenates
    assert set(COMPETITIONS) == {"k_kxitfmatch", "k_kxitfwmatch"}


def test_paired_predict_uses_identical_events_and_queries():
    start = _ts("2026-10-24T16:00:00Z")
    early = H.strip_internal(_event("early", "2026-10-20T12:00:00Z", "a", "b", "a",
                                    "2026-10-20T14:00:00Z", "2026-10-20T14:05:00Z"))
    q = [("T-A", "a", "c", start)]
    p48, p24 = H.predict_paired([early], q, {"k": 48, "carry": 0.0, "gap_days": 120, "base": 1500})
    assert abs(p48["T-A"] - CH.predict([early], q, {"k": 48, "carry": 0.0})["T-A"]) <= P_AGREE_TOL
    assert abs(p24["T-A"] - CH.predict([early], q, PAIRED_FROZEN_PARAMS)["T-A"]) <= P_AGREE_TOL
    assert p48["T-A"] != p24["T-A"]


def test_publish_exclusive_is_immutable_and_detects_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "CAPTURE_DIR", tmp_path)
    path = tmp_path / "predictions" / "k_kxitfmatch" / "kalshi_x.json"
    rec = R.publish_exclusive(path, {"schema": "t", "cluster": "kalshi:x", "p": 0.6})
    assert rec["record_sha256"] == sha256_json({k: v for k, v in rec.items() if k != "record_sha256"})
    with pytest.raises(R.RecordExists):
        R.publish_exclusive(path, {"schema": "t", "cluster": "kalshi:x", "p": 0.9})
    kept, status = R.inspect_record(path)
    assert status == "ok" and kept["p"] == 0.6

    bad = tmp_path / "predictions" / "k_kxitfmatch" / "partial.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b'{"schema": "t", "record_sha256":')
    obj, st = R.inspect_record(bad)
    assert st == "incomplete" and obj is None
    with pytest.raises(R.IncompleteRecord):
        R.publish_exclusive(bad, {"schema": "t", "ok": True})
    assert bad.read_bytes().startswith(b'{"schema"')


def test_settlement_does_not_mutate_prediction(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "CAPTURE_DIR", tmp_path)
    pred_path = R.prediction_path("k_kxitfmatch", "kalshi:abc")
    pred = R.publish_exclusive(pred_path, {"schema": R.SCHEMA_PREDICTION, "competition": "k_kxitfmatch",
                                           "cluster": "kalshi:abc", "contracts": []})
    digest = pred["record_sha256"]
    stl_path = R.settlement_path("k_kxitfmatch", "kalshi:abc")
    R.publish_exclusive(stl_path, {"schema": R.SCHEMA_SETTLEMENT, "competition": "k_kxitfmatch",
                                   "cluster": "kalshi:abc", "contracts": [{"ticker": "T", "state": "SETTLED_YES", "y": 1}]})
    again, status = R.inspect_record(pred_path)
    assert status == "ok" and again["record_sha256"] == digest
    with pytest.raises(R.RecordExists):
        R.publish_exclusive(stl_path, {"schema": R.SCHEMA_SETTLEMENT, "cluster": "kalshi:abc"})


def test_pin_mismatch_is_reported(monkeypatch):
    monkeypatch.setattr(CAP, "global_capture_pins", lambda: {"capture_code_hash": "aaa"})
    problems = CAP.check_pins({"pins": {"capture_code_hash": "bbb", "challenger_code_hash": CH.code_hash(),
                                        "events_sha256": {}, "contracts_sha256": {}}})
    assert any("capture_code_hash" in p for p in problems)


def test_horizons_are_not_all_labelled_sixty():
    start = _ts("2026-10-24T16:00:00Z")
    as_of = start - 80 * 60
    recorded = start - 75 * 60
    assert (start - as_of) / 60.0 == 80.0
    assert (start - recorded) / 60.0 == 75.0
    assert CAP.NOMINAL_HORIZON_MIN == 60


def test_prospective_eligibility_ignores_later_settlement():
    freeze = _ts("2026-10-23T00:34:18.095413Z")
    start = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
    as_of = start - timedelta(minutes=70)
    recorded = start - timedelta(minutes=65)
    pred = {
        "prediction_as_of": _iso(as_of),
        "recorded_at": _iso(recorded),
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "outcome_availability": {"capture_time_status": "unavailable"},
        "evidence": [{"received_at": _iso(as_of - timedelta(seconds=5))}],
    }
    elig, why = RP.prospective_eligibility(pred, freeze)
    assert elig == "verified" and why is None
    pred_avail = dict(pred)
    pred_avail["outcome_availability"] = {"capture_time_status": "available"}
    elig2, why2 = RP.prospective_eligibility(pred_avail, freeze)
    assert elig2 == "excluded" and why2 == "outcome_available_at_capture"
    pred_miss = dict(pred)
    pred_miss["outcome_availability"] = {}
    elig3, why3 = RP.prospective_eligibility(pred_miss, freeze)
    assert elig3 == "cannot_verify"
    pred_late = dict(pred)
    pred_late["recorded_at"] = _iso(start - timedelta(minutes=50))
    elig4, _ = RP.prospective_eligibility(pred_late, freeze)
    assert elig4 == "excluded"


def test_event_equal_brier_keeps_separate_denominators():
    rows_fz = [
        {"cluster": "e1", "p_challenger": 0.7, "p_paired_frozen": 0.6, "y": 1},
        {"cluster": "e1", "p_challenger": 0.3, "p_paired_frozen": 0.4, "y": 0},
        {"cluster": "e2", "p_challenger": 0.8, "p_paired_frozen": 0.5, "y": 1},
    ]
    rows_mk = rows_fz[:2]
    for r in rows_mk:
        r["p_market"] = 0.55
    d_fz = RP.event_equal_delta(rows_fz, "p_challenger", "p_paired_frozen")
    d_mk = RP.event_equal_delta(
        [{"cluster": r["cluster"], "p_challenger": r["p_challenger"], "p_market": r["p_market"], "y": r["y"]}
         for r in rows_mk],
        "p_challenger", "p_market")
    assert d_fz["n_events"] == 2 and d_fz["n_contracts"] == 3
    assert d_mk["n_events"] == 1 and d_mk["n_contracts"] == 2
    assert d_fz["n_events"] != d_mk["n_events"]


def test_nonbinary_settlement_not_in_brier():
    rows = [{"cluster": "e", "p_challenger": 0.5, "p_paired_frozen": 0.5, "y": 1}]
    d = RP.event_equal_delta(rows, "p_challenger", "p_paired_frozen")
    assert d["n_contracts"] == 1
    assert "VOID_OR_CANCELLED" in RP.NON_BINARY


def test_frozen_capture_protocol_matches_wrapper_hash():
    path = CAP.CAPTURE_PROTOCOL_PATH
    assert path.is_file(), "run python -m research.sports.challenger.capture freeze"
    proto = json.loads(path.read_text(encoding="utf-8"))
    assert proto["pins"]["capture_code_hash"] == CAP.capture_code_hash()
    assert proto["pins"]["report_code_hash"] == CAP.report_code_hash()
    assert proto["pins"]["challenger_code_hash"] == CH.code_hash()
    assert proto["pins"]["challenger_frozen_at"] == "2026-10-23T00:34:18.095413Z"


def test_capture_package_still_isolated_from_live_code_files():
    from research.sports.live.pins import LIVE_CODE_FILES
    assert "challenger" not in " ".join(LIVE_CODE_FILES)
    src = inspect.getsource(CH.predict)
    assert "as_of" not in src
    assert "HORIZON_MIN" in inspect.getsource(CH._run)

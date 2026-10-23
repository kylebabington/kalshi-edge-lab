"""ITF challenger study: input stops, mapping, reconstruction labels, selection rules, isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.sports import evaluate as EV
from research.sports import models as M
from research.sports.challenger import diagnose as DG
from research.sports.challenger import inputs as IN
from research.sports.challenger import itf_singles_v1 as CH
from research.sports.challenger.constants import (
    CAPTURE_PROTOCOL_SHA256, COMPETITIONS, FROZEN_EVENTS_SHA256, P_AGREE_TOL,
    PHASE1_PROTOCOL_SHA256,
)
from research.sports.core import sha256_bytes
from research.sports.live import pins as PN
from research.sports.phase2 import config as P2


def test_frozen_pins_unchanged():
    assert EV.code_hash() == P2.PHASE1_CODE_HASH
    proto = json.loads((Path(PN.protocol_path())).read_text(encoding="utf-8"))
    assert PN.live_code_hash() == proto["pins"]["live_code_hash"]
    assert proto["pins"]["phase1_protocol_sha256"] == PHASE1_PROTOCOL_SHA256
    from research.sports.core import PROTOCOLS, sha256_file
    assert sha256_file(PROTOCOLS / "sports_phase2_capture_v2.json") == CAPTURE_PROTOCOL_SHA256


def test_p_agree_tolerance_is_documented():
    assert P_AGREE_TOL == 1e-12


def test_contract_side_mapping_two_participants():
    rec = {"contracts": [
        {"ticker": "A-YES", "status": "ok", "participant": "uu-aaa"},
        {"ticker": "A-NO", "status": "ok", "participant": "uu-bbb"},
    ]}
    a, b, why = DG.contract_side(rec, "A-YES")
    assert why is None and a == "uu-aaa" and b == "uu-bbb"
    a2, b2, why2 = DG.contract_side(rec, "A-NO")
    assert why2 is None and a2 == "uu-bbb" and b2 == "uu-aaa"


def test_contract_side_missing_ticker():
    rec = {"contracts": [{"ticker": "X", "status": "ok", "participant": "u"}]}
    a, b, why = DG.contract_side(rec, "Y")
    assert a is None and "ticker_not_on_capture_record" == why


def test_inspect_matches_konly_run_on_tiny_history():
    events = [
        {"source_game_id": "g1", "start": "2026-06-01T12:00:00Z", "home": "p1", "away": "p2",
         "winner_id": "p1", "available_at": "2026-06-01T14:00:00Z", "completed": True, "neutral": True},
        {"source_game_id": "g2", "start": "2026-06-02T12:00:00Z", "home": "p1", "away": "p3",
         "winner_id": "p3", "available_at": "2026-06-02T14:00:00Z", "completed": True, "neutral": True},
    ]
    q = [("g2", "p1", "p3", M.ts("2026-06-02T12:00:00Z"))]
    params = {"k": 24, "carry": 0.0}
    info = DG.inspect_at_queries(events, q, params)["g2"]
    pr = M._konly_run(events, q, params)["g2"]
    assert abs(info["p"] - pr) <= P_AGREE_TOL
    assert info["n_a"] == 1 and info["seen_a"] is True
    assert info["n_b"] == 0 and info["seen_b"] is False


def test_reconstructed_n0_is_not_a_live_cold_start_label():
    row = {
        "history_kind": "reconstructed_frozen_only", "p_agree": False, "min_n": 0,
        "event_key": "e", "ticker": "t", "d_brier": 0.1, "w_event": 0.001,
        "frozen_p": 0.5, "rating_diff": None,
    }
    row["agreement_slice"] = (
        "capture_time_agree" if row["history_kind"] == "capture_time" and row.get("p_agree") else
        "reconstructed_disagree"
    )
    assert row["agreement_slice"] != "capture_time_agree"
    assert row["min_n"] == 0
    # study rule: n_bin slices are only interpreted on capture_time_agree


def test_name_collisions_are_review_candidates_not_merged():
    targets = {
        "u1": {"name": "Ada Ng", "short_name": "A. Ng"},
        "u2": {"name": "Ada Ng", "short_name": "A. Ng"},
        "u3": {"name": "Bo Li"},
    }
    rows = DG.identity_table({"u1", "u2", "u3"}, targets)
    by = {r["participant"]: r for r in rows}
    assert by["u1"]["identity_status"] == "name_collision_review_candidate"
    assert by["u2"]["same_name_uuid_count"] == 2
    assert by["u1"]["merged"] is False and by["u2"]["merged"] is False
    assert by["u3"]["identity_status"] == "named"


def test_surface_scan_does_not_invent_fields():
    assert DG.surface_fields_present(["home", "away", "winner_id", "available_at"]) == []
    assert DG.surface_fields_present(["surface", "home"]) == ["surface"]


def test_selection_rules_are_recorded_before_comparison():
    rules = CH.selection_rules()
    assert rules["recorded_before_comparison"] is True
    assert "2026-07-01" in rules["chronological_split"]["select_start"]
    assert "october_21_snapshot" in rules["chronological_split"]
    assert 24 in rules["candidates"]["k"]
    assert 0.0 in rules["candidates"]["carry"]
    assert "never used for selection" in rules["chronological_split"]["october_21_snapshot"]


def test_select_params_keeps_separate_pools():
    men = [{"source_game_id": "m1", "start": "2026-07-10T12:00:00Z", "home": "mA", "away": "mB",
            "winner_id": "mA", "available_at": "2026-07-10T16:00:00Z", "completed": True, "neutral": True}]
    women = [{"source_game_id": "w1", "start": "2026-07-10T12:00:00Z", "home": "wA", "away": "wB",
              "winner_id": "wA", "available_at": "2026-07-10T16:00:00Z", "completed": True, "neutral": True}]
    out = CH.select_params([], CH.selection_rules(), pools=[men, women])
    assert out["pools"] == 2 and out["select_events"] == 2


def test_frozen_protocol_matches_code_hash():
    proto = CH.load_protocol()
    assert proto["challenger_code_hash"] == CH.code_hash()
    assert proto["selected"]["params"]["k"] == 48
    assert proto["frozen_at"] == "2026-10-23T00:34:18.095413Z"


def test_challenger_select_is_deterministic_and_ignores_post_window_labels():
    events = []
    for i in range(20):
        day = 10 + i
        a, b = (f"p{i % 5}", f"p{(i + 1) % 5}")
        events.append({
            "source_game_id": f"g{i}", "start": f"2026-07-{day:02d}T12:00:00Z",
            "home": a, "away": b, "winner_id": a,
            "available_at": f"2026-07-{day:02d}T16:00:00Z", "completed": True, "neutral": True,
        })
    events.append({
        "source_game_id": "after", "start": "2026-10-15T12:00:00Z", "home": "p0", "away": "p1",
        "winner_id": "p1", "available_at": "2026-10-15T16:00:00Z", "completed": True, "neutral": True,
    })
    rules = CH.selection_rules()
    s1 = CH.select_params(events, rules)
    s2 = CH.select_params(events, rules)
    assert s1["params"] == s2["params"]
    assert s1["select_events"] == 20
    assert s1["n_compared"] == 8


def test_challenger_replay_hides_unreleased_results():
    events = [
        {"source_game_id": "early", "start": "2026-07-01T12:00:00Z", "home": "a", "away": "b",
         "winner_id": "a", "available_at": "2026-07-01T14:00:00Z", "completed": True, "neutral": True},
        {"source_game_id": "late", "start": "2026-07-02T12:00:00Z", "home": "a", "away": "c",
         "winner_id": "a", "available_at": "2026-07-02T18:00:00Z", "completed": True, "neutral": True},
    ]
    # query for a vs c at 2026-07-02 12:00; cutoff is 11:00; late result available_at 18:00 is unused
    q = [("q", "a", "c", M.ts("2026-07-02T12:00:00Z"))]
    p = CH.predict(events, q, {"k": 24, "carry": 0.0})["q"]
    p_only_early = CH.predict(events[:1], q, {"k": 24, "carry": 0.0})["q"]
    assert abs(p - p_only_early) <= P_AGREE_TOL
    assert p > 0.5


def test_challenger_package_is_not_live_code():
    from research.sports.live.pins import LIVE_CODE_FILES
    assert "challenger" not in " ".join(LIVE_CODE_FILES)
    assert EV.CODE_FILES and "challenger" not in " ".join(EV.CODE_FILES)


def test_mismatched_history_hash_constant_covers_both_competitions():
    assert set(FROZEN_EVENTS_SHA256) == set(COMPETITIONS)


def test_input_error_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(IN, "LISTING_GZ", tmp_path / "missing.tsv.gz")
    with pytest.raises(Exception):
        IN.load_listing()

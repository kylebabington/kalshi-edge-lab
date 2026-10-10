"""Sports Phase 1 v2 (retrospective evaluation correction) tests: offline, temporary directories."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.sports import benchmark as BM
from research.sports import evaluate as EV
from research.sports import manifest as MAN
from research.sports import models as M
from research.sports.core import canonical_json, sha256_file
from research.sports.run import main as run_main

REPO = Path(__file__).resolve().parents[1]
PRE, TEST = "2025-06-01T00:00:00Z", "2026-03-01T00:00:00Z"


def _contracts(series, pre_agree, pre_n, test_agree, test_n):
    out = []
    for i in range(pre_n):
        out.append({"series": series, "status": "ok", "agree": int(i < pre_agree), "start": PRE})
    for i in range(test_n):
        out.append({"series": series, "status": "ok", "agree": int(i < test_agree), "start": TEST})
    return out


# ------------------------------------------------------------------ pre-test-only reconciliation gate

@pytest.mark.parametrize("agree,n,cohort", [(30, 30, "strict"), (29, 30, "failed"), (49, 50, "strict"),
                                            (48, 50, "failed"), (29, 29, "exploratory"), (0, 0, "exploratory")])
def test_cohort_boundaries(agree, n, cohort):
    assert EV.assign_cohort(agree, n) == cohort
    assert EV.reconciliation(_contracts("S", agree, n, 0, 0)).get("S", {"cohort": "exploratory"})["cohort"] == cohort


@pytest.mark.parametrize("pre_agree,pre_n,cohort", [(30, 30, "strict"), (29, 30, "failed"), (5, 5, "exploratory")])
def test_test_period_outcomes_cannot_change_cohort(pre_agree, pre_n, cohort):
    variants = [(0, 0), (200, 200), (0, 200), (100, 200)]   # test-period agreement from none to perfect
    got = {EV.reconciliation(_contracts("S", pre_agree, pre_n, a, n))["S"]["cohort"] for a, n in variants}
    assert got == {cohort}


def test_all_settled_never_admits_to_strict():
    # 10 pre-test (all agree) + 200 test-period (all agree): v1 would admit on all-settled; v2 keeps it exploratory
    g = EV.reconciliation(_contracts("S", 10, 10, 200, 200))["S"]
    assert g["cohort"] == "exploratory" and g["all_rate"] == 1.0 and g["pre_n"] == 10


# ------------------------------------------------------------------ evaluate: cohorts and contract exclusions

def _setup_eval(tmp_path, monkeypatch, parent_rows_override=None):
    for name in ("NORMALIZED", "PREDICTIONS", "RESULTS", "PROTOCOLS"):
        monkeypatch.setattr(EV, name, tmp_path / name.lower())
    contracts = []
    for s, pre_agree in (("STRICT", 30), ("EXPL", 5), ("FAIL", 25)):
        for i, c in enumerate(_contracts(s, pre_agree, 30 if s != "EXPL" else 5, 0, 0)):
            contracts.append({**c, "ticker": f"{s}-PRE{i}", "family": "game_winner", "kalshi_outcome": "yes"})
        for i in range(3):
            contracts.append({"series": s, "status": "ok", "agree": 0 if i == 0 else 1, "start": TEST,
                              "ticker": f"{s}-T{i}", "family": "game_winner", "kalshi_outcome": "yes"})
    d = EV.NORMALIZED / "toy"
    d.mkdir(parents=True)
    (d / "contracts.jsonl").write_bytes(b"".join(canonical_json(c) + b"\n" for c in contracts))
    (d / "events.jsonl").write_bytes(b"")
    (d / "build_summary.json").write_text(json.dumps({"hash_contracts": "hc", "hash_events": "he"}))
    preds = [{"ticker": c["ticker"], "model": m, "p": 0.6, "cluster": c["ticker"], "start": TEST}
             for c in contracts if c["start"] == TEST for m in ("elo", "naive")]
    preds.sort(key=lambda r: (r["ticker"], r["model"]))
    monkeypatch.setitem(M.PREDICT, "toy_source", lambda c, e, x, p: [dict(r) for r in preds])
    gate = EV.reconciliation(contracts)
    proto = {"code_hash": EV.code_hash(), "supersedes": "parent_v",
             "competitions": {"toy": {"data": {"contracts": "hc", "events": "he"}, "params": {}, "gate": gate}}}
    EV.PROTOCOLS.mkdir(parents=True)
    (EV.PROTOCOLS / "child_v.json").write_text(json.dumps(proto))
    parent_rows = parent_rows_override if parent_rows_override is not None else preds
    pj = EV.PREDICTIONS / "parent_v" / "toy.jsonl"
    pj.parent.mkdir(parents=True)
    pj.write_bytes(b"".join(canonical_json({**r, "protocol": "parent_v"}) + b"\n" for r in parent_rows))
    comp = SimpleNamespace(key="toy", sport="Toy", source="toy_source")
    args = SimpleNamespace(protocol="child_v", family=None)
    return args, comp


def test_evaluate_assigns_cohorts_and_excludes_disagreements(tmp_path, monkeypatch):
    args, comp = _setup_eval(tmp_path, monkeypatch)
    EV.stage_evaluate(args, [comp], lambda m: None)
    out = EV.RESULTS / "child_v"
    rows = [json.loads(x) for x in (out / "scored.jsonl").read_text().splitlines()]
    assert {r["cohort"] for r in rows} == {"strict", "exploratory"}
    assert not any(r["series"] == "FAIL" for r in rows)                      # failed series excluded everywhere
    assert not any(r["ticker"].endswith("-T0") for r in rows)                  # disagreeing contracts excluded
    st = json.loads((out / "evaluation_status.json").read_text())["toy"]
    assert st["parent_journal_identical"] is True
    assert st["test_disagreements_by_series"] == {"EXPL": 1, "STRICT": 1}
    assert st["excluded"]["series failed pre-test reconciliation gate"] == 6
    assert st["scored_rows_by_cohort"] == {"strict": 4, "exploratory": 4}
    groups = {(m["level"], m["cohort"]) for m in json.loads((out / "metrics.json").read_text())}
    assert ("competition", "strict") in groups and ("competition", "exploratory") in groups


def test_evaluate_refuses_forecasts_that_differ_from_parent(tmp_path, monkeypatch):
    args, comp = _setup_eval(tmp_path, monkeypatch, parent_rows_override=[])
    with pytest.raises(SystemExit, match="differ from the parent_v journal"):
        EV.stage_evaluate(args, [comp], lambda m: None)


# ------------------------------------------------------------------ event-equal weighting

def test_event_equal_weighting_by_hand():
    # event A: 3 strikes, model p=0.9 all y=0 (loss 0.81 each); event B: 1 strike, p=0.9 y=1 (loss 0.01)
    rows = [{"ticker": f"A{i}", "cluster": "A", "p": 0.9, "y": 0} for i in range(3)] + \
           [{"ticker": "B0", "cluster": "B", "p": 0.9, "y": 1}]
    m = EV.metrics(rows)
    assert m["brier"] == pytest.approx((3 * 0.81 + 0.01) / 4)
    assert m["brier_event"] == pytest.approx((0.81 + 0.01) / 2)
    naive = [{**r, "p": 0.5} for r in rows]                                   # loss 0.25 everywhere
    b = EV.paired_bootstrap(rows, naive, 3)
    assert b["d_brier"] == pytest.approx((3 * 0.56 - 0.24) / 4)
    assert b["d_brier_event"] == pytest.approx((0.56 - 0.24) / 2)
    assert b["d_brier_event_ci"][0] <= b["d_brier_event"] <= b["d_brier_event_ci"][1]
    assert b == EV.paired_bootstrap(rows, naive, 3)


def test_contract_weighted_view_unchanged_by_event_fields():
    rows = [{"ticker": f"T{i}", "cluster": f"C{i // 2}", "p": 0.3 + 0.05 * i, "y": i % 2} for i in range(12)]
    naive = [{**r, "p": 0.5} for r in rows]
    b = EV.paired_bootstrap(rows, naive, 11)
    per = {}
    for r, o in zip(rows, naive):
        per.setdefault(r["cluster"], []).append((r["p"] - r["y"]) ** 2 - (o["p"] - o["y"]) ** 2)
    assert b["d_brier"] == pytest.approx(sum(sum(v) for v in per.values()) / 12)
    assert b["d_brier_event"] == pytest.approx(sum(sum(v) / len(v) for v in per.values()) / len(per))


# ------------------------------------------------------------------ market benchmark sufficiency

def _bench_rows(n_strict, n_expl):
    rows = {"elo": [], "naive": []}
    mids, cohort = {}, {}
    for i in range(n_strict + n_expl):
        t = f"T{i}"
        for m, p in (("elo", 0.6), ("naive", 0.5)):
            rows[m].append({"ticker": t, "cluster": f"C{i}", "model": m, "p": p, "y": i % 2})
        mids[t] = 0.55
        cohort[t] = "strict" if i < n_strict else "exploratory"
    return rows, mids, cohort


def test_final_matched_sufficiency_threshold():
    rows, mids, _ = _bench_rows(49, 0)
    v = BM.compare_view(rows, mids, lambda t: True, "espn_team", 1)
    assert v["matched_clusters"] == 49 and v["sufficiency"] == BM.INSUFFICIENT
    assert "d_brier_ci" not in v["model_minus_kalshi"] and "d_brier" in v["model_minus_kalshi"]
    rows, mids, _ = _bench_rows(50, 0)
    v = BM.compare_view(rows, mids, lambda t: True, "espn_team", 1)
    assert v["sufficiency"] == BM.SUFFICIENT and "d_brier_ci" in v["model_minus_kalshi"]


def test_matched_count_is_after_quote_filters():
    rows, mids, _ = _bench_rows(60, 0)
    for t in list(mids)[:11]:                                                 # 11 quotes failed the filters
        del mids[t]
    assert BM.compare_view(rows, mids, lambda t: True, "espn_team", 1)["sufficiency"] == BM.INSUFFICIENT


def test_strict_view_uses_strict_losses_only():
    rows, mids, cohort = _bench_rows(52, 30)
    allv = BM.compare_view(rows, mids, lambda t: True, "espn_team", 1)
    strict = BM.compare_view(rows, mids, lambda t: cohort[t] == "strict", "espn_team", 1)
    assert allv["matched_clusters"] == 82 and strict["matched_clusters"] == 52
    assert strict["model"]["n"] == 52 and strict["model_minus_kalshi"]["clusters"] == 52
    rows, mids, cohort = _bench_rows(45, 30)                                 # mixed 75 matched, strict 45
    strict = BM.compare_view(rows, mids, lambda t: cohort[t] == "strict", "espn_team", 1)
    allv = BM.compare_view(rows, mids, lambda t: True, "espn_team", 1)
    assert allv["sufficiency"] == BM.SUFFICIENT and strict["sufficiency"] == BM.INSUFFICIENT


def test_kalshi_only_comparisons_are_descriptive():
    rows, mids, _ = _bench_rows(60, 0)
    v = BM.compare_view(rows, mids, lambda t: True, "kalshi_only", 1)
    assert v["sufficiency"] == BM.DESCRIPTIVE_KO and "d_brier_ci" not in v["model_minus_kalshi"]


def test_insufficient_samples_excluded_from_market_headline():
    from research.sports.report import market_headline
    good = {"views": {"all_matched": {"sufficiency": BM.SUFFICIENT},
                      "strict_matched": {"model_minus_kalshi": {"n": 60, "d_brier": 0.01, "d_brier_ci": [0.005, 0.02]},
                                         "naive_minus_kalshi": {"n": 60, "d_brier": 0.03, "d_brier_ci": [0.01, 0.05]}}},
            "headline_eligible": True, "source": "espn_team", "competition": "a"}
    bad = {"views": {"all_matched": {"sufficiency": BM.SUFFICIENT},
                     "strict_matched": {"model_minus_kalshi": {"n": 40, "d_brier": -0.2}}},
           "headline_eligible": False, "source": "espn_team", "competition": "b"}
    h = market_headline([good, bad])
    assert h["headline"] == 1 and h["model_vs_kalshi"] == {"worse": 1}
    assert h["independent_insufficient_strict"] == ["b"]


def test_sample_frame_is_pinned_and_capped():
    per = {f"C{i}": {f"K{i}"} for i in range(60)}
    cmap = {f"K{i}": {"side": "home"} for i in range(60)}
    a = BM.sample_clusters("sports_phase1_v1", "x", "kalshi_only", per, cmap)
    assert len(a) == 40 and a == BM.sample_clusters("sports_phase1_v1", "x", "kalshi_only", per, cmap)
    assert a != BM.sample_clusters("sports_phase1_v2", "x", "kalshi_only", per, cmap)   # why the parent name is pinned


# ------------------------------------------------------------------ manifests and v1 preservation

def test_manifest_refuses_missing_and_mismatched(tmp_path, monkeypatch):
    for name, sub in (("REPO_ROOT", ""), ("PROTOCOLS", "protocols"), ("PREDICTIONS", "pred"), ("RESULTS", "res"),
                      ("DOCS", "docs")):
        monkeypatch.setattr(MAN, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setitem(MAN.DOC_FILES, "pv", ["a.md"])
    files = [tmp_path / "protocols" / "pv.json", tmp_path / "pred" / "pv" / "c.jsonl", tmp_path / "docs" / "a.md"] + \
            [tmp_path / "res" / "pv" / f for f in MAN.RESULT_FILES]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    (tmp_path / "docs" / "a.md").unlink()
    with pytest.raises(SystemExit, match="missing"):
        MAN.write_manifest("pv", None, "", lambda m: None)
    (tmp_path / "docs" / "a.md").write_text("x")
    MAN.write_manifest("pv", None, "note", lambda m: None)
    assert MAN.verify("pv") == []
    (tmp_path / "res" / "pv" / "metrics.json").write_text("changed")
    (tmp_path / "pred" / "pv" / "extra.jsonl").write_text("y")
    (tmp_path / "docs" / "a.md").unlink()
    probs = MAN.verify("pv")
    assert any("hash mismatch" in p and "metrics.json" in p for p in probs)
    assert any("unexpected journal" in p for p in probs) and any("missing" in p for p in probs)
    with pytest.raises(SystemExit):                                          # existing manifest never overwritten
        MAN.write_manifest("pv", None, "note", lambda m: None)
    assert MAN.verify("other") == [f"manifest missing: protocols/other.artifacts.json"]


def test_manifest_checked_against_reference(tmp_path, monkeypatch):
    for name, sub in (("REPO_ROOT", ""), ("PROTOCOLS", "protocols"), ("PREDICTIONS", "pred"), ("RESULTS", "res"),
                      ("DOCS", "docs")):
        monkeypatch.setattr(MAN, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setitem(MAN.DOC_FILES, "pv", [])
    for f in [tmp_path / "protocols" / "pv.json"] + [tmp_path / "res" / "pv" / f for f in MAN.RESULT_FILES]:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    ref = tmp_path / "ref.json"
    ref.write_text(json.dumps({**MAN.summary_hashes("pv"), "metrics_sha256": "0" * 64}))
    with pytest.raises(SystemExit, match="differ"):
        MAN.write_manifest("pv", ref, "", lambda m: None)
    ref.write_text(json.dumps(MAN.summary_hashes("pv")))
    body = MAN.write_manifest("pv", ref, "retro", lambda m: None)
    assert body["checked_against"]["result"] == "match"


def test_v1_protocol_and_tracked_docs_preserved():
    man = json.loads((REPO / "research/sports/protocols/sports_phase1_v1.artifacts.json").read_text(encoding="utf-8"))
    assert man["checked_against"]["result"] == "match" and "retrospectively" in man["note"]
    proto = "research/sports/protocols/sports_phase1_v1.json"
    assert man["files"][proto] == "b7e8ddc7aab7a348bee97cf63bc36c7cd0c0005388b21f596c05de4805cc6c24"
    assert sha256_file(REPO / proto) == man["files"][proto]
    sup = json.loads((REPO / "research/sports/protocols/sports_phase1_v1.docs_portable_v1.json")
                     .read_text(encoding="utf-8"))
    for d in MAN.DOC_FILES["sports_phase1_v1"]:
        rel = f"docs/research/{d}"
        assert sup["files"][rel]["exact_sha256_in_original_manifest"] == man["files"][rel], rel
        assert MAN.portable_sha256(REPO / rel) == sup["files"][rel]["portable_sha256"], rel


def test_v1_is_refused_by_writing_stages_and_v2_docs_are_distinct():
    assert not set(MAN.DOC_FILES["sports_phase1_v1"]) & set(MAN.DOC_FILES["sports_phase1_v2"])
    for stage in ("select", "evaluate", "benchmark", "report", "all"):
        with pytest.raises(SystemExit, match="preserved"):
            run_main([stage, "--protocol", "sports_phase1_v1", "--cache-only"])
    args = SimpleNamespace(protocol="sports_phase1_v1")
    for fn in (EV.stage_select, EV.stage_evaluate):
        with pytest.raises(SystemExit, match="preserved"):
            fn(args, [], lambda m: None)

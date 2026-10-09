"""Consolidated Phase 1 reporting for a correction protocol (v2): coverage, cohorts, results.

Writes versioned tracked summaries under docs/research/ (deterministic given the cached inputs):
  sports_phase1_results_v2.md, sports_phase1_coverage_v2.csv, sports_phase1_results_v2.csv,
  sports_phase1_v1_to_v2_comparison.md
The preserved v1 documents are never written by this module.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .competitions import all_competitions, load_inventory
from .core import NORMALIZED, RESULTS, read_jsonl
from .evaluate import COHORT_RULES, GATE_AGREEMENT_PCT, GATE_MIN_N, INTERPRETATION, MIN_MATCHED_CLUSTERS, PRESERVED, \
    TEST_PERIOD_NOTE, classify, code_hash, load_protocol
from .benchmark import DESCRIPTIVE_KO, INSUFFICIENT, SUFFICIENT
from . import manifest as man

DOCS = man.DOCS
FEAS = DOCS / "sports_feasibility_v1.md"
NOT_PHASE1_FAMILY = {
    "player_prop": "player/appearance-conditioned; pregame participation not reconstructable as-of (not modelled)",
    "tournament_outright": "outright/bracket: few independent outcomes per season; needs prospective capture + simulation",
    "series_playoff": "playoff series: few outcomes; needs game model + bracket simulation (not in Phase 1)",
    "season_wins": "season totals: one outcome per team-season; prospective capture required",
    "awards": "awards: voting outcomes without as-of inputs",
    "league_leaders": "league leaders: one outcome per stat-season",
    "rankings_polls": "rankings/polls: poll processes (KenPom paid)",
    "draft": "draft: consensus mocks not archived as-of",
    "personnel_ops": "out of scope (non-sporting outcome)",
    "other_novelty": "out of scope (novelty)",
    "combo_parlay": "out of scope (derived combination)",
    "test_placeholder": "out of scope (test)",
}
COHORT_ORDER = ["strict", "exploratory", "kalshi_settlement_only"]
COHORT_LABEL = {"strict": "strict (settlement-concordant, pre-test reconciled)",
                "exploratory": "exploratory (insufficient pre-test reconciliation evidence)",
                "kalshi_settlement_only": "Kalshi-settlement-only (no independent source)"}


def v1_classes() -> dict[tuple[str, str], str]:
    out = {}
    text = FEAS.read_text(encoding="utf-8")
    block = text.split("<!-- BEGIN:matrix -->", 1)[1].split("<!-- END:matrix -->", 1)[0]
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 13 and re.fullmatch(r"[A-F]", cells[12]):
            out[(cells[0], cells[1])] = cells[12]
    return out


def _f(x, nd=4):
    return "" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _ci(d, key):
    if not d or d.get("n", 0) == 0 or key not in d:
        return ""
    if f"{key}_ci" not in d:
        return f"{d[key]:+.4f} (no CI)"
    lo, hi = d[f"{key}_ci"]
    return f"{d[key]:+.4f} [{lo:+.4f}, {hi:+.4f}]"


def _cls(d, key="d_brier"):
    return classify(d[f"{key}_ci"]) if d and d.get("n") and f"{key}_ci" in d else ""


def _load(protocol):
    out = RESULTS / protocol
    j = lambda p: json.loads((out / p).read_text(encoding="utf-8")) if (out / p).exists() else None
    return out, j("metrics.json") or [], j("evaluation_status.json") or {}, j("benchmark_metrics.json") or [], \
        j("closing_lines.json")


def _csv(path: Path, rows: list[dict]):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


# --------------------------------------------------------------------------------------------
# Coverage of the whole inventory
# --------------------------------------------------------------------------------------------

def build_coverage(protocol: str, parent: str):
    inv = load_inventory()
    comps, specs, excl = all_competitions(inv)
    cls = v1_classes()
    proto = load_protocol(protocol)
    _, metrics, status, _, _ = _load(protocol)
    pdocs = dict(zip(["results_md", "coverage", "results_csv", "extra"], man.DOC_FILES[parent]))
    v1_status = {r["series_ticker"]: r["phase1_status"] for r in _read_csv(DOCS / pdocs["coverage"])}
    attempted_sports = {c.sport for c in comps}
    series_role = {}
    for c in comps:
        for s, kind, seg in specs[c.key]:
            series_role[s] = (c, "kalshi_settlement_only" if c.source == "kalshi_only" else "independent_outcomes", None)
        for e in excl[c.key]:
            series_role[e["series"]] = (c, "excluded_spec", e["reason"])
    per_series = defaultdict(Counter)
    for c in comps:
        p = NORMALIZED / c.key / "contracts.jsonl"
        if not p.exists():
            continue
        for r in read_jsonl(p):
            per_series[r["series"]]["contracts"] += 1
            per_series[r["series"]]["ok"] += int(r["status"] == "ok")
    test_scored = {}
    for m in metrics:
        if m["level"] == "series":
            prim = m["models"].get(m["primary"], {})
            test_scored[m["group"]] = (prim.get("n", 0), prim.get("clusters", 0))
    rows = []
    for r in inv:
        s = r["series_ticker"]
        role = series_role.get(s)
        out = {"series_ticker": s, "series_title": r["series_title"], "sport": r["sport"],
               "competition_top": r["competition_top"], "contract_family": r["contract_family"],
               "v1_cell_class": cls.get((r["sport"], r["contract_family"]), ""),
               "traded_markets": r["traded_markets"], "volume_fp_contracts": r["volume_fp_total"],
               "sport_supported": r["sport"] in attempted_sports}
        cohort, gate_pre, gate_all, dis = "", "", "", ""
        if role:
            comp, kind, why = role
            entry = proto["competitions"].get(comp.key, {})
            gate = (entry.get("gate") or {}).get(s)
            n, k = test_scored.get(s, (0, 0))
            d = (status.get(comp.key, {}).get("test_disagreements_by_series") or {}).get(s, 0)
            if gate:
                gate_pre = f"{gate['pre_agree']}/{gate['pre_n']}"
                gate_all = f"{gate['all_agree']}/{gate['all_n']}"
            if kind == "excluded_spec":
                st, reason = "excluded_spec", why
            elif entry.get("blocked") or (entry.get("params") or {}).get("blocked"):
                st, reason = "blocked", entry.get("blocked") or entry["params"]["blocked"]
            elif kind == "kalshi_settlement_only":
                cohort = "kalshi_settlement_only"
                st, reason = ("kalshi_settlement_only_evaluated", "") if n else \
                    ("no_test_contracts", "no scored contracts in the test window")
            elif gate is None:
                st, reason = "excluded_no_reconciled_contracts", "no settled contracts with a resolved independent outcome"
            elif gate["cohort"] == "failed":
                cohort = "failed"
                st, reason = "excluded_gate_failed", f"pre-test agreement {gate_pre} < {GATE_AGREEMENT_PCT}%"
            else:
                cohort = gate["cohort"]
                if n:
                    st = f"{cohort}_evaluated"
                    reason = "" if cohort == "strict" else f"only {gate['pre_n']} pre-test settled contracts (< {GATE_MIN_N})"
                else:
                    st = "no_test_contracts"
                    reason = "all test contracts disagree with Kalshi settlement" if d else "no scored contracts in the test window"
            dis = d
            out.update(attempted_competition=comp.key, phase1_path=kind, cohort=cohort, coverage_status=st, reason=reason,
                       contracts_built=per_series[s]["contracts"], contracts_ok=per_series[s]["ok"],
                       pre_test_agreement=gate_pre, all_settled_agreement=gate_all, test_disagreements_excluded=dis,
                       test_contracts=n, test_clusters=k)
        else:
            fam = r["contract_family"]
            if r["sport"].startswith(("Non-sport", "Unassigned")):
                why = "not a sporting outcome (v1 class E)"
            elif fam in NOT_PHASE1_FAMILY:
                why = NOT_PHASE1_FAMILY[fam]
            elif int(float(r["traded_markets"] or 0)) == 0:
                why = "no traded markets"
            elif fam == "game_winner":
                why = "game-winner series not two-way structured or not in a Phase 1 adapter"
            else:
                why = "no independent results adapter for this competition in Phase 1 (game_winner only via Kalshi-only path)"
            st = "not_attempted_supported_sport" if out["sport_supported"] else "not_attempted_unsupported_sport"
            out.update(attempted_competition="", phase1_path="not_attempted", cohort="", coverage_status=st, reason=why,
                       contracts_built="", contracts_ok="", pre_test_agreement="", all_settled_agreement="",
                       test_disagreements_excluded="", test_contracts="", test_clusters="")
        out["v1_status"] = v1_status.get(s, "")
        rows.append(out)
    return rows


def results_rows(metrics) -> list[dict]:
    out = []
    for m in metrics:
        if m["level"] == "series":
            continue
        prim = m["models"].get(m["primary"], {})
        nv = m["models"].get("naive", {})
        sk = m.get("skill_vs_naive") or {}
        ci = lambda k, i: _f((sk.get(f"{k}_ci") or [None, None])[i])
        cw, ee = _cls(sk, "d_brier"), _cls(sk, "d_brier_event")
        out.append({"level": m["level"], "key": m["key"], "cohort": m["cohort"], "family": m["family"],
                    "primary_model": m["primary"], "test_contracts": prim.get("n"), "clusters": prim.get("clusters"),
                    "sufficient": m["sufficient"],
                    "brier_model": _f(prim.get("brier")), "brier_naive": _f(nv.get("brier")),
                    "brier_event_model": _f(prim.get("brier_event")), "brier_event_naive": _f(nv.get("brier_event")),
                    "logloss_model": _f(prim.get("log_loss")), "logloss_naive": _f(nv.get("log_loss")),
                    "ece_model": _f(prim.get("ece")), "base_rate": _f(prim.get("base_rate")),
                    "d_brier_vs_naive": _f(sk.get("d_brier")), "d_brier_ci_lo": ci("d_brier", 0),
                    "d_brier_ci_hi": ci("d_brier", 1), "class_contract_weighted": cw if m["sufficient"] else "",
                    "d_brier_event_vs_naive": _f(sk.get("d_brier_event")), "d_brier_event_ci_lo": ci("d_brier_event", 0),
                    "d_brier_event_ci_hi": ci("d_brier_event", 1), "class_event_equal": ee if m["sufficient"] else "",
                    "d_logloss_vs_naive": _f(sk.get("d_log_loss")), "d_logloss_ci_lo": ci("d_log_loss", 0),
                    "d_logloss_ci_hi": ci("d_log_loss", 1),
                    "d_logloss_event_vs_naive": _f(sk.get("d_log_loss_event")),
                    "d_logloss_event_ci_lo": ci("d_log_loss_event", 0), "d_logloss_event_ci_hi": ci("d_log_loss_event", 1),
                    "weighting_class_flip": bool(m["sufficient"] and cw and ee and cw != ee),
                    "classification_note": "exploratory, unadjusted for multiple comparisons"})
    return out


def identity_stats(comps) -> list[dict]:
    out = []
    for c in comps:
        p = NORMALIZED / c.key / "build_summary.json"
        if not p.exists():
            continue
        summ = json.loads(p.read_text(encoding="utf-8"))
        if summ.get("blocked"):
            out.append({"competition": c.key, "blocked": summ["blocked"]})
            continue
        out.append({"competition": c.key, "excluded": dict(summ["contracts_excluded"])})
    return out


# --------------------------------------------------------------------------------------------
# Headline computations
# --------------------------------------------------------------------------------------------

def naive_headline(metrics, cohort) -> dict:
    """Per family: groups, sufficient groups and exploratory classes under both weightings."""
    out = defaultdict(lambda: {"groups": 0, "sufficient": 0, "cw": Counter(), "ee": Counter()})
    for m in metrics:
        if m["level"] != "competition" or m["cohort"] != cohort:
            continue
        f = out[m["family"]]
        f["groups"] += 1
        sk = m.get("skill_vs_naive") or {}
        if m["sufficient"] and sk.get("n"):
            f["sufficient"] += 1
            f["cw"][_cls(sk, "d_brier")] += 1
            f["ee"][_cls(sk, "d_brier_event")] += 1
    return dict(sorted(out.items()))


def v1_naive_headline(v1_metrics, cmap) -> dict:
    out = {"independent": defaultdict(Counter), "kalshi_only": defaultdict(Counter)}
    for m in v1_metrics:
        if m["level"] != "competition":
            continue
        c = cmap.get(m["key"])
        pool = "kalshi_only" if c and c.source == "kalshi_only" else "independent"
        sk = m.get("skill_vs_naive") or {}
        if m["sufficient"] and sk.get("n"):
            out[pool][m["family"]][classify(sk["d_brier_ci"])] += 1
    return out


def market_headline(bench) -> dict:
    rows = [b for b in bench if not b.get("skipped")]
    head = [b for b in rows if b.get("headline_eligible")]
    out = {"benchmarked": len(rows), "skipped": len(bench) - len(rows),
           "independent": sum(b["source"] != "kalshi_only" for b in rows),
           "kalshi_only": sum(b["source"] == "kalshi_only" for b in rows),
           "headline": len(head),
           "model_vs_kalshi": Counter(_cls(b["views"]["strict_matched"].get("model_minus_kalshi")) for b in head),
           "naive_vs_kalshi": Counter(_cls(b["views"]["strict_matched"].get("naive_minus_kalshi")) for b in head),
           "independent_insufficient_strict": sorted(b["competition"] for b in rows if b["source"] != "kalshi_only"
                                                     and not b.get("headline_eligible")),
           "all_view_sufficient": sum(b["views"]["all_matched"]["sufficiency"] == SUFFICIENT for b in rows)}
    return out


# --------------------------------------------------------------------------------------------
# Stage
# --------------------------------------------------------------------------------------------

def stage_report(args, log):
    protocol = args.protocol
    if protocol in PRESERVED:
        raise SystemExit(f"{protocol} is preserved; its documents are not regenerated")
    proto = load_protocol(protocol)
    parent = proto["supersedes"]
    names = dict(zip(["results_md", "coverage", "results_csv", "comparison"], man.DOC_FILES[protocol]))
    out_dir, metrics, status, bench, closing = _load(protocol)
    comps, specs, excl = all_competitions()
    cov = build_coverage(protocol, parent)
    _csv(DOCS / names["coverage"], cov)
    _csv(DOCS / names["results_csv"], results_rows(metrics))
    ids = identity_stats(comps)
    hashes = {**man.summary_hashes(protocol), "code_hash": code_hash(),
              "benchmark_metrics_sha256": man.sha256_file(out_dir / "benchmark_metrics.json"),
              "benchmark_quotes_sha256": man.sha256_file(out_dir / "benchmark_quotes.jsonl"),
              "closing_lines_sha256": man.sha256_file(out_dir / "closing_lines.json")}
    v1_problems = man.verify(parent)
    v1_man = json.loads(man.manifest_path(parent).read_text(encoding="utf-8"))
    ctx = {"protocol": protocol, "parent": parent, "proto": proto, "cov": cov, "metrics": metrics, "status": status,
           "bench": bench, "closing": closing, "ids": ids, "hashes": hashes, "excl": excl, "comps": comps,
           "cmap": {c.key: c for c in comps}, "names": names, "v1_problems": v1_problems, "v1_manifest": v1_man}
    (DOCS / names["results_md"]).write_text(write_results_md(ctx), encoding="utf-8")
    (DOCS / names["comparison"]).write_text(write_comparison_md(ctx), encoding="utf-8")
    (out_dir / "report_hashes.json").write_text(json.dumps(hashes, indent=1, sort_keys=True), encoding="utf-8")
    log(f"report written; parent verify {'OK' if not v1_problems else v1_problems[:3]}; hashes {json.dumps(hashes)}")


# --------------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------------

def _headline_table(h) -> str:
    rows = [[fam, v["groups"], v["sufficient"],
             f"{v['cw']['better']} / {v['cw']['unclear']} / {v['cw']['worse']}",
             f"{v['ee']['better']} / {v['ee']['unclear']} / {v['ee']['worse']}"] for fam, v in h.items()]
    return _table(["Family", "Competition groups", "Meeting min. clusters",
                   "Contract-weighted better / unclear / worse", "Event-equal better / unclear / worse"], rows) \
        if rows else "_none_"


def write_results_md(ctx) -> str:
    protocol, parent, metrics, bench, cov = ctx["protocol"], ctx["parent"], ctx["metrics"], ctx["bench"], ctx["cov"]
    status, closing, cmap, names = ctx["status"], ctx["closing"], ctx["cmap"], ctx["names"]
    L = [f"# Sports Phase 1 — historical baselines, retrospective correction ({protocol})\n",
         f"RESEARCH_ONLY / NO_BET. Generated by `python -m research.sports.run report --protocol {protocol}`. "
         f"{TEST_PERIOD_NOTE} Parameters and forecasts are those of `{parent}` (prediction journals verified "
         "identical apart from the protocol field); only the evaluation rules changed. No profitability or "
         "executable-edge claims are made.\n",
         f"**Interpretation.** {INTERPRETATION}\n"]
    # ---- 0 headline vs naive
    L.append("## 0. Headline: primary model vs naive forecasts (exploratory, unadjusted)\n")
    L.append("Counts of competition × family groups whose 95% cluster-bootstrap interval of ΔBrier (model − naive) "
             "lies below zero (better), above zero (worse) or straddles it (unclear). Only groups meeting the minimum "
             "cluster count are classified. Contract-weighted gives every listed contract equal weight; event-equal "
             "averages paired losses within each event first, so games with many listed strikes do not dominate.\n")
    for co in COHORT_ORDER:
        L.append(f"### {COHORT_LABEL[co]}\n")
        L.append(_headline_table(naive_headline(metrics, co)))
        L.append("")
    L.append("The strict cohort is the headline; exploratory and Kalshi-settlement-only results are reported "
             "separately and are not pooled with it.\n")
    # ---- 1 market
    mh = market_headline(bench)
    L.append("## 1. Headline: primary model vs Kalshi market reference (exploratory, unadjusted)\n")
    L.append(f"The benchmark sample is the preserved `{parent}` sample (candidates from the {parent} scored rows, "
             f"tickers from its quote file; re-derived and asserted identical; quotes recomputed from the cache and "
             f"asserted identical). A comparison is classified only if it has at least {MIN_MATCHED_CLUSTERS} unique "
             "matched event clusters after all quote filters, computed on strict-cohort clusters only "
             "(strict-only losses, CIs and counts). Kalshi-settlement-only comparisons are descriptive (frozen "
             "40-game cap). Δ = model − Kalshi: positive means Kalshi was better. One market per cluster, so "
             "contract-weighted and event-equal views coincide here.\n")
    L.append(f"* Competitions benchmarked: {mh['benchmarked']} ({mh['independent']} independent-source, "
             f"{mh['kalshi_only']} Kalshi-settlement-only); {mh['skipped']} below the 50-cluster eligibility frame.")
    L.append(f"* Strict-cohort comparisons with ≥ {MIN_MATCHED_CLUSTERS} matched clusters (headline): {mh['headline']}. "
             f"Model vs Kalshi: model better {mh['model_vs_kalshi']['better']}, unclear "
             f"{mh['model_vs_kalshi']['unclear']}, Kalshi better {mh['model_vs_kalshi']['worse']}. "
             f"Naive vs Kalshi: naive better {mh['naive_vs_kalshi']['better']}, unclear "
             f"{mh['naive_vs_kalshi']['unclear']}, Kalshi better {mh['naive_vs_kalshi']['worse']}.")
    L.append(f"* Independent-source competitions without a sufficient strict matched sample (INSUFFICIENT, "
             f"descriptive only): {len(mh['independent_insufficient_strict'])}"
             + (f" ({', '.join(mh['independent_insufficient_strict'])})" if mh['independent_insufficient_strict'] else "") + ".")
    tt = next((b for b in bench if b["competition"] == "k_kxttmatch"), None)
    if tt:
        L.append(f"* `k_kxttmatch` (table tennis) had {tt['parent_matched']} matched events in {parent} "
                 f"(sampled {tt['sampled']}); it is {tt['views']['all_matched']['sufficiency']} and was not a valid "
                 "comparison.")
    L.append("* Hourly candles are sparse; fees, spread costs and slippage are not modelled. This is a calibration "
             "reference, not evidence of executable edge.\n")
    brow = []
    for b in bench:
        if b.get("skipped"):
            continue
        a, s = b["views"]["all_matched"], b["views"]["strict_matched"]
        brow.append([b["competition"], "Kalshi-only" if b["source"] == "kalshi_only" else "independent",
                     b["eligible_clusters"], b["sampled"], b["parent_matched"], b["matched_all"], b["matched_strict"],
                     a["sufficiency"].split(" ")[0], _ci(a.get("model_minus_kalshi"), "d_brier"),
                     s["sufficiency"].split(" ")[0] if b["source"] != "kalshi_only" else "n/a",
                     _f(s["kalshi"].get("brier")), _f(s["model"].get("brier")),
                     _ci(s.get("model_minus_kalshi"), "d_brier"), _ci(s.get("naive_minus_kalshi"), "d_brier"),
                     _f(b["median_quote_age_s"] / 60 if b["median_quote_age_s"] is not None else None, 0)])
    L.append(_table(["Competition", "Source", "Eligible clusters", "Sampled", f"Matched ({parent})", "Matched (all)",
                     "Matched (strict)", "All-matched", "All-matched Δ model−Kalshi", "Strict", "Brier Kalshi (strict)",
                     "Brier model (strict)", "Strict Δ model−Kalshi [CI]", "Strict Δ naive−Kalshi [CI]",
                     "Median quote age (min)"], brow))
    L.append("")
    L.append("### Closing-line comparison (NFL, post-cutoff information, reported separately)\n")
    for view in ("strict", "all"):
        v = (closing or {}).get("views", {}).get(view, {})
        if v.get("closing_line", {}).get("n"):
            L.append(f"* {view}: matched {v['n_matched']} test contracts; closing-line Brier "
                     f"{_f(v['closing_line']['brier'])}; " + "; ".join(
                         f"{m} Brier {_f(v[m]['brier'])} (Δ vs closing {_ci(v.get(m + '_minus_closing'), 'd_brier')})"
                         for m in ("elo", "score", "naive") if m in v))
        else:
            L.append(f"* {view}: no matched NFL test games")
    L.append("")
    # ---- 2 cohorts
    L.extend(cohort_sections(ctx))
    # ---- 3 weighting
    L.append("## 4. Weighting sensitivity (every competition × family × cohort)\n")
    L.append("ΔBrier vs naive under contract weighting (v1-comparable) and event-equal weighting, with the "
             "exploratory class of each (blank when below the minimum cluster count). `flip` marks a class change.\n")
    wrows, flips = [], 0
    for m in metrics:
        if m["level"] != "competition":
            continue
        sk = m.get("skill_vs_naive") or {}
        prim = m["models"][m["primary"]]
        cw = _cls(sk, "d_brier") if m["sufficient"] else ""
        ee = _cls(sk, "d_brier_event") if m["sufficient"] else ""
        flip = bool(cw and ee and cw != ee)
        flips += flip
        wrows.append([m["key"], m["cohort"], m["family"], m["primary"], prim["n"], prim["clusters"],
                      _f(prim["n"] / prim["clusters"], 2), _ci(sk, "d_brier"), cw, _ci(sk, "d_brier_event"), ee,
                      "flip" if flip else ""])
    L.append(f"Class flips between weightings among classified groups: {flips}.\n")
    L.append(_table(["Competition", "Cohort", "Family", "Model", "Contracts", "Clusters", "Contracts/cluster",
                     "ΔBrier contract-weighted [CI]", "Class", "ΔBrier event-equal [CI]", "Class", "Flip"], wrows))
    L.append("")
    # ---- 5 coverage
    L.extend(coverage_sections(ctx))
    # ---- 6 exclusions
    L.append("## 6. Exclusions and blockers\n")
    blocked = [(k, v.get("reason", "")) for k, v in sorted(status.items()) if v.get("status") == "blocked"]
    L.append(f"Blocked competitions: {len(blocked)}.\n")
    if blocked:
        L.append(_table(["Competition", "Reason"], blocked))
        L.append("")
    agg = Counter()
    for i in ctx["ids"]:
        for k, v in (i.get("excluded") or {}).items():
            agg[k] += v
    L.append("Contract-level exclusions during build (all competitions):\n")
    L.append(_table(["Reason", "Contracts"], agg.most_common()))
    L.append("")
    ev_ex = Counter()
    for v in status.values():
        for k, n in (v.get("excluded") or {}).items():
            ev_ex[k] += n
    L.append("Excluded at evaluation (predictions; each contract has one row per model):\n")
    L.append(_table(["Reason", "Predictions"], ev_ex.most_common()) if ev_ex else "_none_")
    L.append("")
    sx = Counter(e["reason"] for v in ctx["excl"].values() for e in v)
    L.append("Series excluded from competition adapters (no payoff parser / segment), top 25 reasons:\n")
    L.append(_table(["Reason", "Series"], sx.most_common(25)))
    L.append("")
    L.append("Known blockers: UEFA cup qualifiers are absent from ESPN's feeds (unresolved identity); no reachable "
             "independent tennis results source (tennis is Kalshi-settlement-only); segment/score props without a "
             "payoff parser are excluded; table-tennis series had no candle ended by the cutoff; player props are "
             "not modelled (no pregame-deployable participation data).\n")
    # ---- 7 hashes & reproduction
    L.append("## 7. Hashes, preservation and reproduction\n")
    L.append(_table(["Item", "Value"], [[k, v] for k, v in ctx["hashes"].items()]))
    L.append("")
    vm = ctx["v1_manifest"]
    L.append(f"`{parent}` preservation: verified against `research/sports/protocols/{parent}.artifacts.json` "
             f"({len(vm['files'])} files): **{'OK' if not ctx['v1_problems'] else 'FAILED: ' + '; '.join(ctx['v1_problems'][:5])}**. "
             f"That manifest was assembled retrospectively ({vm['assembled_at']}) and its run-level hashes were "
             f"checked against the original run's hashes ({(vm.get('checked_against') or {}).get('result', 'not checked')}).\n")
    L.append("```powershell\n"
             f".venv\\Scripts\\python.exe -m research.sports.run verify --protocol {parent}\n"
             f".venv\\Scripts\\python.exe -m research.sports.run all --cache-only --protocol {protocol}\n"
             f".venv\\Scripts\\python.exe -m research.sports.run verify --protocol {protocol}\n"
             f".venv\\Scripts\\python.exe -m research.sports.run evaluate --sport Soccer --family total --protocol {protocol}\n"
             "```\n")
    L.append(f"`{parent}` itself is reproduced at commit `{PRESERVED[parent]}` (this code refuses to rewrite it). "
             "Raw responses: `data/cache/sports/raw/` (byte-exact, gitignored); normalized data and journals: "
             f"`data/sports/` (gitignored); metrics: `data/results/sports_phase1/{protocol}/` (gitignored).\n")
    return "\n".join(L) + "\n"


def cohort_sections(ctx) -> list[str]:
    proto, cov, status, metrics, parent = ctx["proto"], ctx["cov"], ctx["status"], ctx["metrics"], ctx["parent"]
    L = ["## 2. Reconciliation cohorts\n"]
    L.append(f"* strict: {COHORT_RULES['strict']}.\n* exploratory: {COHORT_RULES['exploratory']}.\n"
             f"* failed: {COHORT_RULES['failed']}.\n* {COHORT_RULES['contract_level']}.\n"
             f"* {parent} admitted series with fewer than {GATE_MIN_N} pre-test contracts using all settled contracts "
             "(including the test period); v2 does not, so those series are exploratory.\n")
    sport_c = defaultdict(Counter)
    comp_rows = []
    for key, e in sorted(proto["competitions"].items()):
        g = e.get("gate")
        if not g:
            continue
        cnt = Counter(v["cohort"] for v in g.values())
        sport_c[e["sport"]].update(cnt)
        st = status.get(key, {})
        rows_by = st.get("scored_rows_by_cohort") or {}
        dis = sum((st.get("test_disagreements_by_series") or {}).values())
        comp_rows.append([key, e["sport"], cnt["strict"], cnt["exploratory"], cnt["failed"],
                          rows_by.get("strict", 0), rows_by.get("exploratory", 0), dis])
    L.append("Independent-source series by sport and cohort:\n")
    L.append(_table(["Sport", "Strict series", "Exploratory series", "Failed series"],
                    [[s, c["strict"], c["exploratory"], c["failed"]] for s, c in sorted(sport_c.items())]))
    L.append("")
    L.append("By competition (scored rows count every model's prediction; disagreements are unique test contracts):\n")
    L.append(_table(["Competition", "Sport", "Strict series", "Exploratory series", "Failed series",
                     "Strict scored rows", "Exploratory scored rows", "Test disagreements excluded"], comp_rows))
    L.append("")
    fam_c = defaultdict(Counter)
    for m in metrics:
        if m["level"] == "competition":
            fam_c[m["family"]][m["cohort"]] += m["models"][m["primary"]]["n"]
    L.append("Scored test contracts (primary model) by family and cohort:\n")
    L.append(_table(["Family"] + COHORT_ORDER, [[f] + [c.get(x, 0) for x in COHORT_ORDER] for f, c in sorted(fam_c.items())]))
    L.append("")
    moves = Counter((r["v1_status"], r["coverage_status"]) for r in cov if r["phase1_path"] != "not_attempted")
    L.append(f"Membership changes ({parent} status → v2 status, attempted series):\n")
    L.append(_table([f"{parent} status", "v2 status", "Series"], [[a, b, n] for (a, b), n in sorted(moves.items())]))
    L.append("")
    L.append("## 3. Settlement disagreements\n")
    L.append("Series with any disagreement between the independent payoff and Kalshi settlement. Pre-test counts "
             "determine the cohort; test-period disagreements are excluded contract by contract and never change "
             "a cohort.\n")
    drows = []
    for key, e in sorted(proto["competitions"].items()):
        dis = (status.get(key, {}).get("test_disagreements_by_series") or {})
        for s, g in sorted((e.get("gate") or {}).items()):
            if g["all_agree"] < g["all_n"] or dis.get(s):
                drows.append([key, s, g["cohort"], f"{g['pre_agree']}/{g['pre_n']}", g["pre_n"] - g["pre_agree"],
                              f"{g['all_agree']}/{g['all_n']}", dis.get(s, 0)])
    L.append(_table(["Competition", "Series", "Cohort", "Pre-test agreement", "Pre-test disagreements",
                     "All-settled agreement", "Test contracts excluded"], drows) if drows else "_none_")
    L.append("")
    return L


def coverage_sections(ctx) -> list[str]:
    cov, comps, names = ctx["cov"], ctx["comps"], ctx["names"]
    L = ["## 5. Coverage of all inventory series\n"]
    st = Counter(r["coverage_status"] for r in cov)
    attempted = sorted({c.key for c in comps})
    sports = sorted({c.sport for c in comps})
    L.append(f"Every one of the {len(cov):,} inventory series has an explicit status in `{names['coverage']}`. "
             f"Attempted competitions: {len(attempted)} across {len(sports)} supported sports "
             f"({', '.join(sports)}). A supported sport is one with at least one attempted competition; series in "
             "supported sports may still be unattempted (other families or competitions).\n")
    L.append(_table(["Coverage status", "Series"], sorted(st.items())))
    L.append("")
    by = defaultdict(Counter)
    for r in cov:
        by[r["sport"]][r["coverage_status"]] += 1
    cols = sorted(st)
    L.append("Series by sport and coverage status:\n")
    L.append(_table(["Sport"] + cols, [[s] + [c.get(x, 0) for x in cols] for s, c in
                                       sorted(by.items(), key=lambda kv: -sum(kv[1].values()))]))
    L.append("")
    return L


def write_comparison_md(ctx) -> str:
    parent, protocol, metrics, bench, cov, cmap = ctx["parent"], ctx["protocol"], ctx["metrics"], ctx["bench"], \
        ctx["cov"], ctx["cmap"]
    _, v1_metrics, v1_status, v1_bench, _ = _load(parent)
    L = [f"# Sports Phase 1: {parent} → {protocol} comparison\n",
         f"RESEARCH_ONLY / NO_BET. {TEST_PERIOD_NOTE} Forecasts are unchanged (journals identical apart from "
         "the protocol field); differences come only from the evaluation corrections below. "
         f"{INTERPRETATION}\n",
         "## What changed\n",
         f"1. Reconciliation: {parent} admitted series with fewer than {GATE_MIN_N} pre-test contracts using all "
         "settled contracts, including the test period. v2 admits only pre-test-reconciled series to the strict "
         "cohort; the others are a separately labelled exploratory cohort.",
         f"2. Market benchmark: the {parent} sample is preserved exactly, but a comparison needs at least "
         f"{MIN_MATCHED_CLUSTERS} unique matched clusters after all quote filters, measured on strict-cohort "
         "clusters with strict-only losses and CIs. Kalshi-settlement-only comparisons are descriptive.",
         "3. Weighting: event-equal metrics are reported alongside contract-weighted ones.",
         "4. Interpretation: classes are labelled exploratory and unadjusted; naive and market comparisons are separate.\n"]
    ident = sum(1 for v in ctx["status"].values() if v.get("parent_journal_identical") is True)
    L.append(f"Prediction journals identical to {parent}: {ident} of "
             f"{sum(1 for v in ctx['status'].values() if v.get('status') == 'evaluated')} evaluated competitions.\n")
    L.append("## Headline vs naive (contract-weighted classes, competition × family)\n")
    v1h = v1_naive_headline(v1_metrics, cmap)
    v2s, v2e, v2k = naive_headline(metrics, "strict"), naive_headline(metrics, "exploratory"), \
        naive_headline(metrics, "kalshi_settlement_only")
    fams = sorted(set(v1h["independent"]) | set(v2s) | set(v2e))
    fmt = lambda c: f"{c['better']} / {c['unclear']} / {c['worse']}"
    L.append(_table(["Family", f"{parent} independent (b/u/w)", "v2 strict (b/u/w)", "v2 exploratory (b/u/w)"],
                    [[f, fmt(v1h["independent"].get(f, Counter())), fmt(v2s[f]["cw"]) if f in v2s else "0 / 0 / 0",
                      fmt(v2e[f]["cw"]) if f in v2e else "0 / 0 / 0"] for f in fams]))
    L.append("")
    kf = sorted(set(v1h["kalshi_only"]) | set(v2k))
    L.append(_table(["Family", f"{parent} Kalshi-only (b/u/w)", "v2 Kalshi-settlement-only (b/u/w)"],
                    [[f, fmt(v1h["kalshi_only"].get(f, Counter())), fmt(v2k[f]["cw"]) if f in v2k else "0 / 0 / 0"]
                     for f in kf]))
    L.append("")
    flips = sum(1 for r in results_rows(metrics) if r["level"] == "competition" and r["weighting_class_flip"])
    L.append(f"Event-equal weighting changes the class of {flips} classified competition × family × cohort groups "
             "(see the weighting table in the results document).\n")
    L.append("## Market benchmark\n")
    v1rows = [b for b in v1_bench if not b.get("skipped") and b.get("model_minus_kalshi", {}).get("n")]
    c1 = Counter(classify(b["model_minus_kalshi"]["d_brier_ci"]) for b in v1rows)
    mh = market_headline(bench)
    L.append(f"* {parent}: {len(v1rows)} competitions classified regardless of matched size, mixed cohorts: "
             f"model better {c1['better']}, unclear {c1['unclear']}, Kalshi better {c1['worse']}.")
    L.append(f"* {protocol}: {mh['headline']} strict comparisons with ≥ {MIN_MATCHED_CLUSTERS} matched clusters: model "
             f"better {mh['model_vs_kalshi']['better']}, unclear {mh['model_vs_kalshi']['unclear']}, Kalshi better "
             f"{mh['model_vs_kalshi']['worse']}. {mh['kalshi_only']} Kalshi-settlement-only comparisons are "
             f"descriptive (frozen 40-game cap); {len(mh['independent_insufficient_strict'])} independent-source "
             "comparisons are INSUFFICIENT on strict clusters.")
    small = sorted((b["parent_matched"], b["competition"]) for b in bench if not b.get("skipped")
                   and b["parent_matched"] < MIN_MATCHED_CLUSTERS)
    L.append(f"* {len(small)} {parent} comparisons had fewer than {MIN_MATCHED_CLUSTERS} matched events; "
             f"`k_kxttmatch` had {next((n for n, k in small if k == 'k_kxttmatch'), 'n/a')}.\n")
    L.append("## Coverage status changes (all 3,871 series)\n")
    moves = Counter((r["v1_status"], r["coverage_status"]) for r in cov)
    L.append(_table([f"{parent} status", f"{protocol} status", "Series"], [[a, b, n] for (a, b), n in sorted(moves.items())]))
    L.append("")
    return "\n".join(L) + "\n"

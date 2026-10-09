"""Consolidated Phase 1 reporting: coverage of the whole inventory, results, exclusions, hashes.

Writes tracked summaries under docs/research/ (small, deterministic given the cached inputs):
  sports_phase1_results_v1.md, sports_phase1_coverage_v1.csv, sports_phase1_results_v1.csv,
  sports_audit_supplement_v1_1.md
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .competitions import all_competitions, load_inventory
from .core import NORMALIZED, PREDICTIONS, PROTOCOLS, RAW_ROOT, REPO_ROOT, RESULTS, Fetcher, CacheMiss, read_jsonl, \
    sha256_file, sha256_json
from .evaluate import code_hash, protocol_path

DOCS = REPO_ROOT / "docs" / "research"
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
    lo, hi = d[f"{key}_ci"]
    return f"{d[key]:+.4f} [{lo:+.4f}, {hi:+.4f}]"


def _load(protocol):
    out = RESULTS / protocol
    j = lambda p: json.loads((out / p).read_text(encoding="utf-8")) if (out / p).exists() else None
    return out, j("metrics.json") or [], j("evaluation_status.json") or {}, j("benchmark_metrics.json") or [], \
        j("closing_lines.json")


def build_coverage(protocol: str):
    inv = load_inventory()
    comps, specs, excl = all_competitions(inv)
    cls = v1_classes()
    proto = json.loads(protocol_path(protocol).read_text(encoding="utf-8"))
    _, metrics, status, _, _ = _load(protocol)
    series_role = {}
    for c in comps:
        for s, kind, seg in specs[c.key]:
            series_role[s] = (c, "kalshi_settlement_only" if c.source == "kalshi_only" else "independent_outcomes", None)
        for e in excl[c.key]:
            series_role[e["series"]] = (c, "excluded_spec", e["reason"])
    per_series = defaultdict(Counter)
    test_scored = defaultdict(set)
    for c in comps:
        p = NORMALIZED / c.key / "contracts.jsonl"
        if not p.exists():
            continue
        for r in read_jsonl(p):
            per_series[r["series"]]["contracts"] += 1
            per_series[r["series"]]["ok"] += int(r["status"] == "ok")
    for m in metrics:
        if m["level"] == "series":
            prim = m["models"].get(m["primary"], {})
            test_scored[m["group"]] = (prim.get("n", 0), prim.get("clusters", 0))
    rows = []
    for r in inv:
        s = r["series_ticker"]
        v1 = cls.get((r["sport"], r["contract_family"]), "")
        role = series_role.get(s)
        out = {"series_ticker": s, "series_title": r["series_title"], "sport": r["sport"],
               "competition_top": r["competition_top"], "contract_family": r["contract_family"],
               "v1_cell_class": v1, "traded_markets": r["traded_markets"], "volume_fp_contracts": r["volume_fp_total"]}
        if role:
            comp, kind, why = role
            entry = proto["competitions"].get(comp.key, {})
            gate = (entry.get("gate") or {}).get(s)
            n, k = test_scored.get(s, (0, 0))
            st = status.get(comp.key, {})
            if kind == "excluded_spec":
                phase = "excluded"
                reason = why
            elif entry.get("blocked") or (entry.get("params") or {}).get("blocked"):
                phase, reason = "blocked", entry.get("blocked") or entry["params"]["blocked"]
            elif kind == "independent_outcomes" and gate is not None and not gate["verified"]:
                phase, reason = "excluded", f"reconciliation gate failed ({gate['agree']}/{gate['n']})"
            elif kind == "independent_outcomes" and gate is None:
                phase, reason = "excluded", "no reconciled settled contracts (identity/outcome exclusions)"
            elif n > 0:
                phase, reason = "evaluated", ""
            else:
                phase, reason = "no test-period contracts", "no scored contracts in the test window"
            out.update(phase1_competition=comp.key, phase1_path=kind, phase1_status=phase, reason=reason,
                       contracts_built=per_series[s]["contracts"], contracts_ok=per_series[s]["ok"],
                       gate_agreement=f"{gate['agree']}/{gate['n']} ({gate['basis']})" if gate else "",
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
            out.update(phase1_competition="", phase1_path="not_attempted", phase1_status="not attempted", reason=why,
                       contracts_built="", contracts_ok="", gate_agreement="", test_contracts="", test_clusters="")
        rows.append(out)
    return rows


def _csv(path: Path, rows: list[dict]):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def results_rows(metrics) -> list[dict]:
    out = []
    for m in metrics:
        if m["level"] == "series":
            continue
        prim = m["models"].get(m["primary"], {})
        nv = m["models"].get("naive", {})
        sk = m.get("skill_vs_naive") or {}
        out.append({"level": m["level"], "key": m["key"], "family": m["family"], "primary_model": m["primary"],
                    "test_contracts": prim.get("n"), "clusters": prim.get("clusters"), "sufficient": m["sufficient"],
                    "brier_model": _f(prim.get("brier")), "brier_naive": _f(nv.get("brier")),
                    "logloss_model": _f(prim.get("log_loss")), "logloss_naive": _f(nv.get("log_loss")),
                    "ece_model": _f(prim.get("ece")), "base_rate": _f(prim.get("base_rate")),
                    "d_brier_vs_naive": _f(sk.get("d_brier")),
                    "d_brier_ci_lo": _f((sk.get("d_brier_ci") or [None, None])[0]),
                    "d_brier_ci_hi": _f((sk.get("d_brier_ci") or [None, None])[1]),
                    "d_logloss_vs_naive": _f(sk.get("d_log_loss")),
                    "d_logloss_ci_lo": _f((sk.get("d_log_loss_ci") or [None, None])[0]),
                    "d_logloss_ci_hi": _f((sk.get("d_log_loss_ci") or [None, None])[1])})
    return out


def volume_units_check(fetcher: Fetcher) -> dict:
    """Sum of trade counts equals volume_fp for a sample market (cached request)."""
    p = NORMALIZED / "ufc" / "contracts.jsonl"
    if not p.exists():
        return {"status": "ufc not built"}
    rows = [r for r in read_jsonl(p) if r["ticker"] == "KXUFCDISTANCE-26OCT10FRARIB-DIST"]
    if not rows:
        return {"status": "sample market not present"}
    tot, n, cur = 0.0, 0, None
    try:
        while True:
            prm = {"ticker": rows[0]["ticker"], "limit": 1000}
            if cur:
                prm["cursor"] = cur
            x = fetcher.get("kalshi", "https://external-api.kalshi.com/trade-api/v2/markets/trades", prm).json()
            for t in x.get("trades", []):
                tot += float(t.get("count_fp") or t.get("count") or 0)
                n += 1
            cur = x.get("cursor")
            if not cur:
                break
    except CacheMiss:
        return {"status": "not cached"}
    return {"ticker": rows[0]["ticker"], "volume_fp": rows[0]["volume_fp"], "trades": n, "sum_trade_count_fp": round(tot, 2)}


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
        ev, ms, src = set(), set(), set()
        both = sum(v.get("tickers_in_both_tiers", 0) for v in summ["dedupe"].values())
        raw = sum(v.get("raw_rows", 0) for v in summ["dedupe"].values())
        for r in read_jsonl(NORMALIZED / c.key / "contracts.jsonl"):
            if r["status"] != "ok":
                continue
            ev.add(r["event_ticker"])
            if r.get("milestone_id"):
                ms.add(r["milestone_id"])
            src.add(r["cluster"])
        out.append({"competition": c.key, "sport": c.sport, "source": c.source, "raw_market_rows": raw,
                    "unique_tickers": summ["markets"], "tickers_in_both_tiers": both, "ok_contracts": summ["contracts_ok"],
                    "kalshi_events": len(ev), "kalshi_milestones": len(ms), "unique_outcome_units": len(src),
                    "excluded": dict(summ["contracts_excluded"])})
    return out


def stage_report(args, log):
    protocol = args.protocol
    out_dir, metrics, status, bench, closing = _load(protocol)
    comps, specs, excl = all_competitions()
    cov = build_coverage(protocol)
    _csv(DOCS / "sports_phase1_coverage_v1.csv", cov)
    res = results_rows(metrics)
    if res:
        _csv(DOCS / "sports_phase1_results_v1.csv", res)
    ids = identity_stats(comps)
    fetcher = Fetcher(cache_only=args.cache_only, min_interval=args.min_interval)
    vol = volume_units_check(fetcher)
    proto_p = protocol_path(protocol)
    journals = sorted((PREDICTIONS / protocol).glob("*.jsonl")) if (PREDICTIONS / protocol).exists() else []
    hashes = {
        "protocol_file_sha256": sha256_file(proto_p),
        "code_hash": code_hash(),
        "normalized_data_manifest_sha256": sha256_json({k: v.get("data") for k, v in
                                                       json.loads(proto_p.read_text(encoding="utf-8"))["competitions"].items()}),
        "prediction_journals": len(journals),
        "prediction_journals_manifest_sha256": sha256_json({p.name: sha256_file(p) for p in journals}),
        "scored_rows_sha256": sha256_file(out_dir / "scored.jsonl") if (out_dir / "scored.jsonl").exists() else None,
        "metrics_sha256": sha256_file(out_dir / "metrics.json") if (out_dir / "metrics.json").exists() else None,
    }
    write_results_md(protocol, cov, metrics, status, bench, closing, ids, hashes, excl, comps)
    write_supplement(ids, vol, cov, comps)
    (out_dir / "report_hashes.json").write_text(json.dumps(hashes, indent=1, sort_keys=True), encoding="utf-8")
    log(f"report written; hashes {json.dumps(hashes)}")


# --------------------------------------------------------------------------------------------
# Markdown writers
# --------------------------------------------------------------------------------------------

def _table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def _sign(ci):
    lo, hi = ci
    return "better" if hi < 0 else "worse" if lo > 0 else "unclear"


def _key_findings(metrics, bench, closing, cmap) -> list[str]:
    out = []
    for label, pick in (("independent-outcome", lambda c: c and c.source != "kalshi_only"),
                        ("Kalshi-settlement-only", lambda c: c and c.source == "kalshi_only")):
        groups = [m for m in metrics if m["level"] == "competition" and pick(cmap.get(m["key"]))]
        suff = [m for m in groups if m["sufficient"] and m.get("skill_vs_naive", {}).get("n")]
        by_fam = defaultdict(Counter)
        for m in suff:
            by_fam[m["family"]][_sign(m["skill_vs_naive"]["d_brier_ci"])] += 1
        out.append(f"* {label.capitalize()} competition×family groups: {len(groups)} evaluated, {len(suff)} meet the "
                   "minimum cluster count. Primary model vs naive benchmark (95% CI of ΔBrier):")
        for fam, c in sorted(by_fam.items()):
            out.append(f"  * {fam}: better {c['better']}, unclear {c['unclear']}, worse {c['worse']}")
    rows = [b for b in bench if not b.get("skipped") and b.get("model_minus_kalshi", {}).get("n")]
    c = Counter(_sign(b["model_minus_kalshi"]["d_brier_ci"]) for b in rows)
    out.append(f"* Kalshi pre-cutoff mid vs primary model (game winners, {len(rows)} competitions with quotes): "
               f"Kalshi significantly better in {c['worse']}, model significantly better in {c['better']}, "
               f"unclear in {c['unclear']}. Baselines are calibrated references, not a source of edge.")
    if closing and closing.get("closing_line", {}).get("n"):
        out.append(f"* NFL closing line (post-cutoff information) Brier {_f(closing['closing_line']['brier'])} on "
                   f"{closing['n_matched']} test contracts; Elo Δ vs closing "
                   f"{_ci(closing.get('elo_minus_closing'), 'd_brier')}.")
    out.append("* No profitability or executable-edge conclusions: hourly candles are sparse, fees/slippage are not "
               "modelled, and the benchmark samples at most 150 (independent) or 40 (Kalshi-only) games per competition.\n")
    return out


def write_results_md(protocol, cov, metrics, status, bench, closing, ids, hashes, excl, comps):
    cmap = {c.key: c for c in comps}
    L = []
    L.append("# Sports Phase 1 — historical baselines (v1)\n")
    L.append("RESEARCH_ONLY / NO_BET. Generated by `python -m research.sports.run report`; numbers come from "
             f"protocol `{protocol}` (frozen before test scoring). No profitability or executable-edge claims are made; "
             "Kalshi comparisons use sparse hourly quotes at the forecast cutoff.\n")
    L.append("## 0. Key findings (computed)\n")
    L.extend(_key_findings(metrics, bench, closing, cmap))
    # coverage summary
    by_status = Counter(r["phase1_status"] for r in cov)
    by_path = Counter(r["phase1_path"] for r in cov)
    L.append("## 1. Coverage across the entire inventory\n")
    L.append(f"All {len(cov):,} inventory series are listed in `sports_phase1_coverage_v1.csv` with their Phase 1 "
             "path, status and reason.\n")
    L.append(_table(["Phase 1 path", "Series"], sorted(by_path.items())))
    L.append("")
    L.append(_table(["Phase 1 status", "Series"], sorted(by_status.items())))
    L.append("")
    sport_rows = defaultdict(Counter)
    for r in cov:
        sport_rows[r["sport"]][r["phase1_status"]] += 1
    statuses = sorted(by_status)
    L.append("Series by sport and status:\n")
    L.append(_table(["Sport"] + statuses, [[s] + [c.get(x, 0) for x in statuses] for s, c in
                                           sorted(sport_rows.items(), key=lambda kv: -sum(kv[1].values()))]))
    L.append("")
    # evaluated combos
    L.append("## 2. Implemented and evaluated combinations (test period)\n")
    from .models import SPLITS, HORIZON_MIN
    L.append(f"Test window: games starting {SPLITS['test_start'][:10]} to {SPLITS['test_end'][:10]} (exclusive); "
             f"parameters selected on {SPLITS['train_start'][:10]}–{SPLITS['test_start'][:10]} only; ratings burn-in "
             f"from {SPLITS['history_start'][:10]}. Forecast cutoff = scheduled start − {HORIZON_MIN} min. "
             "Δ = primary model − naive benchmark (negative is better), 95% cluster-bootstrap CI. "
             "`ok` = minimum independent clusters met.\n")
    comp_rows, ko_rows = [], []
    for m in metrics:
        if m["level"] != "competition":
            continue
        prim = m["models"][m["primary"]]
        nv = m["models"].get("naive", {})
        sk = m.get("skill_vs_naive")
        c = cmap.get(m["key"])
        row = [m["key"], c.sport if c else "", m["family"], m["primary"], prim["n"], prim["clusters"],
               "ok" if m["sufficient"] else "insufficient", _f(prim["brier"]), _f(nv.get("brier")),
               _ci(sk, "d_brier"), _ci(sk, "d_log_loss"), _f(prim["ece"], 3)]
        (ko_rows if c and c.source == "kalshi_only" else comp_rows).append(row)
    hdr = ["Competition", "Sport", "Family", "Model", "Contracts", "Clusters", "Sample", "Brier", "Brier naive",
           "ΔBrier [CI]", "ΔLogLoss [CI]", "ECE"]
    L.append("### 2a. Independent-outcome competitions\n")
    L.append(_table(hdr, comp_rows) if comp_rows else "_none evaluated_")
    L.append("")
    L.append("### 2b. Pooled by sport (independent outcomes and Kalshi-settlement-only pools)\n")
    pool_rows = []
    for m in metrics:
        if m["level"] != "sport_pool":
            continue
        prim = m["models"][m["primary"]]
        nv = m["models"].get("naive", {})
        sk = m.get("skill_vs_naive")
        pool_rows.append([m["key"], m["family"], m["primary"], prim["n"], prim["clusters"],
                          "ok" if m["sufficient"] else "insufficient", _f(prim["brier"]), _f(nv.get("brier")),
                          _ci(sk, "d_brier"), _ci(sk, "d_log_loss"), _f(prim["ece"], 3)])
    L.append(_table(["Sport | pool", "Family", "Model", "Contracts", "Clusters", "Sample", "Brier", "Brier naive",
                     "ΔBrier [CI]", "ΔLogLoss [CI]", "ECE"], pool_rows) if pool_rows else "_none_")
    L.append("")
    L.append("### 2c. Kalshi-settlement-only competitions (class B: labels are Kalshi settlements, no independent check)\n")
    ko_suff = [r for r in ko_rows if r[6] == "ok"]
    L.append(f"{len(ko_rows)} Kalshi-only competitions produced test forecasts; {len(ko_suff)} meet the minimum "
             "cluster count and are listed; all are in `sports_phase1_results_v1.csv`.\n")
    L.append(_table(hdr, ko_suff) if ko_suff else "_none with sufficient sample_")
    L.append("")
    # benchmark
    L.append("## 3. Kalshi price benchmark at the forecast cutoff (game winners)\n")
    L.append("Mid of the closing yes bid/ask of the latest hourly candle that *ended* at or before the cutoff; "
             "quotes older than 3 h or one-sided books are not used and are counted. Sampled clusters ordered by hash "
             "(labels unused). Δ = model − Kalshi (positive means Kalshi was better).\n")
    brow = []
    skipped = [b for b in bench if b.get("skipped")]
    if skipped:
        L.append(f"{len(skipped)} competitions with game-winner test forecasts were below the minimum cluster count "
                 "and were not benchmarked.\n")
    for b in bench:
        if b.get("skipped"):
            continue
        k = b["kalshi"]
        brow.append([b["competition"], b["test_clusters"], b["sampled"], b["quote_status"].get("ok", 0),
                     ", ".join(f"{s}:{n}" for s, n in sorted(b["quote_status"].items()) if s != "ok"),
                     _f(b["median_quote_age_s"] / 60 if b["median_quote_age_s"] is not None else None, 0),
                     _f(k.get("brier")), _f(b["model"].get("brier")), _f(b["naive"].get("brier")),
                     _ci(b.get("model_minus_kalshi"), "d_brier"), _ci(b.get("model_minus_kalshi"), "d_log_loss")])
    L.append(_table(["Competition", "Test clusters", "Sampled", "Fresh quotes", "Missing/stale", "Median age (min)",
                     "Brier Kalshi", "Brier model", "Brier naive", "Δ Brier model−Kalshi [CI]", "Δ LogLoss [CI]"], brow)
             if brow else "_benchmark not run_")
    L.append("")
    L.append("### Closing-line comparison (NFL, reported separately)\n")
    if closing and closing.get("closing_line", {}).get("n"):
        cl = closing["closing_line"]
        L.append(f"{closing['timing']}. Matched {closing['n_matched']} test contracts. Closing-line Brier "
                 f"{_f(cl['brier'])}; " + "; ".join(
                     f"{m} Brier {_f(closing[m]['brier'])} (Δ vs closing {_ci(closing.get(m + '_minus_closing'), 'd_brier')})"
                     for m in ("elo", "score", "naive") if m in closing) + "\n")
    else:
        L.append("_no matched NFL test games_\n")
    # exclusions
    L.append("## 4. Exclusions and blockers\n")
    blocked = [(k, v.get("reason", "")) for k, v in sorted(status.items()) if v.get("status") == "blocked"]
    L.append(f"Blocked competitions ({len(blocked)}):\n")
    L.append(_table(["Competition", "Reason"], blocked) if blocked else "_none_")
    L.append("")
    agg = Counter()
    for i in ids:
        for k, v in (i.get("excluded") or {}).items():
            agg[k] += v
    L.append("Contract-level exclusions during build (all competitions):\n")
    L.append(_table(["Reason", "Contracts"], agg.most_common()))
    L.append("")
    ev_ex = Counter()
    for v in status.values():
        for k, n in (v.get("excluded") or {}).items():
            ev_ex[k] += n
    L.append("Excluded at evaluation (gate / mismatch / unfitted scope):\n")
    L.append(_table(["Reason", "Predictions"], ev_ex.most_common()) if ev_ex else "_none_")
    L.append("")
    sx = Counter(e["reason"] for v in excl.values() for e in v)
    L.append("Series excluded from competition adapters (no payoff parser / segment):\n")
    L.append(_table(["Reason", "Series"], sx.most_common(25)))
    L.append("")
    # hashes
    L.append("## 5. Data, protocol and code hashes\n")
    L.append(_table(["Item", "SHA-256"], [[k, v] for k, v in hashes.items()]))
    L.append("")
    L.append("## 6. Reproduction\n")
    L.append("```powershell\n"
             "# full run (network, rate-limited single-flight; ~0.5 s between requests per host)\n"
             f".venv\\Scripts\\python.exe -m research.sports.run all --protocol {protocol}\n"
             "# cache-only rerun (no network); must reproduce journals, metrics and hashes above\n"
             f".venv\\Scripts\\python.exe -m research.sports.run all --cache-only --protocol {protocol}\n"
             "# filters\n"
             f".venv\\Scripts\\python.exe -m research.sports.run evaluate --sport Soccer --family total --protocol {protocol}\n"
             f".venv\\Scripts\\python.exe -m research.sports.run build --competition nba,nhl --cache-only\n"
             "```\n")
    L.append("Raw responses: `data/cache/sports/raw/<source>/` (byte-exact body + metadata with sha256, gitignored). "
             "Normalized data, crosswalks and write-once prediction journals: `data/sports/` (gitignored). "
             "Metrics: `data/results/sports_phase1/<protocol>/` (gitignored). Protocol: "
             f"`research/sports/protocols/{protocol}.json` (tracked).\n")
    # prospective
    L.append("## 7. Categories needing prospective collection\n")
    pro = Counter()
    for r in cov:
        if r["v1_cell_class"] == "C" or r["contract_family"] == "player_prop":
            pro[(r["sport"], r["contract_family"])] += 1
    for m in metrics:
        if m["level"] == "competition" and not m["sufficient"]:
            pro[(f"{m['key']} (insufficient test sample)", m["family"])] += 0
    L.append(_table(["Sport / competition", "Family", "Series"], [[a, b, n] for (a, b), n in sorted(pro.items())]))
    L.append("")
    (DOCS / "sports_phase1_results_v1.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def write_supplement(ids, vol, cov, comps):
    L = ["# Sports audit supplement v1.1 (corrections to v1)\n",
         "Preserves `sports_feasibility_v1.md` / `sports_inventory_v1.csv` unchanged; this file records "
         "corrections and per-competition readiness measured during Phase 1. Generated by the report stage.\n",
         "## 1. Readiness is per competition and contract specification\n",
         "A successful sample request in v1 did not establish historical coverage. Phase 1 measures, per series: "
         "settled markets with a resolved identity, agreement between an independently computed payoff and Kalshi "
         "settlement (the reconciliation gate), and test-period sample size. See `sports_phase1_coverage_v1.csv` "
         "(columns `contracts_ok`, `gate_agreement`, `test_contracts`, `test_clusters`, `phase1_status`).\n",
         "## 2. `volume_fp` units\n",
         f"`volume_fp` is a fixed-point **contract count**, not dollars. Check: {json.dumps(vol)}. The v1 "
         "\"Volume\" columns are therefore contracts traded; notional dollars would require prices.\n",
         "## 3. Live/historical duplication and event groups vs unique outcomes\n",
         "Markets are deduplicated by ticker across the live and historical tiers (settled copy preferred). "
         "Kalshi event tickers are not unique games: several series (winner, spread, total, segments) each create "
         "their own event per game, and doubleheaders or duplicate milestones occur. Outcome units below are the "
         "independent source game / fight / tournament (or the Kalshi milestone for Kalshi-only data).\n"]
    rows = []
    for i in ids:
        if i.get("blocked"):
            rows.append([i["competition"], "", "", "", "", "", "", "blocked: " + i["blocked"][:80]])
            continue
        if i["source"] == "kalshi_only" and i["ok_contracts"] < 200:
            continue
        rows.append([i["competition"], i["raw_market_rows"], i["unique_tickers"], i["tickers_in_both_tiers"],
                     i["ok_contracts"], i["kalshi_events"], i["kalshi_milestones"], i["unique_outcome_units"]])
    L.append(_table(["Competition", "Raw market rows", "Unique tickers", "In both tiers", "OK contracts",
                     "Kalshi events", "Kalshi milestones", "Unique outcome units"], rows))
    L.append("\n(Kalshi-only competitions with fewer than 200 usable contracts omitted from this table; all are in the coverage CSV.)\n")
    L.append("## 4. Tennis source blocker re-checked (2026-10-08)\n")
    L.append("* Jeff Sackmann `tennis_atp` / `tennis_wta`: HTTP 404 on github.com, the GitHub API and "
             "raw.githubusercontent.com; the owner's public repository list no longer includes them. "
             "**Unavailable generally** (not a local network issue). Third-party mirrors exist but are unverified and "
             "were not used.\n"
             "* tennis-data.co.uk: HTTP 403 (Cloudflare \"Sorry, you have been blocked\") for every URL from this "
             "machine, while an independent fetch from a different network returned the page. **Available generally, "
             "blocked from this machine's IP.** Its files are Excel workbooks.\n"
             "* Consequence: tennis match winners are evaluated on the Kalshi-settlement-only path (class B); "
             "tennis spreads/totals/set markets stay excluded until an independent results source is reachable.\n")
    L.append("## 5. Other corrections\n")
    L.append("* Discovery-cache series files hold per-series summaries only, so Phase 1 re-fetched full market "
             "records for the selected series (not the whole 3,871-series crawl).\n"
             "* ESPN scoreboard date ranges (`YYYYMMDD-YYYYMMDD`) return HTTP 400; month (`YYYYMM`) or day queries are used.\n"
             "* Archived markets return no data from the batch candlesticks endpoint; per-market historical candles are used.\n"
             "* Soccer full-time markets settle on 90 minutes plus stoppage time; the adapter requires that wording "
             "and scores regulation from half-time line scores.\n")
    (DOCS / "sports_audit_supplement_v1_1.md").write_text("\n".join(L) + "\n", encoding="utf-8")

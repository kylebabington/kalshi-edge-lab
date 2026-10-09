"""Protocol freeze (select) and chronological test evaluation (evaluate).

``select`` writes a versioned protocol holding splits, horizon, grids, chosen parameters,
availability lags, reconciliation cohorts, exclusion rules, metrics, bootstrap settings, minimum
samples, input data hashes and a code hash. ``sports_phase1_v2`` is a retrospective evaluation
correction of ``sports_phase1_v1``: parameters are copied verbatim from v1 (no refitting) and
the predictions must equal the v1 journals byte-for-byte apart from the protocol field.
``evaluate`` refuses to score if the code or the normalized inputs no longer match the protocol,
writes predictions to a write-once journal *before* reading labels, then scores them.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

from .core import NORMALIZED, PREDICTIONS, PROTOCOLS, RESULTS, canonical_json, read_jsonl, sha256_bytes, \
    sha256_file, utc_now_iso, write_json, write_jsonl, write_once_jsonl
from .engines import clip
from . import models as M

GATE_MIN_AGREEMENT = 0.98
GATE_AGREEMENT_PCT = 98          # integer form of the agreement threshold (exact rational comparison)
GATE_MIN_N = 30
BOOTSTRAP = {"B": 2000, "seed": 20261008, "ci": [0.025, 0.975], "resample_unit": "cluster (source game / fight / tournament / Kalshi event)"}
MIN_CLUSTERS = {"default": 50, "field_finish": 15}
MIN_MATCHED_CLUSTERS = 50
ECE_BINS = 10
CODE_FILES = ["contracts.py", "engines.py", "models.py", "evaluate.py", "build.py", "adapters.py", "competitions.py",
              "benchmark.py"]
# Protocols whose artifacts are preserved and must never be rewritten by this code (commit that produced them).
PRESERVED = {"sports_phase1_v1": "7620d4c"}
# Correction protocols: parameters and the market-benchmark sample are inherited from the parent.
PARENT = {"sports_phase1_v2": "sports_phase1_v1"}
COHORTS = ("strict", "exploratory", "kalshi_settlement_only")
COHORT_RULES = {
    "strict": f"independent-source series with >= {GATE_MIN_N} pre-test settled contracts and >= {GATE_AGREEMENT_PCT}% "
              "agreement between the independent payoff and Kalshi settlement on those pre-test contracts",
    "exploratory": f"independent-source series with fewer than {GATE_MIN_N} pre-test settled contracts (insufficient "
                   "pre-test evidence); all-settled agreement is reported but never used for admission",
    "failed": f"independent-source series with >= {GATE_MIN_N} pre-test settled contracts and < {GATE_AGREEMENT_PCT}% "
              "agreement: excluded from every cohort",
    "kalshi_settlement_only": "competitions without an independent results source; labels are Kalshi settlements",
    "contract_level": "test contracts whose independent payoff disagrees with Kalshi settlement are excluded from every "
                      "cohort and counted per series; scored independent-source cohorts are settlement-concordant",
    "test_period_never_used": True,
}
BENCHMARK = {
    "families": ["game_winner"],
    "market_per_cluster": "home-side winner market (team sports); lexicographically first ticker otherwise",
    "cap_clusters": {"independent": 150, "kalshi_only": 40},
    "eligibility": "competitions whose game_winner test sample meets min_clusters (network budget)",
    "sample": "clusters ordered by sha256(protocol|competition|cluster); labels not used",
    "candle": "hourly candle with end_period_ts <= cutoff (latest such); mid of closing yes bid/ask",
    "lookback_hours": 24, "max_quote_age_hours": 3,
    "empty_book": "bid <= 0 or ask >= 1 treated as no quote",
    "closing_lines": "NFL nflverse closing moneylines (vig removed) reported separately as post-cutoff information",
}
BENCHMARK_V2 = {
    "sample_source": "preserved parent sample: candidate clusters from the parent scored.jsonl (game_winner) and "
                     "sampled tickers from the parent benchmark_quotes.jsonl, re-derived and asserted identical; "
                     "series newly admitted in v2 cannot enter the benchmark",
    "quotes": "recomputed from the byte-exact candle cache with the parent quote rules and asserted identical",
    "min_matched_clusters": MIN_MATCHED_CLUSTERS,
    "matched": "unique event clusters with a fresh two-sided quote after all quote filters",
    "views": {"all_matched": "every matched sampled cluster (mixed cohorts), descriptive unless >= minimum",
              "strict_matched": "only clusters whose market belongs to a strict-cohort series; strict-only losses, "
                                "CIs and cluster counts; the only view used for headline market counts"},
    "insufficient": "fewer than min_matched_clusters: descriptive metrics only, no interval classification",
    "kalshi_settlement_only": "descriptive only (frozen 40-game cap is below the minimum)",
}
EXCLUSION_RULES = [
    "Kalshi void / fair-price or unsettled markets",
    f"series failing the pre-test reconciliation gate (>= {GATE_MIN_N} pre-test settled contracts, < "
    f"{GATE_AGREEMENT_PCT}% agreement); series with fewer pre-test contracts are exploratory, never strict",
    "individual contracts where the independent payoff disagrees with Kalshi settlement",
    "ambiguous or unresolved participant / game identity (never merged automatically)",
    "preseason games (NBA, WNBA, NFL, MLB, NHL)",
    "scopes or competitions with too few training games to fit (reported as blocked)",
    "player/appearance-conditioned markets are not modelled (no pregame-deployable participation data)",
]
METRICS = {"brier": "mean (p - y)^2", "log_loss": f"mean -log(clip(p,1e-4))", "ece": f"{ECE_BINS} equal-width bins",
           "skill": "paired difference model - benchmark of per-contract loss, cluster bootstrap CI",
           "weighting": {"contract_weighted": "every scored contract weighs equally (v1 comparable)",
                         "event_equal": "per-contract (paired) losses averaged within each event cluster, then "
                                        "clusters averaged equally; bootstrap over the same cluster draws"}}
INTERPRETATION = ("Interval-based better/unclear/worse classes are exploratory and unadjusted for multiple "
                  "comparisons. Beating naive forecasts and beating market reference forecasts are reported "
                  "separately. RESEARCH_ONLY / NO_BET: no profitability or executable-edge claims.")
TEST_PERIOD_NOTE = ("The 2026 test period was already examined in sports_phase1_v1; v2 is a retrospective evaluation "
                    "correction, not a newly untouched holdout.")


def code_hash() -> str:
    here = Path(__file__).parent
    return sha256_bytes(b"".join(sha256_file(here / f).encode() for f in CODE_FILES))


def protocol_path(version: str) -> Path:
    return PROTOCOLS / f"{version}.json"


def load_comp(key: str):
    d = NORMALIZED / key
    summ = json.loads((d / "build_summary.json").read_text(encoding="utf-8"))
    if summ.get("blocked"):
        return summ, [], []
    return summ, list(read_jsonl(d / "contracts.jsonl")), list(read_jsonl(d / "events.jsonl"))


def assign_cohort(pre_agree: int, pre_n: int) -> str:
    """Cohort from pre-test evidence only: strict, exploratory (too little evidence) or failed."""
    if pre_n < GATE_MIN_N:
        return "exploratory"
    return "strict" if pre_agree * 100 >= GATE_AGREEMENT_PCT * pre_n else "failed"


def reconciliation(contracts) -> dict:
    """Per-series agreement between the independent payoff and Kalshi settlement.

    The cohort depends on pre-test contracts only; ``all_*`` (which includes the test period) is
    descriptive and never used for admission.
    """
    by = defaultdict(lambda: {"pre": [0, 0], "all": [0, 0]})
    for c in contracts:
        if c["status"] != "ok" or c.get("agree") is None:
            continue
        g = by[c["series"]]
        g["all"][0] += c["agree"]
        g["all"][1] += 1
        if c.get("start") and M.ts(c["start"]) < M.T_TEST:
            g["pre"][0] += c["agree"]
            g["pre"][1] += 1
    out = {}
    for s, g in sorted(by.items()):
        (pa, pn), (aa, an) = g["pre"], g["all"]
        out[s] = {"pre_agree": pa, "pre_n": pn, "pre_rate": pa / pn if pn else None,
                  "all_agree": aa, "all_n": an, "all_rate": aa / an if an else None,
                  "cohort": assign_cohort(pa, pn)}
    return out


def load_protocol(version: str) -> dict:
    path = protocol_path(version)
    if not path.exists():
        raise SystemExit(f"protocol {version} not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def stage_select(args, comps, log):
    if args.protocol in PRESERVED:
        raise SystemExit(f"{args.protocol} is preserved; reproduce it at commit {PRESERVED[args.protocol]}")
    parent = PARENT.get(args.protocol)
    if parent is None:
        raise SystemExit(f"unknown protocol {args.protocol}; correction protocols: {sorted(PARENT)}")
    pproto = load_protocol(parent)
    path = protocol_path(args.protocol)
    proto = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    body = {
        "protocol": args.protocol, "research_only": True, "no_bet": True,
        "supersedes": parent, "retrospective_correction": True, "test_period_status": TEST_PERIOD_NOTE,
        "params_source": f"copied verbatim from {parent} (no refitting, no model tuning)",
        "parent_protocol_file_sha256": sha256_file(protocol_path(parent)),
        "splits": M.SPLITS, "horizon_minutes_before_start": M.HORIZON_MIN,
        "grids": {"elo": M.ELO_GRID, "fight_elo": M.FIGHT_ELO_GRID, "alpha": M.ALPHA_GRID, "negbin_r": M.NEGBIN_R_GRID,
                  "field": M.FIELD_GRID, "distance_shrink": M.DIST_SHRINK_GRID, "kalshi_only_default": M.KONLY_DEFAULT,
                  "kalshi_only_min_train": M.KONLY_MIN_TRAIN, "field_sims": M.FIELD_SIMS},
        "availability": {
            "rule": "a source result is usable at start + result_lag_hours (per competition, conservative); golf at "
                    "end date + 1 day + lag; Kalshi-only results at the latest Kalshi settlement_ts of the event; "
                    "unfinished events never update ratings",
            "current_retrieval_time_not_used": True},
        "reconciliation_gate": {"min_agreement_pct": GATE_AGREEMENT_PCT, "min_pre_test_n": GATE_MIN_N,
                                "comparison": "agree * 100 >= pct * n (exact)", "cohorts": COHORT_RULES},
        "exclusions": EXCLUSION_RULES, "metrics": METRICS, "bootstrap": BOOTSTRAP, "min_clusters": MIN_CLUSTERS,
        "kalshi_benchmark": {**BENCHMARK, **BENCHMARK_V2, "sample_protocol": parent},
        "interpretation": INTERPRETATION,
        "label": "Kalshi settlement (yes=1/no=0); independent-source contracts must also agree with the independent outcome",
        "code_hash": code_hash(),
        "competitions": dict(proto.get("competitions", {})),
    }
    for c in comps:
        pe = pproto["competitions"].get(c.key)
        entry = {"sport": c.sport, "source": c.source, "name": c.name, "result_lag_hours": c.result_lag_hours}
        if pe is None:
            entry["blocked"] = f"not in parent protocol {parent}"
        elif pe.get("blocked"):
            entry["blocked"] = pe["blocked"]
        else:
            summ, contracts, _ = load_comp(c.key)
            data = None if summ.get("blocked") else {"contracts": summ["hash_contracts"], "events": summ["hash_events"]}
            if data != pe.get("data"):
                raise SystemExit(f"{c.key}: normalized data differ from the {parent} manifest; a correction protocol "
                                 "must evaluate the same inputs")
            entry["data"] = data
            entry["params"] = pe["params"]
            if c.source != "kalshi_only":
                entry["gate"] = reconciliation(contracts)
        body["competitions"][c.key] = entry
        log(f"selected {c.key}: {'BLOCKED ' + entry['blocked'] if 'blocked' in entry else 'ok'}")
    old = {k: v for k, v in proto.items() if k != "frozen_at"}
    if old == json.loads(json.dumps(body, default=str)):
        log("protocol unchanged")
    elif proto:
        raise SystemExit(f"{path.name} is frozen and the recomputed content differs; bump the protocol version")
    else:
        body["frozen_at"] = utc_now_iso()
        write_json(path, body)
        log(f"protocol written {path}")


# --------------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------------

def _losses(r) -> tuple[float, float]:
    return (r["p"] - r["y"]) ** 2, (-math.log(clip(r["p"])) if r["y"] else -math.log(1 - clip(r["p"])))


def metrics(rows) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    per = defaultdict(lambda: [0.0, 0.0, 0])
    for r in rows:
        b, l = _losses(r)
        g = per[r["cluster"]]
        g[0] += b
        g[1] += l
        g[2] += 1
    brier = sum((r["p"] - r["y"]) ** 2 for r in rows) / n
    logl = sum(-math.log(clip(r["p"])) if r["y"] else -math.log(1 - clip(r["p"])) for r in rows) / n
    brier_event = sum(g[0] / g[2] for g in per.values()) / len(per)
    logl_event = sum(g[1] / g[2] for g in per.values()) / len(per)
    bins = defaultdict(lambda: [0, 0.0, 0])
    for r in rows:
        b = min(ECE_BINS - 1, int(r["p"] * ECE_BINS))
        bins[b][0] += 1
        bins[b][1] += r["p"]
        bins[b][2] += r["y"]
    ece = sum(abs(v[1] / v[0] - v[2] / v[0]) * v[0] for v in bins.values()) / n
    return {"n": n, "clusters": len(per), "brier": brier, "log_loss": logl, "ece": ece,
            "brier_event": brier_event, "log_loss_event": logl_event,
            "mean_p": sum(r["p"] for r in rows) / n, "base_rate": sum(r["y"] for r in rows) / n,
            "reliability": [{"bin": b, "n": v[0], "mean_p": v[1] / v[0], "freq": v[2] / v[0]} for b, v in sorted(bins.items())]}


def paired_bootstrap(a_rows, b_rows, seed) -> dict:
    """Mean loss difference a - b on matched contracts; CI by resampling clusters.

    Contract-weighted (``d_brier``) and event-equal (``d_brier_event``: mean of per-cluster mean
    differences) views share the same cluster draws.
    """
    bmap = {r["ticker"]: r for r in b_rows}
    per = defaultdict(lambda: [0.0, 0.0, 0])
    for r in a_rows:
        o = bmap.get(r["ticker"])
        if o is None:
            continue
        d_b = (r["p"] - r["y"]) ** 2 - (o["p"] - o["y"]) ** 2
        la = -math.log(clip(r["p"])) if r["y"] else -math.log(1 - clip(r["p"]))
        lb = -math.log(clip(o["p"])) if o["y"] else -math.log(1 - clip(o["p"]))
        g = per[r["cluster"]]
        g[0] += d_b
        g[1] += la - lb
        g[2] += 1
    keys = sorted(per)
    if not keys:
        return {"n": 0}
    vals = [per[k] for k in keys]
    N = sum(v[2] for v in vals)
    point = (sum(v[0] for v in vals) / N, sum(v[1] for v in vals) / N)
    K = len(vals)
    ev = [(v[0] / v[2], v[1] / v[2]) for v in vals]
    point_e = (sum(e[0] for e in ev) / K, sum(e[1] for e in ev) / K)
    rng = random.Random(seed)
    bs_b, bs_l, bs_be, bs_le = [], [], [], []
    for _ in range(BOOTSTRAP["B"]):
        sb = sl = sn = se_b = se_l = 0.0
        for i in rng.choices(range(K), k=K):
            v = vals[i]
            sb += v[0]
            sl += v[1]
            sn += v[2]
            se_b += ev[i][0]
            se_l += ev[i][1]
        bs_b.append(sb / sn)
        bs_l.append(sl / sn)
        bs_be.append(se_b / K)
        bs_le.append(se_l / K)
    for x in (bs_b, bs_l, bs_be, bs_le):
        x.sort()
    lo, hi = int(BOOTSTRAP["ci"][0] * len(bs_b)), int(BOOTSTRAP["ci"][1] * len(bs_b)) - 1
    return {"n": N, "clusters": K, "d_brier": point[0], "d_brier_ci": [bs_b[lo], bs_b[hi]],
            "d_log_loss": point[1], "d_log_loss_ci": [bs_l[lo], bs_l[hi]],
            "d_brier_event": point_e[0], "d_brier_event_ci": [bs_be[lo], bs_be[hi]],
            "d_log_loss_event": point_e[1], "d_log_loss_event_ci": [bs_le[lo], bs_le[hi]]}


def classify(ci) -> str:
    """Exploratory, unadjusted interval classification of a loss difference (negative = first is better)."""
    lo, hi = ci
    return "better" if hi < 0 else "worse" if lo > 0 else "unclear"


def parent_journal_matches(parent: str, key: str, preds: list[dict]) -> bool | None:
    """True if the parent journal holds exactly these predictions (apart from the protocol field)."""
    p = PREDICTIONS / parent / f"{key}.jsonl"
    if not p.exists():
        return None
    data = b"".join(canonical_json({**r, "protocol": parent}) + b"\n" for r in preds)
    return sha256_bytes(data) == sha256_file(p)


def primary_model(family: str, models: set) -> str:
    if family == "game_winner" and "elo" in models:
        return "elo"
    return "score" if "score" in models else ("elo" if "elo" in models else "naive")


def min_clusters(family: str) -> int:
    return MIN_CLUSTERS.get(family, MIN_CLUSTERS["default"])


def stage_evaluate(args, comps, log):
    if args.protocol in PRESERVED:
        raise SystemExit(f"{args.protocol} is preserved; reproduce it at commit {PRESERVED[args.protocol]}")
    path = protocol_path(args.protocol)
    if not path.exists():
        raise SystemExit("protocol not frozen; run the select stage first")
    proto = json.loads(path.read_text(encoding="utf-8"))
    parent = proto.get("supersedes")
    if proto["code_hash"] != code_hash():
        raise SystemExit("code changed since the protocol was frozen; bump --protocol and re-run select")
    fams = {x.strip() for x in (args.family or "").split(",") if x.strip()}
    out_dir = RESULTS / args.protocol
    scored_all = []
    status = {}
    for c in comps:
        entry = proto["competitions"].get(c.key)
        if entry is None:
            status[c.key] = {"status": "not in protocol"}
            continue
        if entry.get("blocked"):
            status[c.key] = {"status": "blocked", "reason": entry["blocked"]}
            continue
        if entry["params"].get("blocked"):
            status[c.key] = {"status": "blocked", "reason": entry["params"]["blocked"]}
            continue
        summ, contracts, events = load_comp(c.key)
        if {"contracts": summ["hash_contracts"], "events": summ["hash_events"]} != entry["data"]:
            status[c.key] = {"status": "blocked", "reason": "normalized data differ from protocol manifest"}
            log(f"REFUSED {c.key}: data hash mismatch")
            continue
        preds = M.PREDICT[c.source](c, events, contracts, entry["params"])
        preds = sorted(preds, key=lambda r: (r["ticker"], r["model"]))
        jh = write_once_jsonl(PREDICTIONS / args.protocol / f"{c.key}.jsonl",
                              [{**p, "protocol": args.protocol} for p in preds])
        same = parent_journal_matches(parent, c.key, preds) if parent else None
        if parent and not same:
            raise SystemExit(f"{c.key}: predictions differ from the {parent} journal ({same}); a correction protocol "
                             "must reuse the parent forecasts unchanged")
        # labels are joined only after the journal is on disk
        by_t = {x["ticker"]: x for x in contracts}
        gate = entry.get("gate") or {}
        ex = Counter()
        disagree = defaultdict(set)
        cohort_rows = Counter()
        n_scored = 0
        for p in preds:
            if p["p"] is None:
                ex[p.get("reason", "no prediction")] += 1
                continue
            x = by_t[p["ticker"]]
            if fams and x["family"] not in fams:
                continue
            if c.source == "kalshi_only":
                cohort = "kalshi_settlement_only"
            else:
                g = gate.get(x["series"])
                if not g:
                    ex["series has no reconciled settled contracts"] += 1
                    continue
                if g["cohort"] == "failed":
                    ex["series failed pre-test reconciliation gate"] += 1
                    continue
                if x.get("agree") == 0:
                    ex["independent payoff disagrees with Kalshi settlement"] += 1
                    disagree[x["series"]].add(p["ticker"])
                    continue
                cohort = g["cohort"]
            scored_all.append({"competition": c.key, "sport": c.sport, "source": c.source, "family": x["family"],
                               "series": x["series"], "cohort": cohort, "ticker": p["ticker"], "cluster": p["cluster"],
                               "model": p["model"], "p": p["p"], "y": int(x["kalshi_outcome"] == "yes"),
                               "start": p["start"], "volume_fp": x.get("volume_fp")})
            cohort_rows[cohort] += 1
            n_scored += 1
        status[c.key] = {"status": "evaluated", "journal_sha256": jh, "predictions": len(preds),
                         "parent_journal_identical": same, "scored_rows": n_scored, "scored_rows_by_cohort": dict(cohort_rows),
                         "excluded": dict(ex),
                         "test_disagreements_by_series": {s: len(t) for s, t in sorted(disagree.items())}}
        log(f"evaluated {c.key}: rows={n_scored} {dict(cohort_rows)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "scored.jsonl", scored_all)
    write_json(out_dir / "evaluation_status.json", status)
    summarize(scored_all, out_dir, log)


def summarize(scored, out_dir, log):
    groups = defaultdict(list)
    for r in scored:
        co = r["cohort"]
        groups[("competition", r["competition"], r["family"], co)].append(r)
        groups[("series", r["competition"], r["series"], co)].append(r)
        groups[("sport_pool", r["sport"], r["family"], co)].append(r)
    results = []
    for (level, key, fam, cohort), rows in sorted(groups.items()):
        by_model = defaultdict(list)
        for r in rows:
            by_model[r["model"]].append(r)
        models = set(by_model)
        family = fam if level != "series" else rows[0]["family"]
        prim = primary_model(family, models)
        rec = {"level": level, "key": key, "group": fam, "family": family, "cohort": cohort, "primary": prim,
               "models": {m: metrics(v) for m, v in sorted(by_model.items())}}
        for m in rec["models"].values():
            m.pop("reliability", None) if level == "series" else None
        nclu = rec["models"][prim].get("clusters", 0)
        rec["min_clusters"] = min_clusters(family)
        rec["sufficient"] = nclu >= rec["min_clusters"]
        if level != "series" and prim != "naive" and "naive" in by_model:
            rec["skill_vs_naive"] = paired_bootstrap(by_model[prim], by_model["naive"],
                                                     BOOTSTRAP["seed"] + M.seed_of(level, key, fam) % 100000)
        results.append(rec)
    write_json(out_dir / "metrics.json", results)
    log(f"summarized {len(results)} groups")

"""Protocol freeze (select) and chronological test evaluation (evaluate).

``select`` fits parameters on the training window only and writes a versioned protocol holding
splits, horizon, grids, chosen parameters, availability lags, reconciliation gates, exclusion
rules, metrics, bootstrap settings, minimum samples, input data hashes and a code hash.
``evaluate`` refuses to score if the code or the normalized inputs no longer match the protocol,
writes predictions to a write-once journal *before* reading labels, then scores them.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

from .core import NORMALIZED, PREDICTIONS, PROTOCOLS, RESULTS, read_jsonl, sha256_bytes, sha256_file, utc_now_iso, \
    write_json, write_once_jsonl
from .engines import clip
from . import models as M

GATE_MIN_AGREEMENT = 0.98
GATE_MIN_N = 30
BOOTSTRAP = {"B": 2000, "seed": 20261008, "ci": [0.025, 0.975], "resample_unit": "cluster (source game / fight / tournament / Kalshi event)"}
MIN_CLUSTERS = {"default": 50, "field_finish": 15}
ECE_BINS = 10
CODE_FILES = ["contracts.py", "engines.py", "models.py", "evaluate.py", "build.py", "adapters.py", "competitions.py",
              "benchmark.py"]
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
EXCLUSION_RULES = [
    "Kalshi void / fair-price or unsettled markets",
    "contracts whose spec fails the reconciliation gate (independent payoff must reproduce Kalshi settlement "
    f"on >= {GATE_MIN_AGREEMENT:.0%} of pre-test settled markets; if fewer than {GATE_MIN_N} pre-test markets exist, "
    "all settled markets are used, which compares two outcome sources but no forecasts)",
    "individual contracts where the independent payoff disagrees with Kalshi settlement",
    "ambiguous or unresolved participant / game identity (never merged automatically)",
    "preseason games (NBA, WNBA, NFL, MLB, NHL)",
    "scopes or competitions with too few training games to fit (reported as blocked)",
    "player/appearance-conditioned markets are not modelled (no pregame-deployable participation data)",
]
METRICS = {"brier": "mean (p - y)^2", "log_loss": f"mean -log(clip(p,1e-4))", "ece": f"{ECE_BINS} equal-width bins",
           "skill": "paired difference model - benchmark of per-contract loss, cluster bootstrap CI"}


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


def reconciliation(contracts) -> dict:
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
        basis = "pre_test" if g["pre"][1] >= GATE_MIN_N else "all_settled"
        a, n = g["pre"] if basis == "pre_test" else g["all"]
        rate = a / n if n else None
        out[s] = {"basis": basis, "agree": a, "n": n, "rate": rate,
                  "all_agree": g["all"][0], "all_n": g["all"][1],
                  "verified": bool(n >= 10 and rate is not None and rate >= GATE_MIN_AGREEMENT)}
    return out


def stage_select(args, comps, log):
    path = protocol_path(args.protocol)
    proto = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    body = {
        "protocol": args.protocol, "research_only": True, "no_bet": True,
        "splits": M.SPLITS, "horizon_minutes_before_start": M.HORIZON_MIN,
        "grids": {"elo": M.ELO_GRID, "fight_elo": M.FIGHT_ELO_GRID, "alpha": M.ALPHA_GRID, "negbin_r": M.NEGBIN_R_GRID,
                  "field": M.FIELD_GRID, "distance_shrink": M.DIST_SHRINK_GRID, "kalshi_only_default": M.KONLY_DEFAULT,
                  "kalshi_only_min_train": M.KONLY_MIN_TRAIN, "field_sims": M.FIELD_SIMS},
        "availability": {
            "rule": "a source result is usable at start + result_lag_hours (per competition, conservative); golf at "
                    "end date + 1 day + lag; Kalshi-only results at the latest Kalshi settlement_ts of the event; "
                    "unfinished events never update ratings",
            "current_retrieval_time_not_used": True},
        "reconciliation_gate": {"min_agreement": GATE_MIN_AGREEMENT, "min_pre_test_n": GATE_MIN_N},
        "exclusions": EXCLUSION_RULES, "metrics": METRICS, "bootstrap": BOOTSTRAP, "min_clusters": MIN_CLUSTERS,
        "kalshi_benchmark": BENCHMARK,
        "label": "Kalshi settlement (yes=1/no=0) for contracts passing the gate; independent outcome reported as reconciliation",
        "code_hash": code_hash(),
        "competitions": dict(proto.get("competitions", {})),
    }
    for c in comps:
        summ, contracts, events = load_comp(c.key)
        entry = {"sport": c.sport, "source": c.source, "name": c.name, "result_lag_hours": c.result_lag_hours}
        if summ.get("blocked"):
            entry["blocked"] = summ["blocked"]
        else:
            entry["data"] = {"contracts": summ["hash_contracts"], "events": summ["hash_events"]}
            if c.source != "kalshi_only":
                entry["gate"] = reconciliation(contracts)
            try:
                entry["params"] = M.SELECT[c.source](c, events, contracts)
            except Exception as exc:
                entry["blocked"] = f"selection error: {exc!r}"
        body["competitions"][c.key] = entry
        log(f"selected {c.key}: {'BLOCKED ' + entry['blocked'] if 'blocked' in entry else 'ok'}")
    old = {k: v for k, v in proto.items() if k != "frozen_at"}
    if old != json.loads(json.dumps(body, default=str)):
        body["frozen_at"] = utc_now_iso()
        write_json(path, body)
        log(f"protocol written {path}")
    else:
        log("protocol unchanged")


# --------------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------------

def metrics(rows) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    brier = sum((r["p"] - r["y"]) ** 2 for r in rows) / n
    logl = sum(-math.log(clip(r["p"])) if r["y"] else -math.log(1 - clip(r["p"])) for r in rows) / n
    bins = defaultdict(lambda: [0, 0.0, 0])
    for r in rows:
        b = min(ECE_BINS - 1, int(r["p"] * ECE_BINS))
        bins[b][0] += 1
        bins[b][1] += r["p"]
        bins[b][2] += r["y"]
    ece = sum(abs(v[1] / v[0] - v[2] / v[0]) * v[0] for v in bins.values()) / n
    return {"n": n, "clusters": len({r["cluster"] for r in rows}), "brier": brier, "log_loss": logl, "ece": ece,
            "mean_p": sum(r["p"] for r in rows) / n, "base_rate": sum(r["y"] for r in rows) / n,
            "reliability": [{"bin": b, "n": v[0], "mean_p": v[1] / v[0], "freq": v[2] / v[0]} for b, v in sorted(bins.items())]}


def paired_bootstrap(a_rows, b_rows, seed) -> dict:
    """Mean loss difference a - b on matched contracts; CI by resampling clusters."""
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
    rng = random.Random(seed)
    K = len(vals)
    bs_b, bs_l = [], []
    for _ in range(BOOTSTRAP["B"]):
        sb = sl = sn = 0.0
        for i in rng.choices(range(K), k=K):
            v = vals[i]
            sb += v[0]
            sl += v[1]
            sn += v[2]
        bs_b.append(sb / sn)
        bs_l.append(sl / sn)
    bs_b.sort()
    bs_l.sort()
    lo, hi = int(BOOTSTRAP["ci"][0] * len(bs_b)), int(BOOTSTRAP["ci"][1] * len(bs_b)) - 1
    return {"n": N, "clusters": K, "d_brier": point[0], "d_brier_ci": [bs_b[lo], bs_b[hi]],
            "d_log_loss": point[1], "d_log_loss_ci": [bs_l[lo], bs_l[hi]]}


def primary_model(family: str, models: set) -> str:
    if family == "game_winner" and "elo" in models:
        return "elo"
    return "score" if "score" in models else ("elo" if "elo" in models else "naive")


def min_clusters(family: str) -> int:
    return MIN_CLUSTERS.get(family, MIN_CLUSTERS["default"])


def stage_evaluate(args, comps, log):
    path = protocol_path(args.protocol)
    if not path.exists():
        raise SystemExit("protocol not frozen; run the select stage first")
    proto = json.loads(path.read_text(encoding="utf-8"))
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
        # labels are joined only after the journal is on disk
        by_t = {x["ticker"]: x for x in contracts}
        gate = entry.get("gate") or {}
        ex = Counter()
        n_scored = 0
        for p in preds:
            if p["p"] is None:
                ex[p.get("reason", "no prediction")] += 1
                continue
            x = by_t[p["ticker"]]
            if fams and x["family"] not in fams:
                continue
            if c.source != "kalshi_only":
                g = gate.get(x["series"])
                if not g or not g["verified"]:
                    ex["series failed reconciliation gate"] += 1
                    continue
                if x.get("agree") == 0:
                    ex["independent payoff disagrees with Kalshi settlement"] += 1
                    continue
            scored_all.append({"competition": c.key, "sport": c.sport, "source": c.source, "family": x["family"],
                               "series": x["series"], "ticker": p["ticker"], "cluster": p["cluster"],
                               "model": p["model"], "p": p["p"], "y": int(x["kalshi_outcome"] == "yes"),
                               "start": p["start"], "volume_fp": x.get("volume_fp")})
            n_scored += 1
        status[c.key] = {"status": "evaluated", "journal_sha256": jh, "predictions": len(preds),
                         "scored_rows": n_scored, "excluded": dict(ex)}
        log(f"evaluated {c.key}: rows={n_scored}")
    out_dir.mkdir(parents=True, exist_ok=True)
    from .core import write_jsonl
    write_jsonl(out_dir / "scored.jsonl", scored_all)
    write_json(out_dir / "evaluation_status.json", status)
    summarize(scored_all, out_dir, log)


def summarize(scored, out_dir, log):
    groups = defaultdict(list)
    for r in scored:
        groups[("competition", r["competition"], r["family"])].append(r)
        groups[("series", r["competition"], r["series"])].append(r)
        pool = "kalshi_only" if r["source"] == "kalshi_only" else "independent"
        groups[("sport_pool", f"{r['sport']}|{pool}", r["family"])].append(r)
    results = []
    for (level, key, fam), rows in sorted(groups.items()):
        by_model = defaultdict(list)
        for r in rows:
            by_model[r["model"]].append(r)
        models = set(by_model)
        family = fam if level != "series" else rows[0]["family"]
        prim = primary_model(family, models)
        rec = {"level": level, "key": key, "group": fam, "family": family, "primary": prim,
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

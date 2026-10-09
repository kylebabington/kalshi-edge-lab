"""Kalshi price benchmark at the forecast cutoff, plus a separate NFL closing-line comparison.

Only candles that had *ended* by the cutoff are used. Quote age and missing coverage are
recorded per market. These are sparse hourly quotes: they support a calibration comparison,
not claims about executable edge or profitability.

A correction protocol preserves the parent's benchmark sample exactly: candidate clusters come
from the parent ``scored.jsonl`` and sampled tickers from the parent ``benchmark_quotes.jsonl``;
both are re-derived with the parent's rules and asserted identical. Series first admitted by the
correction cannot enter the sample. Market comparisons are computed separately for all matched
clusters and for strict-cohort clusters only; only the strict view can reach the headline.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from . import adapters as ad
from .core import RESULTS, Fetcher, CacheMiss, read_jsonl, write_json, write_jsonl
from .evaluate import BENCHMARK, BOOTSTRAP, MIN_CLUSTERS, MIN_MATCHED_CLUSTERS, PRESERVED, load_comp, load_protocol, \
    metrics, paired_bootstrap, primary_model
from . import models as M

INSUFFICIENT = "INSUFFICIENT"
SUFFICIENT = "SUFFICIENT"
DESCRIPTIVE_KO = "DESCRIPTIVE (Kalshi-settlement-only, frozen 40-game cap)"
QUOTE_FIELDS = ("status", "candle_end", "age_s", "bid", "ask", "mid")


def _px(side: dict | None) -> float | None:
    if not side:
        return None
    v = side.get("close_dollars", side.get("close"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def quote_at_cutoff(candles: list[dict], cutoff_ts: int) -> dict:
    """Latest candle whose period ended at or before the cutoff, with a two-sided book."""
    done = [c for c in candles if int(c["end_period_ts"]) <= cutoff_ts]
    if not done:
        return {"status": "no candle ended by cutoff"}
    last = max(done, key=lambda c: int(c["end_period_ts"]))
    bid, ask = _px(last.get("yes_bid")), _px(last.get("yes_ask"))
    age = cutoff_ts - int(last["end_period_ts"])
    if bid is None or ask is None or bid <= 0 or ask >= 1 or ask < bid:
        return {"status": "no two-sided quote in last ended candle", "candle_end": int(last["end_period_ts"]), "age_s": age}
    out = {"candle_end": int(last["end_period_ts"]), "age_s": age, "bid": bid, "ask": ask, "mid": (bid + ask) / 2}
    out["status"] = "ok" if age <= BENCHMARK["max_quote_age_hours"] * 3600 else "stale"
    return out


def _order(protocol, comp, cluster):
    return hashlib.sha256(f"{protocol}|{comp}|{cluster}".encode()).hexdigest()


def stream_rows(path: Path, family: str, keep=None):
    """Rows of a (large) scored.jsonl for one family; canonical JSON allows a substring pre-filter."""
    needle = f'"family":"{family}"'
    with open(path, encoding="utf-8") as f:
        for line in f:
            if needle not in line:
                continue
            r = json.loads(line)
            if r["family"] == family and (keep is None or keep(r)):
                yield r


def sample_clusters(sample_protocol: str, comp_key: str, source: str, per_cluster: dict[str, set], cmap: dict):
    """Deterministic benchmark sample (labels unused): one market per cluster, hash order, capped."""
    chosen = {}
    for cl, tickers in per_cluster.items():
        ts_ = sorted(tickers)
        home = [t for t in ts_ if cmap[t].get("side") == "home" and not cmap[t].get("tie")]
        chosen[cl] = home[0] if home else ts_[0]
    cap = BENCHMARK["cap_clusters"]["kalshi_only" if source == "kalshi_only" else "independent"]
    order = sorted(chosen, key=lambda cl: _order(sample_protocol, comp_key, cl))[:cap]
    return [(cl, chosen[cl]) for cl in order]


def sufficiency(matched_clusters: int, source: str) -> str:
    if source == "kalshi_only":
        return DESCRIPTIVE_KO
    return SUFFICIENT if matched_clusters >= MIN_MATCHED_CLUSTERS else INSUFFICIENT


def compare_view(model_rows: dict[str, list], mids: dict[str, float], keep, source: str, seed: int) -> dict:
    """Model / naive / Kalshi on matched sampled markets selected by ``keep(ticker)``.

    Interval estimates are kept only for SUFFICIENT views; otherwise point values are descriptive.
    """
    rows = {m: [r for r in v if r["ticker"] in mids and keep(r["ticker"])] for m, v in model_rows.items()}
    rows = {m: v for m, v in rows.items() if v}
    prim = primary_model("game_winner", set(rows))
    kal = [{**r, "model": "kalshi", "p": mids[r["ticker"]]} for r in rows.get(prim, [])]
    n_clusters = len({r["cluster"] for r in kal})
    out = {"matched_clusters": n_clusters, "matched_contracts": len(kal), "primary": prim,
           "sufficiency": sufficiency(n_clusters, source),
           "kalshi": metrics(kal), "model": metrics(rows.get(prim, [])), "naive": metrics(rows.get("naive", []))}
    for m in (out["kalshi"], out["model"], out["naive"]):
        m.pop("reliability", None)
    if kal:
        out["model_minus_kalshi"] = paired_bootstrap(rows[prim], kal, seed + 7)
        out["naive_minus_kalshi"] = paired_bootstrap(rows.get("naive", []), kal, seed + 11)
        if out["sufficiency"] != SUFFICIENT:
            for k in ("model_minus_kalshi", "naive_minus_kalshi"):
                out[k] = {f: v for f, v in out[k].items() if not f.endswith("_ci")}
    return out


def stage_benchmark(args, comps, fetcher: Fetcher, log):
    if args.protocol in PRESERVED:
        raise SystemExit(f"{args.protocol} is preserved; reproduce it at commit {PRESERVED[args.protocol]}")
    proto = load_protocol(args.protocol)
    parent = proto["kalshi_benchmark"]["sample_protocol"]
    out_dir, pdir = RESULTS / args.protocol, RESULTS / parent
    keys = {c.key for c in comps}
    fam = BENCHMARK["families"][0]
    # parent artifacts: candidate membership, eligibility and the sampled tickers with their quotes
    cand = defaultdict(lambda: defaultdict(set))
    meta = {}
    for r in stream_rows(pdir / "scored.jsonl", fam, lambda r: r["competition"] in keys):
        cand[r["competition"]][r["cluster"]].add(r["ticker"])
        meta[r["competition"]] = (r["source"], r["sport"])
    pquotes = defaultdict(list)
    for q in read_jsonl(pdir / "benchmark_quotes.jsonl"):
        pquotes[q["competition"]].append(q)
    pmetrics = {b["competition"]: b for b in json.loads((pdir / "benchmark_metrics.json").read_text(encoding="utf-8"))}
    sampled_tickers = {q["ticker"] for k in keys for q in pquotes.get(k, [])}
    cur = defaultdict(lambda: defaultdict(list))
    cohort_of = {}
    for r in stream_rows(out_dir / "scored.jsonl", fam, lambda r: r["ticker"] in sampled_tickers):
        cur[r["competition"]][r["model"]].append(r)
        cohort_of[r["ticker"]] = r["cohort"]
    bench_rows, summary = [], []
    for comp_key in sorted(cand):
        per_cluster = cand[comp_key]
        source, sport = meta[comp_key]
        prev = pmetrics.get(comp_key)
        if prev is None:
            raise SystemExit(f"{comp_key}: missing from the {parent} benchmark summary; sample cannot be preserved")
        if len(per_cluster) < MIN_CLUSTERS["default"]:
            if not prev.get("skipped"):
                raise SystemExit(f"{comp_key}: parent eligibility differs from re-derived eligibility")
            summary.append({"competition": comp_key, "source": source, "sport": sport,
                            "eligible_clusters": len(per_cluster), "sampled": 0,
                            "skipped": "fewer than 50 game-winner test clusters in the parent sample frame; not benchmarked"})
            continue
        _, contracts, _ = load_comp(comp_key)
        cmap = {c["ticker"]: c for c in contracts}
        sample = sample_clusters(parent, comp_key, source, per_cluster, cmap)
        frozen = [(q["cluster"], q["ticker"]) for q in pquotes.get(comp_key, [])]
        if sample != frozen:
            raise SystemExit(f"{comp_key}: re-derived sample differs from the {parent} benchmark_quotes.jsonl")
        mids, statuses, ages = {}, defaultdict(int), []
        for (cl, t), pq in zip(sample, pquotes[comp_key]):
            c = cmap[t]
            cutoff = int(M.ts(c["start"])) - M.HORIZON_MIN * 60
            try:
                candles, st = ad.kalshi_candles(fetcher, c["series"], t, cutoff - BENCHMARK["lookback_hours"] * 3600,
                                                cutoff, c["tier"] == "historical")
                q = quote_at_cutoff(candles, cutoff) if st == 200 else {"status": f"candles http {st}"}
            except CacheMiss:
                q = {"status": "cache miss"}
            if any(q.get(f) != pq.get(f) for f in QUOTE_FIELDS) or cutoff != pq["cutoff_ts"]:
                raise SystemExit(f"{comp_key} {t}: quote differs from the {parent} artifact")
            if t not in cohort_of:
                q = {**q, "status": "not scored under this protocol"}
            bench_rows.append({"competition": comp_key, "cluster": cl, "ticker": t, "cutoff_ts": cutoff,
                               "tier": c["tier"], "cohort": cohort_of.get(t), **q})
            statuses[q["status"]] += 1
            if q["status"] == "ok":
                mids[t] = q["mid"]
                ages.append(q["age_s"])
        ages.sort()
        model_rows = cur.get(comp_key, {})
        seed = BOOTSTRAP["seed"]
        rec = {"competition": comp_key, "source": source, "sport": sport,
               "eligible_clusters": len(per_cluster), "sampled": len(sample),
               "matched_all": len({cl for cl, t in sample if t in mids}),
               "matched_strict": len({cl for cl, t in sample if t in mids and cohort_of.get(t) == "strict"}),
               "quote_status": dict(statuses), "median_quote_age_s": ages[len(ages) // 2] if ages else None,
               "parent_matched": (prev.get("kalshi") or {}).get("clusters", 0),
               "views": {"all_matched": compare_view(model_rows, mids, lambda t: True, source, seed),
                         "strict_matched": compare_view(model_rows, mids, lambda t: cohort_of.get(t) == "strict",
                                                        source, seed)}}
        rec["headline_eligible"] = source != "kalshi_only" and rec["views"]["strict_matched"]["sufficiency"] == SUFFICIENT
        summary.append(rec)
        log(f"benchmark {comp_key}: sampled={len(sample)} matched={rec['matched_all']} strict={rec['matched_strict']} "
            f"headline={rec['headline_eligible']}")
    write_jsonl(out_dir / "benchmark_quotes.jsonl", bench_rows)
    write_json(out_dir / "benchmark_metrics.json", summary)
    closing_lines(args, fetcher, out_dir, log)


def _implied(ml):
    if ml is None:
        return None
    return 100.0 / (ml + 100.0) if ml > 0 else -ml / (-ml + 100.0)


def closing_lines(args, fetcher, out_dir, log):
    """NFL closing moneylines (after the cutoff) vs the frozen forecasts, reported separately, per cohort."""
    try:
        raw = fetcher.get("nflverse", ad.NFLVERSE_GAMES)
    except CacheMiss:
        write_json(out_dir / "closing_lines.json", {"status": "nflverse not cached"})
        return
    games = {f"espn:{g['espn']}": g for g in ad.parse_nflverse_games(raw) if g.get("espn")}
    rows = list(stream_rows(out_dir / "scored.jsonl", "game_winner", lambda r: r["competition"] == "nfl"))
    _, contracts, _ = load_comp("nfl")
    cmap = {c["ticker"]: c for c in contracts}
    out = {"timing": "closing moneyline (near kickoff, after the 60-minute cutoff): information not available at the "
                     "forecast cutoff; shown for context only", "views": {}}
    for view, keep in (("all", lambda r: True), ("strict", lambda r: r["cohort"] == "strict")):
        by_model, close_rows = defaultdict(list), []
        for r in rows:
            c = cmap[r["ticker"]]
            g = games.get(r["cluster"])
            if not keep(r) or not g or c.get("side") not in ("home", "away"):
                continue
            ph, pa = _implied(g["home_moneyline"]), _implied(g["away_moneyline"])
            if ph is None or pa is None:
                continue
            p_home = ph / (ph + pa)
            by_model[r["model"]].append(r)
            if r["model"] == "naive":
                close_rows.append({**r, "model": "closing_line", "p": p_home if c["side"] == "home" else 1 - p_home})
        v = {"closing_line": metrics(close_rows), "n_matched": len(close_rows)}
        for m, rs in by_model.items():
            v[m] = metrics(rs)
            v[f"{m}_minus_closing"] = paired_bootstrap(rs, close_rows, BOOTSTRAP["seed"] + 13)
        for k in list(v):
            if isinstance(v[k], dict):
                v[k].pop("reliability", None)
        out["views"][view] = v
    write_json(out_dir / "closing_lines.json", out)
    log(f"closing lines: matched all={out['views']['all']['n_matched']} strict={out['views']['strict']['n_matched']}")

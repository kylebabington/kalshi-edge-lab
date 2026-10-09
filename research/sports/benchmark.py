"""Kalshi price benchmark at the forecast cutoff, plus a separate NFL closing-line comparison.

Only candles that had *ended* by the cutoff are used. Quote age and missing coverage are
recorded per market. These are sparse hourly quotes: they support a calibration comparison,
not claims about executable edge or profitability.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from . import adapters as ad
from .core import RESULTS, Fetcher, CacheMiss, read_jsonl, write_json, write_jsonl
from .evaluate import BENCHMARK, BOOTSTRAP, MIN_CLUSTERS, metrics, paired_bootstrap, primary_model, protocol_path
from . import models as M


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


def stage_benchmark(args, comps, fetcher: Fetcher, log):
    out_dir = RESULTS / args.protocol
    scored = list(read_jsonl(out_dir / "scored.jsonl"))
    keys = {c.key for c in comps}
    by_comp = defaultdict(list)
    for r in scored:
        if r["competition"] in keys and r["family"] in BENCHMARK["families"]:
            by_comp[r["competition"]].append(r)
    contracts_cache = {}
    bench_rows, summary = [], []
    for comp_key, rows in sorted(by_comp.items()):
        source = rows[0]["source"]
        from .evaluate import load_comp
        _, contracts, _ = load_comp(comp_key)
        cmap = {c["ticker"]: c for c in contracts}
        per_cluster = defaultdict(list)
        for r in rows:
            per_cluster[r["cluster"]].append(r)
        if len(per_cluster) < MIN_CLUSTERS["default"]:
            summary.append({"competition": comp_key, "source": source, "sport": rows[0]["sport"],
                            "test_clusters": len(per_cluster), "skipped": "insufficient test sample; not benchmarked"})
            continue
        chosen = {}
        for cl, rs in per_cluster.items():
            tickers = sorted({r["ticker"] for r in rs})
            home = [t for t in tickers if cmap[t].get("side") == "home" and not cmap[t].get("tie")]
            chosen[cl] = home[0] if home else tickers[0]
        cap = BENCHMARK["cap_clusters"]["kalshi_only" if source == "kalshi_only" else "independent"]
        sample = sorted(chosen, key=lambda cl: _order(args.protocol, comp_key, cl))[:cap]
        got = []
        for cl in sample:
            t = chosen[cl]
            c = cmap[t]
            cutoff = int(M.ts(c["start"])) - M.HORIZON_MIN * 60
            try:
                candles, st = ad.kalshi_candles(fetcher, c["series"], t, cutoff - BENCHMARK["lookback_hours"] * 3600,
                                                cutoff, c["tier"] == "historical")
                q = quote_at_cutoff(candles, cutoff) if st == 200 else {"status": f"candles http {st}"}
            except CacheMiss:
                q = {"status": "cache miss"}
            bench_rows.append({"competition": comp_key, "cluster": cl, "ticker": t, "cutoff_ts": cutoff,
                               "tier": c["tier"], **q})
            if q["status"] == "ok":
                got.append((t, q["mid"]))
        mids = dict(got)
        model_rows = defaultdict(list)
        for r in rows:
            if r["ticker"] in mids:
                model_rows[r["model"]].append(r)
        prim = primary_model("game_winner", set(model_rows))
        kal = [{**r, "model": "kalshi", "p": mids[r["ticker"]]} for r in model_rows.get(prim, [])]
        status_counts = defaultdict(int)
        for b in bench_rows:
            if b["competition"] == comp_key:
                status_counts[b["status"]] += 1
        ages = sorted(b["age_s"] for b in bench_rows if b["competition"] == comp_key and b["status"] == "ok")
        rec = {"competition": comp_key, "source": source, "sport": rows[0]["sport"], "test_clusters": len(chosen),
               "sampled": len(sample), "quote_status": dict(status_counts),
               "median_quote_age_s": ages[len(ages) // 2] if ages else None, "primary": prim,
               "kalshi": metrics(kal), "model": metrics(model_rows.get(prim, [])),
               "naive": metrics(model_rows.get("naive", []))}
        for m in (rec["kalshi"], rec["model"], rec["naive"]):
            m.pop("reliability", None)
        if kal:
            rec["model_minus_kalshi"] = paired_bootstrap(model_rows[prim], kal, BOOTSTRAP["seed"] + 7)
            rec["naive_minus_kalshi"] = paired_bootstrap(model_rows.get("naive", []), kal, BOOTSTRAP["seed"] + 11)
        summary.append(rec)
        log(f"benchmark {comp_key}: sampled={len(sample)} ok={len(got)}")
    write_jsonl(out_dir / "benchmark_quotes.jsonl", bench_rows)
    write_json(out_dir / "benchmark_metrics.json", summary)
    closing_lines(args, fetcher, scored, out_dir, log)


def _implied(ml):
    if ml is None:
        return None
    return 100.0 / (ml + 100.0) if ml > 0 else -ml / (-ml + 100.0)


def closing_lines(args, fetcher, scored, out_dir, log):
    """NFL closing moneylines (after the cutoff) vs the frozen forecasts, reported separately."""
    try:
        raw = fetcher.get("nflverse", ad.NFLVERSE_GAMES)
    except CacheMiss:
        write_json(out_dir / "closing_lines.json", {"status": "nflverse not cached"})
        return
    games = {f"espn:{g['espn']}": g for g in ad.parse_nflverse_games(raw) if g.get("espn")}
    rows = [r for r in scored if r["competition"] == "nfl" and r["family"] == "game_winner"]
    from .evaluate import load_comp
    _, contracts, _ = load_comp("nfl")
    cmap = {c["ticker"]: c for c in contracts}
    by_model = defaultdict(list)
    close_rows = []
    for r in rows:
        c = cmap[r["ticker"]]
        g = games.get(r["cluster"])
        if not g or c.get("side") not in ("home", "away"):
            continue
        ph, pa = _implied(g["home_moneyline"]), _implied(g["away_moneyline"])
        if ph is None or pa is None:
            continue
        p_home = ph / (ph + pa)
        by_model[r["model"]].append(r)
        if r["model"] == "naive":
            close_rows.append({**r, "model": "closing_line", "p": p_home if c["side"] == "home" else 1 - p_home})
    out = {"timing": "closing moneyline (near kickoff, after the 60-minute cutoff): information not available at the "
                     "forecast cutoff; shown for context only",
           "closing_line": metrics(close_rows), "n_matched": len(close_rows)}
    for m, v in by_model.items():
        out[m] = metrics(v)
        out[f"{m}_minus_closing"] = paired_bootstrap(v, close_rows, BOOTSTRAP["seed"] + 13)
    for k in list(out):
        if isinstance(out[k], dict):
            out[k].pop("reliability", None)
    write_json(out_dir / "closing_lines.json", out)
    log(f"closing lines: matched {len(close_rows)}")

"""Historical Kalshi market inputs for Phase 2: listing times, frozen sample, budgeted fetch, quotes.

The sample is chosen without labels, scores or quotes: events in a fixed hash order, a fixed cap
of events per (competition, family, period) and of contracts per event, and only contracts that
were listed by the forecast cutoff. The complete request list (with tier truncation against the
budget) is frozen in a write-once manifest *before* any request. ``fetch`` walks that manifest in
order, records every network request in an append-only ledger, resumes after interruption,
never replaces failed quotes and never expands the sample.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path

from .. import adapters as ad
from .. import models as M
from ..benchmark import quote_at_cutoff
from ..core import RAW_ROOT, CacheMiss, Fetcher, parse_ts, read_jsonl, request_key, sha256_json, utc_now_iso, \
    write_json, write_jsonl
from . import config as C


def market_dir(protocol: str) -> Path:
    return C.MARKET_DIR / protocol


# ---------------------------------------------------------------- listing times

def listing_ts(m: dict) -> float | None:
    """When the contract existed: the later of created_time and open_time (None if neither is known)."""
    vals = [parse_ts(m.get(k)) for k in ("created_time", "open_time")]
    vals = [v.timestamp() for v in vals if v is not None]
    return max(vals) if vals else None


def build_listing_map(series: list[str], log) -> tuple[dict, dict]:
    """ticker -> listing ts from the cached raw market pages (cache-only; no network)."""
    fetcher = Fetcher(cache_only=True)
    out, missing = {}, {}
    for s in sorted(set(series)):
        try:
            raw, _ = ad.fetch_kalshi_series_markets(fetcher, s)
        except CacheMiss:
            missing[s] = "raw market pages not cached"
            continue
        for m in raw:
            ts_ = listing_ts(m)
            t = m["ticker"]
            if t not in out or (ts_ is not None and (out[t] is None or ts_ > out[t])):
                out[t] = ts_
    log(f"listing map: {len(out)} tickers from {len(set(series)) - len(missing)} series ({len(missing)} uncached)")
    return out, missing


# ---------------------------------------------------------------- sampling

def period_of(start_ts: float) -> str | None:
    for role, folds in C.SAMPLE_PERIOD.items():
        for f in folds:
            a, b = (M.ts(x) for x in C.PERIODS[f])
            if a <= start_ts < b:
                return role
    return None


def event_order(comp: str, family: str, period: str, cluster: str) -> str:
    return hashlib.sha256(f"{C.SAMPLE_SEED}|{comp}|{family}|{period}|{cluster}".encode()).hexdigest()


def _lower_median(xs):
    xs = sorted(xs)
    return xs[(len(xs) - 1) // 2] if xs else None


def pick_contracts(family: str, cs: list[dict]) -> list[dict]:
    """Fixed per-event contract rule (labels and prices unused)."""
    cs = sorted(cs, key=lambda c: c["ticker"])
    if family == "game_winner":
        home = [c for c in cs if c.get("side") == "home" and not c.get("tie")]
        return [home[0] if home else cs[0]]
    if family in ("spread", "team_total"):
        out = []
        for side in ("home", "away"):
            sc = [c for c in cs if c.get("side") == side and c.get("strike") is not None]
            k = _lower_median([c["strike"] for c in sc])
            if k is not None:
                out.append(min((c for c in sc if c["strike"] == k), key=lambda c: c["ticker"]))
        return out
    if family == "total":
        sc = [c for c in cs if c.get("strike") is not None]
        ks = sorted({c["strike"] for c in sc})
        if not ks:
            return []
        lo, hi = ks[(len(ks) - 1) // 2], ks[len(ks) // 2]
        out = [min((c for c in sc if c["strike"] == lo), key=lambda c: c["ticker"])]
        if hi != lo:
            out.append(min((c for c in sc if c["strike"] == hi), key=lambda c: c["ticker"]))
        return out
    raise ValueError(family)


def candidate_events(comp, contracts, forecast_tickers: set, gate: dict, listing: dict) -> tuple[dict, dict]:
    """{(family, period): {cluster: [eligible contracts]}} plus eligibility counters."""
    out = defaultdict(lambda: defaultdict(list))
    why = defaultdict(int)
    for c in contracts:
        if c["status"] != "ok" or c["family"] not in C.MARKET_FAMILIES or not c.get("start"):
            continue
        start = M.ts(c["start"])
        per = period_of(start)
        if per is None:
            continue
        if c["ticker"] not in forecast_tickers:
            why["no frozen-model forecast"] += 1
            continue
        if comp.source != "kalshi_only" and (gate.get(c["series"]) or {}).get("cohort") in (None, "failed"):
            why["series failed or unreconciled"] += 1
            continue
        lt = listing.get(c["ticker"])
        if lt is None:
            why["listing time unknown"] += 1
            continue
        if lt > start - M.HORIZON_MIN * 60:
            why["listed after cutoff"] += 1
            continue
        out[(c["family"], per)][c["cluster"]].append(c)
        why["eligible"] += 1
    return out, dict(why)


def sample_competition(comp, cand: dict) -> list[dict]:
    kind = "kalshi_only" if comp.source == "kalshi_only" else "independent"
    events = []
    for (family, period), per_cluster in sorted(cand.items()):
        cap = C.EVENT_CAPS[kind][period]
        order = sorted(per_cluster, key=lambda cl: event_order(comp.key, family, period, cl))
        n = 0
        for cl in order:
            if n >= cap:
                break
            picks = pick_contracts(family, per_cluster[cl])
            if not picks:
                continue
            n += 1
            events.append({"competition": comp.key, "source": comp.source, "family": family, "period": period,
                           "cluster": cl, "order": event_order(comp.key, family, period, cl),
                           "contracts": [{"ticker": c["ticker"], "series": c["series"], "tier": c["tier"],
                                          "cutoff_ts": int(M.ts(c["start"])) - M.HORIZON_MIN * 60} for c in picks]})
    return events


def tier_of(family: str, period: str) -> str | None:
    for name, fams, periods in C.TIERS:
        fams = (fams,) if isinstance(fams, str) else fams
        if family in fams and period in periods:
            return name
    return None


def candle_request(c: dict) -> tuple[str, dict, bool]:
    if c["tier"] == "historical":
        url = f"{ad.KALSHI_BASE}/historical/markets/{c['ticker']}/candlesticks"
    else:
        url = f"{ad.KALSHI_BASE}/series/{c['series']}/markets/{c['ticker']}/candlesticks"
    lb = C.QUOTE["lookback_hours"] * 3600
    return url, {"start_ts": c["cutoff_ts"] - lb, "end_ts": c["cutoff_ts"], "period_interval": C.QUOTE["period_interval_min"]}, \
        c["tier"] == "historical"


def is_cached(url: str, params: dict, root: Path = RAW_ROOT) -> bool:
    key = request_key(url, params)
    d = root / "kalshi" / key[:2]
    return (d / f"{key}.body").exists() and (d / f"{key}.meta.json").exists()


def apply_budget(events: list[dict], budget: int, cached) -> tuple[list[dict], dict]:
    """Keep whole tiers in order; truncate the first tier that does not fit; drop the rest."""
    by_tier = defaultdict(list)
    for e in events:
        t = tier_of(e["family"], e["period"])
        if t is not None:
            by_tier[t].append({**e, "tier": t})
    kept, used, seen, report = [], 0, set(), {}
    stop = False
    for name, _, _ in C.TIERS:
        evs = sorted(by_tier.get(name, []), key=lambda e: (e["order"], e["competition"], e["family"], e["period"]))
        rep = {"events_candidate": len(evs), "events_kept": 0, "uncached_requests": 0, "cached_requests": 0}
        report[name] = rep
        if stop:
            rep["dropped"] = True
            continue
        for e in evs:
            new = []
            for c in e["contracts"]:
                url, params, _ = candle_request(c)
                k = request_key(url, params)
                if k not in seen and not cached(url, params):
                    new.append(k)
            if used + len(set(new)) > budget:
                stop = True
                rep["truncated"] = True
                break
            for c in e["contracts"]:
                url, params, _ = candle_request(c)
                k = request_key(url, params)
                if k not in seen:
                    seen.add(k)
                    if k in new:
                        rep["uncached_requests"] += 1
                    else:
                        rep["cached_requests"] += 1
            used += len(set(new))
            kept.append(e)
            rep["events_kept"] += 1
    return kept, {"tiers": report, "uncached_requests": used, "budget": budget}


def runtime_estimate(n_requests: int, root: Path = RAW_ROOT) -> dict:
    """Lower bound from the minimum spacing; expected adds the median observed Kalshi latency."""
    el = []
    d = root / "kalshi"
    if d.exists():
        for sub in sorted(d.iterdir())[:64]:
            for mp in sorted(sub.glob("*.meta.json"))[:20]:
                try:
                    el.append(float(json.loads(mp.read_text(encoding="utf-8")).get("elapsed_s") or 0))
                except (ValueError, OSError):
                    pass
    med = statistics.median(el) if el else 0.3
    lower = n_requests * C.BUDGET["min_interval_s"]
    exp = n_requests * max(C.BUDGET["min_interval_s"], med)
    return {"unique_requests": n_requests, "lower_bound_s": lower, "expected_s": round(exp, 1),
            "median_latency_s": med, "latency_sample": len(el),
            "note": "lower bound = requests x 0.5 s minimum spacing; retries (<= max_retries_total, exponential "
                    "backoff up to 60 s each) and slow responses can extend it"}


def manifest_path(protocol: str) -> Path:
    return market_dir(protocol) / "sample_manifest.json"


def freeze_manifest(protocol: str, body: dict, log) -> dict:
    """Write once; a recomputed manifest must be identical apart from its timestamp."""
    path = manifest_path(protocol)
    core = {k: v for k, v in body.items() if k not in ("frozen_at", "estimate")}
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if {k: v for k, v in old.items() if k not in ("frozen_at", "estimate")} != json.loads(json.dumps(core)):
            raise SystemExit(f"{path} is frozen and the recomputed sample differs; never re-sample a frozen manifest")
        log(f"sample manifest unchanged ({len(old['events'])} events)")
        return old
    body = {**body, "frozen_at": utc_now_iso()}
    write_json(path, body)
    log(f"sample manifest frozen: {path} ({len(body['events'])} events)")
    return body


# ---------------------------------------------------------------- budgeted, resumable fetch

def ledger_path(protocol: str) -> Path:
    return market_dir(protocol) / "fetch_ledger.jsonl"


def manifest_requests(man: dict) -> list[tuple[str, dict, dict]]:
    out, seen = [], set()
    for e in man["events"]:
        for c in e["contracts"]:
            url, params, _ = candle_request(c)
            k = request_key(url, params)
            if k not in seen:
                seen.add(k)
                out.append((k, {"url": url, "params": params}, c))
    return out


def fetch(protocol: str, log, fetcher: Fetcher | None = None) -> dict:
    man = json.loads(manifest_path(protocol).read_text(encoding="utf-8"))
    if man["manifest_sha256"] != sha256_json(man["events"]):
        raise SystemExit("sample manifest events do not match their recorded hash")
    budget = man["budget"]
    fetcher = fetcher or Fetcher(min_interval=budget["min_interval_s"], max_retries=budget["per_request_max_retries"])
    lp = ledger_path(protocol)
    done = {}
    if lp.exists():
        for r in read_jsonl(lp):
            done[r["key"]] = r
    unique = sum(1 for r in done.values() if r.get("network"))
    retries = sum(r.get("retries", 0) for r in done.values())
    reqs = manifest_requests(man)
    log(f"fetch: {len(reqs)} manifest requests; ledger has {len(done)} (network {unique}, retries {retries})")
    stopped = None
    with open(lp, "a", encoding="utf-8") as f:
        for k, rq, c in reqs:
            if k in done:
                continue
            if fetcher.cached("kalshi", rq["url"], rq["params"]) is not None:
                rec = {"key": k, "ticker": c["ticker"], "network": False, "status": "cached"}
            else:
                if unique >= budget["frozen_unique_requests"]:
                    stopped = "unique-request budget reached"
                    break
                if retries >= budget["max_retries_total"]:
                    stopped = "retry budget exhausted"
                    break
                before = dict(fetcher.stats)
                raw = fetcher.get("kalshi", rq["url"], rq["params"])
                r_used = fetcher.stats["retries"] - before["retries"]
                unique += 1
                retries += r_used
                rec = {"key": k, "ticker": c["ticker"], "network": True, "status": raw.status,
                       "attempts": raw.meta.get("attempts"), "retries": r_used, "at": utc_now_iso()}
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            f.flush()
            done[k] = rec
            if unique and unique % 500 == 0 and rec.get("network"):
                log(f"fetch progress: unique={unique} retries={retries} ledger={len(done)}/{len(reqs)}")
    summary = {"manifest_requests": len(reqs), "ledger_entries": len(done), "unique_network_requests": unique,
               "retries": retries, "stopped": stopped,
               "statuses": dict(sorted(_count(r["status"] for r in done.values()).items(), key=str)),
               "remaining": len(reqs) - len(done)}
    log(f"fetch summary: {json.dumps(summary)}")
    return summary


def _count(xs):
    out = defaultdict(int)
    for x in xs:
        out[str(x)] += 1
    return out


# ---------------------------------------------------------------- quotes (cache-only)

def quotes(man: dict, log) -> list[dict]:
    fetcher = Fetcher(cache_only=True)
    rows = []
    for e in man["events"]:
        for c in e["contracts"]:
            url, params, hist = candle_request(c)
            try:
                raw = fetcher.get("kalshi", url, params)
                if raw.ok:
                    q = quote_at_cutoff(raw.json().get("candlesticks") or [], c["cutoff_ts"])
                else:
                    q = {"status": f"candles http {raw.status}"}
            except CacheMiss:
                q = {"status": "not fetched (budget, retry cap or interruption)"}
            rows.append({"competition": e["competition"], "source": e["source"], "family": e["family"],
                         "period": e["period"], "tier": e["tier"], "cluster": e["cluster"], "ticker": c["ticker"],
                         "cutoff_ts": c["cutoff_ts"], "stratum": "phase2_sample", **q})
    log(f"quotes: {len(rows)} sampled contracts, ok={sum(1 for r in rows if r['status'] == 'ok')}")
    return rows

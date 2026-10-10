"""Phase 2 stages: oof -> sample -> fetch -> select (freeze protocol) -> evaluate -> report.

Labels are joined only after the forecasts they score are on disk in a write-once journal. The
protocol (rules, fitted calibration/blend parameters, coverage and market-support tables) is
frozen by ``select`` before any 2026 scoring; ``evaluate`` refuses to run if the Phase 2 code,
Phase 1 code or Phase 1 artifacts changed since.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .. import evaluate as EV
from .. import manifest as MAN
from .. import models as M
from ..core import PREDICTIONS, PROTOCOLS, RESULTS, read_jsonl, sha256_bytes, sha256_file, sha256_json, \
    utc_now_iso, write_json, write_jsonl, write_once_jsonl
from . import calib as CAL
from . import config as C
from . import folds as F
from . import market as MK

CODE_FILES2 = ["config.py", "folds.py", "calib.py", "market.py", "pipeline.py"]
CAND_ORDER = ["frozen", "calibrated", "market", "blend"]


def code_hash2() -> str:
    here = Path(__file__).parent
    return sha256_bytes(b"".join(sha256_file(here / f).encode() for f in CODE_FILES2))


def out_dir(protocol: str) -> Path:
    return C.RESULTS2 / protocol


def protocol_path(protocol: str) -> Path:
    return PROTOCOLS / f"{protocol}.json"


def require_phase1(log) -> dict:
    """Phase 1 code and v2 research artifacts must be exactly as frozen."""
    if EV.code_hash() != C.PHASE1_CODE_HASH:
        raise SystemExit("Phase 1 code hash changed; Phase 2 depends on the frozen Phase 1 models")
    d = MAN.verify_detail(C.PHASE1_PROTOCOL)
    if d["research"]:
        raise SystemExit(f"{C.PHASE1_PROTOCOL} research artifacts fail exact verification: {d['research'][:3]}")
    log(f"Phase 1 preserved: code hash pinned, {C.PHASE1_PROTOCOL} research artifacts verified (exact bytes)")
    return EV.load_protocol(C.PHASE1_PROTOCOL)


def phase1_entry(proto1: dict, comp):
    e = proto1["competitions"].get(comp.key)
    if e is None:
        return None, f"not in {C.PHASE1_PROTOCOL}"
    if e.get("blocked"):
        return None, f"blocked in Phase 1: {e['blocked']}"
    if e["params"].get("blocked"):
        return None, f"blocked in Phase 1: {e['params']['blocked']}"
    return e, None


def labels_for(comp, contracts, gate) -> dict:
    """ticker -> label record under the Phase 1 v2 cohort and exclusion rules."""
    out = {}
    for x in contracts:
        if x["status"] != "ok":
            continue
        if comp.source == "kalshi_only":
            cohort = "kalshi_settlement_only"
        else:
            g = gate.get(x["series"])
            if not g or g["cohort"] == "failed" or x.get("agree") == 0:
                continue
            cohort = g["cohort"]
        out[x["ticker"]] = {"y": int(x["kalshi_outcome"] == "yes"), "cohort": cohort, "series": x["series"]}
    return out


def oof_path(protocol, key) -> Path:
    return C.OOF_DIR / protocol / f"{key}.jsonl"


def frozen_eval_rows(key: str, contracts) -> list[dict]:
    """Primary Phase 1 forecast per 2026 contract, read verbatim from the v2 journal."""
    fam = {c["ticker"]: c["family"] for c in contracts}
    by = defaultdict(list)
    p = PREDICTIONS / C.PHASE1_PROTOCOL / f"{key}.jsonl"
    if not p.exists():
        return []
    for r in read_jsonl(p):
        by[r["ticker"]].append(r)
    out = []
    for t in sorted(by):
        r = F.primary_row(by[t], fam[t])
        if r is not None:
            out.append({"ticker": t, "fold": "EVAL", "model": r["model"], "p": r["p"], "cluster": r["cluster"],
                        "start": r["start"], "cutoff": r["cutoff"], "family": fam[t]})
    return out


# ---------------------------------------------------------------- stage: oof

def stage_oof(protocol, comps, log):
    proto1 = require_phase1(log)
    status = {}
    for c in comps:
        e, why = phase1_entry(proto1, c)
        if e is None:
            status[c.key] = {"status": "unavailable", "reason": why}
            continue
        summ, contracts, events = EV.load_comp(c.key)
        if {"contracts": summ.get("hash_contracts"), "events": summ.get("hash_events")} != e["data"]:
            raise SystemExit(f"{c.key}: normalized data differ from {C.PHASE1_PROTOCOL}")
        rows, fst = [], {}
        for f in C.FOLDS:
            st, r = F.run_fold(c, events, contracts, f)
            fst[f] = st
            rows += r
        jh = write_once_jsonl(oof_path(protocol, c.key), [{**r, "protocol": protocol} for r in rows])
        status[c.key] = {"status": "ok", "sport": c.sport, "source": c.source, "folds": fst, "rows": len(rows),
                         "journal_sha256": jh}
        log(f"oof {c.key}: " + " ".join(f"{f}={fst[f]['status']}:{fst[f].get('oof_rows', 0)}" for f in C.FOLDS))
    write_json(out_dir(protocol) / "oof_status.json", status)


# ---------------------------------------------------------------- stage: sample

def _tier_sort(e):
    return ([t for t, _, _ in C.TIERS].index(e["tier"]), e["order"], e["competition"], e["family"], e["period"])


def verify_sample(protocol, events, elig, lsha, v1sha, log) -> dict:
    """Re-derive the label-free sample and check it against the frozen manifest (never re-freeze).

    The budget split is a record of the cache state at freeze time; when no tier was truncated the
    frozen events must equal the complete re-derived sample.
    """
    man = json.loads(MK.manifest_path(protocol).read_text(encoding="utf-8"))
    if man["manifest_sha256"] != sha256_json(man["events"]):
        raise SystemExit("frozen sample manifest events do not match their recorded hash")
    problems = []
    if man["listing_map_sha256"] != lsha:
        problems.append("listing map differs")
    if man["eligibility"] != json.loads(json.dumps(elig)):
        problems.append("eligibility counts differ")
    if man["preserved_phase1_sample"]["sha256"] != v1sha:
        problems.append("preserved Phase 1 sample differs")
    tiers = man["truncation"]["tiers"]
    if any(r.get("truncated") or r.get("dropped") for r in tiers.values()):
        problems.append("frozen sample was truncated; re-derivation compares candidates only")
    else:
        full = sorted(({**e, "tier": MK.tier_of(e["family"], e["period"])} for e in events
                       if MK.tier_of(e["family"], e["period"])), key=_tier_sort)
        if sha256_json(full) != man["manifest_sha256"]:
            problems.append("re-derived events differ from the frozen manifest")
    if problems:
        raise SystemExit(f"sample re-derivation failed: {problems}")
    log(f"sample re-derived from cache and identical to the frozen manifest ({len(man['events'])} events)")
    return man


def stage_sample(protocol, comps, log) -> dict:
    proto1 = require_phase1(log)
    per_comp, series = [], set()
    for c in comps:
        e, why = phase1_entry(proto1, c)
        if e is None:
            continue
        _, contracts, _ = EV.load_comp(c.key)
        series |= {x["series"] for x in contracts if x["status"] == "ok" and x["family"] in C.MARKET_FAMILIES}
        per_comp.append((c, e, contracts))
    listing, uncached_series = MK.build_listing_map(sorted(series), log)
    lpath = MK.market_dir(protocol) / "listing_times.json"
    lsha = write_json(lpath, {"rule": C.ELIGIBILITY, "uncached_series": uncached_series, "listing_ts": listing})
    events, elig = [], {}
    for c, e, contracts in per_comp:
        fc = {r["ticker"] for r in read_jsonl(oof_path(protocol, c.key))} if oof_path(protocol, c.key).exists() else set()
        fc |= {r["ticker"] for r in frozen_eval_rows(c.key, contracts)}
        cand, why = MK.candidate_events(c, contracts, fc, e.get("gate") or {}, listing)
        elig[c.key] = {"counts": why, "candidate_events": {f"{f}|{p}": len(v) for (f, p), v in sorted(cand.items())}}
        events += MK.sample_competition(c, cand)
    v1q = RESULTS / "sports_phase1_v1" / "benchmark_quotes.jsonl"
    if MK.manifest_path(protocol).exists():
        return verify_sample(protocol, events, elig, lsha, sha256_file(v1q), log)
    kept, budget_rep = MK.apply_budget(events, C.BUDGET["max_unique_requests"], MK.is_cached)
    kept.sort(key=_tier_sort)
    v1_listing = Counter()
    for q in read_jsonl(v1q):
        lt = listing.get(q["ticker"])
        v1_listing["unknown" if lt is None else "listed_by_cutoff" if lt <= q["cutoff_ts"] else "listed_after_cutoff"] += 1
    body = {"protocol": protocol, "policy": {"seed": C.SAMPLE_SEED, "event_caps": C.EVENT_CAPS,
                                             "periods": C.SAMPLE_PERIOD, "contract_rules": C.CONTRACT_RULES,
                                             "eligibility": C.ELIGIBILITY, "tiers": C.TIERS, "quote": C.QUOTE,
                                             "families": C.MARKET_FAMILIES, "no_new_quotes": C.NO_NEW_QUOTES},
            "listing_map_sha256": lsha, "eligibility": elig,
            "budget": {**C.BUDGET, "frozen_unique_requests": budget_rep["uncached_requests"]},
            "truncation": budget_rep, "events": kept, "manifest_sha256": sha256_json(kept),
            "preserved_phase1_sample": {"source": "data/results/sports_phase1/sports_phase1_v1/benchmark_quotes.jsonl",
                                        "sha256": sha256_file(v1q), "listing_status": dict(v1_listing),
                                        "note": "kept unchanged as a separate stratum; not re-sampled"},
            "estimate": MK.runtime_estimate(budget_rep["uncached_requests"])}
    man = MK.freeze_manifest(protocol, body, log)
    est = man["estimate"]
    log(f"ESTIMATE: {est['unique_requests']} uncached unique requests (budget {C.BUDGET['max_unique_requests']}, "
        f"retry cap {C.BUDGET['max_retries_total']}); lower bound {est['lower_bound_s'] / 3600:.2f} h, expected "
        f"{est['expected_s'] / 3600:.2f} h (median latency {est['median_latency_s']} s)")
    for t, r in man["truncation"]["tiers"].items():
        log(f"  tier {t}: {r}")
    return man


# ---------------------------------------------------------------- stage: select (freeze protocol)

def _quote_map(rows) -> dict:
    return {r["ticker"]: r["mid"] for r in rows if r["status"] == "ok"}


def matched_rows(rows, quotes: dict) -> list[dict]:
    """Rows usable by a market-assisted model: a frozen forecast and a valid cutoff quote for the same contract."""
    return [{**r, "p_mkt": quotes[r["ticker"]]} for r in rows if r.get("p") is not None and r["ticker"] in quotes]


def build_quotes(protocol, log) -> list[dict]:
    man = json.loads(MK.manifest_path(protocol).read_text(encoding="utf-8"))
    rows = MK.quotes(man, log)
    listing = json.loads((MK.market_dir(protocol) / "listing_times.json").read_text(encoding="utf-8"))["listing_ts"]
    for q in read_jsonl(RESULTS / "sports_phase1_v1" / "benchmark_quotes.jsonl"):
        lt = listing.get(q["ticker"])
        rows.append({"competition": q["competition"], "family": "game_winner", "period": "evaluation",
                     "tier": "phase1_v1", "cluster": q["cluster"], "ticker": q["ticker"], "cutoff_ts": q["cutoff_ts"],
                     "stratum": "phase1_v1_sample",
                     "listing": "unknown" if lt is None else "by_cutoff" if lt <= q["cutoff_ts"] else "after_cutoff",
                     **{k: q.get(k) for k in ("status", "candle_end", "age_s", "bid", "ask", "mid") if k in q}})
    return rows


def stage_select(protocol, comps, log):
    proto1 = require_phase1(log)
    path = protocol_path(protocol)
    od = out_dir(protocol)
    oof_status = json.loads((od / "oof_status.json").read_text(encoding="utf-8"))
    qrows = build_quotes(protocol, log)
    qsha = write_jsonl(od / "market_quotes.jsonl", qrows)
    p2q = _quote_map([q for q in qrows if q["stratum"] == "phase2_sample" and q["period"] in ("train", "validation")])
    groups, support = {}, defaultdict(lambda: defaultdict(Counter))
    for q in qrows:
        s = support[(q["competition"], q["family"])][f"{q['stratum']}|{q['period']}"]
        s["contracts"] += 1
        s["ok"] += q["status"] == "ok"
    for c in comps:
        e, why = phase1_entry(proto1, c)
        if e is None:
            continue
        _, contracts, _ = EV.load_comp(c.key)
        lab = labels_for(c, contracts, e.get("gate") or {})
        rows = [r for r in read_jsonl(oof_path(protocol, c.key))] if oof_path(protocol, c.key).exists() else []
        by_fam = defaultdict(list)
        for r in rows:
            if r["ticker"] in lab:
                by_fam[r["family"]].append({**r, **lab[r["ticker"]]})
        fams = sorted({x["family"] for x in contracts if x["status"] == "ok"})
        for fam in fams:
            rs = by_fam.get(fam, [])
            tr = [r for r in rs if r["fold"] in ("F1", "F2")]
            va = [r for r in rs if r["fold"] == "F3"]
            g = {"competition": c.key, "sport": c.sport, "source": c.source, "family": fam,
                 "calibration": CAL.select_calibration(tr, va, rs)}
            if fam in C.MARKET_FAMILIES:
                mt, mv = matched_rows(tr, p2q), matched_rows(va, p2q)
                g["blend"] = CAL.select_blend(mt, mv, mt + mv)
            else:
                g["blend"] = {"status": f"unavailable: {C.NO_NEW_QUOTES.get(fam, 'no market quotes')}"}
            groups[f"{c.key}|{fam}"] = g
        log(f"select {c.key}: " + " ".join(f"{f}:cal={groups[f'{c.key}|{f}']['calibration']['status'][:11]}"
                                            f"/blend={groups[f'{c.key}|{f}']['blend']['status'][:11]}" for f in fams))
    body = {
        "protocol": protocol, "research_only": True, "no_bet": True,
        "phase1": {"protocol": C.PHASE1_PROTOCOL, "protocol_file_sha256": sha256_file(protocol_path(C.PHASE1_PROTOCOL)),
                   "code_hash": C.PHASE1_CODE_HASH, "frozen": "Phase 1 models, protocols and results are read-only"},
        "code_hash_phase2": code_hash2(),
        "periods": C.PERIODS, "roles": C.ROLE, "evaluation_status": C.EVAL_NOTE,
        "boundary": {"rule": "fold parameters selected with training window ending at fold_start - horizon - "
                             "(result_lag_hours + extra); every selection label verified available by the fold's "
                             "first cutoff", "extra_lag_hours": C.BOUNDARY_EXTRA_LAG_H},
        "candidates": C.CANDIDATES, "calibration_group": C.CAL_GROUP, "calibration": C.CAL, "blend": C.BLEND,
        "cohorts": EV.COHORT_RULES, "labels": "Kalshi settlement; Phase 1 v2 cohort and contract exclusions",
        "comparisons": [{"a": a, "b": b, "rows": r, "difference": "a - b (negative = a better)"} for a, b, r in C.COMPARISONS],
        "primary_metric": "Brier (continuity with Phase 1); log loss reported alongside",
        "min_matched_clusters": C.MIN_MATCHED, "sufficiency": C.SUFFICIENCY_RULE, "bootstrap": C.BOOTSTRAP2,
        "interpretation": EV.INTERPRETATION,
        "market_sample": {"manifest_sha256": json.loads(MK.manifest_path(protocol).read_text(encoding="utf-8"))["manifest_sha256"],
                          "quotes_sha256": qsha, "quote_rule": C.QUOTE, "no_new_quotes": C.NO_NEW_QUOTES},
        "oof_journals": {k: v.get("journal_sha256") for k, v in sorted(oof_status.items()) if v.get("journal_sha256")},
        "capture": C.CAPTURE,
        "groups": groups,
        "market_support": {f"{k[0]}|{k[1]}": {s: dict(v) for s, v in sorted(d.items())}
                           for k, d in sorted(support.items())},
    }
    body = json.loads(json.dumps(body, default=str))
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if {k: v for k, v in old.items() if k != "frozen_at"} != body:
            raise SystemExit(f"{path.name} is frozen and the recomputed content differs; bump the protocol version")
        log("protocol unchanged")
        return old
    body["frozen_at"] = utc_now_iso()
    write_json(path, body)
    log(f"protocol frozen {path}")
    return body


# ---------------------------------------------------------------- stage: evaluate (retrospective 2026)

def _label_index(keys: set) -> dict:
    """ticker -> (y, cohort, series, sport) from the v2 scored rows (same exclusions as Phase 1)."""
    out = {}
    with open(RESULTS / C.PHASE1_PROTOCOL / "scored.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["competition"] in keys and r["ticker"] not in out:
                out[r["ticker"]] = (r["y"], r["cohort"], r["series"], r["sport"])
    return out


def compare(a: list[dict], b: list[dict], family: str, seed: int) -> dict:
    bm = {r["ticker"]: r for r in b}
    A = [r for r in a if r["ticker"] in bm]
    B = [bm[r["ticker"]] for r in A]
    k = len({r["cluster"] for r in A})
    need = C.MIN_MATCHED.get(family, C.MIN_MATCHED["default"])
    out = {"matched_contracts": len(A), "matched_clusters": k, "min_clusters": need, "sufficient": k >= need}
    if not A:
        return out
    ma, mb = EV.metrics(A), EV.metrics(B)
    for m in (ma, mb):
        m.pop("reliability", None)
    out["a"], out["b"] = ma, mb
    d = EV.paired_bootstrap(A, B, seed)
    if not out["sufficient"]:
        d = {f: v for f, v in d.items() if not f.endswith("_ci")}
    else:
        out["class"] = {w: EV.classify(d[f"{x}_ci"]) for w, x in
                        (("brier_contract", "d_brier"), ("brier_event", "d_brier_event"),
                         ("log_loss_contract", "d_log_loss"), ("log_loss_event", "d_log_loss_event"))}
    out["diff"] = d
    return out


def stage_evaluate(protocol, comps, log):
    path = protocol_path(protocol)
    if not path.exists():
        raise SystemExit("Phase 2 protocol not frozen; run the select stage first")
    proto = json.loads(path.read_text(encoding="utf-8"))
    if proto["code_hash_phase2"] != code_hash2():
        raise SystemExit("Phase 2 code changed since the protocol was frozen; bump the protocol version")
    require_phase1(log)
    od = out_dir(protocol)
    qrows = list(read_jsonl(od / "market_quotes.jsonl"))
    if sha256_file(od / "market_quotes.jsonl") != proto["market_sample"]["quotes_sha256"]:
        raise SystemExit("market quotes differ from the frozen protocol")
    eval_q = {}
    for q in qrows:
        if q["period"] == "evaluation" and q["status"] == "ok":
            eval_q.setdefault(q["ticker"], {})[q["stratum"]] = q["mid"]
    keys = {c.key for c in comps}
    cand_rows = defaultdict(list)
    status = {}
    for c in comps:
        _, contracts, _ = EV.load_comp(c.key) if (PREDICTIONS / C.PHASE1_PROTOCOL / f"{c.key}.jsonl").exists() else (None, [], [])
        frozen = frozen_eval_rows(c.key, contracts) if contracts else []
        if not frozen:
            status[c.key] = {"status": "no 2026 frozen forecasts"}
            continue
        jrows = []
        for r in frozen:
            g = proto["groups"].get(f"{c.key}|{r['family']}", {})
            base = {k: r[k] for k in ("ticker", "cluster", "start", "cutoff", "family")}
            jrows.append({**base, "candidate": "frozen", "model": r["model"], "p": r["p"]})
            cal = g.get("calibration", {})
            if cal.get("status") == "ok":
                jrows.append({**base, "candidate": "calibrated", "p": CAL.apply_calibration(cal["params"], r["p"])})
            for stratum, mid in sorted((eval_q.get(r["ticker"]) or {}).items()):
                jrows.append({**base, "candidate": "market", "stratum": stratum, "p": mid})
                bl = g.get("blend", {})
                if bl.get("status") == "ok":
                    jrows.append({**base, "candidate": "blend", "stratum": stratum,
                                  "p": CAL.apply_blend(bl["params"], r["p"], mid)})
        jrows.sort(key=lambda x: (x["ticker"], CAND_ORDER.index(x["candidate"]), x.get("stratum", "")))
        jh = write_once_jsonl(C.JOURNALS / protocol / f"{c.key}.jsonl", [{**x, "protocol": protocol} for x in jrows])
        status[c.key] = {"status": "journal written", "journal_sha256": jh, "rows": len(jrows)}
        for x in jrows:
            cand_rows[c.key].append(x)
    # labels are read only after every journal is on disk
    lab = _label_index(keys)
    scored = []
    for key in sorted(cand_rows):
        comp = next(c for c in comps if c.key == key)
        n = Counter()
        for x in cand_rows[key]:
            L = lab.get(x["ticker"])
            if L is None:
                n["not scored in Phase 1 v2 (excluded contract)"] += 1
                continue
            scored.append({"competition": key, "sport": comp.sport, "source": comp.source, "family": x["family"],
                           "series": L[2], "cohort": L[1], "ticker": x["ticker"], "cluster": x["cluster"],
                           "candidate": x["candidate"], "stratum": x.get("stratum", "all"), "p": x["p"], "y": L[0]})
            n[x["candidate"]] += 1
        status[key]["scored_rows"] = dict(n)
    write_jsonl(od / "scored.jsonl", scored)
    write_json(od / "evaluation_status.json", status)
    summarize(scored, od, log)


def summarize(scored, od, log):
    groups = defaultdict(lambda: defaultdict(list))
    for r in scored:
        for level, key in (("competition", r["competition"]), ("sport_pool", r["sport"])):
            groups[(level, key, r["family"], r["cohort"])][(r["candidate"], r["stratum"])].append(r)
    results = []
    for (level, key, fam, cohort), d in sorted(groups.items()):
        rec = {"level": level, "key": key, "family": fam, "cohort": cohort, "comparisons": {}}
        frozen_all = d.get(("frozen", "all"), [])
        rec["n_frozen"] = len(frozen_all)
        for i, (a, b, _) in enumerate(C.COMPARISONS):
            strata = ["all"] if (a, b) == ("calibrated", "frozen") else ["phase2_sample", "phase1_v1_sample"]
            for s in strata:
                A = d.get((a, s if a in ("market", "blend") else "all"), [])
                B = d.get((b, s if b in ("market", "blend") else "all"), [])
                if s != "all":
                    mk = {r["ticker"] for r in d.get(("market", s), [])}
                    A = [r for r in A if r["ticker"] in mk]
                    B = [r for r in B if r["ticker"] in mk]
                if not A or not B:
                    continue
                seed = C.BOOTSTRAP2["seed"] + M.seed_of(level, key, fam, cohort, a, b, s) % 100000
                rec["comparisons"][f"{a}-{b}|{s}"] = compare(A, B, fam, seed)
        results.append(rec)
    write_json(od / "metrics.json", results)
    log(f"phase2 summarized {len(results)} groups")

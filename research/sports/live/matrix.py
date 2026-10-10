"""Offline live-availability matrix (no network).

* competition x family (all 313 competitions of the supported universe): live adapter, frozen
  primary model, calibration / blend maps, market candidate, comparability;
* series level: every one of the 3,871 inventory series (joined through the Phase 2 coverage table)
  is ``supported``, ``unsupported`` (with the reason) or ``unattempted`` (never in scope).
"""

from __future__ import annotations

import collections
import csv
import json
from pathlib import Path

from .. import evaluate as EV
from ..competitions import all_competitions
from ..contracts import family as family_of
from ..core import NORMALIZED, PROTOCOLS, REPO_ROOT, read_jsonl
from ..phase2 import config as P2
from . import config as C

DOCS = REPO_ROOT / "docs" / "research"
COVERAGE_CSV = DOCS / "sports_phase2_coverage_v1.csv"
SERIES_CSV = DOCS / "sports_capture_v2_series_matrix.csv"
COMP_CSV = DOCS / "sports_capture_v2_matrix.csv"


def primary_model(comp, entry, kind: str, seg) -> tuple[str | None, str | None]:
    """(model, unavailability reason) under phase2.folds.primary_row for this contract kind."""
    prm = (entry or {}).get("params") or {}
    fam = family_of(kind, seg)
    if comp.source == "kalshi_only":
        return "elo", None
    if comp.source == "espn_fight":
        return ("elo", None) if kind == "winner" else ("score", None)
    if comp.source in ("espn_golf", "jolpica"):
        return "score", None
    if fam == "game_winner" and prm.get("elo"):
        return "elo", None
    sc = (prm.get("scopes") or {}).get(seg or "full") or {}
    if "model" in sc:
        return "score", None
    return None, f"frozen {seg or 'full'} score model not fitted ({sc.get('blocked', 'scope absent')})"


def _gate_cohort(comp, entry, series):
    if comp.source == "kalshi_only":
        return "kalshi_settlement_only"
    g = ((entry or {}).get("gate") or {}).get(series)
    return g.get("cohort") if g else None


def series_status(comp, entry, series, kind, seg) -> tuple[str, str]:
    ad = C.ADAPTERS[comp.source]
    if ad["status"] != "supported":
        return "unsupported", f"live adapter unsupported: {ad['reason']}"
    if entry is None or entry.get("blocked") or (entry.get("params") or {}).get("blocked"):
        why = (entry or {}).get("blocked") or ((entry or {}).get("params") or {}).get("blocked") or "absent"
        return "unsupported", f"no unblocked {C.PHASE1_PROTOCOL} model ({why})"
    cohort = _gate_cohort(comp, entry, series)
    if cohort in (None, "failed"):
        return "unsupported", "series failed or unreconciled in the sports_phase1_v2 gate (never scored)"
    model, why = primary_model(comp, entry, kind, seg)
    if model is None:
        return "unsupported", why
    note = " (prediction-time field; not exactly comparable)" if ad["comparability"] != "exact" else ""
    return "supported", f"frozen {model} model{note}"


def build(log=print) -> tuple[list[dict], list[dict]]:
    comps, specs, excl = all_competitions()
    proto1 = EV.load_protocol(C.PHASE1_PROTOCOL)
    groups = json.loads((PROTOCOLS / f"{C.PARENT_PROTOCOL}.json").read_text(encoding="utf-8"))["groups"]
    spec_of, excl_of = {}, {}
    for c in comps:
        for s, kind, seg in specs[c.key]:
            spec_of[s] = (c, kind, seg)
        for x in excl[c.key]:
            excl_of[x["series"]] = (c, x["reason"])
    comp_rows = []
    fam_series = collections.defaultdict(lambda: collections.defaultdict(list))
    for s, (c, kind, seg) in spec_of.items():
        fam_series[c.key][family_of(kind, seg)].append((s, kind, seg))
    for c in comps:
        entry = proto1["competitions"].get(c.key)
        n_ok = collections.Counter()
        p = NORMALIZED / c.key / "contracts.jsonl"
        if p.exists():
            for x in read_jsonl(p):
                if x["status"] == "ok":
                    n_ok[x["family"]] += 1
        for fam in sorted(fam_series[c.key]):
            ss = fam_series[c.key][fam]
            st = collections.Counter(series_status(c, entry, s, k, g)[0] for s, k, g in ss)
            reasons = sorted({series_status(c, entry, s, k, g)[1] for s, k, g in ss})
            g = groups.get(f"{c.key}|{fam}") or {}
            cal = (g.get("calibration") or {}).get("status", f"no {C.PARENT_PROTOCOL} group")
            bl = (g.get("blend") or {}).get("status", f"no {C.PARENT_PROTOCOL} group")
            mk = ("candle rule at prediction_as_of (frozen per-event contract rule)" if fam in C.MARKET_FAMILIES
                  else "unavailable: " + P2.NO_NEW_QUOTES.get(fam, "family outside the market scope"))
            ad = C.ADAPTERS[c.source]
            comp_rows.append({
                "competition": c.key, "sport": c.sport, "source": c.source, "family": fam, "series": len(ss),
                "series_supported": st["supported"], "series_unsupported": st["unsupported"],
                "live_adapter": ad["status"], "comparability": ad.get("comparability", ""),
                "frozen": "available" if st["supported"] else "unavailable",
                "frozen_detail": "; ".join(reasons),
                "calibrated": "available" if cal == "ok" and st["supported"] else "unavailable",
                "calibration_status": cal,
                "market": "available" if fam in C.MARKET_FAMILIES and st["supported"] else "unavailable",
                "market_rule": mk,
                "blend": "available" if bl == "ok" and st["supported"] else "unavailable", "blend_status": bl,
                "historical_ok_contracts": n_ok.get(fam, 0)})
    series_rows = []
    with open(COVERAGE_CSV, encoding="utf-8") as f:
        cov = list(csv.DictReader(f))
    for r in cov:
        s = r["series_ticker"]
        out = {"series_ticker": s, "sport": r["sport"], "competition_top": r["competition_top"],
               "contract_family": r["contract_family"], "phase1_coverage_status": r["phase1_coverage_status"]}
        if s in spec_of:
            c, kind, seg = spec_of[s]
            entry = proto1["competitions"].get(c.key)
            status, why = series_status(c, entry, s, kind, seg)
            fam = family_of(kind, seg)
            g = groups.get(f"{c.key}|{fam}") or {}
            out.update({"competition": c.key, "source": c.source, "family": fam, "live_status": status,
                        "live_reason": why, "comparability": C.ADAPTERS[c.source].get("comparability", ""),
                        "calibration_status": (g.get("calibration") or {}).get("status", ""),
                        "blend_status": (g.get("blend") or {}).get("status", ""),
                        "market_candidate": "candle rule" if fam in C.MARKET_FAMILIES else "out of market scope"})
        elif s in excl_of:
            c, why = excl_of[s]
            out.update({"competition": c.key, "source": c.source, "family": "", "live_status": "unsupported",
                        "live_reason": f"no payoff parser / spec: {why}", "comparability": "",
                        "calibration_status": "", "blend_status": "", "market_candidate": ""})
        else:
            out.update({"competition": "", "source": "", "family": "", "live_status": "unattempted",
                        "live_reason": r["phase1_reason"] or r["phase1_coverage_status"], "comparability": "",
                        "calibration_status": "", "blend_status": "", "market_candidate": ""})
        series_rows.append(out)
    return comp_rows, series_rows


def write(comp_rows, series_rows) -> None:
    for path, rows in ((COMP_CSV, comp_rows), (SERIES_CSV, series_rows)):
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)


def summarize(comp_rows, series_rows, log) -> None:
    comps = {r["competition"] for r in comp_rows}
    by_src = collections.Counter()
    for k in comps:
        by_src[next(r["source"] for r in comp_rows if r["competition"] == k)] += 1
    log(f"competitions: {len(comps)} by source {dict(by_src)}")
    log(f"competition x family rows: {len(comp_rows)}")
    for cand in ("frozen", "calibrated", "market", "blend"):
        n = sum(1 for r in comp_rows if r[cand] == "available")
        log(f"  {cand:<10} available in {n} / {len(comp_rows)} competition x family groups")
    st = collections.Counter(r["live_status"] for r in series_rows)
    log(f"series: {len(series_rows)} -> {dict(st)}")
    reasons = collections.Counter(r["live_reason"] for r in series_rows if r["live_status"] == "unsupported")
    for why, n in reasons.most_common(8):
        log(f"  unsupported {n:>4}: {why[:110]}")
    fam = collections.Counter((r["family"], r["live_status"]) for r in series_rows if r["family"])
    for (f, s), n in sorted(fam.items()):
        log(f"  family {f:<13} {s:<12} {n}")
    log("unsupported live adapters: " + ", ".join(f"{k} ({v['reason'][:60]}...)" for k, v in C.ADAPTERS.items()
                                                 if v["status"] != "supported"))
    log("note: five-league game-winner coverage is a subset; this matrix is the complete live scope")
    for r in comp_rows:
        if r["calibrated"] == "available" or r["blend"] == "available":
            log(f"  fitted maps: {r['competition']}|{r['family']} calibration={r['calibration_status']} "
                f"blend={r['blend_status']}")


def stage_matrix(log, write_files: bool = True) -> tuple[list[dict], list[dict]]:
    comp_rows, series_rows = build(log)
    if write_files:
        write(comp_rows, series_rows)
        log(f"wrote {Path(COMP_CSV).name} ({len(comp_rows)} rows), {Path(SERIES_CSV).name} ({len(series_rows)} rows)")
    summarize(comp_rows, series_rows, log)
    return comp_rows, series_rows

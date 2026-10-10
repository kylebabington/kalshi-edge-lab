"""Phase 2 documentation: results summary, per-series coverage across the whole universe, results CSV.

Output is deterministic (no wall-clock timestamps) so cache-only reruns reproduce it byte-for-byte.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict

from .. import manifest as MAN
from ..core import NORMALIZED, REPO_ROOT, read_jsonl, sha256_file, sha256_json, write_json
from . import config as C
from . import market as MK
from .pipeline import out_dir, protocol_path

DOCS = REPO_ROOT / "docs" / "research"
COV_DOC = "sports_phase2_coverage_v1.csv"
RES_DOC = "sports_phase2_results_v1.csv"
MD_DOC = "sports_phase2_results_v1.md"
P1_COVERAGE = DOCS / "sports_phase1_coverage_v2.csv"
CMP_KEYS = ["calibrated-frozen|all", "frozen-market|phase2_sample", "blend-market|phase2_sample",
            "blend-frozen|phase2_sample", "frozen-market|phase1_v1_sample", "blend-market|phase1_v1_sample",
            "blend-frozen|phase1_v1_sample"]
COHORTS = ["strict", "exploratory", "kalshi_settlement_only"]


def _csv_bytes(header, rows) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue().encode("utf-8")


def _fmt(x, nd=4):
    return "" if x is None else f"{x:+.{nd}f}" if isinstance(x, float) else str(x)


def series_families() -> dict:
    out = {}
    for d in sorted(NORMALIZED.iterdir()):
        p = d / "contracts.jsonl"
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                i = line.find('"series":"')
                if i < 0:
                    continue
                s = line[i + 10:line.index('"', i + 10)]
                if (d.name, s) not in out:
                    c = json.loads(line)
                    out[(d.name, s)] = c["family"]
    return out


def ledger_summary(protocol) -> dict:
    lp = MK.ledger_path(protocol)
    if not lp.exists():
        return {"ledger_entries": 0}
    rows = list(read_jsonl(lp))
    return {"ledger_entries": len(rows), "unique_network_requests": sum(1 for r in rows if r.get("network")),
            "retries": sum(r.get("retries", 0) for r in rows),
            "statuses": dict(sorted(Counter(str(r["status"]) for r in rows).items()))}


def stage_report(protocol, log):
    od = out_dir(protocol)
    proto = json.loads(protocol_path(protocol).read_text(encoding="utf-8"))
    man = json.loads(MK.manifest_path(protocol).read_text(encoding="utf-8"))
    oof_status = json.loads((od / "oof_status.json").read_text(encoding="utf-8"))
    metrics = json.loads((od / "metrics.json").read_text(encoding="utf-8"))
    quotes = list(read_jsonl(od / "market_quotes.jsonl"))
    groups = proto["groups"]
    # OOF rows per (competition, family, fold)
    oof_n = Counter()
    for key, st in oof_status.items():
        p = C.OOF_DIR / protocol / f"{key}.jsonl"
        if p.exists():
            for r in read_jsonl(p):
                oof_n[(key, r["family"], r["fold"])] += 1
    sample_n = Counter((e["competition"], e["family"], e["period"]) for e in man["events"])
    quote_ok = defaultdict(set)
    for q in quotes:
        if q["status"] == "ok":
            quote_ok[(q["competition"], q["family"], q["stratum"], q["period"])].add(q["cluster"])
    eval_n = Counter()
    for r in read_jsonl(od / "scored.jsonl"):
        if r["candidate"] == "frozen":
            eval_n[(r["competition"], r["family"])] += 1
    comp_metrics = {(m["key"], m["family"], m["cohort"]): m for m in metrics if m["level"] == "competition"}

    # ---- coverage CSV over every series in the Phase 1 universe
    fam_of = series_families()
    with open(P1_COVERAGE, encoding="utf-8", newline="") as f:
        p1 = list(csv.DictReader(f))
    header = ["series_ticker", "sport", "competition_top", "contract_family", "phase1_coverage_status", "phase1_reason",
              "cohort", "competition", "phase2_family", "oof_F1", "oof_F2", "oof_F3", "calibration_status",
              "blend_status", "market_events_train", "market_events_validation", "market_events_evaluation",
              "quoted_events_train", "quoted_events_validation", "quoted_events_evaluation", "eval_frozen_contracts",
              "phase2_status"]
    rows, status_counts = [], Counter()
    for r in p1:
        comp, s = r["attempted_competition"], r["series_ticker"]
        fam = fam_of.get((comp, s)) if comp else None
        g = groups.get(f"{comp}|{fam}") if fam else None
        if r["coverage_status"] in ("not_attempted_supported_sport", "not_attempted_unsupported_sport",
                                    "excluded_spec", "excluded_gate_failed", "excluded_no_reconciled_contracts",
                                    "blocked", "no_test_contracts") or g is None:
            st = f"phase1:{r['coverage_status']}"
            cal = blend = ""
        else:
            cal, blend = g["calibration"]["status"], g["blend"]["status"]
            st = "calibrated_ok" if cal == "ok" else "calibration_unavailable"
            st += "+blend_ok" if blend == "ok" else "+blend_unavailable"
        status_counts[st] += 1
        k = (comp, fam)
        rows.append([s, r["sport"], r["competition_top"], r["contract_family"], r["coverage_status"], r["reason"],
                     r["cohort"], comp, fam, oof_n.get((comp, fam, "F1"), 0) if fam else "",
                     oof_n.get((comp, fam, "F2"), 0) if fam else "", oof_n.get((comp, fam, "F3"), 0) if fam else "",
                     cal, blend, sample_n.get((comp, fam, "train"), 0) if fam else "",
                     sample_n.get((comp, fam, "validation"), 0) if fam else "",
                     sample_n.get((comp, fam, "evaluation"), 0) if fam else "",
                     len(quote_ok.get((comp, fam, "phase2_sample", "train"), ())) if fam else "",
                     len(quote_ok.get((comp, fam, "phase2_sample", "validation"), ())) if fam else "",
                     len(quote_ok.get((comp, fam, "phase2_sample", "evaluation"), ())) if fam else "",
                     eval_n.get(k, 0) if fam else "", st])
    (DOCS / COV_DOC).write_bytes(_csv_bytes(header, rows))

    # ---- results CSV (every comparison at competition and sport-pool level)
    rh = ["level", "key", "family", "cohort", "comparison", "stratum", "matched_clusters", "matched_contracts",
          "sufficient", "d_brier", "d_brier_lo", "d_brier_hi", "d_brier_event", "d_brier_event_lo", "d_brier_event_hi",
          "d_log_loss", "d_log_loss_event", "class_brier_contract", "class_brier_event"]
    rr = []
    for m in metrics:
        for ck, v in sorted(m["comparisons"].items()):
            d = v.get("diff", {})
            cmp_, stratum = ck.split("|")
            rr.append([m["level"], m["key"], m["family"], m["cohort"], cmp_, stratum, v["matched_clusters"],
                       v["matched_contracts"], v["sufficient"], d.get("d_brier"), *(d.get("d_brier_ci") or [None, None]),
                       d.get("d_brier_event"), *(d.get("d_brier_event_ci") or [None, None]), d.get("d_log_loss"),
                       d.get("d_log_loss_event"), (v.get("class") or {}).get("brier_contract"),
                       (v.get("class") or {}).get("brier_event")])
    (DOCS / RES_DOC).write_bytes(_csv_bytes(rh, rr))

    # ---- markdown
    cal_st = Counter(g["calibration"]["status"] for g in groups.values())
    bl_st = Counter(g["blend"]["status"] for g in groups.values())
    cal_sel = Counter(g["calibration"]["selected"]["form"] for g in groups.values() if g["calibration"]["status"] == "ok")
    folds = Counter()
    for st in oof_status.values():
        for f, s in (st.get("folds") or {}).items():
            folds[(f, s["status"].split(":")[0])] += 1
    led = ledger_summary(protocol)
    qst = Counter((q["stratum"], q["period"], q["status"]) for q in quotes)
    lines = [f"# Sports Phase 2 results ({protocol})", "",
             "RESEARCH_ONLY / NO_BET. Retrospective development results; no trades, promotion or profitability claims.",
             "", f"> {C.EVAL_NOTE}", "",
             "## What is frozen", "",
             f"- Phase 1 models, protocols and results are read-only (Phase 1 code hash `{C.PHASE1_CODE_HASH[:16]}...`, "
             f"`{C.PHASE1_PROTOCOL}` research artifacts verified byte-exact before every stage).",
             f"- Phase 2 protocol `research/sports/protocols/{protocol}.json` (sha256 `{sha256_file(protocol_path(protocol))[:16]}...`) "
             "holds the periods, boundaries, candidate definitions, calibration/blend grids, optimizer, thresholds, "
             "tie-breaks, the fitted parameters per group, cohort rules, comparisons, sufficiency, bootstrap and the "
             "market-support table; it was written before any 2026 scoring.",
             f"- Market sample manifest sha256 `{man['manifest_sha256'][:16]}...` frozen before any request.", "",
             "## Periods", "",
             "| period | range (UTC) | role |", "|---|---|---|"]
    for p, (a, b) in C.PERIODS.items():
        lines.append(f"| {p} | {a[:10]} to {b[:10]} | {C.ROLE[p]} |")
    lines += ["", "## Coverage across the supported universe", "",
              f"Every one of the {len(p1)} Kalshi sports series in the Phase 1 universe appears in "
              f"`docs/research/{COV_DOC}` with its Phase 1 status and Phase 2 status.", "",
              "| Phase 2 status (per series) | series |", "|---|---|"]
    for k, v in sorted(status_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"| {k} | {v} |")
    lines += ["", "Out-of-fold folds per competition (all 313 attempted competitions):", "",
              "| fold | status | competitions |", "|---|---|---|"]
    for (f, s), v in sorted(folds.items()):
        lines.append(f"| {f} | {s} | {v} |")
    lines += ["", f"Calibration groups (competition x family): {len(groups)}.", "",
              "| calibration status | groups |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(cal_st.items(), key=lambda x: (-x[1], x[0]))]
    lines += ["", "Selected calibration forms (available groups): " +
              ", ".join(f"{k} {v}" for k, v in sorted(cal_sel.items())), "",
              "| competition | family | train events | validation events | form | lambda | a | b | "
              "F3 event-equal log loss: identity | selected |", "|---|---|---|---|---|---|---|---|---|---|"]
    for gk, g in sorted(groups.items()):
        cg = g["calibration"]
        if cg["status"] != "ok":
            continue
        ident = next(c["val_log_loss_event"] for c in cg["candidates"] if c["form"] == "identity")
        sel = next(c["val_log_loss_event"] for c in cg["candidates"] if c["status"] == "ok"
                   and c["form"] == cg["selected"]["form"] and c["lambda"] == cg["selected"]["lambda"])
        lines.append(f"| {g['competition']} | {g['family']} | {cg['train_support']['events']} | "
                     f"{cg['validation_support']['events']} | {cg['selected']['form']} | {cg['selected']['lambda']} | "
                     f"{cg['params']['a']:.4f} | {cg['params']['b']:.4f} | {ident:.5f} | {sel:.5f} |")
    lines += ["", "When identity is selected the calibrated candidate equals the frozen model and the "
              "calibrated-frozen difference is exactly zero.", "",
              "| blend status | groups |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(bl_st.items(), key=lambda x: (-x[1], x[0]))]
    ok_bl = [(gk, g) for gk, g in sorted(groups.items()) if g["blend"]["status"] == "ok"]
    if ok_bl:
        lines += ["", "| competition | family | matched train events | matched validation events | w (model) | "
                  "lambda | c |", "|---|---|---|---|---|---|---|"]
        for gk, g in ok_bl:
            b = g["blend"]
            lines.append(f"| {g['competition']} | {g['family']} | {b['train_support']['events']} | "
                         f"{b['validation_support']['events']} | {b['selected']['w']} | {b['selected']['lambda']} | "
                         f"{b['params']['c']:.4f} |")
    lines += ["", "No new historical quotes were fetched for segments, score props, field finish or player props; "
              "their market and blend candidates are unavailable with that reason.", "",
              "## Historical market sample and fetch", "",
              f"- Frozen events: {len(man['events'])}; frozen uncached unique requests: "
              f"{man['budget']['frozen_unique_requests']} (budget {man['budget']['max_unique_requests']}, retry cap "
              f"{man['budget']['max_retries_total']}, per-request retries {man['budget']['per_request_max_retries']}).",
              f"- Pre-fetch estimate: lower bound {man['estimate']['lower_bound_s'] / 3600:.2f} h at 0.5 s spacing "
              "(retries and slow responses extend it).",
              f"- Fetch ledger: {led.get('ledger_entries', 0)} entries, {led.get('unique_network_requests', 0)} unique "
              f"network requests, {led.get('retries', 0)} retries, statuses {led.get('statuses', {})}.",
              f"- Tier truncation: " + "; ".join(f"{t}: kept {r['events_kept']}/{r['events_candidate']}"
                                                  + (" (truncated)" if r.get("truncated") else "")
                                                  + (" (dropped)" if r.get("dropped") else "")
                                                  for t, r in man["truncation"]["tiers"].items()),
              f"- Preserved Phase 1 benchmark sample kept as its own stratum: listing status "
              f"{man['preserved_phase1_sample']['listing_status']}.", "",
              "| stratum | period | quote status | contracts |", "|---|---|---|---|"]
    lines += [f"| {s} | {p} | {q} | {v} |" for (s, p, q), v in sorted(qst.items())]
    # comparison classes at competition level (sufficient only)
    lines += ["", "## 2026 retrospective comparisons (competition level)", "",
              "Differences are a - b (negative = a better). Classes use the 95% cluster-bootstrap interval of the "
              "Brier difference and are exploratory and unadjusted for multiple comparisons. Only comparisons whose "
              f"final matched sample reaches the minimum ({C.MIN_MATCHED['default']} event clusters; field finish "
              f"{C.MIN_MATCHED['field_finish']}) are classified; the rest are descriptive.", "",
              "| comparison | stratum | cohort | groups | sufficient | contract-weighted better/unclear/worse | "
              "event-equal better/unclear/worse |", "|---|---|---|---|---|---|---|"]
    for ck in CMP_KEYS:
        for co in COHORTS:
            ms = [m["comparisons"][ck] for m in metrics if m["level"] == "competition" and m["cohort"] == co
                  and ck in m["comparisons"]]
            if not ms:
                continue
            suf = [v for v in ms if v["sufficient"]]
            cc = Counter(v["class"]["brier_contract"] for v in suf)
            ce = Counter(v["class"]["brier_event"] for v in suf)
            a, s = ck.split("|")
            lines.append(f"| {a} | {s} | {co} | {len(ms)} | {len(suf)} | {cc['better']}/{cc['unclear']}/{cc['worse']} "
                         f"| {ce['better']}/{ce['unclear']}/{ce['worse']} |")
    lines += ["", "Sufficient competition-level comparisons:", "",
              "| competition | family | cohort | comparison | stratum | clusters | d_brier (contract) [95% CI] | "
              "d_brier (event-equal) [95% CI] |", "|---|---|---|---|---|---|---|---|"]
    for m in metrics:
        if m["level"] != "competition":
            continue
        for ck in CMP_KEYS:
            v = m["comparisons"].get(ck)
            if not v or not v["sufficient"]:
                continue
            d = v["diff"]
            a, s = ck.split("|")
            lines.append(f"| {m['key']} | {m['family']} | {m['cohort']} | {a} | {s} | {v['matched_clusters']} | "
                         f"{_fmt(d['d_brier'])} [{_fmt(d['d_brier_ci'][0])}, {_fmt(d['d_brier_ci'][1])}] | "
                         f"{_fmt(d['d_brier_event'])} [{_fmt(d['d_brier_event_ci'][0])}, {_fmt(d['d_brier_event_ci'][1])}] |")
    lines += ["", "## Limitations", "",
              "- 2026 was examined in Phase 1; these are retrospective development results, not confirmation.",
              "- Kalshi sports contracts start in 2025, so out-of-fold training/validation data exist only for "
              "competitions with 2025 markets; elsewhere calibration and blends are unavailable (thresholds were not "
              "lowered).",
              "- Market references are sparse hourly candles (last candle ended by the cutoff, age <= 3 h); they are "
              "not executable prices.",
              "- Contracts are sampled per event with fixed caps, only from contracts listed by the cutoff (listing "
              "time = later of created_time and open_time from cached market pages). The preserved Phase 1 sample was "
              "drawn without a listing check; its listed-after-cutoff contracts are counted above.",
              "- Calibration and blends are fitted per competition x family; cohorts are reported separately but share "
              "one fitted map within a group.",
              f"- The frozen Kalshi-settlement-only training cap ({C.EVENT_CAPS['kalshi_only']['train']} events) equals "
              f"the blend training minimum ({C.BLEND['min_train']['events']} matched events), so one missing quote "
              "makes a Kalshi-only blend unavailable. Neither the cap nor the threshold was changed after sampling.",
              "- Event-equal and contract-weighted results coincide for game-winner market comparisons (one sampled "
              "contract per event); they can differ elsewhere.",
              "- No closing lines, later quotes or reconstructed injury information are used.", "",
              "## Prospective capture (manual only; no scheduler)", "",
              "```powershell",
              ".venv\\Scripts\\python.exe -m research.sports.phase2.run capture --mode checkpoint",
              ".venv\\Scripts\\python.exe -m research.sports.phase2.run capture --mode early",
              ".venv\\Scripts\\python.exe -m research.sports.phase2.run capture-replay --record <record.json>",
              "```", "",
              f"Checkpoint window: [cutoff - {C.CAPTURE['window_minutes_before_cutoff']} min, cutoff], cutoff = start - "
              "60 min = evidence cutoff. Captures outside the window are not recorded; captures after the cutoff, or "
              "with evidence retrieved after it, are MISSED; nothing is backfilled. Early snapshots are development "
              "records labelled with their actual horizon.", "",
              "## Reproduction", "",
              "```powershell",
              ".venv\\Scripts\\python.exe -m research.sports.phase2.run reproduce   # cache-only: oof, sample, select, "
              "evaluate, report",
              f".venv\\Scripts\\python.exe -m research.sports.phase2.run verify --protocol {protocol}",
              f".venv\\Scripts\\python.exe -m research.sports.run verify --protocol {C.PHASE1_PROTOCOL}",
              "```", ""]
    (DOCS / MD_DOC).write_bytes(("\n".join(lines)).encode("utf-8"))
    js = MAN.journal_paths(protocol)
    hashes = {"protocol_file_sha256": sha256_file(protocol_path(protocol)), "prediction_journals": len(js),
              "prediction_journals_manifest_sha256": sha256_json({p.name: sha256_file(p) for p in js}),
              "scored_rows_sha256": sha256_file(od / "scored.jsonl"), "metrics_sha256": sha256_file(od / "metrics.json"),
              "oof_journals_manifest_sha256": sha256_json(proto["oof_journals"]),
              "market_quotes_sha256": sha256_file(od / "market_quotes.jsonl"),
              "sample_manifest_events_sha256": man["manifest_sha256"],
              "docs": {d: MAN.portable_sha256(DOCS / d) for d in (MD_DOC, COV_DOC, RES_DOC)}}
    write_json(od / "report_hashes.json", hashes)
    log(f"phase2 report written: {MD_DOC}, {COV_DOC} ({len(rows)} series), {RES_DOC} ({len(rr)} rows)")

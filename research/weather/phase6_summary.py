"""Compact tracked Phase 6 research summary (markdown) from the local result JSONs.

Reads the gitignored result JSONs (never modifies them) and writes
docs/research/phase6_<version>_summary.md. No paired rows or cache contents
are included.

    python -m research.weather.phase6_summary
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from kalshi import cache
from research.weather.checkpoints import CHECKPOINT_IDS
from research.weather.models import MODEL_GFS_OPERATIONAL_LATEST, MODEL_HRRR_OPERATIONAL_LATEST
from research.weather.phase5 import RESULTS_DIR
from research.weather.phase6 import (
    PHASE6_VERSION,
    VERSIONS,
    WINDOW_FIX_TO_VERSION,
    result_paths,
)
from research.weather.phase6_coverage_audit import AUDIT_CSV_PATH, AUDIT_JSON_PATH, DOCS_DIR
from research.weather.phase6_v2_1_migration import VERIFICATION_PATH
from research.weather.phase6_window_diff import DIFF_PATH

SUMMARY_PATH = DOCS_DIR / f"phase6_{PHASE6_VERSION}_summary.md"
SCOPES = tuple(CHECKPOINT_IDS) + ("pooled_intraday", "pooled_all")
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    payload = cache.read_json(path, default=None)
    if not isinstance(payload, dict):
        raise RuntimeError(f"missing {path}")
    return payload


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _f(x: Any, nd: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _ci(d: dict[str, Any]) -> str:
    ci = d.get("ci95") or [None, None]
    return f"{_f(d.get('mean'))} [{_f(ci[0])}, {_f(ci[1])}]"


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def build_summary() -> str:
    res = result_paths(PHASE6_VERSION)
    cov = _load(res["coverage"])
    comp = _load(res["comparison"])
    meth = _load(res["methodology"])
    diff = _load(DIFF_PATH)
    audit = _load(AUDIT_JSON_PATH)
    ver = _load(VERIFICATION_PATH)
    src = cov["observation_source"]
    cc = comp["common_cohort"]
    L: list[str] = []
    a = L.append

    a(f"# Phase 6 {PHASE6_VERSION} research summary — KNYC as-of observations + operational replay")
    a("")
    a("**Status: RESEARCH_ONLY / NO_BET.** Incumbent: calibrated GFS. "
      "`SHADOW_GFS_HRRR_EQUAL_V1` (0.5·P_gfs + 0.5·P_hrrr) is **SHADOW ONLY** — not promoted. "
      "No prices, trades, sizing or learned weights. A confidence interval excluding zero would not be validation.")
    a("")
    a(f"Version `{PHASE6_VERSION}` supersedes `{meth['supersedes']}` "
      f"(change type `{meth['change_type']}`): {meth['relabel']['explanation']}")
    a("")

    a("## Methodology")
    a("")
    mw = meth["model_window"]
    obs = meth["observation_trust_rules"]
    a(f"- **Climate day (shared):** {mw['shared_with_observation_window']}.")
    a(f"- **Observation window:** {obs['window']}; newest usable report ≤ {obs['max_newest_age_min']:.0f} min old, "
      f"no gap > {obs['max_gap_min_including_midnight_to_first']:.0f} min.")
    a(f"- **Availability (frozen):** {meth['availability_assumption']['rule']}.")
    a(f"- **Version C model window:** {mw['version_C']}.")
    a("- **Incomplete-window reasons (all REPLAY_UNAVAILABLE):** "
      + "; ".join(f"{k.replace('_', ' ')} → `{v}`" for k, v in mw["completeness_reasons"].items() if k != "all_remain")
      + ".")
    rs = mw["run_selection_and_latency"]
    a(f"- **Run selection:** inherited unchanged from Phase 5 (GFS latency {rs['gfs_latency_hours']:.0f}h, "
      f"HRRR latency {rs['hrrr_latency_hours']:.0f}h, newest available run).")
    a(f"- **Calibration:** {meth['calibration']['pool']}; versions recalibrated separately.")
    a(f"- **Cohorts:** {meth['cohorts']['eligible_set']}; {meth['cohorts']['common_cohort']}.")
    b = meth["bootstrap"]
    a(f"- **Bootstrap:** {b['unit']}, n={b['n_boot']}, seed={b['seed']}, {b['interval']}.")
    a("")

    a("## Observation coverage")
    a("")
    a(f"IEM ASOS KNYC, {src['target_dates']} target dates: {src['raw_records']} reports, "
      f"{src['valid_records']} valid, rejections {src['rejections'] or 'none'}.")
    a("")
    a("| Checkpoint | Status | Newest usable age min (min/median/max) | Delayed reports excluded | Obs binds (C) |")
    a("|---|---|---|---|---|")
    for cid, s in cov["per_checkpoint_observations"].items():
        age = s["newest_usable_age_min"]
        bind = s["obs_binding_rate_version_C"]
        a(f"| {cid} | {s['status_counts']} | {_f(age.get('min'),0)}/{_f(age.get('median'),0)}/{_f(age.get('max'),0)} | "
          f"{s['delayed_reports_excluded_total']} | {_f(bind.get('rate'),3)} (n={bind['n']}) |")
    a("")
    a("| Checkpoint | Eligible pairs (both models FULL in C) | Common cohort |")
    a("|---|---|---|")
    for cid in CHECKPOINT_IDS:
        a(f"| {cid} | {cov['eligible_pairs_by_checkpoint'][cid]} | {cov['common_cohort_by_checkpoint'][cid]} |")
    a("")
    wc = cov["model_window_coverage_version_C"]
    a("Version C model-window unavailability: "
      + "; ".join(
          f"{m.split('_')[0].upper()} {cid}: {wc[m][cid]['unavailable_reasons']}"
          for m in (MODEL_GFS_OPERATIONAL_LATEST, MODEL_HRRR_OPERATIONAL_LATEST)
          for cid in CHECKPOINT_IDS
          if wc[m][cid]["unavailable_reasons"]
      ) + ".")
    a("")

    a(f"## Results (common cohort: {cc['n_pairs']} pairs, {cc['n_dates']} dates)")
    a("")
    a("Multiclass Brier (lower is better); shadow − GFS is the paired mean with date-clustered 95% CI.")
    a("")
    for version in VERSIONS:
        a(f"**{version}**")
        a("")
        a("| Scope | n | GFS | HRRR | Shadow | Shadow − GFS [95% CI] |")
        a("|---|---|---|---|---|---|")
        for scope in SCOPES:
            blk = cc["results"][version].get(scope)
            if not blk:
                continue
            p = blk["probabilistic"]
            a(f"| {scope} | {blk['n']} | {_f(p['gfs'].get('brier'))} | {_f(p['hrrr'].get('brier'))} | "
              f"{_f(p['shadow'].get('brier'))} | {_ci(blk['paired_delta_brier']['shadow_minus_gfs'])} |")
        a("")
    a("`d0_0900` has only 4 common-cohort pairs and is not interpretable on its own.")
    a("")

    a("## v1 → v2 (climate-day model-window fix)")
    a("")
    w = diff["window"]
    t = w["totals"]
    a(f"- Exact expected-hour sets compared per row ({w['v1_reconstruction_check']['rows_checked']} rows; "
      f"v1 reconstruction mismatches: {w['v1_reconstruction_check']['n_mismatches']}).")
    a(f"- Rows gaining the final 00:00 EDT hour: {t.get('rows_gained_final_hour_only', 0)}; "
      f"rows also losing the target-date 00:00 EDT leading hour (dminus1_1800, same 24-hour count): "
      f"{t.get('rows_lost_leading_hour_only', 0)}.")
    by = {}
    for x in w["max_changes"]:
        by.setdefault(f"{x['model'].split('_')[0].upper()} {x['checkpoint_id']}", []).append(f"{x['target_date']} {x['delta_f']:+.1f}°F")
    a(f"- Model maxima changed: {len(w['max_changes'])} — "
      + "; ".join(f"{k}: {', '.join(v)}" for k, v in by.items()) + ".")
    a(f"- Newly unavailable: {len(w['newly_unavailable'])} (all HRRR d0_0900; see diagnosis below).")
    sc = diff["scores"]
    a(f"- Common cohort {sc['common_cohort']['v1']['n_pairs']} → {sc['common_cohort']['v2']['n_pairs']} pairs "
      f"(d0_0900 {sc['common_cohort']['v1']['by_checkpoint']['d0_0900']} → {sc['common_cohort']['v2']['by_checkpoint']['d0_0900']}).")
    a("")
    a("| Version C pooled_intraday | v1 | v2 |")
    a("|---|---|---|")
    pooled = sc["brier_by_version_scope"]["C_remaining_day_max_plus_obs"]["pooled_intraday"]
    a(f"| n | {pooled['n_v1']} | {pooled['n_v2']} |")
    for m in ("gfs", "hrrr", "shadow"):
        a(f"| {m.upper()} Brier | {_f(pooled[m]['v1'])} | {_f(pooled[m]['v2'])} |")
    a(f"| Shadow − GFS [95% CI] | {_ci(pooled['shadow_minus_gfs']['v1'])} | {_ci(pooled['shadow_minus_gfs']['v2'])} |")
    concl = sc["conclusion_version_C_pooled_intraday"]
    a("")
    a(f"Conclusion changed: **{concl['changed']}** (shadow point estimate better than GFS in both; interval includes zero in both).")
    a("")

    a(f"## v2 → {PHASE6_VERSION} (diagnostic reason relabel)")
    a("")
    ch = ver["checks"]
    a(f"- Verification passed: **{ver['passed']}** (failures: {ver['failures'] or 'none'}).")
    a(f"- Relabeled rows: HRRR C {ch['hrrr_c']['relabeled_rows']}, HRRR B (window-diagnostic column only) "
      f"{ch['hrrr_b']['relabeled_rows']}, GFS C/B {ch['gfs_c']['relabeled_rows']}/{ch['gfs_b']['relabeled_rows']}; "
      f"unexpected cell changes: {sum(ch[k]['n_unexpected'] for k in ('gfs_c','hrrr_c','gfs_b','hrrr_b'))}.")
    a(f"- Coverage reason counts: {ch['coverage']['window_reason_counts_v2']} → {ch['coverage']['window_reason_counts_v2_1']} "
      f"(totals preserved: {ch['coverage']['totals_preserved']}).")
    s = ch["scoring_summary"]
    a(f"- Scores, CIs, paired rows, eligibility and cohort membership identical: "
      f"{all(s[k] for k in ('paired_rows_identical','results_identical','transitions_identical','eligible_pairs_identical','common_cohort_by_checkpoint_identical'))}. "
      f"{s['probabilities_note']}")
    a(f"- Methodology differs from v2 only by the planned additions: {ch['methodology']['matches_planned_changes_exactly']}. "
      f"v1/v2 files byte-for-byte unchanged ({ch['protected_files']['n_hashed']} hashed, changed: {ch['protected_files']['changed'] or 'none'}).")
    a("")

    a("## 09:00 coverage diagnosis (HRRR d0_0900)")
    a("")
    su = audit["summary"]
    pol = audit["fetcher_init_policy"]
    a(f"- {su['n_rows']} unavailable rows; selected init hours {su['selected_init_hours']}, "
      f"real-value horizon {su['selected_horizon_hours']} h. The selected 09Z run's last real value is 03Z (23:00 EDT); "
      "the CLI climate day needs 04Z (00:00 EDT). The payload pads later timestamps with nulls.")
    a(f"- Newer latency-eligible inits (10Z) per row: cache status totals {su['newer_inits_cache_status_total']} — "
      "not represented in the local Phase 5 cache; rows with a newer usable run: "
      f"{su['rows_with_newer_eligible_usable_run']}.")
    a(f"- Older eligible runs with a usable high existed for {su['rows_with_any_other_eligible_usable_run']} rows "
      f"({su['rows_with_older_run_covering_window']} with an hourly-cached run covering the window, e.g. 06Z extended cycle). "
      "Phase 5's `latest_available_by_latency` policy prefers the newest available run, so they were not selected. "
      "Nothing was substituted.")
    a(f"- Local Phase 5 HRRR cache init-hour profile: {su['phase5_cache_init_hour_profile']}; values {su['phase5_cache_value_profile']}.")
    a(f"- Source restriction: {pol['conclusion']} ({pol['cache_write_rule']}.)")
    a(f"- Per-row table: [`{AUDIT_CSV_PATH.name}`]({AUDIT_CSV_PATH.name}).")
    a("")

    a("## Artifacts")
    a("")
    a("Tracked: `data/weather/calibration/*_v1.csv`, `*_v2.csv`, `*_v2_1.csv`; this summary; the audit CSV. "
      "Local only (gitignored `data/results/`): the result JSONs below, the window diff, audit and verification JSONs, "
      "the v1 backup, and all raw caches.")
    a("")
    a("| Local JSON | SHA-256 |")
    a("|---|---|")
    for version in ("v1", WINDOW_FIX_TO_VERSION, PHASE6_VERSION):
        for p in result_paths(version).values():
            if p.exists():
                a(f"| `{_rel(p)}` | `{_sha(p)}` |")
    for p in (DIFF_PATH, AUDIT_JSON_PATH, VERIFICATION_PATH):
        a(f"| `{_rel(p)}` | `{_sha(p)}` |")
    a("")
    return "\n".join(L)


def main() -> int:
    text = build_summary()
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

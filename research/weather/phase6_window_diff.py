"""Read-only Phase 6 v1 → v2 diff for the climate-day model-window fix.

v1 ended the version-C window at the next America/New_York calendar midnight;
v2 ends it at the CLI climate-day end (midnight EST). For every version-C row
this compares the exact expected UTC valid-time sets: v1's are reconstructed
from its old calendar-day rule (and checked against the recorded v1 start and
hour count), v2's come from the recorded climate-day start/end.

Nothing is tuned and no Phase 6 artifact is modified; the only output is
data/results/phase6_window_fix_diff_v2.json.

    python -m research.weather.phase6_window_diff
"""

from __future__ import annotations

import csv
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kalshi import cache
from research.weather.asof_observations import local_day_bounds_utc
from research.weather.calibration import parse_float
from research.weather.checkpoints import CHECKPOINT_IDS
from research.weather.models import (
    DECISION_NO_BET,
    DECISION_RESEARCH_ONLY,
    MODEL_GFS_OPERATIONAL_LATEST,
    MODEL_HRRR_OPERATIONAL_LATEST,
    REPLAY_MODE_FULL_OPERATIONAL,
    REPLAY_MODE_UNAVAILABLE,
)
from research.weather.phase5 import RESULTS_DIR
from research.weather.phase6 import (
    VERSION_C,
    VERSIONS,
    WINDOW_FIX_FROM_VERSION,
    WINDOW_FIX_TO_VERSION,
    calibration_paths,
    eligible_keys,
    result_paths,
)

DIFF_PATH = RESULTS_DIR / f"phase6_window_fix_diff_{WINDOW_FIX_TO_VERSION}.json"
MODELS = (MODEL_GFS_OPERATIONAL_LATEST, MODEL_HRRR_OPERATIONAL_LATEST)
SCOPES = tuple(CHECKPOINT_IDS) + ("pooled_intraday", "pooled_all")


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)


def _ceil_hour(dt: datetime) -> datetime:
    floored = dt.replace(minute=0, second=0, microsecond=0)
    return floored if floored == dt else floored + timedelta(hours=1)


def _hours(start: datetime, end: datetime) -> list[datetime]:
    out, cursor = [], start
    while cursor < end:
        out.append(cursor)
        cursor += timedelta(hours=1)
    return out


def v1_expected_hours(row: dict[str, Any]) -> list[datetime]:
    """v1 rule: [ceil(max(checkpoint, NY midnight)), next NY midnight)."""
    as_of = _parse_dt(row["checkpoint_as_of"])
    day_start, day_end = local_day_bounds_utc(row["target_date"])
    return _hours(_ceil_hour(max(as_of, day_start)), day_end)


def v2_expected_hours(row: dict[str, Any]) -> list[datetime]:
    start = _parse_dt(row.get("remaining_window_start_utc"))
    end = _parse_dt(row.get("remaining_window_end_utc_exclusive"))
    if start is None or end is None:
        raise ValueError(f"v2 row lacks recorded window bounds: {row.get('target_date')} {row.get('checkpoint_id')}")
    return _hours(start, end)


def _row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["model"]), str(row["target_date"]), str(row["checkpoint_id"])


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "sd": None}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "sd": statistics.stdev(values) if len(values) > 1 else None,
    }


def _local_label(dt: datetime) -> str:
    from research.weather.checkpoints import NYC_TZ

    return dt.astimezone(NYC_TZ).strftime("%Y-%m-%d %H:%M %Z")


def window_diff(v1_rows: list[dict[str, Any]], v2_rows: list[dict[str, Any]]) -> dict[str, Any]:
    v1_by = {_row_key(r): r for r in v1_rows}
    v2_by = {_row_key(r): r for r in v2_rows}
    if set(v1_by) != set(v2_by):
        raise RuntimeError("v1 and v2 version-C row sets differ; runs/rows must be identical")

    v1_reconstruction_mismatches = []
    per_cp: dict[str, dict[str, Counter]] = {
        m: {cid: Counter() for cid in CHECKPOINT_IDS} for m in MODELS
    }
    max_changes: list[dict[str, Any]] = []
    newly_unavailable: list[dict[str, Any]] = []
    for key in sorted(v1_by):
        model, target_date, cid = key
        r1, r2 = v1_by[key], v2_by[key]
        h1 = v1_expected_hours(r1)
        h2 = v2_expected_hours(r2)
        rec_start = _parse_dt(r1.get("remaining_window_start_utc"))
        rec_n = parse_float(r1.get("remaining_window_expected_hours"))
        if (h1[0] if h1 else None) != rec_start or len(h1) != (int(rec_n) if rec_n is not None else -1):
            v1_reconstruction_mismatches.append({"key": key, "recorded_start": str(rec_start), "recorded_n": rec_n})
        s1, s2 = set(h1), set(h2)
        added, removed = sorted(s2 - s1), sorted(s1 - s2)
        c = per_cp[model][cid]
        c["rows"] += 1
        if s1 == s2:
            c["window_identical"] += 1
        if added:
            c["rows_gained_hours"] += 1
            c["hours_added_total"] += len(added)
        if removed:
            c["rows_lost_hours"] += 1
            c["hours_removed_total"] += len(removed)
        if added and removed:
            c["rows_shifted_same_count" if len(added) == len(removed) else "rows_added_and_removed"] += 1
        if added and h2 and added[-1] == h2[-1] and s2 - s1 == {h2[-1]}:
            c["rows_gained_final_hour_only"] += 1
        if removed and h1 and removed == [h1[0]]:
            c["rows_lost_leading_hour_only"] += 1

        m1 = parse_float(r1.get("remaining_day_model_high_f"))
        m2 = parse_float(r2.get("remaining_day_model_high_f"))
        ok1 = r1.get("remaining_window_status") == "OK"
        ok2 = r2.get("remaining_window_status") == "OK"
        if ok1 and ok2 and m1 is not None and m2 is not None and m1 != m2:
            c["max_changed"] += 1
            c["max_increased" if m2 > m1 else "max_decreased"] += 1
            max_changes.append(
                {
                    "model": model,
                    "target_date": target_date,
                    "checkpoint_id": cid,
                    "v1_max_f": m1,
                    "v2_max_f": m2,
                    "delta_f": m2 - m1,
                    "hours_added_local": [_local_label(t) for t in added],
                    "hours_removed_local": [_local_label(t) for t in removed],
                }
            )
        if ok1 and not ok2:
            c["newly_unavailable"] += 1
            newly_unavailable.append(
                {"model": model, "target_date": target_date, "checkpoint_id": cid,
                 "reason": r2.get("remaining_window_reason"), "replay_reason": r2.get("replay_reason")}
            )
        if r1.get("replay_mode") != r2.get("replay_mode"):
            c[f"replay_mode_{r1.get('replay_mode')}_to_{r2.get('replay_mode')}"] += 1

    totals: Counter = Counter()
    for m in MODELS:
        for cid in CHECKPOINT_IDS:
            totals.update(per_cp[m][cid])
    return {
        "totals": dict(totals),
        "by_model_checkpoint": {m: {cid: dict(per_cp[m][cid]) for cid in CHECKPOINT_IDS} for m in MODELS},
        "max_changes": max_changes,
        "newly_unavailable": newly_unavailable,
        "v2_unavailable_total": sum(1 for r in v2_rows if r.get("replay_mode") == REPLAY_MODE_UNAVAILABLE),
        "v1_unavailable_total": sum(1 for r in v1_rows if r.get("replay_mode") == REPLAY_MODE_UNAVAILABLE),
        "v1_reconstruction_check": {
            "rule": "[ceil(max(checkpoint, America/New_York midnight)), next America/New_York midnight)",
            "rows_checked": len(v1_by),
            "mismatches_vs_recorded_v1_start_and_count": v1_reconstruction_mismatches[:20],
            "n_mismatches": len(v1_reconstruction_mismatches),
        },
    }


def calibration_diff(v1_rows: list[dict[str, Any]], v2_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Residual pools (version C FULL rows on each run's eligible set) by model/checkpoint."""
    out: dict[str, Any] = {}
    elig = {"v1": eligible_keys(v1_rows), "v2": eligible_keys(v2_rows)}
    for label, rows in (("v1", v1_rows), ("v2", v2_rows)):
        block: dict[str, Any] = {}
        for m in MODELS:
            block[m] = {}
            for cid in CHECKPOINT_IDS:
                vals = [
                    parse_float(r.get("residual_f"))
                    for r in rows
                    if r.get("model") == m
                    and r.get("checkpoint_id") == cid
                    and r.get("replay_mode") == REPLAY_MODE_FULL_OPERATIONAL
                    and (r["target_date"], cid) in elig[label]
                ]
                block[m][cid] = _stats([v for v in vals if v is not None])
        out[label] = block
    changed_residuals: Counter = Counter()
    v1_by = {_row_key(r): parse_float(r.get("residual_f")) for r in v1_rows}
    for r in v2_rows:
        a, b = v1_by.get(_row_key(r)), parse_float(r.get("residual_f"))
        if a is not None and b is not None and a != b:
            changed_residuals[f"{r['model']}|{r['checkpoint_id']}"] += 1
    out["rows_with_changed_residual"] = dict(changed_residuals)
    out["eligible_pairs"] = {
        "v1": len(elig["v1"]),
        "v2": len(elig["v2"]),
        "only_v1": sorted(map(list, elig["v1"] - elig["v2"])),
        "only_v2": sorted(map(list, elig["v2"] - elig["v1"])),
    }
    return out


def _paired_index(comp: dict[str, Any], version: str) -> dict[tuple[str, str], dict[str, Any]]:
    rows = ((comp.get("common_cohort") or {}).get("paired_rows") or {}).get(version) or []
    return {(r["target_date"], r["checkpoint_id"]): r for r in rows}


def scores_diff(comp1: dict[str, Any], comp2: dict[str, Any]) -> dict[str, Any]:
    cc1, cc2 = comp1["common_cohort"], comp2["common_cohort"]
    k1 = set(_paired_index(comp1, VERSION_C))
    k2 = set(_paired_index(comp2, VERSION_C))
    cohort = {
        "v1": {"n_pairs": cc1["n_pairs"], "n_dates": cc1["n_dates"], "by_checkpoint": cc1["by_checkpoint"]},
        "v2": {"n_pairs": cc2["n_pairs"], "n_dates": cc2["n_dates"], "by_checkpoint": cc2["by_checkpoint"]},
        "only_v1": sorted(map(list, k1 - k2)),
        "only_v2": sorted(map(list, k2 - k1)),
        "identical_membership": k1 == k2,
    }

    briers: dict[str, Any] = {}
    for version in VERSIONS:
        briers[version] = {}
        for scope in SCOPES:
            b1 = cc1["results"][version].get(scope)
            b2 = cc2["results"][version].get(scope)
            if not b1 or not b2:
                continue
            entry: dict[str, Any] = {"n_v1": b1["n"], "n_v2": b2["n"]}
            for model in ("gfs", "hrrr", "shadow"):
                x1 = b1["probabilistic"][model].get("brier")
                x2 = b2["probabilistic"][model].get("brier")
                entry[model] = {"v1": x1, "v2": x2, "v2_minus_v1": (x2 - x1) if x1 is not None and x2 is not None else None}
            d1 = b1["paired_delta_brier"]["shadow_minus_gfs"]
            d2 = b2["paired_delta_brier"]["shadow_minus_gfs"]
            entry["shadow_minus_gfs"] = {
                "v1": {"mean": d1.get("mean"), "ci95": d1.get("ci95"), "n_clusters": d1.get("n_clusters")},
                "v2": {"mean": d2.get("mean"), "ci95": d2.get("ci95"), "n_clusters": d2.get("n_clusters")},
            }
            briers[version][scope] = entry

    # Per-pair C changes on pairs present in both cohorts (forecast change only).
    p1, p2 = _paired_index(comp1, VERSION_C), _paired_index(comp2, VERSION_C)
    common = sorted(set(p1) & set(p2))
    pair_changes: dict[str, Any] = {}
    for model in ("gfs", "hrrr", "shadow"):
        deltas = [
            float(p2[k][f"{model}_brier"]) - float(p1[k][f"{model}_brier"])
            for k in common
            if p1[k].get(f"{model}_brier") is not None and p2[k].get(f"{model}_brier") is not None
        ]
        pair_changes[model] = {
            "n_pairs": len(deltas),
            "n_changed": sum(1 for d in deltas if abs(d) > 1e-12),
            "mean_delta": statistics.fmean(deltas) if deltas else None,
        }
    residual_n_changed = {
        model: sum(1 for k in common if p1[k].get(f"{model}_residual_n") != p2[k].get(f"{model}_residual_n"))
        for model in ("gfs", "hrrr")
    }
    forecast_changed = {
        model: sum(
            1 for k in common if p1[k].get(f"{model}_forecast_high_f") != p2[k].get(f"{model}_forecast_high_f")
        )
        for model in ("gfs", "hrrr")
    }

    def _conclusion(d: dict[str, Any]) -> dict[str, Any]:
        mean, ci = d.get("mean"), d.get("ci95") or [None, None]
        excludes_zero = ci[0] is not None and ci[1] is not None and (ci[0] > 0 or ci[1] < 0)
        return {
            "shadow_better_point_estimate": mean is not None and mean < 0,
            "ci95_excludes_zero": excludes_zero,
        }

    pooled = briers[VERSION_C]["pooled_intraday"]["shadow_minus_gfs"]
    c1, c2 = _conclusion(pooled["v1"]), _conclusion(pooled["v2"])
    return {
        "common_cohort": cohort,
        "brier_by_version_scope": briers,
        "version_C_common_pair_changes": {
            "n_common_pairs": len(common),
            "brier": pair_changes,
            "residual_n_changed": residual_n_changed,
            "calibrated_input_forecast_changed": forecast_changed,
        },
        "conclusion_version_C_pooled_intraday": {
            "v1": c1,
            "v2": c2,
            "changed": c1 != c2,
            "note": "An interval excluding zero is not validation; the shadow remains SHADOW ONLY.",
        },
    }


def run_window_diff(write: bool = True) -> dict[str, Any]:
    prev, cur = WINDOW_FIX_FROM_VERSION, WINDOW_FIX_TO_VERSION
    cal1, cal2 = calibration_paths(prev), calibration_paths(cur)
    res1, res2 = result_paths(prev), result_paths(cur)
    v1_rows = _load_csv(cal1["gfs_obs_replay_csv"]) + _load_csv(cal1["hrrr_obs_replay_csv"])
    v2_rows = _load_csv(cal2["gfs_obs_replay_csv"]) + _load_csv(cal2["hrrr_obs_replay_csv"])
    comp1 = cache.read_json(res1["comparison"], default=None)
    comp2 = cache.read_json(res2["comparison"], default=None)
    if not comp1 or not comp2:
        raise RuntimeError("both v1 and v2 phase6_operational_comparison JSONs are required")
    out = {
        "schema": "phase6_window_fix_diff",
        "from_version": prev,
        "to_version": cur,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision_status": [DECISION_RESEARCH_ONLY, DECISION_NO_BET],
        "shadow_status": "SHADOW ONLY",
        "promotion": False,
        "parameters_tuned": False,
        "inputs": {
            "v1_csvs": [str(cal1["gfs_obs_replay_csv"]), str(cal1["hrrr_obs_replay_csv"])],
            "v2_csvs": [str(cal2["gfs_obs_replay_csv"]), str(cal2["hrrr_obs_replay_csv"])],
            "v1_comparison": str(res1["comparison"]),
            "v2_comparison": str(res2["comparison"]),
        },
        "window": window_diff(v1_rows, v2_rows),
        "calibration": calibration_diff(v1_rows, v2_rows),
        "scores": scores_diff(comp1, comp2),
    }
    if write:
        cache.write_json(DIFF_PATH, out)
    return out


def main() -> int:
    out = run_window_diff()
    w, s = out["window"], out["scores"]
    print(f"window totals: {w['totals']}")
    print(f"v1 reconstruction mismatches: {w['v1_reconstruction_check']['n_mismatches']}")
    print(f"max changes: {len(w['max_changes'])}; newly unavailable: {len(w['newly_unavailable'])}")
    print(f"common cohort v1={s['common_cohort']['v1']['n_pairs']} v2={s['common_cohort']['v2']['n_pairs']} "
          f"identical={s['common_cohort']['identical_membership']}")
    pooled = s["brier_by_version_scope"][VERSION_C]["pooled_intraday"]
    for m in ("gfs", "hrrr", "shadow"):
        print(f"C pooled_intraday {m}: v1={pooled[m]['v1']} v2={pooled[m]['v2']}")
    print(f"C shadow-gfs: {pooled['shadow_minus_gfs']}")
    print(f"conclusion: {s['conclusion_version_C_pooled_intraday']}")
    print(f"wrote {DIFF_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

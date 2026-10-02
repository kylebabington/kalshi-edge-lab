"""Phase 6 v2 → v2_1 migration: diagnostic coverage-reason relabel only.

Builds the `_v2_1` artifacts from the existing `_v2` artifacts and the local
hourly-series cache (no downloads, no rerun of selection, calibration or
scoring), then verifies that the ONLY differences are the planned ones:

* exactly 53 HRRR d0_0900 version-C rows change remaining_window_reason and the
  model_window part of replay_reason from missing_or_null_hours_in_remaining_window
  to selected_run_horizon_short_null_padded; the matching version-B rows carry the
  same window-diagnostic column and change only that column;
* the matching reason-count keys in the coverage summaries are renamed with
  identical counts;
* phase6_version / generated_at / the phase6= provenance token;
* the planned methodology additions (supersedes, change_type, relabel,
  completeness_reasons, window_fix_history rename).

Every other CSV cell and JSON leaf must be identical. v1 and v2 files are
hashed before and after and must be byte-for-byte unchanged.

    python -m research.weather.phase6_v2_1_migration
"""

from __future__ import annotations

import copy
import csv
import hashlib
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kalshi import cache
from research.weather.checkpoints import CHECKPOINT_IDS
from research.weather.models import MODEL_GFS_OPERATIONAL_LATEST, MODEL_HRRR_OPERATIONAL_LATEST
from research.weather.phase5 import RESULTS_DIR
from research.weather.phase6 import (
    COMPLETENESS_REASONS,
    MODEL_API_NAME,
    PHASE6_CHANGE_TYPE,
    PHASE6_PREVIOUS_VERSION,
    PHASE6_VERSION,
    RELABEL_V2_TO_V2_1,
    VERSION_B,
    VERSION_C,
    _count_modes,
    calibration_paths,
    methodology_payload,
    result_paths,
)
from research.weather.shadow import SHADOW_HYPOTHESIS_PATH
from research.weather.sources.single_run_hourly import (
    WINDOW_STATUS_OK,
    hourly_cache_path,
    parse_hourly_series,
    remaining_day_high,
)

FROM = PHASE6_PREVIOUS_VERSION
TO = PHASE6_VERSION
OLD_REASON = RELABEL_V2_TO_V2_1["from_reason"]
NEW_REASON = RELABEL_V2_TO_V2_1["to_reason"]
WINDOW_PREFIX = "model_window_unavailable:"
VERIFICATION_PATH = RESULTS_DIR / f"phase6_{TO}_migration_verification.json"
BACKUP_DIR = RESULTS_DIR / "phase6_v1_backup"


class MigrationError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_paths() -> list[Path]:
    paths: list[Path] = []
    for version in ("v1", "v2"):
        paths += list(calibration_paths(version).values()) + list(result_paths(version).values())
    paths += sorted(BACKUP_DIR.glob("*.json")) if BACKUP_DIR.exists() else []
    return paths


def _hash_protected() -> dict[str, str]:
    return {str(p): _sha256(p) for p in protected_paths() if p.exists()}


def _guard_output(path: Path) -> None:
    if f"_{TO}." not in path.name or path in set(protected_paths()):
        raise MigrationError(f"refusing to write non-{TO} path: {path}")


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    _guard_output(path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["model"]), str(row["target_date"]), str(row["checkpoint_id"])


def _bump_meta(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    out["phase6_version"] = TO
    out["provenance"] = row["provenance"].replace(f";phase6={FROM};", f";phase6={TO};")
    return out


def cached_series(model: str, init_iso: str) -> list:
    """Hourly series from the local cache only; never downloads."""
    path = hourly_cache_path(MODEL_API_NAME.get(model, model), datetime.fromisoformat(init_iso))
    if not path.exists():
        raise MigrationError(f"hourly cache missing (no download allowed): {path}")
    return parse_hourly_series((cache.read_json(path, default={}) or {}).get("response"))


# ---------------------------------------------------------------------------
# CSV migration (independent recomputation of the window reason)
# ---------------------------------------------------------------------------


def migrate_c_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    series_cache: dict[tuple[str, str], list] = {}
    out = []
    for row in rows:
        model, target_date, cid = _row_key(row)
        skey = (model, row["selected_run_init"])
        if skey not in series_cache:
            series_cache[skey] = cached_series(*skey)
        window = remaining_day_high(
            series_cache[skey],
            target_date=target_date,
            checkpoint_as_of=datetime.fromisoformat(row["checkpoint_as_of"]),
        )
        # Cross-check the cache reproduces v2 exactly before relabeling anything.
        if window["status"] != row["remaining_window_status"]:
            raise MigrationError(f"window status drift for {_row_key(row)}")
        if str(window["expected_hours"]) != row["remaining_window_expected_hours"]:
            raise MigrationError(f"expected-hours drift for {_row_key(row)}")
        if str(window["covered_hours"]) != row["remaining_window_covered_hours"]:
            raise MigrationError(f"covered-hours drift for {_row_key(row)}")
        if window["window_start_utc"] != row["remaining_window_start_utc"] or (
            window["window_end_utc_exclusive"] != row["remaining_window_end_utc_exclusive"]
        ):
            raise MigrationError(f"window bounds drift for {_row_key(row)}")
        if window["status"] == WINDOW_STATUS_OK:
            if float(window["model_remaining_day_high_f"]) != float(row["remaining_day_model_high_f"]):
                raise MigrationError(f"window max drift for {_row_key(row)}")

        new = _bump_meta(row)
        old_reason = row["remaining_window_reason"]
        new_reason = window["reason"] or ""
        if new_reason != old_reason:
            new["remaining_window_reason"] = new_reason
            prefix_old = f"{WINDOW_PREFIX}{old_reason}"
            if not row["replay_reason"].startswith(prefix_old):
                raise MigrationError(f"unexpected replay_reason for {_row_key(row)}: {row['replay_reason']}")
            new["replay_reason"] = f"{WINDOW_PREFIX}{new_reason}" + row["replay_reason"][len(prefix_old):]
        out.append(new)
    return out


def migrate_b_rows(rows: list[dict[str, str]], c_reason_by_key: dict[tuple[str, str, str], str]) -> list[dict[str, str]]:
    """B rows carry a copy of the version-C window diagnostic; only that column follows C."""
    out = []
    for row in rows:
        new = _bump_meta(row)
        new["remaining_window_reason"] = c_reason_by_key[_row_key(row)]
        out.append(new)
    return out


# ---------------------------------------------------------------------------
# JSON migration
# ---------------------------------------------------------------------------


def window_coverage(rows_c: list[dict[str, str]]) -> dict[str, Any]:
    """Same aggregation as run_phase6_obs_replay's model_window_coverage_version_C."""
    out: dict[str, Any] = {}
    for model in (MODEL_GFS_OPERATIONAL_LATEST, MODEL_HRRR_OPERATIONAL_LATEST):
        out[model] = {}
        for cid in CHECKPOINT_IDS:
            subset = [r for r in rows_c if r["model"] == model and r["checkpoint_id"] == cid]
            out[model][cid] = {
                "status_counts": dict(Counter(str(r["remaining_window_status"]) for r in subset)),
                "unavailable_reasons": dict(
                    Counter(
                        str(r["remaining_window_reason"])
                        for r in subset
                        if r["remaining_window_status"] != WINDOW_STATUS_OK
                    )
                ),
            }
    return out


def migrate_coverage(cov_v2: dict[str, Any], rows_c: list[dict[str, str]]) -> dict[str, Any]:
    cov = copy.deepcopy(cov_v2)
    cov["phase6_version"] = TO
    cov["generated_at"] = datetime.now(timezone.utc).isoformat()
    cov["model_window_coverage_version_C"] = window_coverage(rows_c)
    cov["replay_labels"][VERSION_C] = _count_modes(rows_c)
    return cov


def _copy_with_meta(payload: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(payload)
    out["phase6_version"] = TO
    out["generated_at"] = datetime.now(timezone.utc).isoformat()
    return out


def build_methodology() -> dict[str, Any]:
    hyp = cache.read_json(SHADOW_HYPOTHESIS_PATH, default=None)
    if not isinstance(hyp, dict):
        raise MigrationError("frozen shadow hypothesis missing; refusing to register a new one")
    return methodology_payload(hyp)


# ---------------------------------------------------------------------------
# Verification (checks the SPECIFIC planned changes against v2)
# ---------------------------------------------------------------------------


def _deep_diff(a: Any, b: Any, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        diffs = []
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}.{k}" if path else str(k)
            if k not in a:
                diffs.append(f"added:{p}")
            elif k not in b:
                diffs.append(f"removed:{p}")
            else:
                diffs += _deep_diff(a[k], b[k], p)
        return diffs
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"length:{path}"]
        diffs = []
        for i, (x, y) in enumerate(zip(a, b)):
            diffs += _deep_diff(x, y, f"{path}[{i}]")
        return diffs
    return [] if a == b else [f"changed:{path}"]


def _strip_meta(payload: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(payload)
    out.pop("generated_at", None)
    out.pop("phase6_version", None)
    return out


def _rename_key(block: dict[str, int], old: str, new: str) -> dict[str, int]:
    return {(new if k == old else k): v for k, v in block.items()}


def expected_coverage(cov_v2: dict[str, Any]) -> dict[str, Any]:
    """v2 coverage with exactly the planned reason-key renames applied."""
    cov = _strip_meta(cov_v2)
    hrrr, cid = RELABEL_V2_TO_V2_1["model"], RELABEL_V2_TO_V2_1["checkpoint_id"]
    win = cov["model_window_coverage_version_C"][hrrr][cid]
    win["unavailable_reasons"] = _rename_key(win["unavailable_reasons"], OLD_REASON, NEW_REASON)
    labels = cov["replay_labels"][VERSION_C][hrrr][cid]
    labels["reasons"] = {
        (k.replace(f"{WINDOW_PREFIX}{OLD_REASON}", f"{WINDOW_PREFIX}{NEW_REASON}")
         if k.startswith(f"{WINDOW_PREFIX}{OLD_REASON}") else k): v
        for k, v in labels["reasons"].items()
    }
    return cov


def expected_methodology(meth_v2: dict[str, Any]) -> dict[str, Any]:
    m = _strip_meta(meth_v2)
    m["supersedes"] = FROM
    m["change_type"] = PHASE6_CHANGE_TYPE
    m["relabel"] = copy.deepcopy(RELABEL_V2_TO_V2_1)
    mw = m["model_window"]
    mw["completeness_reasons"] = copy.deepcopy(COMPLETENESS_REASONS)
    mw["window_fix_history"] = mw.pop("supersedes")
    return m


def verify_csv_pair(
    name: str,
    v2_rows: list[dict[str, str]],
    v21_rows: list[dict[str, str]],
    *,
    relabel_cells: dict[str, tuple[str, str]],
    relabel_keys: set[tuple[str, str, str]],
) -> dict[str, Any]:
    """Allowed cell changes: version/provenance on every row; relabel_cells on relabel_keys only."""
    if len(v2_rows) != len(v21_rows):
        raise MigrationError(f"{name}: row count changed")
    unexpected: list[str] = []
    changed_keys: set[tuple[str, str, str]] = set()
    for a, b in zip(v2_rows, v21_rows):
        key = _row_key(a)
        if key != _row_key(b) or set(a) != set(b):
            raise MigrationError(f"{name}: row order/columns changed at {key}")
        for col in a:
            if a[col] == b[col]:
                continue
            if col == "phase6_version" and (a[col], b[col]) == (FROM, TO):
                continue
            if col == "provenance" and b[col] == a[col].replace(f";phase6={FROM};", f";phase6={TO};"):
                continue
            if key in relabel_keys and col in relabel_cells:
                old, new = relabel_cells[col]
                if a[col].startswith(old) and b[col] == new + a[col][len(old):]:
                    changed_keys.add(key)
                    continue
            unexpected.append(f"{key}:{col}:{a[col]!r}->{b[col]!r}")
    return {"rows": len(v2_rows), "relabeled_rows": len(changed_keys), "unexpected": unexpected[:20],
            "n_unexpected": len(unexpected), "relabeled_keys": changed_keys}


def verify(
    v2: dict[str, Any],
    v21: dict[str, Any],
    hashes_before: dict[str, str],
    hashes_after: dict[str, str],
) -> dict[str, Any]:
    hrrr, cid = RELABEL_V2_TO_V2_1["model"], RELABEL_V2_TO_V2_1["checkpoint_id"]
    target = {
        _row_key(r) for r in v2["hrrr_c"]
        if r["checkpoint_id"] == cid and r["remaining_window_reason"] == OLD_REASON
    }
    checks: dict[str, Any] = {}
    failures: list[str] = []

    if len(target) != RELABEL_V2_TO_V2_1["expected_rows"]:
        failures.append(f"expected {RELABEL_V2_TO_V2_1['expected_rows']} target rows, found {len(target)}")
    if any(k[0] != hrrr or k[2] != cid for k in target):
        failures.append("target rows outside HRRR d0_0900")

    c_cells = {
        "remaining_window_reason": (OLD_REASON, NEW_REASON),
        "replay_reason": (f"{WINDOW_PREFIX}{OLD_REASON}", f"{WINDOW_PREFIX}{NEW_REASON}"),
    }
    b_cells = {"remaining_window_reason": (OLD_REASON, NEW_REASON)}
    for name, cells, keys in (
        ("gfs_c", c_cells, set()),
        ("hrrr_c", c_cells, target),
        ("gfs_b", b_cells, set()),
        ("hrrr_b", b_cells, target),
    ):
        res = verify_csv_pair(name, v2[name], v21[name], relabel_cells=cells, relabel_keys=keys)
        relabeled = res.pop("relabeled_keys")
        if res["n_unexpected"]:
            failures.append(f"{name}: {res['n_unexpected']} unexpected cell changes")
        if relabeled != keys:
            failures.append(f"{name}: relabeled set != planned set ({len(relabeled)} vs {len(keys)})")
        if name == "hrrr_c":
            fully = {
                _row_key(b) for a, b in zip(v2[name], v21[name])
                if a["remaining_window_reason"] != b["remaining_window_reason"]
                and a["replay_reason"] != b["replay_reason"]
            }
            if fully != target:
                failures.append("hrrr_c: not every target row changed both reason fields")
        checks[name] = res

    for name in ("asof_csv", "obs_csv"):
        same = v2[f"{name}_sha"] == v21[f"{name}_sha"]
        checks[name] = {"byte_identical": same}
        if not same:
            failures.append(f"{name}: not byte-identical")

    cov_exp = expected_coverage(v2["coverage"])
    cov_diff = _deep_diff(cov_exp, _strip_meta(v21["coverage"]))
    win_v2 = v2["coverage"]["model_window_coverage_version_C"][hrrr][cid]["unavailable_reasons"]
    win_v21 = v21["coverage"]["model_window_coverage_version_C"][hrrr][cid]["unavailable_reasons"]
    lab_v2 = v2["coverage"]["replay_labels"][VERSION_C][hrrr][cid]["reasons"]
    lab_v21 = v21["coverage"]["replay_labels"][VERSION_C][hrrr][cid]["reasons"]
    checks["coverage"] = {
        "unexpected_diffs": cov_diff[:20],
        "window_reason_counts_v2": win_v2,
        "window_reason_counts_v2_1": win_v21,
        "replay_reason_counts_v2": lab_v2,
        "replay_reason_counts_v2_1": lab_v21,
        "totals_preserved": sum(win_v2.values()) == sum(win_v21.values()) and sum(lab_v2.values()) == sum(lab_v21.values()),
        "replay_labels_B_identical": v2["coverage"]["replay_labels"][VERSION_B] == v21["coverage"]["replay_labels"][VERSION_B],
    }
    if cov_diff:
        failures.append(f"coverage: {len(cov_diff)} unexpected diffs")
    if not checks["coverage"]["totals_preserved"]:
        failures.append("coverage: reason totals not preserved")

    for name in ("comparison", "shadow_eval"):
        diff = _deep_diff(_strip_meta(v2[name]), _strip_meta(v21[name]))
        checks[name] = {"identical_excluding_version_and_generated_at": not diff, "diffs": diff[:20]}
        if diff:
            failures.append(f"{name}: {len(diff)} diffs")

    meth_diff = _deep_diff(expected_methodology(v2["methodology"]), _strip_meta(v21["methodology"]))
    checks["methodology"] = {"matches_planned_changes_exactly": not meth_diff, "diffs": meth_diff[:20]}
    if meth_diff:
        failures.append(f"methodology: {len(meth_diff)} unplanned diffs")

    comp_v2, comp_v21 = v2["comparison"], v21["comparison"]
    checks["scoring_summary"] = {
        "common_cohort_n_pairs": [comp_v2["common_cohort"]["n_pairs"], comp_v21["common_cohort"]["n_pairs"]],
        "paired_rows_identical": comp_v2["common_cohort"]["paired_rows"] == comp_v21["common_cohort"]["paired_rows"],
        "results_identical": comp_v2["common_cohort"]["results"] == comp_v21["common_cohort"]["results"],
        "transitions_identical": comp_v2["common_cohort"]["transitions"] == comp_v21["common_cohort"]["transitions"],
        "eligible_pairs_identical": v2["coverage"]["eligible_pairs_by_checkpoint"] == v21["coverage"]["eligible_pairs_by_checkpoint"],
        "common_cohort_by_checkpoint_identical": v2["coverage"]["common_cohort_by_checkpoint"] == v21["coverage"]["common_cohort_by_checkpoint"],
        "probabilities_note": (
            "Per-bucket probabilities are not persisted in Phase 6 artifacts. Every scoring "
            "input (forecast_high_f, residual_f, replay_mode, eligibility, cohort keys) is "
            "cell-identical and every persisted per-pair Brier / log loss and bootstrap CI is "
            "identical, so the probabilities are unchanged."
        ),
    }

    changed_protected = sorted(k for k in hashes_before if hashes_before[k] != hashes_after.get(k))
    checks["protected_files"] = {"n_hashed": len(hashes_before), "changed": changed_protected}
    if changed_protected:
        failures.append(f"protected v1/v2 files changed: {changed_protected}")

    return {"passed": not failures, "failures": failures, "checks": checks}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _load_version(version: str) -> dict[str, Any]:
    cal, res = calibration_paths(version), result_paths(version)
    out: dict[str, Any] = {}
    for name, key in (("gfs_c", "gfs_obs_replay_csv"), ("hrrr_c", "hrrr_obs_replay_csv"),
                      ("gfs_b", "gfs_obs_bridge_csv"), ("hrrr_b", "hrrr_obs_bridge_csv")):
        out[f"{name}_fields"], out[name] = _read_csv(cal[key])
    out["asof_csv_sha"] = _sha256(cal["asof_obs_csv"])
    out["obs_csv_sha"] = _sha256(cal["obs_normalized_csv"])
    for name in ("coverage", "comparison", "shadow_eval", "methodology"):
        payload = cache.read_json(res[name], default=None)
        if not isinstance(payload, dict):
            raise MigrationError(f"missing {version} {name}: {res[name]}")
        out[name] = payload
    return out


def run_migration() -> dict[str, Any]:
    hashes_before = _hash_protected()
    v2 = _load_version(FROM)
    cal_to, res_to = calibration_paths(TO), result_paths(TO)
    cal_from = calibration_paths(FROM)

    gfs_c = migrate_c_rows(v2["gfs_c"])
    hrrr_c = migrate_c_rows(v2["hrrr_c"])
    c_reason = {_row_key(r): r["remaining_window_reason"] for r in gfs_c + hrrr_c}
    gfs_b = migrate_b_rows(v2["gfs_b"], c_reason)
    hrrr_b = migrate_b_rows(v2["hrrr_b"], c_reason)

    _write_csv(cal_to["gfs_obs_replay_csv"], v2["gfs_c_fields"], gfs_c)
    _write_csv(cal_to["hrrr_obs_replay_csv"], v2["hrrr_c_fields"], hrrr_c)
    _write_csv(cal_to["gfs_obs_bridge_csv"], v2["gfs_b_fields"], gfs_b)
    _write_csv(cal_to["hrrr_obs_bridge_csv"], v2["hrrr_b_fields"], hrrr_b)
    for key in ("asof_obs_csv", "obs_normalized_csv"):
        _guard_output(cal_to[key])
        shutil.copyfile(cal_from[key], cal_to[key])

    coverage = migrate_coverage(v2["coverage"], gfs_c + hrrr_c)
    for name, payload in (
        ("coverage", coverage),
        ("comparison", _copy_with_meta(v2["comparison"])),
        ("shadow_eval", _copy_with_meta(v2["shadow_eval"])),
        ("methodology", build_methodology()),
    ):
        _guard_output(res_to[name])
        cache.write_json(res_to[name], payload)

    v21 = _load_version(TO)
    hashes_after = _hash_protected()
    result = verify(v2, v21, hashes_before, hashes_after)
    result.update(
        {
            "schema": "phase6_migration_verification",
            "from_version": FROM,
            "to_version": TO,
            "change_type": PHASE6_CHANGE_TYPE,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "network_used": False,
            "outputs": {**{k: str(v) for k, v in cal_to.items()}, **{k: str(v) for k, v in res_to.items()}},
            "output_sha256": {str(p): _sha256(p) for p in list(cal_to.values()) + list(res_to.values())},
            "protected_sha256": hashes_after,
        }
    )
    cache.write_json(VERIFICATION_PATH, result)
    if not result["passed"]:
        raise MigrationError(f"verification failed: {result['failures']}")
    return result


def main() -> int:
    result = run_migration()
    checks = result["checks"]
    print(f"verification passed: {result['passed']}")
    for name in ("gfs_c", "hrrr_c", "gfs_b", "hrrr_b"):
        print(f"  {name}: rows={checks[name]['rows']} relabeled={checks[name]['relabeled_rows']} unexpected={checks[name]['n_unexpected']}")
    print(f"  coverage reasons v2={checks['coverage']['window_reason_counts_v2']} v2_1={checks['coverage']['window_reason_counts_v2_1']}")
    print(f"  replay reasons v2={checks['coverage']['replay_reason_counts_v2']} v2_1={checks['coverage']['replay_reason_counts_v2_1']}")
    print(f"  scoring: {checks['scoring_summary']}")
    print(f"  methodology planned-only: {checks['methodology']['matches_planned_changes_exactly']}")
    print(f"  protected files hashed={checks['protected_files']['n_hashed']} changed={checks['protected_files']['changed']}")
    print(f"wrote {VERIFICATION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

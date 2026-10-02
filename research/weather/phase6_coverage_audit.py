"""Read-only audit of version-C HRRR d0_0900 rows that are REPLAY_UNAVAILABLE (Phase 6 v2).

For each unavailable row this reports the selected run, its last real hourly
value, the final valid time the CLI climate day requires, and every other
exact HRRR init that passed the EXISTING Phase 5 rule (newest init with
init + 3h <= checkpoint and a usable target-date high), classified by how it
is represented in the local Phase 5 run cache:

    absent         — not represented in the local Phase 5 cache
    present_null   — cache entry present with a null/unusable high
    present_usable — cache entry present with a usable high

An absent entry does not by itself prove the run was unavailable or that a
fetch failed. Nothing is substituted; selection is unchanged. Local files
only — no network.

    python -m research.weather.phase6_coverage_audit
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kalshi import cache
from research.weather.asof_observations import climate_day_bounds_utc
from research.weather.models import HRRR_MODEL, HRRR_PUBLICATION_LATENCY, MODEL_HRRR_OPERATIONAL_LATEST
from research.weather.phase5 import HRRR_OPERATIONAL_LOOKBACK_HOURS, RESULTS_DIR
from research.weather.phase6 import (
    PHASE6_VERSION,
    WINDOW_FIX_TO_VERSION,
    calibration_paths,
)
from research.weather.sources.hrrr import HRRR_RUN_CACHE
from research.weather.sources.single_run_hourly import hourly_cache_path, parse_hourly_series

AUDIT_CHECKPOINT = "d0_0900"
DOCS_DIR = Path(__file__).resolve().parents[2] / "docs" / "research"
AUDIT_JSON_PATH = RESULTS_DIR / f"phase6_hrrr_d0_0900_coverage_audit_{PHASE6_VERSION}.json"
AUDIT_CSV_PATH = DOCS_DIR / f"phase6_{PHASE6_VERSION}_hrrr_d0_0900_coverage_audit.csv"

CACHE_ABSENT = "absent"
CACHE_PRESENT_NULL = "present_null"
CACHE_PRESENT_USABLE = "present_usable"

FETCHER_INIT_POLICY = {
    "phase5_selection": (
        "research/weather/phase5.py choose_operational_hrrr_run walks every whole-hour init "
        f"from the checkpoint back {HRRR_OPERATIONAL_LOOKBACK_HOURS}h, keeps inits with "
        "init + 3h <= checkpoint, calls get_hrrr_run_high for each, and keeps the newest "
        "available_at with a non-null high (policy latest_available_by_latency)"
    ),
    "fetcher": (
        "research/weather/sources/hrrr.py get_hrrr_run_high requests any whole-hour init from "
        "the Open-Meteo Single Runs API; it has no 3-hour cycle restriction"
    ),
    "cache_write_rule": (
        "get_hrrr_run_high writes a cache entry only when it obtains a non-null high; failed "
        "requests, non-OK responses and runs without target-date values are not written, so "
        "the cache cannot distinguish 'requested and unusable' from 'never requested'"
    ),
    "deliberate_three_hour_restriction_in_repo_code": False,
    "logs_available": False,
    "conclusion": (
        "The three-hourly pattern in the local Phase 5 cache is not a restriction in this "
        "repository's code. Hourly inits are not represented in the local Phase 5 cache; "
        "whether the source lacks them or the requests returned nothing usable is not "
        "established by local logs or code."
    ),
}


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def phase5_cache_status(disk: dict[str, Any], target_date: str, init: datetime) -> tuple[str, float | None]:
    key = f"{HRRR_MODEL}|{target_date}|{init.strftime('%Y-%m-%dT%H:%M')}"
    if key not in disk:
        return CACHE_ABSENT, None
    try:
        value = None if disk[key] is None else float(disk[key])
    except (TypeError, ValueError):
        value = None
    return (CACHE_PRESENT_USABLE, value) if value is not None else (CACHE_PRESENT_NULL, None)


def latency_eligible_inits(checkpoint: datetime, lookback_hours: int = HRRR_OPERATIONAL_LOOKBACK_HOURS) -> list[datetime]:
    """Whole-hour inits passing the Phase 5 rule init + 3h <= checkpoint (newest first)."""
    cursor = checkpoint.replace(minute=0, second=0, microsecond=0)
    out = []
    for hours_ago in range(0, lookback_hours + 1):
        init = cursor - timedelta(hours=hours_ago)
        if init <= checkpoint and init + HRRR_PUBLICATION_LATENCY <= checkpoint:
            out.append(init)
    return out


def _hourly_coverage(init: datetime, final_valid: datetime) -> dict[str, Any]:
    path = hourly_cache_path(HRRR_MODEL, init)
    if not path.exists():
        return {"hourly_cached": False}
    payload = (cache.read_json(path, default={}) or {}).get("response")
    series = parse_hourly_series(payload)
    real = [t for t, v in series if v is not None]
    last_real = max(real) if real else None
    return {
        "hourly_cached": True,
        "last_non_null_valid_utc": last_real.isoformat() if last_real else None,
        "last_payload_timestamp_utc": series[-1][0].isoformat() if series else None,
        "horizon_hours": (last_real - init).total_seconds() / 3600.0 if last_real else None,
        "covers_final_valid": bool(last_real and last_real >= final_valid),
    }


def audit_row(row: dict[str, Any], disk: dict[str, Any]) -> dict[str, Any]:
    target_date = row["target_date"]
    checkpoint = _utc(row["checkpoint_as_of"])
    selected = _utc(row["selected_run_init"])
    final_valid = climate_day_bounds_utc(target_date)[1] - timedelta(hours=1)
    sel_cov = _hourly_coverage(selected, final_valid)

    newer, older = [], []
    for init in latency_eligible_inits(checkpoint):
        if init == selected:
            continue
        status, high = phase5_cache_status(disk, target_date, init)
        entry = {"init_utc": init.isoformat(), "phase5_cache": status, "phase5_high_f": high}
        if init > selected:
            newer.append(entry)
        else:
            if status == CACHE_PRESENT_USABLE:
                entry.update(_hourly_coverage(init, final_valid))
            older.append(entry)

    newer_counts = Counter(e["phase5_cache"] for e in newer)
    older_usable = [e for e in older if e["phase5_cache"] == CACHE_PRESENT_USABLE]
    older_covering = [e for e in older_usable if e.get("covers_final_valid")]
    older_unknown = [e for e in older_usable if not e.get("hourly_cached")]

    if newer_counts.get(CACHE_PRESENT_USABLE):
        explanation = (
            "INCONSISTENT: a newer latency-eligible init has a usable high in the local Phase 5 "
            "cache, yet Phase 5 selected an older run"
        )
    else:
        explanation = (
            f"Phase 5 selected the newest init with a usable high in its cache ({selected:%H}Z). "
            f"{len(newer)} newer latency-eligible hourly init(s) are not represented in the local "
            "Phase 5 cache"
            + (f" ({newer_counts[CACHE_PRESENT_NULL]} present with null high)" if newer_counts.get(CACHE_PRESENT_NULL) else "")
            + ". Older eligible runs"
            + (f" covering the window ({', '.join(e['init_utc'][5:13] + 'Z' for e in older_covering[:3])})" if older_covering else "")
            + " were not selected because the policy prefers the newest available run."
        )

    return {
        "target_date": target_date,
        "checkpoint_id": row["checkpoint_id"],
        "checkpoint_utc": checkpoint.isoformat(),
        "selected_run_init_utc": selected.isoformat(),
        "selected_available_at_utc": _utc(row["available_at"]).isoformat(),
        "selected_last_non_null_valid_utc": sel_cov.get("last_non_null_valid_utc"),
        "selected_last_payload_timestamp_utc": sel_cov.get("last_payload_timestamp_utc"),
        "selected_horizon_hours": sel_cov.get("horizon_hours"),
        "required_final_valid_utc": final_valid.isoformat(),
        "window_reason_v2": row.get("remaining_window_reason"),
        "newer_latency_eligible": newer,
        "newer_cache_counts": {
            CACHE_ABSENT: newer_counts.get(CACHE_ABSENT, 0),
            CACHE_PRESENT_NULL: newer_counts.get(CACHE_PRESENT_NULL, 0),
            CACHE_PRESENT_USABLE: newer_counts.get(CACHE_PRESENT_USABLE, 0),
        },
        "older_usable_inits": [e["init_utc"] for e in older_usable],
        "older_usable_covering_window": [e["init_utc"] for e in older_covering],
        "older_usable_hourly_not_cached": [e["init_utc"] for e in older_unknown],
        "another_run_eligible_under_phase5_rule": bool(older_usable or newer_counts.get(CACHE_PRESENT_USABLE)),
        "newer_run_eligible_under_phase5_rule": bool(newer_counts.get(CACHE_PRESENT_USABLE)),
        "explanation": explanation,
    }


def run_audit(write: bool = True) -> dict[str, Any]:
    rows = _load_csv(calibration_paths(WINDOW_FIX_TO_VERSION)["hrrr_obs_replay_csv"])
    target = [
        r for r in rows
        if r["checkpoint_id"] == AUDIT_CHECKPOINT
        and r["model"] == MODEL_HRRR_OPERATIONAL_LATEST
        and r["remaining_window_status"] != "OK"
    ]
    disk = cache.read_json(HRRR_RUN_CACHE, default={}) or {}
    audited = [audit_row(r, disk) for r in sorted(target, key=lambda r: r["target_date"])]

    init_hours = Counter()
    status_values = Counter()
    for key, value in disk.items():
        parts = key.split("|")
        if len(parts) == 3 and "T" in parts[2]:
            init_hours[parts[2][-5:-3] + "Z"] += 1
            status_values["null" if value is None else "usable"] += 1

    summary = {
        "n_rows": len(audited),
        "selected_init_hours": dict(Counter(a["selected_run_init_utc"][11:13] + "Z" for a in audited)),
        "selected_horizon_hours": dict(Counter(a["selected_horizon_hours"] for a in audited)),
        "rows_with_newer_eligible_usable_run": sum(a["newer_run_eligible_under_phase5_rule"] for a in audited),
        "rows_with_any_other_eligible_usable_run": sum(a["another_run_eligible_under_phase5_rule"] for a in audited),
        "rows_with_older_run_covering_window": sum(bool(a["older_usable_covering_window"]) for a in audited),
        "newer_inits_cache_status_total": dict(
            sum((Counter(a["newer_cache_counts"]) for a in audited), Counter())
        ),
        "phase5_cache_init_hour_profile": dict(sorted(init_hours.items())),
        "phase5_cache_value_profile": dict(status_values),
    }
    out = {
        "schema": "phase6_hrrr_d0_0900_coverage_audit",
        "phase6_version": PHASE6_VERSION,
        "source_rows": str(calibration_paths(WINDOW_FIX_TO_VERSION)["hrrr_obs_replay_csv"]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "network_used": False,
        "selection_changed": False,
        "fetcher_init_policy": FETCHER_INIT_POLICY,
        "summary": summary,
        "rows": audited,
    }
    if write:
        cache.write_json(AUDIT_JSON_PATH, out)
        write_audit_csv(audited, AUDIT_CSV_PATH)
    return out


AUDIT_CSV_FIELDS = [
    "target_date",
    "checkpoint_utc",
    "selected_run_init_utc",
    "selected_available_at_utc",
    "selected_last_non_null_valid_utc",
    "required_final_valid_utc",
    "newer_inits_absent_from_phase5_cache",
    "newer_inits_present_null",
    "newer_inits_present_usable",
    "older_usable_inits_covering_window",
    "another_run_eligible_under_phase5_rule",
    "explanation",
]


def write_audit_csv(audited: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_CSV_FIELDS)
        writer.writeheader()
        for a in audited:
            writer.writerow(
                {
                    "target_date": a["target_date"],
                    "checkpoint_utc": a["checkpoint_utc"],
                    "selected_run_init_utc": a["selected_run_init_utc"],
                    "selected_available_at_utc": a["selected_available_at_utc"],
                    "selected_last_non_null_valid_utc": a["selected_last_non_null_valid_utc"],
                    "required_final_valid_utc": a["required_final_valid_utc"],
                    "newer_inits_absent_from_phase5_cache": a["newer_cache_counts"][CACHE_ABSENT],
                    "newer_inits_present_null": a["newer_cache_counts"][CACHE_PRESENT_NULL],
                    "newer_inits_present_usable": a["newer_cache_counts"][CACHE_PRESENT_USABLE],
                    "older_usable_inits_covering_window": " ".join(
                        i[5:13] + "Z" for i in a["older_usable_covering_window"]
                    ),
                    "another_run_eligible_under_phase5_rule": a["another_run_eligible_under_phase5_rule"],
                    "explanation": a["explanation"],
                }
            )
    return path


def main() -> int:
    out = run_audit()
    print(json.dumps(out["summary"], indent=1, default=str))
    print(f"wrote {AUDIT_JSON_PATH}")
    print(f"wrote {AUDIT_CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

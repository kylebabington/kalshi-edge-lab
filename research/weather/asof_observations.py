"""As-of KNYC observed-high reconstruction for historical checkpoints (Phase 6).

Availability assumption (frozen before any Phase 6 result was computed):
    available_at = observation_time + OBS_AVAILABILITY_LAG (20 min)
The IEM archive supplies no publication time. When a record does supply an
availability time, the later of the two is used.

Observation window: the NWS CLI climate day that settles nws_cli_knyc events,
which runs midnight-to-midnight Local Standard Time (EST, UTC-5) all year.
During EDT that is 01:00 EDT on the target date through 00:59 EDT the next day,
so 00:00–00:59 EDT reports belong to the previous climate day.

Trust rules (frozen): a checkpoint's observed high is OK only when at least
one usable observation exists since climate-day start, the newest usable
observation is at most OBS_MAX_NEWEST_AGE_MIN old at the checkpoint, and no
gap (including midnight → first observation) exceeds OBS_MAX_GAP_MIN.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from research.weather.checkpoints import NYC_TZ
from research.weather.sources.knyc_history import EXPECTED_STATION_ID, TEMPERATURE_UNITS

OBS_AVAILABILITY_LAG = timedelta(minutes=20)
AVAILABILITY_BASIS = "assumed_fixed_lag_20m_after_observation_time"
CLIMATE_DAY_TZ = timezone(timedelta(hours=-5), "EST")
OBS_WINDOW_BASIS = "nws_cli_climate_day_local_standard_time_utc-5"
OBS_MAX_NEWEST_AGE_MIN = 120.0
OBS_MAX_GAP_MIN = 180.0
TEMP_MIN_F = -40.0
TEMP_MAX_F = 130.0

OBS_STATUS_OK = "OK"
OBS_STATUS_MISSING = "MISSING"
OBS_STATUS_STALE = "STALE"
OBS_STATUS_GAP = "GAP"

REJECT_WRONG_STATION = "wrong_station"
REJECT_INVALID_TIMESTAMP = "invalid_timestamp"
REJECT_MISSING_TEMPERATURE = "missing_temperature"
REJECT_INVALID_TEMPERATURE = "invalid_temperature"
REJECT_OUT_OF_RANGE = "out_of_range_temperature"
REJECT_INVALID_UNITS = "invalid_units"
REJECT_DUPLICATE_EXACT = "duplicate_exact_collapsed"
REJECT_DUPLICATE_CONFLICT = "duplicate_conflicting_dropped"


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def record_available_at(record: dict[str, Any]) -> datetime | None:
    obs_time = _parse_utc(record.get("observation_time_utc"))
    if obs_time is None:
        return None
    assumed = obs_time + OBS_AVAILABILITY_LAG
    supplied = _parse_utc(record.get("availability_time_utc"))
    if supplied is not None and supplied > assumed:
        return supplied
    return assumed


def _station_ok(record: dict[str, Any], expected: str) -> bool:
    if str(record.get("station_id") or "").upper() != expected:
        return False
    metar = str(record.get("raw_metar") or "").upper()
    if metar:
        tokens = metar.split()
        body = tokens[1:] if tokens and tokens[0] in ("METAR", "SPECI") else tokens
        if not body or body[0] != expected:
            return False
    return True


def validate_observations(
    records: Iterable[dict[str, Any]],
    *,
    expected_station: str = EXPECTED_STATION_ID,
) -> tuple[list[dict[str, Any]], Counter]:
    """Return (valid_records_sorted, rejection_counts).

    Valid records gain ``obs_time`` and ``available_at`` datetimes.
    Exact duplicates (same time, same temperature) collapse to one; conflicting
    duplicates (same time, different temperatures) are all dropped.
    """
    rejections: Counter = Counter()
    candidates: list[dict[str, Any]] = []
    for record in records:
        if not _station_ok(record, expected_station):
            rejections[REJECT_WRONG_STATION] += 1
            continue
        obs_time = _parse_utc(record.get("observation_time_utc"))
        if obs_time is None:
            rejections[REJECT_INVALID_TIMESTAMP] += 1
            continue
        if str(record.get("units") or TEMPERATURE_UNITS).upper() != TEMPERATURE_UNITS:
            rejections[REJECT_INVALID_UNITS] += 1
            continue
        raw_temp = record.get("temperature_f")
        if raw_temp is None or raw_temp == "" or raw_temp == "M":
            raw_str = str(record.get("temperature_raw") or "").strip()
            if raw_str and raw_str != "M":
                rejections[REJECT_INVALID_TEMPERATURE] += 1
            else:
                rejections[REJECT_MISSING_TEMPERATURE] += 1
            continue
        try:
            temp = float(raw_temp)
        except (TypeError, ValueError):
            rejections[REJECT_INVALID_TEMPERATURE] += 1
            continue
        if temp != temp or not (TEMP_MIN_F <= temp <= TEMP_MAX_F):
            rejections[REJECT_OUT_OF_RANGE] += 1
            continue
        available_at = record_available_at(record)
        enriched = dict(record)
        enriched["temperature_f"] = temp
        enriched["obs_time"] = obs_time
        enriched["available_at"] = available_at
        candidates.append(enriched)

    by_time: dict[datetime, list[dict[str, Any]]] = {}
    for rec in candidates:
        by_time.setdefault(rec["obs_time"], []).append(rec)
    valid: list[dict[str, Any]] = []
    for obs_time, group in by_time.items():
        temps = {float(r["temperature_f"]) for r in group}
        if len(temps) > 1:
            rejections[REJECT_DUPLICATE_CONFLICT] += len(group)
            continue
        if len(group) > 1:
            rejections[REJECT_DUPLICATE_EXACT] += len(group) - 1
            # Keep the latest-available copy (conservative).
            group = sorted(group, key=lambda r: r["available_at"])
        valid.append(group[-1])
    valid.sort(key=lambda r: r["obs_time"])
    return valid, rejections


def local_day_bounds_utc(target_date: str | date) -> tuple[datetime, datetime]:
    """[local midnight, next local midnight) of the NY calendar date, in UTC."""
    d = target_date if isinstance(target_date, date) else datetime.strptime(str(target_date), "%Y-%m-%d").date()
    start_local = datetime(d.year, d.month, d.day, tzinfo=NYC_TZ)
    nxt = d + timedelta(days=1)
    end_local = datetime(nxt.year, nxt.month, nxt.day, tzinfo=NYC_TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def climate_day_bounds_utc(target_date: str | date) -> tuple[datetime, datetime]:
    """[00:00 LST, 24:00 LST) of the NWS CLI climate day, in UTC."""
    d = target_date if isinstance(target_date, date) else datetime.strptime(str(target_date), "%Y-%m-%d").date()
    start = datetime(d.year, d.month, d.day, tzinfo=CLIMATE_DAY_TZ).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def group_by_climate_date(valid: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for rec in valid:
        key = rec["obs_time"].astimezone(CLIMATE_DAY_TZ).date().isoformat()
        out.setdefault(key, []).append(rec)
    return out


def observed_high_asof(
    valid_records: Sequence[dict[str, Any]],
    *,
    target_date: str,
    checkpoint_as_of: datetime,
) -> dict[str, Any]:
    """Max KNYC temperature since climate-day start among records available by the checkpoint."""
    if checkpoint_as_of.tzinfo is None:
        raise ValueError("checkpoint_as_of must be timezone-aware")
    as_of = checkpoint_as_of.astimezone(timezone.utc)
    day_start, day_end = climate_day_bounds_utc(target_date)

    in_day = [r for r in valid_records if day_start <= r["obs_time"] < day_end]
    usable = [r for r in in_day if r["obs_time"] <= as_of and r["available_at"] <= as_of]
    after_checkpoint = sum(1 for r in in_day if r["obs_time"] > as_of)
    delayed = sum(1 for r in in_day if r["obs_time"] <= as_of and r["available_at"] > as_of)
    usable.sort(key=lambda r: r["obs_time"])

    base: dict[str, Any] = {
        "target_date": target_date,
        "checkpoint_as_of": as_of.isoformat(),
        "window_start_utc": day_start.isoformat(),
        "window_basis": OBS_WINDOW_BASIS,
        "availability_basis": AVAILABILITY_BASIS,
        "availability_lag_min": OBS_AVAILABILITY_LAG.total_seconds() / 60.0,
        "n_usable": len(usable),
        "n_delayed_at_checkpoint": delayed,
        "n_after_checkpoint": after_checkpoint,
        "n_not_yet_available": delayed + after_checkpoint,
        "observed_high_so_far_f": None,
        "time_of_high_utc": None,
        "newest_obs_time_utc": None,
        "newest_obs_available_at_utc": None,
        "newest_age_min": None,
        "max_gap_min": None,
    }
    if not usable:
        base["status"] = OBS_STATUS_MISSING
        base["reason"] = "no_usable_observation_since_climate_day_start"
        return base

    highest = max(usable, key=lambda r: (r["temperature_f"], r["obs_time"]))
    newest = usable[-1]
    times = [day_start] + [r["obs_time"] for r in usable]
    max_gap = max((b - a).total_seconds() / 60.0 for a, b in zip(times, times[1:]))
    age = (as_of - newest["obs_time"]).total_seconds() / 60.0

    base.update(
        {
            "observed_high_so_far_f": float(highest["temperature_f"]),
            "time_of_high_utc": highest["obs_time"].isoformat(),
            "newest_obs_time_utc": newest["obs_time"].isoformat(),
            "newest_obs_available_at_utc": newest["available_at"].isoformat(),
            "newest_age_min": age,
            "max_gap_min": max_gap,
        }
    )
    if age > OBS_MAX_NEWEST_AGE_MIN:
        base["status"] = OBS_STATUS_STALE
        base["reason"] = f"newest_usable_obs_age_{age:.0f}m_exceeds_{OBS_MAX_NEWEST_AGE_MIN:.0f}m"
    elif max_gap > OBS_MAX_GAP_MIN:
        base["status"] = OBS_STATUS_GAP
        base["reason"] = f"obs_gap_{max_gap:.0f}m_exceeds_{OBS_MAX_GAP_MIN:.0f}m"
    else:
        base["status"] = OBS_STATUS_OK
        base["reason"] = None
    return base


def summarize_ages(values: Sequence[float]) -> dict[str, Any]:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return {"n": 0}

    def q(p: float) -> float:
        return vals[min(len(vals) - 1, int(round(p * (len(vals) - 1))))]

    return {
        "n": len(vals),
        "min": vals[0],
        "median": statistics.median(vals),
        "p90": q(0.9),
        "max": vals[-1],
    }

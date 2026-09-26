"""KNYC observation trajectory evidence (supporting, not a probability blender)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from nws import NYC_TIMEZONE, get_knyc_observations
from research.weather.evidence import (
    FRESHNESS_UNAVAILABLE,
    QUALITY_MISSING,
    QUALITY_OK,
    QUALITY_UNAVAILABLE,
    ObservationSnapshot,
    apply_freshness,
)

NYC_TZ = NYC_TIMEZONE


def _as_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def build_observation_trajectory(
    observations: list[dict[str, Any]],
    *,
    target_date: str | None = None,
    as_of: datetime | None = None,
    retrieved_at: str | None = None,
) -> ObservationSnapshot:
    """Pure builder for tests. Observations must already be timestamp-sorted."""
    as_of = as_of or datetime.now(timezone.utc)
    retrieved_at = retrieved_at or as_of.isoformat()

    if target_date is None:
        target_date = datetime.now(NYC_TZ).date().isoformat()

    day_rows: list[dict[str, Any]] = []
    for obs in observations:
        ts = obs.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
        if not isinstance(ts, datetime):
            continue
        local = ts.astimezone(NYC_TZ)
        if local.date().isoformat() != target_date:
            continue
        # No-lookahead: only observations at/before as_of
        if ts.astimezone(timezone.utc) > as_of.astimezone(timezone.utc):
            continue
        temp = obs.get("temperature_f")
        if temp is None:
            continue
        try:
            temp_f = float(temp)
        except (TypeError, ValueError):
            continue
        day_rows.append(
            {
                "timestamp": _as_utc_iso(ts),
                "temperature_f": temp_f,
                "dewpoint_f": obs.get("dewpoint_f"),
                "wind": obs.get("wind") or obs.get("windSpeed"),
                "conditions": obs.get("description") or obs.get("conditions"),
            }
        )

    if not day_rows:
        return ObservationSnapshot(
            station_id="KNYC",
            target_date=target_date,
            retrieved_at=retrieved_at,
            freshness_status=FRESHNESS_UNAVAILABLE,
            quality_status=QUALITY_MISSING,
            warnings=["No KNYC observations for target date at as_of"],
        )

    day_rows.sort(key=lambda r: r["timestamp"])
    latest = day_rows[-1]
    highest = max(day_rows, key=lambda r: r["temperature_f"])

    def _temp_at_offset(hours: float) -> float | None:
        target_ts = datetime.fromisoformat(latest["timestamp"]) - timedelta(hours=hours)
        # nearest observation at or before target_ts
        prior = [
            r
            for r in day_rows
            if datetime.fromisoformat(r["timestamp"]) <= target_ts
        ]
        if not prior:
            return None
        return float(prior[-1]["temperature_f"])

    t_now = float(latest["temperature_f"])
    t_1h = _temp_at_offset(1.0)
    t_3h = _temp_at_offset(3.0)
    change_1h = (t_now - t_1h) if t_1h is not None else None
    change_3h = (t_now - t_3h) if t_3h is not None else None

    # Optional morning warming rate: 7–10 AM local, if enough points.
    morning_rate = None
    morning = []
    for row in day_rows:
        local = datetime.fromisoformat(row["timestamp"]).astimezone(NYC_TZ)
        if 7 <= local.hour <= 10:
            morning.append((local, float(row["temperature_f"])))
    if len(morning) >= 2:
        morning.sort(key=lambda item: item[0])
        dt_hours = (morning[-1][0] - morning[0][0]).total_seconds() / 3600.0
        if dt_hours >= 0.5:
            morning_rate = (morning[-1][1] - morning[0][1]) / dt_hours

    freshness_seconds, freshness_status = apply_freshness(
        latest["timestamp"], as_of=as_of
    )
    return ObservationSnapshot(
        station_id="KNYC",
        target_date=target_date,
        retrieved_at=retrieved_at,
        latest_temperature_f=t_now,
        latest_timestamp=latest["timestamp"],
        high_so_far_f=float(highest["temperature_f"]),
        time_of_high=highest["timestamp"],
        change_1h_f=change_1h,
        change_3h_f=change_3h,
        morning_warming_rate_f_per_hour=morning_rate,
        trajectory=day_rows,
        freshness_seconds=freshness_seconds,
        freshness_status=freshness_status,
        quality_status=QUALITY_OK,
        warnings=[],
        metadata={
            "observation_count": len(day_rows),
            "note": (
                "KNYC observations are supporting meteorological evidence. "
                "Not used as a hand-written probability adjustment."
            ),
        },
    )


def collect_observation_trajectory(
    *,
    target_date: str | None = None,
    as_of: datetime | None = None,
) -> ObservationSnapshot:
    as_of = as_of or datetime.now(timezone.utc)
    try:
        observations = get_knyc_observations(limit=200)
        # Enrich with optional fields if present in raw NWS payloads later;
        # current nws.get_knyc_observations returns timestamp/temp/description.
        return build_observation_trajectory(
            observations,
            target_date=target_date,
            as_of=as_of,
            retrieved_at=as_of.isoformat(),
        )
    except Exception as error:  # noqa: BLE001
        return ObservationSnapshot(
            station_id="KNYC",
            target_date=target_date,
            retrieved_at=as_of.isoformat(),
            freshness_status=FRESHNESS_UNAVAILABLE,
            quality_status=QUALITY_UNAVAILABLE,
            warnings=[f"Observation trajectory unavailable: {error}"],
        )

"""Phase 6: historical KNYC as-of observations + remaining-day replay (no network)."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from research.weather import phase5 as phase5_mod
from research.weather import phase6 as phase6_mod
from research.weather.asof_observations import (
    OBS_STATUS_GAP,
    OBS_STATUS_MISSING,
    OBS_STATUS_OK,
    OBS_STATUS_STALE,
    REJECT_DUPLICATE_CONFLICT,
    REJECT_DUPLICATE_EXACT,
    REJECT_MISSING_TEMPERATURE,
    REJECT_OUT_OF_RANGE,
    REJECT_WRONG_STATION,
    climate_day_bounds_utc,
    local_day_bounds_utc,
    observed_high_asof,
    validate_observations,
)
from research.weather.checkpoints import checkpoint_as_of_utc
from research.weather.models import (
    MODEL_GFS_OPERATIONAL_LATEST,
    MODEL_HRRR_OPERATIONAL_LATEST,
    REPLAY_MODE_FULL_OPERATIONAL,
    REPLAY_MODE_MODEL_ONLY,
    REPLAY_MODE_OBS_BRIDGE_DIAGNOSTIC,
    REPLAY_MODE_UNAVAILABLE,
)
from research.weather.phase6 import (
    VERSION_A,
    VERSION_B,
    VERSION_C,
    build_version_rows,
    common_cohort_keys,
    eligible_keys,
    evaluate_version_checkpoint,
    restrict_rows,
    run_phase6_obs_replay,
)
from research.weather.sources.knyc_history import month_chunks, parse_iem_asos_csv
from research.weather.sources.single_run_hourly import (
    WINDOW_REASON_HORIZON_SHORT,
    WINDOW_REASON_MISSING_HOURS,
    WINDOW_STATUS_OK,
    WINDOW_STATUS_UNAVAILABLE,
    parse_hourly_series,
    remaining_day_high,
)

NYC = ZoneInfo("America/New_York")
UTC = timezone.utc


def _obs(ts_utc: datetime, temp: float | None, *, station: str = "KNYC", metar: str | None = None, **extra):
    rec = {
        "station_id": station,
        "station_raw": station[1:],
        "observation_time_utc": ts_utc.astimezone(UTC).isoformat(),
        "availability_time_utc": None,
        "temperature_f": temp,
        "temperature_raw": "M" if temp is None else str(temp),
        "units": "F",
        "raw_metar": metar if metar is not None else f"{station} {ts_utc:%d%H%M}Z AUTO",
        "source": "iem_asos_archive",
    }
    rec.update(extra)
    return rec


def _local(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=NYC)


# ---------------------------------------------------------------------------
# Source parsing
# ---------------------------------------------------------------------------


def test_iem_parser_fixture_keeps_raw_fields():
    text = (
        "station,valid,tmpf,metar\n"
        "NYC,2026-04-02 00:51,58.00,KNYC 020051Z AUTO 36007KT 10SM 14/09 A3022 RMK AO2 T01440089\n"
        "NYC,2026-04-02 03:38,M,SPECI KNYC 020338Z AUTO 07009G19KT\n"
    )
    recs = parse_iem_asos_csv(text, {"retrieved_at": "2026-09-27T00:00:00+00:00", "sha256": "abc"})
    assert len(recs) == 2
    assert recs[0]["station_id"] == "KNYC"
    assert recs[0]["observation_time_utc"] == "2026-04-02T00:51:00+00:00"
    assert recs[0]["temperature_f"] == 58.0
    assert recs[0]["units"] == "F"
    assert recs[0]["response_sha256"] == "abc"
    assert recs[1]["temperature_f"] is None
    assert recs[1]["report_kind"] == "SPECI"
    valid, rej = validate_observations(recs)
    assert len(valid) == 1
    assert rej[REJECT_MISSING_TEMPERATURE] == 1


def test_iem_rate_limit_retried_and_never_cached(tmp_path, monkeypatch):
    from research.weather.sources import knyc_history

    monkeypatch.setattr(knyc_history, "KNYC_OBS_CACHE_DIR", tmp_path)
    monkeypatch.setattr(knyc_history, "RATE_LIMIT_BACKOFF_S", (0, 0))

    class Resp:
        def __init__(self, status, text):
            self.status_code, self.text, self.url = status, text, "u"
            self.ok = status < 400

    replies = iter([Resp(429, "Too many requests from your IP address, slow down."),
                    Resp(200, "station,valid,tmpf,metar\nNYC,2026-04-02 00:51,58.00,KNYC 020051Z AUTO\n")])
    monkeypatch.setattr(knyc_history.requests, "get", lambda *a, **k: next(replies))
    text, meta = knyc_history.fetch_month(date(2026, 4, 1), date(2026, 5, 1), refresh=True)
    assert meta["status"] == "ok" and len(meta["attempts"]) == 2
    assert "KNYC" in text

    monkeypatch.setattr(knyc_history.requests, "get", lambda *a, **k: Resp(429, "Too many requests"))
    text, meta = knyc_history.fetch_month(date(2026, 6, 1), date(2026, 7, 1), refresh=True)
    assert text is None and meta["status"] == "error"
    assert not (tmp_path / "NYC_202606.csv").exists()


def test_month_chunks_contiguous():
    chunks = month_chunks(date(2026, 3, 31), date(2026, 5, 2))
    assert chunks[0] == (date(2026, 3, 1), date(2026, 4, 1))
    assert chunks[-1] == (date(2026, 5, 1), date(2026, 6, 1))
    assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:]))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validation_rejects_wrong_station_invalid_and_duplicates():
    t = datetime(2026, 7, 15, 14, 51, tzinfo=UTC)
    recs = [
        _obs(t, 80.0),
        _obs(t, 80.0),  # exact duplicate → collapsed
        _obs(t + timedelta(hours=1), 81.0),
        _obs(t + timedelta(hours=1), 83.0),  # conflicting duplicate → both dropped
        _obs(t + timedelta(hours=2), 82.0, station="KLGA"),
        _obs(t + timedelta(hours=2, minutes=5), 82.0, metar="KLGA 151656Z AUTO"),
        _obs(t + timedelta(hours=3), None),
        _obs(t + timedelta(hours=4), 150.0),
    ]
    valid, rej = validate_observations(recs)
    assert [r["temperature_f"] for r in valid] == [80.0]
    assert rej[REJECT_DUPLICATE_EXACT] == 1
    assert rej[REJECT_DUPLICATE_CONFLICT] == 2
    assert rej[REJECT_WRONG_STATION] == 2
    assert rej[REJECT_MISSING_TEMPERATURE] == 1
    assert rej[REJECT_OUT_OF_RANGE] == 1


# ---------------------------------------------------------------------------
# No lookahead
# ---------------------------------------------------------------------------


def test_delayed_report_not_usable_at_checkpoint():
    recs = [
        _obs(_local(2026, 7, 15, 10, 51), 84.0),
        _obs(_local(2026, 7, 15, 11, 30), 86.0),  # special, available 11:50
        _obs(_local(2026, 7, 15, 11, 51), 90.0),  # available 12:11 → not usable at 12:00
    ]
    for hh in range(0, 10):
        recs.append(_obs(_local(2026, 7, 15, hh, 51), 70.0 + hh))
    valid, _ = validate_observations(recs)
    res = observed_high_asof(
        valid, target_date="2026-07-15", checkpoint_as_of=checkpoint_as_of_utc("2026-07-15", "d0_1200")
    )
    assert res["status"] == OBS_STATUS_OK
    assert res["observed_high_so_far_f"] == 86.0
    assert res["n_delayed_at_checkpoint"] == 1
    assert res["n_after_checkpoint"] == 0
    assert res["newest_obs_time_utc"] == _local(2026, 7, 15, 11, 30).astimezone(UTC).isoformat()


def test_supplied_late_availability_overrides_assumed_lag():
    late = _obs(
        _local(2026, 7, 15, 11, 0),
        95.0,
        availability_time_utc=_local(2026, 7, 15, 12, 30).astimezone(UTC).isoformat(),
    )
    recs = [late] + [_obs(_local(2026, 7, 15, hh, 51), 70.0) for hh in range(0, 11)]
    valid, _ = validate_observations(recs)
    res = observed_high_asof(
        valid, target_date="2026-07-15", checkpoint_as_of=checkpoint_as_of_utc("2026-07-15", "d0_1200")
    )
    assert res["observed_high_so_far_f"] == 70.0
    assert res["n_delayed_at_checkpoint"] == 1


def test_future_observation_after_checkpoint_rejected():
    recs = [_obs(_local(2026, 7, 15, hh, 51), 70.0) for hh in range(0, 5)]
    recs.append(_obs(_local(2026, 7, 15, 14, 0), 99.0))
    valid, _ = validate_observations(recs)
    res = observed_high_asof(
        valid, target_date="2026-07-15", checkpoint_as_of=checkpoint_as_of_utc("2026-07-15", "d0_0600")
    )
    assert res["observed_high_so_far_f"] == 70.0
    assert res["n_after_checkpoint"] == 1


# ---------------------------------------------------------------------------
# Climate-day midnight + DST
# ---------------------------------------------------------------------------


def test_climate_day_is_local_standard_time_all_year():
    for d in ("2026-01-15", "2026-03-08", "2026-07-06", "2026-11-01"):
        s, e = climate_day_bounds_utc(d)
        assert s.hour == 5 and s.minute == 0
        assert (e - s) == timedelta(hours=24)


def test_edt_after_midnight_report_belongs_to_previous_climate_day():
    # Real 2026-07-06 case: 00:17 EDT report exceeded the final CLI high.
    recs = [_obs(_local(2026, 7, 6, 0, 17), 71.0)] + [
        _obs(_local(2026, 7, 6, hh, 51), 65.0) for hh in range(1, 5)
    ]
    valid, _ = validate_observations(recs)
    res = observed_high_asof(
        valid, target_date="2026-07-06", checkpoint_as_of=checkpoint_as_of_utc("2026-07-06", "d0_0600")
    )
    assert res["status"] == OBS_STATUS_OK
    assert res["observed_high_so_far_f"] == 65.0
    assert res["window_start_utc"] == _local(2026, 7, 6, 1, 0).astimezone(UTC).isoformat()


def test_local_day_bounds_dst_lengths():
    s, e = local_day_bounds_utc("2026-03-08")  # spring forward
    assert s == datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
    assert (e - s) == timedelta(hours=23)
    s, e = local_day_bounds_utc("2026-11-01")  # fall back
    assert s == datetime(2026, 11, 1, 4, 0, tzinfo=UTC)
    assert (e - s) == timedelta(hours=25)


@pytest.mark.parametrize(
    "target,before_midnight_utc,after_midnight_utc",
    [
        ("2026-03-08", datetime(2026, 3, 8, 4, 51, tzinfo=UTC), datetime(2026, 3, 8, 5, 5, tzinfo=UTC)),
        ("2026-11-01", datetime(2026, 11, 1, 4, 51, tzinfo=UTC), datetime(2026, 11, 1, 5, 5, tzinfo=UTC)),
    ],
)
def test_climate_midnight_excludes_previous_evening_on_dst_days(target, before_midnight_utc, after_midnight_utc):
    recs = [_obs(before_midnight_utc, 99.0), _obs(after_midnight_utc, 50.0)]
    cursor = after_midnight_utc + timedelta(hours=1)
    as_of = checkpoint_as_of_utc(target, "d0_0600")
    while cursor < as_of - timedelta(minutes=30):
        recs.append(_obs(cursor, 51.0))
        cursor += timedelta(hours=1)
    valid, _ = validate_observations(recs)
    res = observed_high_asof(valid, target_date=target, checkpoint_as_of=as_of)
    assert res["status"] == OBS_STATUS_OK
    assert res["observed_high_so_far_f"] == 51.0


def test_high_so_far_monotone_across_checkpoints():
    recs = []
    for hh in range(24):
        temp = 70.0 + 10.0 * math.sin(math.pi * max(0, hh - 5) / 14.0) + (hh % 3)
        recs.append(_obs(_local(2026, 7, 15, hh, 51), round(temp)))
    valid, _ = validate_observations(recs)
    highs = [
        observed_high_asof(
            valid, target_date="2026-07-15", checkpoint_as_of=checkpoint_as_of_utc("2026-07-15", cid)
        )["observed_high_so_far_f"]
        for cid in ("d0_0600", "d0_0900", "d0_1200", "d0_1500")
    ]
    assert highs == sorted(highs)


def test_missing_stale_gap_statuses():
    as_of = checkpoint_as_of_utc("2026-07-15", "d0_1200")
    res = observed_high_asof([], target_date="2026-07-15", checkpoint_as_of=as_of)
    assert res["status"] == OBS_STATUS_MISSING
    assert res["observed_high_so_far_f"] is None

    stale = [_obs(_local(2026, 7, 15, hh, 51), 70.0) for hh in range(0, 8)]
    valid, _ = validate_observations(stale)
    assert observed_high_asof(valid, target_date="2026-07-15", checkpoint_as_of=as_of)["status"] == OBS_STATUS_STALE

    gap = [_obs(_local(2026, 7, 15, 0, 51), 70.0)] + [
        _obs(_local(2026, 7, 15, hh, 51), 72.0) for hh in (6, 7, 8, 9, 10)
    ]
    valid, _ = validate_observations(gap)
    assert observed_high_asof(valid, target_date="2026-07-15", checkpoint_as_of=as_of)["status"] == OBS_STATUS_GAP


# ---------------------------------------------------------------------------
# Remaining-day model window
# ---------------------------------------------------------------------------


def _payload(run_init: datetime, hours: int, temp_fn) -> dict:
    times = [(run_init + timedelta(hours=h)) for h in range(hours)]
    return {
        "utc_offset_seconds": 0,
        "hourly": {
            "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
            "temperature_2m": [temp_fn(t) for t in times],
        },
    }


def test_remaining_window_excludes_past_hours():
    run_init = datetime(2026, 7, 15, 6, 0, tzinfo=UTC)
    hot_morning = _local(2026, 7, 15, 9).astimezone(UTC)

    def fn(t):
        return 95.0 if t == hot_morning else 80.0

    series = parse_hourly_series(_payload(run_init, 48, fn))
    early = remaining_day_high(series, target_date="2026-07-15", checkpoint_as_of=checkpoint_as_of_utc("2026-07-15", "d0_0600"))
    late = remaining_day_high(series, target_date="2026-07-15", checkpoint_as_of=checkpoint_as_of_utc("2026-07-15", "d0_1200"))
    assert early["status"] == WINDOW_STATUS_OK and early["model_remaining_day_high_f"] == 95.0
    assert late["status"] == WINDOW_STATUS_OK and late["model_remaining_day_high_f"] == 80.0
    assert late["expected_hours"] == 13  # 12:00 EDT .. 00:00 EDT next day


def test_remaining_window_horizon_short_is_unavailable():
    run_init = datetime(2026, 7, 15, 7, 0, tzinfo=UTC)
    series = parse_hourly_series(_payload(run_init, 18, lambda t: 80.0))  # ends 00Z = 20:00 EDT
    res = remaining_day_high(series, target_date="2026-07-15", checkpoint_as_of=checkpoint_as_of_utc("2026-07-15", "d0_0600"))
    assert res["status"] == WINDOW_STATUS_UNAVAILABLE
    assert res["reason"] == WINDOW_REASON_HORIZON_SHORT
    assert res["model_remaining_day_high_f"] is None


def test_remaining_window_dst_fall_back_dminus1_is_climate_day_24_hours():
    run_init = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    series = parse_hourly_series(_payload(run_init, 72, lambda t: 60.0))
    res = remaining_day_high(series, target_date="2026-11-01", checkpoint_as_of=checkpoint_as_of_utc("2026-11-01", "dminus1_1800"))
    assert res["status"] == WINDOW_STATUS_OK
    assert res["expected_hours"] == 24
    assert res["window_start_utc"] == datetime(2026, 11, 1, 5, 0, tzinfo=UTC).isoformat()  # 01:00 EDT
    assert res["window_end_utc_exclusive"] == datetime(2026, 11, 2, 5, 0, tzinfo=UTC).isoformat()  # 00:00 EST


def _series(start_utc: datetime, hours: int, overrides: dict[datetime, float | None] | None = None, base: float = 80.0):
    overrides = overrides or {}
    out = []
    for h in range(hours):
        t = start_utc + timedelta(hours=h)
        out.append((t, overrides.get(t, base)))
    return out


def test_april_next_day_0000_edt_hour_is_included_and_can_be_the_max():
    d = "2026-04-15"
    midnight_edt_next = _local(2026, 4, 16, 0).astimezone(UTC)  # 04Z = final climate-day hour
    climate_end = midnight_edt_next + timedelta(hours=1)  # 05Z = 00:00 EST, exclusive
    assert climate_end == climate_day_bounds_utc(d)[1]
    series = _series(
        datetime(2026, 4, 15, 6, tzinfo=UTC), 48, {midnight_edt_next: 91.0, climate_end: 99.0}
    )
    res = remaining_day_high(series, target_date=d, checkpoint_as_of=checkpoint_as_of_utc(d, "d0_1200"))
    assert res["status"] == WINDOW_STATUS_OK
    assert res["model_remaining_day_high_f"] == 91.0  # 99 at the exclusive end is ignored
    assert res["expected_hours"] == 13
    assert res["window_end_utc_exclusive"] == climate_end.isoformat()


def test_winter_window_ends_at_midnight_est_exclusive():
    d = "2026-01-15"
    last_hour = datetime(2026, 1, 16, 4, tzinfo=UTC)  # 23:00 EST
    end = datetime(2026, 1, 16, 5, tzinfo=UTC)  # 00:00 EST
    series = _series(datetime(2026, 1, 15, 6, tzinfo=UTC), 48, {last_hour: 85.0, end: 99.0}, base=40.0)
    res = remaining_day_high(series, target_date=d, checkpoint_as_of=checkpoint_as_of_utc(d, "d0_1200"))
    assert res["status"] == WINDOW_STATUS_OK
    assert res["model_remaining_day_high_f"] == 85.0
    assert res["expected_hours"] == 12  # 12:00 .. 23:00 EST
    assert res["window_end_utc_exclusive"] == end.isoformat()


@pytest.mark.parametrize(
    "target,dm1_start,end,d0_1200_hours",
    [
        # Spring forward: climate day 05Z..05Z; noon EDT = 16Z → 16Z..04Z.
        ("2026-03-08", datetime(2026, 3, 8, 5, tzinfo=UTC), datetime(2026, 3, 9, 5, tzinfo=UTC), 13),
        # Fall back: climate day 05Z..05Z; noon EST = 17Z → 17Z..04Z.
        ("2026-11-01", datetime(2026, 11, 1, 5, tzinfo=UTC), datetime(2026, 11, 2, 5, tzinfo=UTC), 12),
    ],
)
def test_dst_transition_dates_use_climate_day_bounds(target, dm1_start, end, d0_1200_hours):
    series = _series(dm1_start - timedelta(hours=12), 72, {end - timedelta(hours=1): 90.0, end: 99.0}, base=50.0)
    dm1 = remaining_day_high(series, target_date=target, checkpoint_as_of=checkpoint_as_of_utc(target, "dminus1_1800"))
    assert dm1["status"] == WINDOW_STATUS_OK
    assert dm1["window_start_utc"] == dm1_start.isoformat()
    assert dm1["window_end_utc_exclusive"] == end.isoformat()
    assert dm1["expected_hours"] == 24
    assert dm1["model_remaining_day_high_f"] == 90.0
    noon = remaining_day_high(series, target_date=target, checkpoint_as_of=checkpoint_as_of_utc(target, "d0_1200"))
    assert noon["status"] == WINDOW_STATUS_OK
    assert noon["expected_hours"] == d0_1200_hours
    assert noon["model_remaining_day_high_f"] == 90.0


def test_dminus1_edt_excludes_target_date_0000_edt_hour():
    d = "2026-07-15"
    target_midnight_edt = _local(2026, 7, 15, 0).astimezone(UTC)  # previous climate day
    series = _series(datetime(2026, 7, 14, 12, tzinfo=UTC), 72, {target_midnight_edt: 99.0})
    res = remaining_day_high(series, target_date=d, checkpoint_as_of=checkpoint_as_of_utc(d, "dminus1_1800"))
    assert res["status"] == WINDOW_STATUS_OK
    assert res["window_start_utc"] == (target_midnight_edt + timedelta(hours=1)).isoformat()
    assert res["model_remaining_day_high_f"] == 80.0


def test_absent_final_hour_is_horizon_short_never_partial_max():
    d = "2026-07-15"
    final = _local(2026, 7, 16, 0).astimezone(UTC)  # 04Z
    start = datetime(2026, 7, 15, 6, tzinfo=UTC)
    series = _series(start, int((final - start).total_seconds() // 3600))  # ends 03Z
    assert series[-1][0] == final - timedelta(hours=1)
    res = remaining_day_high(series, target_date=d, checkpoint_as_of=checkpoint_as_of_utc(d, "d0_1200"))
    assert res["status"] == WINDOW_STATUS_UNAVAILABLE
    assert res["reason"] == WINDOW_REASON_HORIZON_SHORT
    assert res["model_remaining_day_high_f"] is None
    assert res["covered_hours"] == res["expected_hours"] - 1


def test_null_final_hour_is_missing_or_null_never_partial_max():
    d = "2026-07-15"
    final = _local(2026, 7, 16, 0).astimezone(UTC)
    series = _series(datetime(2026, 7, 15, 6, tzinfo=UTC), 48, {final: None})
    res = remaining_day_high(series, target_date=d, checkpoint_as_of=checkpoint_as_of_utc(d, "d0_1200"))
    assert res["status"] == WINDOW_STATUS_UNAVAILABLE
    assert res["reason"] == WINDOW_REASON_MISSING_HOURS
    assert res["model_remaining_day_high_f"] is None


def test_missing_final_hour_makes_version_c_unavailable_and_leaves_b_unchanged():
    d = "2026-07-15"
    g = MODEL_GFS_OPERATIONAL_LATEST
    final = _local(2026, 7, 16, 0).astimezone(UTC)
    start = datetime(2026, 7, 15, 6, tzinfo=UTC)
    short = _series(start, int((final - start).total_seconds() // 3600))
    window = remaining_day_high(short, target_date=d, checkpoint_as_of=checkpoint_as_of_utc(d, "d0_1200"))
    rows = [_p5_row(g, d, "d0_1200", p5_value=85.0)]
    rows_b, rows_c = build_version_rows(rows, {(d, "d0_1200"): _obs_ok(82.0)}, {(g, d, "d0_1200"): window})
    assert rows_c[0]["replay_mode"] == REPLAY_MODE_UNAVAILABLE
    assert WINDOW_REASON_HORIZON_SHORT in rows_c[0]["replay_reason"]
    assert rows_c[0]["model_remaining_day_high_f"] == ""
    assert rows_c[0]["residual_f"] == ""
    assert rows_b[0]["replay_mode"] == REPLAY_MODE_OBS_BRIDGE_DIAGNOSTIC
    assert rows_b[0]["projected_final_high_f"] == 85.0


def test_window_diff_detects_same_count_shift_for_dminus1():
    from research.weather.phase6_window_diff import v1_expected_hours, v2_expected_hours, window_diff

    d = "2026-07-15"
    g = MODEL_GFS_OPERATIONAL_LATEST
    as_of = checkpoint_as_of_utc(d, "dminus1_1800")
    v1 = {
        "model": g, "target_date": d, "checkpoint_id": "dminus1_1800", "checkpoint_as_of": as_of.isoformat(),
        "remaining_window_start_utc": _local(2026, 7, 15, 0).astimezone(UTC).isoformat(),
        "remaining_window_expected_hours": "24", "remaining_window_status": "OK",
        "remaining_day_model_high_f": "80.0", "replay_mode": REPLAY_MODE_FULL_OPERATIONAL,
    }
    s, e = climate_day_bounds_utc(d)
    v2 = dict(v1, remaining_window_start_utc=s.isoformat(), remaining_window_end_utc_exclusive=e.isoformat(),
              remaining_day_model_high_f="82.0")
    h1, h2 = v1_expected_hours(v1), v2_expected_hours(v2)
    assert len(h1) == len(h2) == 24 and set(h1) != set(h2)
    out = window_diff([v1], [v2])
    c = out["by_model_checkpoint"][g]["dminus1_1800"]
    assert c["rows_gained_final_hour_only"] == 1
    assert c["rows_lost_leading_hour_only"] == 1
    assert c["rows_shifted_same_count"] == 1
    assert c.get("window_identical", 0) == 0
    assert c["max_changed"] == 1
    assert out["v1_reconstruction_check"]["n_mismatches"] == 0


def test_phase6_v2_paths_do_not_collide_with_v1():
    assert phase6_mod.PHASE6_VERSION == "v2"
    v1_paths = set(phase6_mod.calibration_paths("v1").values()) | set(phase6_mod.result_paths("v1").values())
    current = {
        phase6_mod.GFS_OBS_REPLAY_CSV, phase6_mod.HRRR_OBS_REPLAY_CSV,
        phase6_mod.GFS_OBS_BRIDGE_CSV, phase6_mod.HRRR_OBS_BRIDGE_CSV,
        phase6_mod.ASOF_OBS_CSV, phase6_mod.OBS_NORMALIZED_CSV,
        phase6_mod.PHASE6_COVERAGE_PATH, phase6_mod.PHASE6_COMPARISON_PATH,
        phase6_mod.PHASE6_SHADOW_EVAL_PATH, phase6_mod.PHASE6_METHODOLOGY_PATH,
    }
    assert not (current & v1_paths)
    assert all("_v2." in p.name for p in current)


def test_hourly_payload_must_be_utc():
    with pytest.raises(ValueError):
        parse_hourly_series({"utc_offset_seconds": -14400, "hourly": {"time": [], "temperature_2m": []}})


# ---------------------------------------------------------------------------
# Version rows, labels, max() semantics
# ---------------------------------------------------------------------------


def _p5_row(model, target_date, cid, *, p5_value, actual=80.0, init="2026-07-15T06:00:00+00:00"):
    return {
        "event_ticker": "EVT",
        "target_date": target_date,
        "checkpoint_id": cid,
        "checkpoint_as_of": checkpoint_as_of_utc(target_date, cid).isoformat(),
        "model": model,
        "selected_run_init": init,
        "available_at": init,
        "run_id": "r",
        "forecast_high_f": str(p5_value),
        "model_remaining_day_high_f": str(p5_value),
        "observed_high_so_far_f": "",
        "projected_final_high_f": str(p5_value),
        "actual_high_f": str(actual),
        "residual_f": str(actual - p5_value),
        "target_regime": "nws_cli_knyc",
        "replay_mode": phase5_mod.replay_mode_for_checkpoint(cid),
        "winning_bucket_label": "79° to 80°",
        "month": "7",
        "season": "summer",
        "provenance": "p5",
    }


def _obs_ok(high):
    return {"status": OBS_STATUS_OK, "observed_high_so_far_f": high, "reason": None, "n_usable": 10, "n_not_yet_available": 1}


def _win_ok(high):
    return {"status": WINDOW_STATUS_OK, "model_remaining_day_high_f": high, "reason": None}


def test_version_rows_max_semantics_and_labels():
    d = "2026-07-15"
    g = MODEL_GFS_OPERATIONAL_LATEST
    rows = [
        _p5_row(g, d, "d0_1200", p5_value=85.0),  # obs binds in C, not in B
        _p5_row(g, d, "d0_1500", p5_value=78.0),  # obs missing → MODEL_ONLY
        _p5_row(g, d, "d0_0900", p5_value=79.0),  # window unavailable
        _p5_row(g, d, "dminus1_1800", p5_value=81.0),
    ]
    asof = {
        (d, "d0_1200"): _obs_ok(82.0),
        (d, "d0_1500"): {"status": OBS_STATUS_MISSING, "observed_high_so_far_f": None, "reason": "none"},
        (d, "d0_0900"): _obs_ok(75.0),
    }
    windows = {
        (g, d, "d0_1200"): _win_ok(79.0),
        (g, d, "d0_1500"): _win_ok(77.0),
        (g, d, "d0_0900"): {"status": WINDOW_STATUS_UNAVAILABLE, "reason": WINDOW_REASON_HORIZON_SHORT},
        (g, d, "dminus1_1800"): _win_ok(80.0),
    }
    rows_b, rows_c = build_version_rows(rows, asof, windows)
    b = {r["checkpoint_id"]: r for r in rows_b}
    c = {r["checkpoint_id"]: r for r in rows_c}

    assert b["d0_1200"]["projected_final_high_f"] == max(82.0, 85.0)
    assert b["d0_1200"]["replay_mode"] == REPLAY_MODE_OBS_BRIDGE_DIAGNOSTIC
    assert c["d0_1200"]["projected_final_high_f"] == max(82.0, 79.0)
    assert c["d0_1200"]["replay_mode"] == REPLAY_MODE_FULL_OPERATIONAL
    assert c["d0_1200"]["obs_binding"] is True
    assert c["d0_1200"]["residual_f"] == pytest.approx(80.0 - 82.0)

    assert c["d0_1500"]["replay_mode"] == REPLAY_MODE_MODEL_ONLY
    assert c["d0_1500"]["observed_high_so_far_f"] == ""  # never the final daily max
    assert c["d0_1500"]["projected_final_high_f"] == 77.0
    assert "obs_MISSING" in c["d0_1500"]["replay_reason"]
    assert b["d0_1500"]["replay_mode"] == REPLAY_MODE_MODEL_ONLY

    assert c["d0_0900"]["replay_mode"] == REPLAY_MODE_UNAVAILABLE
    assert c["d0_0900"]["residual_f"] == ""
    assert WINDOW_REASON_HORIZON_SHORT in c["d0_0900"]["replay_reason"]

    assert c["dminus1_1800"]["replay_mode"] == REPLAY_MODE_FULL_OPERATIONAL
    assert c["dminus1_1800"]["projected_final_high_f"] == 80.0
    # No row using the Phase 5 full-run max is ever labeled FULL.
    assert all(r["replay_mode"] != REPLAY_MODE_FULL_OPERATIONAL for r in rows_b)
    # Phase 5 rows untouched.
    assert rows[0]["replay_mode"] == "MODEL_ONLY_REPLAY"
    assert rows[0]["observed_high_so_far_f"] == ""


def test_phase5_global_flag_not_flipped():
    assert phase5_mod.HISTORICAL_ASOF_OBS_AVAILABLE is False
    assert phase5_mod.replay_mode_for_checkpoint("d0_1200") == REPLAY_MODE_MODEL_ONLY


def test_eligible_keys_require_both_models_full():
    d = "2026-07-15"
    rows = [
        {"target_date": d, "checkpoint_id": "d0_1200", "model": MODEL_GFS_OPERATIONAL_LATEST, "replay_mode": REPLAY_MODE_FULL_OPERATIONAL, "residual_f": 1.0},
        {"target_date": d, "checkpoint_id": "d0_1200", "model": MODEL_HRRR_OPERATIONAL_LATEST, "replay_mode": REPLAY_MODE_UNAVAILABLE, "residual_f": ""},
        {"target_date": d, "checkpoint_id": "d0_1500", "model": MODEL_GFS_OPERATIONAL_LATEST, "replay_mode": REPLAY_MODE_FULL_OPERATIONAL, "residual_f": 1.0},
        {"target_date": d, "checkpoint_id": "d0_1500", "model": MODEL_HRRR_OPERATIONAL_LATEST, "replay_mode": REPLAY_MODE_FULL_OPERATIONAL, "residual_f": -1.0},
    ]
    assert eligible_keys(rows) == {(d, "d0_1500")}
    assert len(restrict_rows(rows, {(d, "d0_1500")})) == 2


def test_common_cohort_requires_shadow_in_every_version():
    k1, k2, k3 = ("2026-07-01", "d0_1200"), ("2026-07-02", "d0_1200"), ("2026-07-03", "d0_1200")
    paired = {
        VERSION_A: {k1: {"shadow_probs": {"x": 1}}, k2: {"shadow_probs": {"x": 1}}, k3: {"shadow_probs": {"x": 1}}},
        VERSION_B: {k1: {"shadow_probs": {"x": 1}}, k2: {"shadow_probs": None}, k3: {"shadow_probs": {"x": 1}}},
        VERSION_C: {k1: {"shadow_probs": {"x": 1}}, k3: {"shadow_probs": {"x": 1}}},
    }
    assert common_cohort_keys(paired) == [k1, k3]


# ---------------------------------------------------------------------------
# Walk-forward + shadow invariance
# ---------------------------------------------------------------------------


def _markets(event_ticker: str) -> list[dict]:
    out = [{"event_ticker": event_ticker, "ticker": f"{event_ticker}-T60", "yes_sub_title": "60° or below"}]
    for lo in range(61, 99, 2):
        out.append({"event_ticker": event_ticker, "ticker": f"{event_ticker}-B{lo}", "yes_sub_title": f"{lo}° to {lo + 1}°"})
    out.append({"event_ticker": event_ticker, "ticker": f"{event_ticker}-T99", "yes_sub_title": "99° or above"})
    return out


def _winner_label(actual: int) -> str:
    if actual <= 60:
        return "60° or below"
    if actual >= 99:
        return "99° or above"
    lo = actual if actual % 2 == 1 else actual - 1
    return f"{lo}° to {lo + 1}°"


def test_walk_forward_pools_only_earlier_dates_same_checkpoint(monkeypatch):
    seen: list[tuple[str, list[str]]] = []
    real = phase6_mod.predict_operational_calibrated

    def spy(*, forecast_high, prior_rows, target_date, buckets):
        seen.append((target_date, [str(r["target_date"]) for r in prior_rows], {r["checkpoint_id"] for r in prior_rows}))
        return real(forecast_high=forecast_high, prior_rows=prior_rows, target_date=target_date, buckets=buckets)

    monkeypatch.setattr(phase6_mod, "predict_operational_calibrated", spy)
    rows = []
    start = date(2026, 4, 1)
    for i in range(30):
        d = (start + timedelta(days=i)).isoformat()
        for model in (MODEL_GFS_OPERATIONAL_LATEST, MODEL_HRRR_OPERATIONAL_LATEST):
            for cid in ("d0_1200", "d0_1500"):
                rows.append(
                    {"event_ticker": "EVT", "target_date": d, "checkpoint_id": cid, "model": model,
                     "target_regime": "nws_cli_knyc", "forecast_high_f": 70.0, "residual_f": 1.0,
                     "actual_high_f": 71.0, "winning_bucket_label": "71° to 72°", "month": 4}
                )
    evaluate_version_checkpoint(
        gfs_rows=[r for r in rows if r["model"] == MODEL_GFS_OPERATIONAL_LATEST],
        hrrr_rows=[r for r in rows if r["model"] == MODEL_HRRR_OPERATIONAL_LATEST],
        markets_by_event={"EVT": _markets("EVT")},
        checkpoint_id="d0_1200",
    )
    assert seen
    for target_date, prior_dates, cps in seen:
        assert all(p < target_date for p in prior_dates)
        assert cps <= {"d0_1200"}


def _synthetic_world(n_days: int = 95):
    """Phase 5 rows + obs + hourly payloads for a deterministic fake world."""
    start = date(2026, 4, 1)
    gfs_rows, hrrr_rows, obs, markets = [], [], [], []
    for i in range(n_days):
        d = start + timedelta(days=i)
        ds = d.isoformat()
        et = f"EVT{i:03d}"
        actual = 66 + (i * 7) % 17
        markets.extend(_markets(et))
        # Diurnal obs: peak at 15:51 local == actual.
        for hh in range(24):
            shape = max(0.0, math.sin(math.pi * max(0, hh - 5) / 20.0))
            obs.append(_obs(_local(d.year, d.month, d.day, hh, 51), float(round(actual - 12 + 12 * shape))))
        for cid in ("dminus1_1800", "d0_0600", "d0_0900", "d0_1200", "d0_1500"):
            as_of = checkpoint_as_of_utc(ds, cid)
            for model, bias, rows in (
                (MODEL_GFS_OPERATIONAL_LATEST, -1.5 + (i % 5) * 0.7, gfs_rows),
                (MODEL_HRRR_OPERATIONAL_LATEST, 1.0 - (i % 3) * 0.9, hrrr_rows),
            ):
                init = (as_of - timedelta(hours=7)).replace(minute=0)
                p5 = actual + bias + (2.0 if cid == "d0_1500" and model == MODEL_HRRR_OPERATIONAL_LATEST else 0.0)
                row = _p5_row(model, ds, cid, p5_value=p5, actual=float(actual), init=init.isoformat())
                row["event_ticker"] = et
                row["winning_bucket_label"] = _winner_label(actual)
                row["month"] = str(d.month)
                row["season"] = "spring" if d.month in (3, 4, 5) else "summer"
                row["actual_high_f"] = str(float(actual))
                rows.append(row)
    return gfs_rows, hrrr_rows, obs, markets


def _fake_fetcher(model, run_init, refresh=False):
    def fn(t):
        local = t.astimezone(NYC)
        return 70.0 + 8.0 * math.sin(math.pi * max(0, local.hour - 6) / 16.0)

    return {"status": "ok", "payload": _payload(run_init, 60, fn), "meta": {"from_cache": True}}


def test_end_to_end_synthetic_versions_and_shadow_invariance(tmp_path, monkeypatch):
    hyp_path = tmp_path / "shadow.json"
    monkeypatch.setattr("research.weather.shadow.SHADOW_HYPOTHESIS_PATH", hyp_path)
    monkeypatch.setattr("research.weather.shadow.HYPOTHESES_DIR", tmp_path)
    monkeypatch.setattr(phase6_mod, "SHADOW_HYPOTHESIS_PATH", hyp_path)
    from research.weather.shadow import load_or_register_shadow_hypothesis

    load_or_register_shadow_hypothesis()
    before = hyp_path.read_bytes()

    gfs_rows, hrrr_rows, obs, markets = _synthetic_world()
    result = run_phase6_obs_replay(
        markets,
        obs_loader=lambda start, end, refresh=False, progress=None: (obs, [{"status": "ok"}]),
        window_fetcher=_fake_fetcher,
        gfs_phase5_rows=gfs_rows,
        hrrr_phase5_rows=hrrr_rows,
        write_outputs=False,
    )
    assert hyp_path.read_bytes() == before
    assert result["shadow_eval"]["hypothesis_file_sha256_before"] == result["shadow_eval"]["hypothesis_file_sha256_after"]

    cohort = result["cohort"]
    assert cohort, "synthetic world should produce a scored common cohort"
    paired = result["paired_by_version"]
    for version in (VERSION_A, VERSION_B, VERSION_C):
        for k in cohort:
            rec = paired[version][k]
            for label, p in rec["shadow_probs"].items():
                assert p == pytest.approx(0.5 * rec["gfs_probs"][label] + 0.5 * rec["hrrr_probs"][label], abs=1e-12)
    # Identical cohort in all versions; C rows in cohort are all FULL.
    c_modes = {(r["target_date"], r["checkpoint_id"], r["model"]): r["replay_mode"] for r in result["rows_c"]}
    for d, cid in cohort:
        assert c_modes[(d, cid, MODEL_GFS_OPERATIONAL_LATEST)] == REPLAY_MODE_FULL_OPERATIONAL
        assert c_modes[(d, cid, MODEL_HRRR_OPERATIONAL_LATEST)] == REPLAY_MODE_FULL_OPERATIONAL
    comp = result["comparison"]["common_cohort"]
    n_by_version = {v: comp["results"][v]["pooled_all"]["n"] for v in (VERSION_A, VERSION_B, VERSION_C)}
    assert len(set(n_by_version.values())) == 1
    assert comp["transitions"]["pooled_intraday"]["A_to_B_obs_effect_old_window"]["gfs"]["bootstrap_unit"] == "event_date"
    # dminus1: A and B are identical inputs → zero A→B change.
    dm1 = comp["transitions"]["dminus1_1800"]["A_to_B_obs_effect_old_window"]["gfs"]
    if dm1["n_obs"]:
        assert dm1["mean"] == pytest.approx(0.0)
    assert result["coverage"]["intraday_result_computable"] is True

"""
Historical weather forecast utilities for Kalshi Edge Lab.

Version 0.6

First goal:

Retrieve what NOAA GFS was forecasting roughly 24 hours
before a historical NYC daily-high event.

We will test ONE historical date before attempting to
download hundreds of dates.
"""
import json

from datetime import datetime, timedelta
from pathlib import Path

import requests

from kalshi import cache as disk_cache


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

PREVIOUS_RUNS_URL = (
    "https://previous-runs-api.open-meteo.com/v1/forecast"
)

SINGLE_RUNS_URL = (
    "https://single-runs-api.open-meteo.com/v1/forecast"
)

NYC_LATITUDE = 40.77
NYC_LONGITUDE = -73.97

# Use the pure global NOAA GFS model.
#
# Do NOT use ncep_gfs_seamless for this experiment.
# In the United States, the seamless product can combine
# GFS with higher-resolution NOAA models such as HRRR.
#
# We want one consistent model so our historical
# calibration actually measures GFS.
GFS_MODEL = "ncep_gfs_global"

# Legacy root-level cache (still read for compatibility).
CACHE_FILE = Path("gfs_run_cache.json")

# Preferred weather-research cache location.
WEATHER_GFS_CACHE = (
    disk_cache.CACHE_ROOT / "weather" / "gfs" / "gfs_run_cache.json"
)


# ---------------------------------------------------------
# HISTORICAL FORECAST
# ---------------------------------------------------------

def load_gfs_cache() -> dict:
    """
    Load previously downloaded GFS forecasts.

    Prefer data/cache/weather/gfs/; fall back to root
    gfs_run_cache.json during migration.
    """

    for path in (WEATHER_GFS_CACHE, CACHE_FILE):
        if not path.exists():
            continue
        try:
            payload = disk_cache.read_json(path, default=None)
            if isinstance(payload, dict):
                return payload
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return {}


def save_gfs_cache(
    cache: dict,
) -> None:
    """
    Save downloaded GFS forecasts to disk (atomic write).
    """

    try:
        WEATHER_GFS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        disk_cache.write_json(WEATHER_GFS_CACHE, cache)
    except OSError as error:
        print(
            "Could not save GFS cache: "
            f"{error}"
        )

def get_gfs_24h_high_forecast(
    date: str,
) -> float | None:
    """
    Retrieve GFS temperatures that were forecast
    approximately 24 hours before each valid hour
    of the requested date.

    Example:

        date = "2026-07-12"

    We retrieve:

        temperature_2m_previous_day1

    and then find the maximum temperature for that day.

    This gives us a useful 24-hour-lead forecast estimate
    for historical calibration.
    """

    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,

        # Only retrieve the date we are testing.
        "start_date": date,
        "end_date": date,

        # previous_day1 means the forecast value
        # from approximately 24 hours before
        # each valid time.
        "hourly": (
            "temperature_2m_previous_day1"
        ),

        "temperature_unit": "fahrenheit",

        "timezone": "America/New_York",

        "models": GFS_MODEL,
    }

    try:
        response = requests.get(
            PREVIOUS_RUNS_URL,
            params=params,
            timeout=20,
        )

        response.raise_for_status()

    except requests.RequestException as error:

        print(
            "Could not retrieve historical "
            f"GFS forecast: {error}"
        )

        return None

    data = response.json()

    hourly = data.get(
        "hourly",
        {},
    )

    temperatures = hourly.get(
        "temperature_2m_previous_day1",
        [],
    )

    # Remove any missing values.
    valid_temperatures = [
        float(temperature)
        for temperature in temperatures
        if temperature is not None
    ]

    if not valid_temperatures:
        return None

    # The highest hourly forecast temperature
    # becomes our forecast high for the day.
    return max(
        valid_temperatures
    )

def get_gfs_run_high(
    date: str,
    run_date_offset: int,
    run_hour: int,
    ) -> float | None:
    """
    Retrieve the NYC high-temperature forecast from
    the previous day's 12 UTC GFS model run.

    Example:

        Target date:
            2026-07-12

        Model run:
            2026-07-11T12:00 UTC

    This is different from previous_day1.

    previous_day1 gives us a rolling 24-hour lead time
    for every valid forecast hour.

    This function instead retrieves ONE specific GFS
    model run and uses that same run for the entire
    target day.

    That is much closer to recreating information that
    could actually have been available to a trader at
    a specific point in time.
    """

    # Convert the date string into a Python datetime.
    target_date = datetime.strptime(
        date,
        "%Y-%m-%d",
    )

    # Choose which calendar date contains the model run.
    #
    # Examples:
    #
    # run_date_offset = -1
    #     previous calendar day
    #
    # run_date_offset = 0
    #     same calendar day as the Kalshi event
    run_date = (
        target_date
        + timedelta(
            days=run_date_offset
        )
    )

    # GFS runs several times per day.
    #
    # For now we are standardizing on the previous
    # day's 12 UTC run.
    #
    # During daylight saving time in New York:
    #
    #     12 UTC = 8:00 AM EDT
    #
    # During standard time:
    #
    #     12 UTC = 7:00 AM EST
    #
    # The model output takes time to become available,
    # so this should comfortably represent information
    # available by later that afternoon/evening.
    run = (
        run_date.strftime(
            "%Y-%m-%d"
        )
        + f"T{run_hour:02d}:00"
    )

        # -----------------------------------------------------
    # CHECK LOCAL CACHE FIRST
    # -----------------------------------------------------

    # The cache key uniquely identifies:
    #
    # target date + exact model run.
    #
        # Include the model name in the key.
    #
    # This is VERY important.
    #
    # A forecast from:
    #
    #     ncep_gfs_seamless
    #
    # is not interchangeable with:
    #
    #     ncep_gfs_global
    #
    # Example:
    #
    # ncep_gfs_global|2026-07-12|2026-07-12T00:00
    cache_key = (
        f"{GFS_MODEL}|{date}|{run}"
    )
    # Legacy keys written before model-name prefix.
    legacy_cache_key = f"{date}|{run}"

    cache = load_gfs_cache()

    if cache_key in cache:
        return float(cache[cache_key])
    if legacy_cache_key in cache:
        return float(cache[legacy_cache_key])

    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,

        # Request HOURLY temperatures from this exact
        # historical GFS model run.
        #
        # We cannot ask Single Runs to calculate a
        # daily maximum for a 12Z run, so we will
        # calculate the target day's maximum ourselves.
        "hourly": "temperature_2m",

        "temperature_unit": "fahrenheit",

        # This makes the returned timestamps use
        # New York local time.
        "timezone": "America/New_York",

        "models": GFS_MODEL,

        # Request ONE exact historical GFS run.
        "run": run,
    }

    try:
        response = requests.get(
            SINGLE_RUNS_URL,
            params=params,
            timeout=20,
        )

        # If Open-Meteo rejects our request, print the
        # actual API error message before raising the
        # HTTP exception.
        if not response.ok:

            print(
                "Open-Meteo error response:"
            )

            print(
                response.text
            )

            response.raise_for_status()

    except requests.RequestException as error:

        print(
            "Could not retrieve exact historical "
            f"GFS run: {error}"
        )

        return None

    data = response.json()

    hourly = data.get(
        "hourly",
        {},
    )

    times = hourly.get(
        "time",
        [],
    )

    temperatures = hourly.get(
        "temperature_2m",
        [],
    )

    # -----------------------------------------------------
    # FIND ONLY HOURS FROM THE TARGET DATE
    # -----------------------------------------------------
    #
    # Single Runs returns the full forecast horizon.
    #
    # Example timestamps:
    #
    #     2026-07-11T08:00
    #     2026-07-11T09:00
    #     ...
    #     2026-07-12T00:00
    #     2026-07-12T01:00
    #
    # We only want temperatures whose local New York
    # date matches the Kalshi event we are testing.

    target_temperatures = []

    for (
        timestamp,
        temperature,
    ) in zip(
        times,
        temperatures,
    ):

        # Skip missing temperature values.
        if temperature is None:
            continue

        # The timestamp starts with YYYY-MM-DD.
        #
        # Example:
        #
        #     "2026-07-12T14:00"
        #
        # starts with:
        #
        #     "2026-07-12"
        if not timestamp.startswith(
            date
        ):
            continue

        target_temperatures.append(
            float(
                temperature
            )
        )

    # If the selected model run does not extend far
    # enough to reach our target date, return None.
    if not target_temperatures:
        return None

        # Our forecast high is the highest hourly
    # 2-meter temperature from that exact GFS run
    # during the target New York calendar date.
    forecast_high = max(
        target_temperatures
    )

    # Save this result so future backtests can reuse
    # it without another API request.
    cache[
        cache_key
    ] = forecast_high

    save_gfs_cache(
        cache
    )

    return forecast_high


# ---------------------------------------------------------
# TEST
# ---------------------------------------------------------

def main() -> None:
    """
    Test one historical event before connecting this
    to the full backtesting pipeline.
    """

    # Test several recent historical dates before we
    # connect this API to the full 1,255-event dataset.
    #
    # We already know the winning Kalshi buckets for
    # these dates from backtest.py.
    test_events = [
        {
            "date": "2026-07-12",
            "winner": "84° to 85°",
        },
        {
            "date": "2026-07-11",
            "winner": "81° to 82°",
        },
        {
            "date": "2026-07-10",
            "winner": "86° or below",
        },
        {
            "date": "2026-07-09",
            "winner": "82° or below",
        },
        {
            "date": "2026-07-08",
            "winner": "84° or above",
        },
    ]

        # Compare several exact GFS model runs.
    #
    # run_date_offset:
    #
    #   -1 = previous calendar day
    #    0 = same calendar day as the Kalshi event
    #
    # run_hour is always UTC.
    run_configs = [
        {
            "label": "Previous day 12Z",
            "date_offset": -1,
            "hour": 12,
        },
        {
            "label": "Previous day 18Z",
            "date_offset": -1,
            "hour": 18,
        },
        {
            "label": "Event day 00Z",
            "date_offset": 0,
            "hour": 0,
        },
        {
            "label": "Event day 06Z",
            "date_offset": 0,
            "hour": 6,
        },
    ]

    print()
    print(
        "KALSHI EDGE LAB — HISTORICAL WEATHER"
    )
    print()

    for event in test_events:

        date = event["date"]
        winner = event["winner"]

        forecast_high = (
            get_gfs_24h_high_forecast(
                date
            )
        )

        print(
            f"Date: {date}"
        )

        print(
            f"Kalshi winner: {winner}"
        )

        # First show the rolling 24-hour-lead forecast
        # that we tested earlier.
        if forecast_high is None:

            print(
                "GFS ~24h forecast: unavailable"
            )

        else:

            print(
                f"GFS ~24h forecast: "
                f"{forecast_high:.1f}°F"
            )

        # -------------------------------------------------
        # EXACT GFS RUNS
        # -------------------------------------------------

        for run_config in run_configs:

            run_high = (
                get_gfs_run_high(
                    date,
                    run_date_offset=(
                        run_config[
                            "date_offset"
                        ]
                    ),
                    run_hour=(
                        run_config[
                            "hour"
                        ]
                    ),
                )
            )

            label = run_config[
                "label"
            ]

            if run_high is None:

                print(
                    f"{label}: unavailable"
                )

            else:

                print(
                    f"{label}: "
                    f"{run_high:.1f}°F"
                )

        print()


if __name__ == "__main__":
    main()
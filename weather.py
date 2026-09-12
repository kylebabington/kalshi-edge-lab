"""
Weather data utilities for Kalshi Edge Lab.

Version 0.2

This module retrieves independent weather forecast data.

IMPORTANT:
This forecast does NOT determine whether a Kalshi contract wins.

Kalshi contracts resolve according to the settlement source
specified in each contract's rules.

We use weather forecasts only to estimate what may happen.
"""

import requests


# ---------------------------------------------------------
# OPEN-METEO CONFIGURATION
# ---------------------------------------------------------

# Open-Meteo's NOAA GFS/HRRR forecast endpoint.
OPEN_METEO_URL = "https://api.open-meteo.com/v1/gfs"

# Open-Meteo's endpoint for probabilistic ensemble forecasts.
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


# NOAA's Global Ensemble Forecast System.
#
# Instead of giving us one forecast, GEFS gives us many
# slightly different forecast scenarios.
ENSEMBLE_MODEL = "ncep_gefs_seamless"


# Approximate coordinates for the Central Park weather station
# area in New York City.
#
# Kalshi's NYC temperature market references NYC weather data,
# so we want our forecast geographically close to that station.
NYC_LATITUDE = 40.77
NYC_LONGITUDE = -73.97


def get_nyc_high_forecast() -> list[dict]:
    """
    Retrieve the daily maximum-temperature forecast for NYC.

    Returns data in this format:

        [
            {
                "date": "2026-09-12",
                "high": 78.4
            },
            ...
        ]

    If the request fails, return an empty list.
    """

    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,

        # We only care about the daily high right now.
        "daily": "temperature_2m_max",

        # Kalshi temperature contracts are in Fahrenheit.
        "temperature_unit": "fahrenheit",

        # Daily weather values require a timezone.
        #
        # This makes "today" correspond to New York time
        # rather than UTC.
        "timezone": "America/New_York",

        # Today + tomorrow is plenty for version 0.2.
        "forecast_days": 2,
    }

    try:
        response = requests.get(
            OPEN_METEO_URL,
            params=params,
            timeout=10,
        )

        response.raise_for_status()

    except requests.RequestException as error:
        print(
            f"Could not retrieve Open-Meteo forecast: {error}"
        )

        return []

    data = response.json()

    # Open-Meteo returns daily information like:
    #
    # "daily": {
    #     "time": ["2026-09-12", "2026-09-13"],
    #     "temperature_2m_max": [78.4, 80.1]
    # }
    daily = data.get("daily", {})

    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])

    forecasts = []

    # zip() lets us pair:
    #
    # date[0] with high[0]
    # date[1] with high[1]
    # etc.
    for date, high in zip(dates, highs):
        forecasts.append(
            {
                "date": date,
                "high": high,
            }
        )

    return forecasts

def get_nyc_high_ensemble() -> dict[str, list[float]]:
    """
    Retrieve NOAA GEFS ensemble forecasts for NYC daily highs.

    Instead of returning one forecast temperature per day,
    this returns many possible forecast temperatures.

    Example:

        {
            "2026-09-12": [
                77.8,
                78.4,
                79.1,
                ...
            ],

            "2026-09-13": [
                78.1,
                79.0,
                77.6,
                ...
            ],
        }

    Each temperature represents one ensemble forecast member.

    The spread between members gives us information about
    forecast uncertainty.
    """

    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,

        # Ask for the maximum temperature for each day.
        "daily": "temperature_2m_max",

        # Use NOAA's GEFS ensemble.
        "models": ENSEMBLE_MODEL,

        # Kalshi uses Fahrenheit for these contracts.
        "temperature_unit": "fahrenheit",

        # Make sure dates correspond to New York calendar days.
        "timezone": "America/New_York",

        # We currently only care about today and tomorrow.
        "forecast_days": 2,
    }

    try:
        response = requests.get(
            ENSEMBLE_URL,
            params=params,
            timeout=10,
        )

        response.raise_for_status()

    except requests.RequestException as error:
        print(
            f"Could not retrieve ensemble forecast: {error}"
        )

        return {}

    data = response.json()

    daily = data.get(
        "daily",
        {},
    )

    dates = daily.get(
        "time",
        [],
    )

    # -----------------------------------------------------
    # FIND ALL TEMPERATURE MEMBER COLUMNS
    # -----------------------------------------------------
    #
    # Open-Meteo returns ensemble data with fields that
    # look approximately like:
    #
    # temperature_2m_max
    # temperature_2m_max_member01
    # temperature_2m_max_member02
    # temperature_2m_max_member03
    # ...
    #
    # Instead of hard-coding every member name, we'll
    # dynamically discover every temperature column.
    # -----------------------------------------------------

    temperature_keys = []

    for key in daily.keys():

        if (
            key == "temperature_2m_max"
            or key.startswith(
                "temperature_2m_max_member"
            )
        ):
            temperature_keys.append(key)

    ensemble_forecasts = {}

    # -----------------------------------------------------
    # Build one list of temperatures for each date.
    # -----------------------------------------------------

    for date_index, date in enumerate(dates):

        member_temperatures = []

        for key in temperature_keys:

            values = daily.get(
                key,
                [],
            )

            # Protect against malformed/incomplete API data.
            if date_index >= len(values):
                continue

            temperature = values[date_index]

            # Some weather-model members can occasionally
            # contain missing/null values.
            if temperature is None:
                continue

            member_temperatures.append(
                float(temperature)
            )

        ensemble_forecasts[date] = (
            member_temperatures
        )

    return ensemble_forecasts
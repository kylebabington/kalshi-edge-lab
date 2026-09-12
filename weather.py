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
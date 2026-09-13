"""
Official NWS observation utilities for Kalshi Edge Lab.

Version 0.4

This module retrieves actual weather observations from:

    KNYC — New York City, Central Park

Unlike weather.py, which contains forecast/model data,
this module deals with observations that have actually occurred.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import requests


# ---------------------------------------------------------
# NWS CONFIGURATION
# ---------------------------------------------------------

NWS_API_BASE = "https://api.weather.gov"

# Iowa Environmental Mesonet provides a parsed archive
# of official NWS Daily Climate Report (CLI) products.
#
# This lets us retrieve the final daily high used in
# historical climate reports instead of trying to
# reconstruct it from hourly observations.
IEM_CLI_URL = (
    "https://mesonet.agron.iastate.edu/json/cli.py"
)

# Official Central Park observation station.
STATION_ID = "KNYC"

# We always want to interpret observations in New York time,
# regardless of the timezone of the computer running Edge Lab.
NYC_TIMEZONE = ZoneInfo("America/New_York")


# The National Weather Service requires a User-Agent header.
#
# We are not inventing an email address here.
# This simply identifies our application.
NWS_HEADERS = {
    "User-Agent": "KalshiEdgeLab/0.4 (personal research project)",
    "Accept": "application/geo+json",
}


# ---------------------------------------------------------
# TEMPERATURE HELPERS
# ---------------------------------------------------------

def celsius_to_fahrenheit(celsius: float) -> float:
    """
    Convert Celsius into Fahrenheit.

    Example:

        25°C -> 77°F
    """

    return (
        celsius * 9 / 5
    ) + 32


# ---------------------------------------------------------
# OBSERVATION FETCHING
# ---------------------------------------------------------

def get_knyc_observations(
    limit: int = 100,
) -> list[dict]:
    """
    Retrieve recent observations from the official
    Central Park NWS station.

    The NWS API returns temperatures in Celsius,
    so we convert them into Fahrenheit.

    Returns data like:

        [
            {
                "timestamp": datetime(...),
                "temperature_f": 77.0,
                "description": "Mostly Cloudy",
            },
            ...
        ]
    """

    url = (
        f"{NWS_API_BASE}/stations/"
        f"{STATION_ID}/observations"
    )

    params = {
        # 100 observations is comfortably enough to cover
        # the current calendar day, even if extra special
        # observations are issued.
        "limit": limit,
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=NWS_HEADERS,
            timeout=10,
        )

        response.raise_for_status()

    except requests.RequestException as error:
        print(
            f"Could not retrieve NWS observations: {error}"
        )

        return []

    data = response.json()

    observations = []

    # NWS returns a GeoJSON FeatureCollection.
    #
    # Each feature represents one observation.
    for feature in data.get("features", []):

        properties = feature.get(
            "properties",
            {},
        )

        timestamp = properties.get(
            "timestamp"
        )

        temperature_data = properties.get(
            "temperature",
            {},
        )

        temperature_c = temperature_data.get(
            "value"
        )

        # Skip incomplete observations.
        if (
            timestamp is None
            or temperature_c is None
        ):
            continue

        try:
            # NWS timestamps are ISO-8601.
            #
            # Example:
            #
            # 2026-09-12T19:51:00+00:00
            observation_time = (
                datetime.fromisoformat(
                    timestamp.replace(
                        "Z",
                        "+00:00",
                    )
                )
            )

        except ValueError:
            # Bad timestamp?
            #
            # Skip it instead of crashing Edge Lab.
            continue

        # Convert from UTC/API time into New York time.
        observation_time = (
            observation_time.astimezone(
                NYC_TIMEZONE
            )
        )

        temperature_f = (
            celsius_to_fahrenheit(
                float(temperature_c)
            )
        )

        observations.append(
            {
                "timestamp": observation_time,
                "temperature_f": temperature_f,
                "description": properties.get(
                    "textDescription"
                ),
            }
        )

    # Put oldest observations first.
    observations.sort(
        key=lambda observation: (
            observation["timestamp"]
        )
    )

    return observations


# ---------------------------------------------------------
# TODAY'S SUMMARY
# ---------------------------------------------------------

def get_today_knyc_summary() -> dict | None:
    """
    Calculate today's Central Park observation summary.

    Instead of relying on a precomputed NWS 24-hour
    maximum value, we calculate the high ourselves
    from today's individual observations.

    Returns:

        {
            "date": ...,
            "latest_temperature": ...,
            "latest_time": ...,
            "high_temperature": ...,
            "high_time": ...,
            "observation_count": ...
        }
    """

    observations = get_knyc_observations()

    if not observations:
        return None

    today = datetime.now(
        NYC_TIMEZONE
    ).date()

    # Keep only observations from today's
    # New York calendar date.
    today_observations = [
        observation
        for observation in observations
        if observation[
            "timestamp"
        ].date() == today
    ]

    if not today_observations:
        return None

    # Because we sorted observations chronologically,
    # the final item is the newest observation.
    latest = today_observations[-1]

    # Find the observation with the highest temperature.
    highest = max(
        today_observations,
        key=lambda observation: (
            observation[
                "temperature_f"
            ]
        ),
    )

    return {
        "date": today.isoformat(),

        "latest_temperature": (
            latest["temperature_f"]
        ),

        "latest_time": (
            latest["timestamp"]
        ),

        "high_temperature": (
            highest["temperature_f"]
        ),

        "high_time": (
            highest["timestamp"]
        ),

        "observation_count": len(
            today_observations
        ),
    }

def get_historical_knyc_highs(
    year: int,
) -> dict[str, float]:
    """
    Retrieve official historical Central Park daily
    high temperatures from parsed NWS CLI reports.

    The Iowa Environmental Mesonet archives and parses
    National Weather Service Daily Climate Report data.

    Returns:

        {
            "2026-07-12": 84.0,
            "2026-07-11": 82.0,
            ...
        }

    The dictionary key is the climate-report date.

    The value is the official daily maximum temperature
    in degrees Fahrenheit.
    """

    params = {
        # Central Park.
        "station": STATION_ID,

        # IEM's endpoint retrieves one year at a time.
        "year": year,

        # Explicitly request normal JSON.
        "fmt": "json",
    }

    try:
        response = requests.get(
            IEM_CLI_URL,
            params=params,
            timeout=20,
        )

        response.raise_for_status()

    except requests.RequestException as error:

        print(
            "Could not retrieve historical "
            f"NWS CLI data: {error}"
        )

        return {}

    data = response.json()

    historical_highs = {}

    # IEM returns:
    #
    # {
    #     "results": [
    #         {
    #             "valid": "2026-07-12",
    #             "high": 84,
    #             ...
    #         }
    #     ]
    # }
    for result in data.get(
        "results",
        [],
    ):

        date = result.get(
            "valid"
        )

        high = result.get(
            "high"
        )

        # IEM uses "M" for a missing climate value.
        if (
            date is None
            or high is None
            or high == "M"
        ):
            continue

        try:
            historical_highs[
                date
            ] = float(
                high
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

    return historical_highs
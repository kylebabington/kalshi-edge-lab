"""
Kalshi Edge Lab
Version 0.1

Purpose:
    Pull live weather prediction-market data from Kalshi
    and display it in a clean terminal table.

At this stage we DO NOT:
    - Place trades
    - Log into Kalshi
    - Use real money
    - Predict weather
    - Calculate expected value

We're just building the market-data foundation first.
"""

import re

from collections import defaultdict
from datetime import datetime
from statistics import mean, median

from rich.console import Console
from rich.table import Table

from weather import (
    get_nyc_high_forecast,
    get_nyc_high_ensemble,
)

from nws import get_today_knyc_summary
from kalshi.client import get_open_markets as fetch_open_markets


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

# Kalshi's public production API.
#
# Public market-data endpoints do not require authentication.
BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


# KXHIGHNY is Kalshi's series ticker for:
#
# "Highest temperature in NYC today?"
#
# We'll hard-code one series in v0.1.
# Later, we'll support multiple cities.
SERIES_TICKER = "KXHIGHNY"


# Rich gives us nicer terminal output.
console = Console()


# ---------------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------------

def get_open_markets(series_ticker: str) -> list[dict]:
    """
    Fetch all currently open markets belonging to a Kalshi series.

    Example:

        KXHIGHNY

    might contain individual contracts for:

        74°F or below
        75°F - 76°F
        77°F - 78°F
        79°F - 80°F
        etc.

    Returns:
        A list of market dictionaries from Kalshi.
    """

    try:
        return fetch_open_markets(series_ticker)
    except Exception as error:
        console.print(
            f"[bold red]Could not retrieve Kalshi markets:[/bold red] {error}"
        )
        return []


# ---------------------------------------------------------
# DATA HELPERS
# ---------------------------------------------------------

def dollars_to_float(value: str | None) -> float:
    
    """
    Convert Kalshi's dollar strings into Python floats.

    Example:

        "0.5400"

    becomes:

        0.54

    If the value is missing or invalid, return 0.0.
    """

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def get_event_date(event_ticker: str) -> str | None:
    """
    Extract the calendar date from a Kalshi weather event ticker.

    Example:

        KXHIGHNY-26SEP12

    becomes:

        2026-09-12

    Kalshi's NYC high-temperature event tickers currently end
    with a date formatted like:

        26SEP12
        YYMMMDD
    """

    try:
        # Split from the final hyphen.
        #
        # KXHIGHNY-26SEP12
        #
        # becomes:
        #
        # 26SEP12
        date_code = event_ticker.rsplit("-", 1)[-1]

        parsed_date = datetime.strptime(
            date_code,
            "%y%b%d",
        )

        return parsed_date.strftime("%Y-%m-%d")

    except (ValueError, AttributeError):
        # We do not want the entire application to crash
        # if Kalshi ever changes its ticker format.
        return None

def group_markets_by_event(
    markets: list[dict],
) -> dict[str, list[dict]]:
    """
    Group individual Kalshi contracts by their parent event.

    Example:

        KXHIGHNY-26SEP12
            74 or below
            75-76
            77-78
            ...

        KXHIGHNY-26SEP13
            77 or below
            78-79
            80-81
            ...

    This prevents contracts from different dates from being
    mixed together.
    """

    grouped_markets = defaultdict(list)

    for market in markets:

        event_ticker = market.get(
            "event_ticker",
            "UNKNOWN",
        )

        grouped_markets[event_ticker].append(
            market
        )

    return dict(grouped_markets)

def format_percent(value: float) -> str:
    """
    Convert a contract price into an approximate percentage.

    Example:

        0.54 -> "54.0%"

    Because a winning Kalshi contract settles at $1,
    a $0.54 price is often interpreted roughly as 54%.
    """

    return f"{value * 100:.1f}%"


def market_sort_value(market: dict) -> float:
    """
    Give each temperature bucket a number we can sort by.

    Kalshi does not guarantee that markets will arrive
    in temperature order.

    Example:

        <= 74°
        75-76°
        77-78°
        79-80°
        >= 83°

    We primarily use floor_strike.

    For the lowest bucket, floor_strike may not exist,
    so we fall back to cap_strike.
    """

    floor = market.get("floor_strike")
    cap = market.get("cap_strike")

    if floor is not None:
        return float(floor)

    if cap is not None:
        # Put "74° or below" before the 75° bucket.
        return float(cap) - 1

    return 0

def temperature_matches_outcome(
    temperature: float,
    outcome: str,
    ) -> bool:
    """
    Determine whether a forecast temperature belongs
    in a Kalshi temperature bucket.

    Examples:

        73.8°F -> "74° or below"

        77.4°F -> "77° to 78°"

        79.1°F -> "79° to 80°"

        83.2°F -> "83° or above"


    IMPORTANT:

    The weather model produces decimal temperatures,
    while Kalshi's market labels use whole degrees.

    For version 0.3 we treat each whole-degree value as
    representing a +/- 0.5°F interval.

    Example:

        Kalshi 77° to 78°

    corresponds approximately to:

        76.5°F <= forecast < 78.5°F

    This is a modeling assumption.

    Later versions will verify the exact settlement/rounding
    behavior and calibrate against historical outcomes.
    """

    # Extract numbers from labels like:
    #
    # "77° to 78°"
    #
    # producing:
    #
    # [77, 78]
    numbers = [
        int(number)
        for number in re.findall(
            r"-?\d+",
            outcome,
        )
    ]

    outcome_lower = outcome.lower()

    # ---------------------------------------------
    # Example:
    #
    # "74° or below"
    # ---------------------------------------------

    if (
        "or below" in outcome_lower
        and numbers
    ):
        upper = numbers[0]

        return temperature < (
            upper + 0.5
        )

    # ---------------------------------------------
    # Example:
    #
    # "83° or above"
    # ---------------------------------------------

    if (
        "or above" in outcome_lower
        and numbers
    ):
        lower = numbers[0]

        return temperature >= (
            lower - 0.5
        )

    # ---------------------------------------------
    # Example:
    #
    # "77° to 78°"
    # ---------------------------------------------

    if (
        "to" in outcome_lower
        and len(numbers) >= 2
    ):
        lower = numbers[0]
        upper = numbers[1]

        return (
            temperature >= lower - 0.5
            and temperature < upper + 0.5
        )

    return False

def calculate_ensemble_probability(
    temperatures: list[float],
    outcome: str,
) -> float:
    """
    Calculate the fraction of ensemble members that
    land inside a specific Kalshi temperature bucket.

    Example:

        18 matching members
        31 total members

        18 / 31 = 0.5806

    which means:

        58.1%
    """

    if not temperatures:
        return 0.0

    matching_members = 0

    for temperature in temperatures:

        if temperature_matches_outcome(
            temperature,
            outcome,
        ):
            matching_members += 1

    return (
        matching_members
        / len(temperatures)
    )

# ---------------------------------------------------------
# DISPLAY
# ---------------------------------------------------------

def display_markets(markets: list[dict]) -> None:
    """
    Display Kalshi markets grouped by event/date.

    This is important because Kalshi may have multiple
    dates from the same series open simultaneously.
    """

    if not markets:
        console.print(
            "[yellow]No open markets were found.[/yellow]"
        )
        return

    # Separate contracts into their parent events.
    grouped_events = group_markets_by_event(markets)

    # -----------------------------------------------------
    # Sort events chronologically.
    # -----------------------------------------------------

    def event_sort_key(item):
        event_ticker, _ = item

        event_date = get_event_date(event_ticker)

        # Unknown dates get pushed toward the end.
        return event_date or "9999-12-31"

    sorted_events = sorted(
        grouped_events.items(),
        key=event_sort_key,
    )

    # -----------------------------------------------------
    # Display one table PER EVENT.
    # -----------------------------------------------------

    for event_ticker, event_markets in sorted_events:

        event_date = get_event_date(event_ticker)

        # Sort temperature buckets from low to high.
        event_markets = sorted(
            event_markets,
            key=market_sort_value,
        )

        table = Table(
            title=(
                f"NYC Daily High — "
                f"{event_date or event_ticker}"
            )
        )

        table.add_column(
            "Outcome",
            justify="left",
            no_wrap=True,
        )

        table.add_column(
            "Bid",
            justify="right",
            no_wrap=True,
        )

        table.add_column(
            "Ask",
            justify="right",
            no_wrap=True,
        )

        table.add_column(
            "Mid",
            justify="right",
            no_wrap=True,
        )

        table.add_column(
            "Sprd",
            justify="right",
            no_wrap=True,
        )

        table.add_column(
            "Vol",
            justify="right",
            no_wrap=True,
        )

        table.add_column(
            "OI",
            justify="right",
            no_wrap=True,
        )

        for market in event_markets:

            yes_bid = dollars_to_float(
                market.get("yes_bid_dollars")
            )

            yes_ask = dollars_to_float(
                market.get("yes_ask_dollars")
            )

            midpoint = (
                yes_bid + yes_ask
            ) / 2

            spread = yes_ask - yes_bid

            volume = dollars_to_float(
                market.get("volume_fp")
            )

            open_interest = dollars_to_float(
                market.get("open_interest_fp")
            )

            outcome = market.get(
                "yes_sub_title",
                market.get(
                    "title",
                    "Unknown",
                ),
            )

            table.add_row(
                outcome,
                format_percent(yes_bid),
                format_percent(yes_ask),
                format_percent(midpoint),
                f"{spread * 100:.1f}¢",
                f"{volume:,.0f}",
                f"{open_interest:,.0f}",
            )

        console.print()
        console.print(
            f"[dim]Event: {event_ticker}[/dim]"
        )

        console.print(table)
    
def display_ensemble_comparison(
    markets: list[dict],
    ensemble_forecasts: dict[str, list[float]],
) -> None:
    """
    Compare NOAA GEFS ensemble probabilities with
    Kalshi's current market probabilities.

    IMPORTANT:

    The difference shown here is NOT yet a verified
    trading edge.

    We have not yet accounted for:

        - Model calibration
        - Kalshi fees
        - Bid/ask execution
        - Settlement-source bias
        - Historical model errors

    This is simply our first raw probability comparison.
    """

    if not ensemble_forecasts:

        console.print(
            "[yellow]"
            "Ensemble forecast unavailable."
            "[/yellow]"
        )

        return

    grouped_events = (
        group_markets_by_event(markets)
    )

    console.print(
        "\n[bold]"
        "NOAA GEFS Ensemble Probability Comparison"
        "[/bold]"
    )

    for event_ticker, event_markets in sorted(
        grouped_events.items(),
        key=lambda item: (
            get_event_date(item[0])
            or "9999-12-31"
        ),
    ):

        event_date = get_event_date(
            event_ticker
        )

        if event_date is None:
            continue

        temperatures = (
            ensemble_forecasts.get(
                event_date,
                [],
            )
        )

        if not temperatures:
            continue

        event_markets = sorted(
            event_markets,
            key=market_sort_value,
        )

        table = Table(
            title=(
                f"GEFS vs Kalshi — "
                f"{event_date}"
            )
        )

        table.add_column(
            "Outcome",
            justify="left",
            no_wrap=True,
        )

        table.add_column(
            "GEFS",
            justify="right",
            no_wrap=True,
        )

        table.add_column(
            "Market",
            justify="right",
            no_wrap=True,
        )

        table.add_column(
            "Raw Diff",
            justify="right",
            no_wrap=True,
        )

        for market in event_markets:

            outcome = market.get(
                "yes_sub_title",
                market.get(
                    "title",
                    "Unknown",
                ),
            )

            # -----------------------------------------
            # OUR WEATHER MODEL PROBABILITY
            # -----------------------------------------

            ensemble_probability = (
                calculate_ensemble_probability(
                    temperatures,
                    outcome,
                )
            )

            # -----------------------------------------
            # KALSHI MARKET PROBABILITY
            # -----------------------------------------

            yes_bid = dollars_to_float(
                market.get(
                    "yes_bid_dollars"
                )
            )

            yes_ask = dollars_to_float(
                market.get(
                    "yes_ask_dollars"
                )
            )

            market_midpoint = (
                yes_bid + yes_ask
            ) / 2

            # -----------------------------------------
            # RAW DIFFERENCE
            # -----------------------------------------
            #
            # Example:
            #
            # GEFS   = 61%
            # Kalshi = 48%
            #
            # Difference = +13 percentage points
            #
            # Again: this is NOT yet verified edge.
            # -----------------------------------------

            raw_difference = (
                ensemble_probability
                - market_midpoint
            )

            table.add_row(
                outcome,
                format_percent(
                    ensemble_probability
                ),
                format_percent(
                    market_midpoint
                ),
                (
                    f"{raw_difference * 100:+.1f}pp"
                ),
            )

        console.print()

        console.print(
            f"[dim]"
            f"Ensemble members: "
            f"{len(temperatures)}"
            f"[/dim]"
        )

        console.print(
            f"[dim]"
            f"Range: "
            f"{min(temperatures):.1f}°F – "
            f"{max(temperatures):.1f}°F"
            f"[/dim]"
        )

        console.print(
            f"[dim]"
            f"Mean: "
            f"{mean(temperatures):.1f}°F | "
            f"Median: "
            f"{median(temperatures):.1f}°F"
            f"[/dim]"
        )

        sorted_temperatures = sorted(
            temperatures
        )

        formatted_temperatures = ", ".join(
            f"{temperature:.1f}"
            for temperature in sorted_temperatures
        )

        console.print(
            f"[dim]"
            f"Members: "
            f"{formatted_temperatures}"
            f"[/dim]"
        )

        console.print(table)

def display_weather_forecast() -> None:
    """
    Display our independent NYC weather forecast.

    This is forecast information only.

    It is NOT yet a probability model and should not
    be interpreted as a trading recommendation.
    """

    forecasts = get_nyc_high_forecast()

    if not forecasts:
        console.print(
            "[yellow]Weather forecast unavailable.[/yellow]"
        )
        return

    console.print(
        "\n[bold]Independent Weather Forecast[/bold]"
    )

    for forecast in forecasts:

        date = forecast["date"]
        high = forecast["high"]

        console.print(
            f"{date}: forecast high [bold]{high:.1f}°F[/bold]"
        )

def display_nws_observations() -> None:
    """
    Display actual observations from Central Park.

    This tells us what has really happened today,
    rather than what a forecast model predicted.
    """

    summary = get_today_knyc_summary()

    console.print(
        "\n[bold]"
        "Official NWS Central Park Observations"
        "[/bold]"
    )

    if summary is None:

        console.print(
            "[yellow]"
            "No Central Park observations available."
            "[/yellow]"
        )

        return

    latest_time = (
        summary["latest_time"]
        .strftime("%I:%M %p")
        .lstrip("0")
    )

    high_time = (
        summary["high_time"]
        .strftime("%I:%M %p")
        .lstrip("0")
    )

    console.print(
        f"Station: [bold]KNYC — Central Park[/bold]"
    )

    console.print(
        f"Date: {summary['date']}"
    )

    console.print(
        f"Latest observation: "
        f"[bold]"
        f"{summary['latest_temperature']:.1f}°F"
        f"[/bold] "
        f"at {latest_time}"
    )

    console.print(
        f"Observed high so far: "
        f"[bold]"
        f"{summary['high_temperature']:.1f}°F"
        f"[/bold] "
        f"at {high_time}"
    )

    console.print(
        f"Observations today: "
        f"{summary['observation_count']}"
    )

    console.print(
        "[dim]"
        "NWS observations may be delayed by "
        "roughly 20 minutes."
        "[/dim]"
    )

# ---------------------------------------------------------
# PROGRAM ENTRY POINT
# ---------------------------------------------------------

def main() -> None:
    """
    Main application workflow.

    Version 0.4:

    1. Retrieve an independent weather forecast.
    2. Retrieve official Central Park observations.
    3. Retrieve Kalshi market prices.
    4. Retrieve the NOAA GEFS ensemble.
    5. Compare forecast probabilities with market prices.
    """

    # -----------------------------------------------------
    # APPLICATION HEADER
    # -----------------------------------------------------

    console.print(
        "\n[bold]KALSHI EDGE LAB[/bold]"
    )

    # -----------------------------------------------------
    # WEATHER FORECAST
    # -----------------------------------------------------

    # Show our deterministic weather forecast.
    display_weather_forecast()

    # -----------------------------------------------------
    # ACTUAL NWS OBSERVATIONS
    # -----------------------------------------------------

    # Show what has actually happened today
    # at the Central Park weather station.
    display_nws_observations()

    # -----------------------------------------------------
    # KALSHI MARKET DATA
    # -----------------------------------------------------

    console.print(
        "\n[bold]"
        "Fetching live Kalshi weather markets..."
        "[/bold]"
    )

    # Retrieve currently open NYC temperature contracts.
    markets = get_open_markets(
        SERIES_TICKER
    )

    # Count how many separate dated events are open.
    event_count = len(
        group_markets_by_event(
            markets
        )
    )

    console.print(
        f"Found [bold]{len(markets)}[/bold] open contracts "
        f"across [bold]{event_count}[/bold] events."
    )

    # Display Kalshi markets grouped by date.
    display_markets(
        markets
    )

    # -----------------------------------------------------
    # GEFS ENSEMBLE
    # -----------------------------------------------------

    console.print(
        "\n[bold]"
        "Fetching NOAA GEFS ensemble..."
        "[/bold]"
    )

    # Retrieve all GEFS ensemble members.
    ensemble_forecasts = (
        get_nyc_high_ensemble()
    )

    # Compare GEFS probabilities against Kalshi prices.
    display_ensemble_comparison(
        markets,
        ensemble_forecasts,
    )


# ---------------------------------------------------------
# PROGRAM START
# ---------------------------------------------------------

# This prevents main() from automatically running
# if another Python file imports this file.
if __name__ == "__main__":
    main()
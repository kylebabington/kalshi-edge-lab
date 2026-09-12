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

import requests

from collections import defaultdict
from datetime import datetime

from rich.console import Console
from rich.table import Table

from weather import get_nyc_high_forecast



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

    url = f"{BASE_URL}/markets"

    # Using params is safer and cleaner than manually building:
    #
    # ?series_ticker=KXHIGHNY&status=open
    params = {
        "series_ticker": series_ticker,
        "status": "open",
    }

    try:
        # timeout=10 prevents our program from hanging forever
        # if Kalshi or our connection stops responding.
        response = requests.get(
            url,
            params=params,
            timeout=10,
        )

        # Raise an exception for HTTP errors such as:
        #
        # 404
        # 500
        # 503
        response.raise_for_status()

    except requests.RequestException as error:
        console.print(
            f"[bold red]Could not retrieve Kalshi markets:[/bold red] {error}"
        )

        return []

    # Convert the JSON response into normal Python objects.
    data = response.json()

    # Kalshi responds with something like:
    #
    # {
    #     "markets": [...],
    #     "cursor": "..."
    # }
    #
    # .get() prevents a KeyError if markets is unexpectedly missing.
    return data.get("markets", [])


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


# ---------------------------------------------------------
# PROGRAM ENTRY POINT
# ---------------------------------------------------------

def main() -> None:
    """
    Main application workflow.

    Version 0.2:

    1. Retrieve an independent weather forecast.
    2. Retrieve Kalshi market prices.
    3. Display both sources.
    """

    console.print(
        "\n[bold]KALSHI EDGE LAB[/bold]"
    )

    # First get independent weather information.
    display_weather_forecast()

    console.print(
        "\n[bold]Fetching live Kalshi weather markets...[/bold]"
    )

    # Then get prediction-market information.
    markets = get_open_markets(SERIES_TICKER)

    event_count = len(
        group_markets_by_event(markets)
    )

    console.print(
        f"Found [bold]{len(markets)}[/bold] open contracts "
        f"across [bold]{event_count}[/bold] events."
    )

    display_markets(markets)


# This prevents main() from automatically running
# if another Python file imports this file later.
if __name__ == "__main__":
    main()
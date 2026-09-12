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
from rich.console import Console
from rich.table import Table


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
    Display Kalshi markets in a human-readable table.
    """

    if not markets:
        console.print(
            "[yellow]No open markets were found for this series.[/yellow]"
        )
        return

    # Sort temperature ranges from lowest to highest.
    markets = sorted(
        markets,
        key=market_sort_value,
    )

    table = Table(
        title="Kalshi Edge Lab — NYC Daily High Temperature"
    )

    table.add_column(
        "Outcome",
        justify="left",
    )

    table.add_column(
        "YES Bid",
        justify="right",
    )

    table.add_column(
        "YES Ask",
        justify="right",
    )

    table.add_column(
        "Mid",
        justify="right",
    )

    table.add_column(
        "Spread",
        justify="right",
    )

    table.add_column(
        "Volume",
        justify="right",
    )

    table.add_column(
        "Open Interest",
        justify="right",
    )

    # -----------------------------------------------------
    # Build one table row for every Kalshi contract.
    # -----------------------------------------------------

    for market in markets:

        # Kalshi currently returns prices as strings:
        #
        # "0.5200"
        #
        # Convert them into floats so we can perform math.
        yes_bid = dollars_to_float(
            market.get("yes_bid_dollars")
        )

        yes_ask = dollars_to_float(
            market.get("yes_ask_dollars")
        )

        # Midpoint between buyers and sellers.
        #
        # Example:
        #
        # bid = 0.52
        # ask = 0.54
        #
        # midpoint = 0.53
        midpoint = (yes_bid + yes_ask) / 2

        # Spread tells us how far apart buyers and sellers are.
        #
        # Example:
        #
        # 0.54 - 0.52 = 0.02
        #
        # That's a 2-cent spread.
        spread = yes_ask - yes_bid

        volume = dollars_to_float(
            market.get("volume_fp")
        )

        open_interest = dollars_to_float(
            market.get("open_interest_fp")
        )

        # yes_sub_title happens to give us excellent
        # labels for these temperature markets:
        #
        # "77° to 78°"
        outcome = market.get(
            "yes_sub_title",
            market.get("title", "Unknown"),
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
    console.print(table)
    console.print()


# ---------------------------------------------------------
# PROGRAM ENTRY POINT
# ---------------------------------------------------------

def main() -> None:
    """
    Main application workflow.

    1. Request Kalshi data.
    2. Receive markets.
    3. Display markets.
    """

    console.print(
        "\n[bold]Fetching live Kalshi weather markets...[/bold]"
    )

    markets = get_open_markets(SERIES_TICKER)

    console.print(
        f"Found [bold]{len(markets)}[/bold] open contracts."
    )

    display_markets(markets)


# This prevents main() from automatically running
# if another Python file imports this file later.
if __name__ == "__main__":
    main()
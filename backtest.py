"""
Kalshi Edge Lab
Historical Backtesting

Version 0.5

First goal:

Retrieve historical settled NYC daily-high temperature
markets and identify the winning temperature bucket
for each event.

We are NOT calculating profitability yet.

First we need a reliable historical dataset.
"""

from collections import defaultdict
from datetime import datetime

import requests

from rich.console import Console
from rich.table import Table


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

SERIES_TICKER = "KXHIGHNY"

console = Console()


# ---------------------------------------------------------
# DATE HELPERS
# ---------------------------------------------------------

def get_event_date(
    event_ticker: str,
) -> str | None:
    """
    Convert:

        KXHIGHNY-26SEP12

    into:

        2026-09-12
    """

    try:
        date_code = (
            event_ticker
            .rsplit("-", 1)[-1]
        )

        parsed_date = datetime.strptime(
            date_code,
            "%y%b%d",
        )

        return parsed_date.strftime(
            "%Y-%m-%d"
        )

    except (
        ValueError,
        AttributeError,
    ):
        return None


# ---------------------------------------------------------
# HISTORICAL KALSHI DATA
# ---------------------------------------------------------

def get_historical_markets() -> list[dict]:
    """
    Retrieve archived NYC daily-high markets.

    Kalshi paginates historical results.

    That means one request may not return everything.

    We keep requesting pages until Kalshi returns
    an empty cursor.
    """

    url = (
        f"{BASE_URL}/historical/markets"
    )

    all_markets = []

    cursor = None

    while True:

        params = {
            "series_ticker": SERIES_TICKER,

            # Kalshi currently allows up to
            # 1000 results per page.
            "limit": 1000,
        }

        # The first request has no cursor.
        #
        # Every request after that uses the cursor
        # returned by the previous page.
        if cursor:
            params["cursor"] = cursor

        try:
            response = requests.get(
                url,
                params=params,
                timeout=20,
            )

            response.raise_for_status()

        except requests.RequestException as error:

            console.print(
                "[bold red]"
                "Could not retrieve historical "
                f"Kalshi markets: {error}"
                "[/bold red]"
            )

            break

        data = response.json()

        markets = data.get(
            "markets",
            [],
        )

        all_markets.extend(
            markets
        )

        cursor = data.get(
            "cursor"
        )

        console.print(
            f"Downloaded "
            f"{len(all_markets)} "
            f"historical contracts...",
            style="dim",
        )

        # No cursor means there are no more pages.
        if not cursor:
            break

    return all_markets


# ---------------------------------------------------------
# GROUP MARKETS INTO EVENTS
# ---------------------------------------------------------

def group_markets_by_event(
    markets: list[dict],
    ) -> dict[str, list[dict]]:
    """
    A single daily temperature event contains several
    mutually exclusive contracts.

    Example:

        74° or below
        75° to 76°
        77° to 78°
        79° to 80°
        ...

    Group them by event ticker.
    """

    events = defaultdict(list)

    for market in markets:

        event_ticker = market.get(
            "event_ticker"
        )

        if not event_ticker:
            continue

        events[
            event_ticker
        ].append(
            market
        )

    return dict(events)

def get_outcome_label(
    market: dict,
    ) -> str:
    """
    Return the human-readable YES outcome label
    for a Kalshi market.

    Examples:

        "77° to 78°"
        "74° or below"
        "83° or above"
    """

    return market.get(
        "yes_sub_title",
        market.get(
            "title",
            "",
        ),
    )


def is_range_bucket_event(
    markets: list[dict],
    ) -> bool:
    """
    Determine whether an event uses the modern
    mutually exclusive temperature-range structure.

    A range event should look roughly like:

        74° or below
        75° to 76°
        77° to 78°
        79° to 80°
        81° to 82°
        83° or above

    That structure has:

        - exactly one "or below" bucket
        - exactly one "or above" bucket
        - all remaining contracts are ranges using "to"

    Older Kalshi markets sometimes used independent
    threshold contracts such as:

        Above 60°
        Above 62°

    Multiple threshold contracts can resolve YES at the
    same time, so they cannot be treated like mutually
    exclusive range buckets.
    """

    # A usable range event needs at least:
    #
    # low tail
    # middle range
    # high tail
    if len(markets) < 3:
        return False

    labels = [
        get_outcome_label(
            market
        ).lower()
        for market in markets
    ]

    below_count = sum(
        "or below" in label
        for label in labels
    )

    above_count = sum(
        "or above" in label
        for label in labels
    )

    range_count = sum(
        " to " in label
        for label in labels
    )

    # Every contract must fit one of the expected
    # mutually exclusive bucket types.
    expected_total = (
        below_count
        + above_count
        + range_count
    )

    return (
        below_count == 1
        and above_count == 1
        and range_count >= 1
        and expected_total == len(markets)
    )

# ---------------------------------------------------------
# FIND WINNING CONTRACT
# ---------------------------------------------------------

def get_winning_market(
    markets: list[dict],
) -> dict | None:
    """
    Return the winning contract only when exactly
    ONE contract resolved YES.

    This is appropriate for mutually exclusive
    range-bucket events.

    If zero or multiple contracts resolved YES,
    return None.
    """

    winners = [
        market
        for market in markets
        if market.get(
            "result"
        ) == "yes"
    ]

    if len(winners) != 1:
        return None

    return winners[0]

def audit_historical_events(
    markets: list[dict],
    ) -> None:
    """
    Audit our historical Kalshi dataset before using it
    for backtesting.

    We want to verify:

    1. Each event has exactly one YES winner.
    2. No event has zero winners.
    3. No event has multiple winners.
    4. See how many contracts each event contains.
    5. Check the historical date range.
    6. Check whether multiple event tickers map to the
       same calendar date.

    We do this BEFORE building any trading model because
    bad historical data would produce bad conclusions.
    """

    grouped_events = (
        group_markets_by_event(
            markets
        )
    )

    exactly_one_winner = 0
    zero_winners = []
    multiple_winners = []

    contract_counts = defaultdict(
        int
    )

    dates_to_events = defaultdict(
        list
    )

    valid_dates = []

    range_events = 0
    usable_range_events = 0

    range_zero_winners = []
    range_multiple_winners = []

    legacy_or_other_events = 0

    # -----------------------------------------------------
    # INSPECT EVERY EVENT
    # -----------------------------------------------------

    for (
        event_ticker,
        event_markets,
    ) in grouped_events.items():

        # Count how many contracts belong to this event.
        contract_count = len(
            event_markets
        )

        contract_counts[
            contract_count
        ] += 1

        # Find ALL YES winners.
        winners = [
            market
            for market in event_markets
            if market.get(
                "result"
            ) == "yes"
        ]

        # -------------------------------------------------
        # GENERAL WINNER COUNTS
        # -------------------------------------------------

        if len(winners) == 1:

            exactly_one_winner += 1

        elif len(winners) == 0:

            zero_winners.append(
                event_ticker
            )

        else:

            multiple_winners.append(
                {
                    "event_ticker": event_ticker,
                    "winner_count": len(
                        winners
                    ),
                }
            )

        # -------------------------------------------------
        # MARKET STRUCTURE CLASSIFICATION
        # -------------------------------------------------

        is_range_event = (
            is_range_bucket_event(
                event_markets
            )
        )

        if is_range_event:

            range_events += 1

            if len(winners) == 1:

                usable_range_events += 1

            elif len(winners) == 0:

                range_zero_winners.append(
                    event_ticker
                )

            else:

                range_multiple_winners.append(
                    event_ticker
                )

        else:

            legacy_or_other_events += 1

        # -------------------------------------------------
        # DATE CHECK
        # -------------------------------------------------

        event_date = get_event_date(
            event_ticker
        )

        if event_date is not None:

            valid_dates.append(
                event_date
            )

            dates_to_events[
                event_date
            ].append(
                event_ticker
            )

    # -----------------------------------------------------
    # LOOK FOR DUPLICATE DATES
    # -----------------------------------------------------

    duplicate_dates = {
        date: event_tickers
        for (
            date,
            event_tickers,
        ) in dates_to_events.items()
        if len(event_tickers) > 1
    }

    # -----------------------------------------------------
    # DISPLAY AUDIT RESULTS
    # -----------------------------------------------------

    console.print(
        "\n[bold]"
        "Historical Dataset Audit"
        "[/bold]"
    )

    console.print(
        f"Total events: "
        f"[bold]{len(grouped_events)}[/bold]"
    )

    console.print(
        f"Range-bucket events: "
        f"[bold]{range_events}[/bold]"
    )

    console.print(
        f"Usable range-bucket events: "
        f"[bold]{usable_range_events}[/bold]"
    )

    console.print(
        f"Legacy / other structures: "
        f"[bold]{legacy_or_other_events}[/bold]"
    )

    console.print(
        f"Range events with zero winners: "
        f"[bold]{len(range_zero_winners)}[/bold]"
    )

    console.print(
        f"Range events with multiple winners: "
        f"[bold]{len(range_multiple_winners)}[/bold]"
    )

    console.print(
        f"Exactly one winner: "
        f"[bold]{exactly_one_winner}[/bold]"
    )

    console.print(
        f"Zero winners: "
        f"[bold]{len(zero_winners)}[/bold]"
    )

    console.print(
        f"Multiple winners: "
        f"[bold]{len(multiple_winners)}[/bold]"
    )

    console.print(
        f"Duplicate calendar dates: "
        f"[bold]{len(duplicate_dates)}[/bold]"
    )

    # -----------------------------------------------------
    # DATE RANGE
    # -----------------------------------------------------

    if valid_dates:

        console.print(
            f"Date range: "
            f"[bold]"
            f"{min(valid_dates)}"
            f" → "
            f"{max(valid_dates)}"
            f"[/bold]"
        )

    # -----------------------------------------------------
    # CONTRACT COUNTS
    # -----------------------------------------------------

    console.print(
        "\n[bold]"
        "Contracts per event:"
        "[/bold]"
    )

    for (
        count,
        number_of_events,
    ) in sorted(
        contract_counts.items()
    ):

        console.print(
            f"  {count} contracts: "
            f"{number_of_events} events"
        )

    # -----------------------------------------------------
    # WARN ABOUT BAD EVENTS
    # -----------------------------------------------------

    if zero_winners:

        console.print(
            "\n[yellow]"
            "Events with zero winners found."
            "[/yellow]"
        )

        for ticker in zero_winners[:10]:

            console.print(
                f"  {ticker}"
            )

    if multiple_winners:

        console.print(
            "\n[red]"
            "Events with multiple winners found."
            "[/red]"
        )

        for event in (
            multiple_winners[:10]
        ):

            console.print(
                f"  "
                f"{event['event_ticker']}: "
                f"{event['winner_count']} winners"
            )

    if duplicate_dates:

        console.print(
            "\n[yellow]"
            "Dates containing multiple event tickers:"
            "[/yellow]"
        )

        for (
            date,
            event_tickers,
        ) in list(
            duplicate_dates.items()
        )[:10]:

            console.print(
                f"  {date}: "
                f"{event_tickers}"
            )


# ---------------------------------------------------------
# DISPLAY HISTORICAL RESULTS
# ---------------------------------------------------------

def display_recent_events(
    markets: list[dict],
    event_limit: int = 20,
) -> None:
    """
    Display recent historical NYC temperature events
    and their winning bucket.
    """

    grouped_events = (
        group_markets_by_event(
            markets
        )
    )

    historical_events = []

    for (
        event_ticker,
        event_markets,
    ) in grouped_events.items():

        if not is_range_bucket_event(
            event_markets
        ):
            continue

        event_date = get_event_date(
            event_ticker
        )

        winner = get_winning_market(
            event_markets
        )

        if (
            event_date is None
            or winner is None
        ):
            continue

        historical_events.append(
            {
                "date": event_date,
                "event_ticker": event_ticker,
                "winner": get_outcome_label(
                    winner
                ),
                "contracts": len(
                    event_markets
                ),
            }
        )

    # Newest dates first.
    historical_events.sort(
        key=lambda event: (
            event["date"]
        ),
        reverse=True,
    )

    table = Table(
        title=(
            "Historical NYC Daily High "
            "Settlements"
        )
    )

    table.add_column(
        "Date",
        no_wrap=True,
    )

    table.add_column(
        "Winning Bucket",
        no_wrap=True,
    )

    table.add_column(
        "Contracts",
        justify="right",
    )

    for event in (
        historical_events[
            :event_limit
        ]
    ):

        table.add_row(
            event["date"],
            event["winner"],
            str(
                event["contracts"]
            ),
        )

    console.print()

    console.print(
        f"[bold]"
        f"Historical contracts: "
        f"{len(markets)}"
        f"[/bold]"
    )

    console.print(
        f"[bold]"
        f"Complete historical events: "
        f"{len(historical_events)}"
        f"[/bold]"
    )

    console.print()

    console.print(table)


# ---------------------------------------------------------
# PROGRAM ENTRY POINT
# ---------------------------------------------------------

def main() -> None:
    """
    Run the historical-data test.
    """

    console.print(
        "\n[bold]"
        "KALSHI EDGE LAB — BACKTEST"
        "[/bold]"
    )

    console.print(
        "\nFetching historical NYC "
        "temperature markets..."
    )

    markets = (
    get_historical_markets()
    )

# Validate the dataset before trusting it.
    audit_historical_events(
    markets
    )

# Then show a sample of the historical results.
    display_recent_events(
    markets
    )


if __name__ == "__main__":
    main()
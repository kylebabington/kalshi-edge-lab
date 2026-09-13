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

import re
import requests

from rich.console import Console
from rich.table import Table

from historical_weather import (
    get_gfs_run_high,
)
from nws import get_historical_knyc_highs


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

def temperature_matches_outcome(
    temperature: float,
    outcome: str,
) -> bool:
    """
    Determine whether a forecast temperature belongs
    inside a Kalshi temperature bucket.

    Examples:

        73.8°F -> "74° or below"

        77.4°F -> "77° to 78°"

        83.2°F -> "83° or above"

    For now we use the same +/- 0.5°F assumption
    used by the live application.
    """

    # Pull the numbers out of labels such as:
    #
    #     "77° to 78°"
    #
    # producing:
    #
    #     [77, 78]
    numbers = [
        int(number)
        for number in re.findall(
            r"-?\d+",
            outcome,
        )
    ]

    outcome_lower = outcome.lower()

    # Example:
    #
    #     "74° or below"
    if (
        "or below" in outcome_lower
        and numbers
    ):

        upper = numbers[0]

        return temperature < (
            upper + 0.5
        )

    # Example:
    #
    #     "83° or above"
    if (
        "or above" in outcome_lower
        and numbers
    ):

        lower = numbers[0]

        return temperature >= (
            lower - 0.5
        )

    # Example:
    #
    #     "77° to 78°"
    if (
        " to " in outcome_lower
        and len(numbers) >= 2
    ):

        lower = numbers[0]
        upper = numbers[1]

        return (
            temperature >= lower - 0.5
            and temperature < upper + 0.5
        )

    return False
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

def get_predicted_market(
    markets: list[dict],
    forecast_temperature: float,
) -> dict | None:
    """
    Find which Kalshi bucket contains our GFS
    forecast temperature.

    Example:

        GFS forecast:
            84.2°F

        Available buckets:
            80° or below
            81° to 82°
            83° to 84°
            85° to 86°
            87° or above

        Result:
            83° to 84°
    """

    for market in markets:

        outcome = get_outcome_label(
            market
        )

        if temperature_matches_outcome(
            forecast_temperature,
            outcome,
        ):
            return market

    return None

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

def display_gfs_backtest_sample(
    markets: list[dict],
    event_limit: int = 10,
) -> None:
    """
    Backtest the previous-day 12Z GFS forecast against
    a small sample of historical Kalshi outcomes.

    For now we intentionally test only a few events.

    We want to verify that:

        1. Historical GFS retrieval works repeatedly.
        2. Forecast temperatures map to the right buckets.
        3. Actual winners are being compared correctly.

    We are NOT calculating trading profitability yet.
    """

    grouped_events = (
        group_markets_by_event(
            markets
        )
    )

    usable_events = []

    # -----------------------------------------------------
    # BUILD OUR LIST OF USABLE EVENTS
    # -----------------------------------------------------

    for (
        event_ticker,
        event_markets,
    ) in grouped_events.items():

        # Ignore legacy threshold-style markets.
        if not is_range_bucket_event(
            event_markets
        ):
            continue

        event_date = get_event_date(
            event_ticker
        )

        if event_date is None:
            continue

        # Exact Single Runs data only exists for the
        # recent portion of our Kalshi history.
        if event_date < "2026-04-02":
            continue

        winner = get_winning_market(
            event_markets
        )

        if winner is None:
            continue

        usable_events.append(
            {
                "date": event_date,
                "markets": event_markets,
                "winner": winner,
            }
        )

    # Newest dates first.
    usable_events.sort(
        key=lambda event: event["date"],
        reverse=True,
    )

    # Only test a small number for now.
    sample_events = usable_events[
        :event_limit
    ]

    

    # -----------------------------------------------------
    # CREATE OUTPUT TABLE
    # -----------------------------------------------------

    table = Table(
        title=(
            "Previous-Day 12Z GFS "
            "Historical Test"
        )
    )

    table.add_column(
        "Date",
        no_wrap=True,
    )

    table.add_column(
        "GFS",
        justify="right",
        no_wrap=True,
    )

    table.add_column(
        "Predicted Bucket",
        no_wrap=True,
    )

    table.add_column(
        "Actual Bucket",
        no_wrap=True,
    )

    table.add_column(
        "Correct?",
        justify="center",
        no_wrap=True,
    )

    tested_events = 0
    correct_predictions = 0

    # -----------------------------------------------------
    # TEST EACH EVENT
    # -----------------------------------------------------

    for event in sample_events:

        event_date = event["date"]
        event_markets = event["markets"]
        winner = event["winner"]

        forecast_high = (
            get_gfs_run_high(
                event_date,
                run_date_offset=-1,
                run_hour=12,
            )
        )

        # If Open-Meteo cannot give us this model run,
        # skip the event rather than pretending we have
        # a forecast.
        if forecast_high is None:
            continue

        predicted_market = (
            get_predicted_market(
                event_markets,
                forecast_high,
            )
        )

        if predicted_market is None:
            continue

        predicted_outcome = (
            get_outcome_label(
                predicted_market
            )
        )

        actual_outcome = (
            get_outcome_label(
                winner
            )
        )

        is_correct = (
            predicted_market.get("ticker")
            == winner.get("ticker")
        )

        tested_events += 1

        if is_correct:
            correct_predictions += 1

        table.add_row(
            event_date,
            f"{forecast_high:.1f}°F",
            predicted_outcome,
            actual_outcome,
            (
                "YES"
                if is_correct
                else "NO"
            ),
        )

    # -----------------------------------------------------
    # DISPLAY RESULTS
    # -----------------------------------------------------

    console.print()
    console.print(table)

    console.print()

    console.print(
        f"Events tested: "
        f"[bold]{tested_events}[/bold]"
    )

    console.print(
        f"Correct bucket predictions: "
        f"[bold]{correct_predictions}[/bold]"
    )

    if tested_events > 0:

        accuracy = (
            correct_predictions
            / tested_events
        )

        console.print(
            f"Bucket accuracy: "
            f"[bold]"
            f"{accuracy * 100:.1f}%"
            f"[/bold]"
        )

def display_gfs_run_comparison(
    markets: list[dict],
    event_limit: int = 10,
) -> None:
    """
    Compare several exact historical GFS model runs
    against the same Kalshi events.

    This helps us determine which point-in-time
    forecast snapshot deserves deeper testing.

    We are still measuring WEATHER FORECAST accuracy.

    This is NOT yet a trading-profitability backtest.
    """

    grouped_events = (
        group_markets_by_event(
            markets
        )
    )

    usable_events = []

    # -----------------------------------------------------
    # BUILD USABLE EVENT LIST
    # -----------------------------------------------------

    for (
        event_ticker,
        event_markets,
    ) in grouped_events.items():

        # Ignore legacy threshold markets.
        if not is_range_bucket_event(
            event_markets
        ):
            continue

        event_date = get_event_date(
            event_ticker
        )

        if event_date is None:
            continue

        # Exact Single Runs history begins only
        # in the recent portion of our dataset.
        if event_date < "2026-04-02":
            continue

        winner = get_winning_market(
            event_markets
        )

        if winner is None:
            continue

        usable_events.append(
            {
                "date": event_date,
                "markets": event_markets,
                "winner": winner,
            }
        )

    # Newest events first.
    usable_events.sort(
        key=lambda event: (
            event["date"]
        ),
        reverse=True,
    )

    sample_events = usable_events[
        :event_limit
    ]

        # -----------------------------------------------------
    # LOAD OFFICIAL NWS DAILY HIGHS
    # -----------------------------------------------------

    # Find every year represented in our sample.
    #
    # Right now this will only be 2026, but writing it
    # this way means the code will continue working when
    # we expand the historical test later.
    needed_years = sorted(
        {
            int(
                event["date"][:4]
            )
            for event in sample_events
        }
    )

    historical_highs = {}

    for year in needed_years:

        historical_highs.update(
            get_historical_knyc_highs(
                year
            )
        )

    # -----------------------------------------------------
    # GFS RUNS WE WANT TO COMPARE
    # -----------------------------------------------------

    run_configs = [
        {
            "label": "Prev 12Z",
            "date_offset": -1,
            "hour": 12,
        },
        {
            "label": "Prev 18Z",
            "date_offset": -1,
            "hour": 18,
        },
        {
            "label": "Day 00Z",
            "date_offset": 0,
            "hour": 0,
        },
        {
            "label": "Day 06Z",
            "date_offset": 0,
            "hour": 6,
        },
    ]

    # Keep a separate accuracy count for each run.
    results = {
        config["label"]: {
            # Kalshi bucket statistics.
            "tested": 0,
            "correct": 0,

            # Actual temperature-error statistics.
            "temperature_tested": 0,
            "absolute_error_sum": 0.0,
            "signed_error_sum": 0.0,
        }
        for config in run_configs
    }

    # -----------------------------------------------------
    # TABLE
    # -----------------------------------------------------

    table = Table(
        title=(
            "Historical GFS Run Comparison"
        )
    )

    table.add_column(
        "Date",
        no_wrap=True,
    )

    table.add_column(
        "Actual",
        no_wrap=True,
    )

    table.add_column(
        "Actual High",
        justify="right",
        no_wrap=True,
    )

    for config in run_configs:

        table.add_column(
            config["label"],
            justify="right",
            no_wrap=True,
        )

    # -----------------------------------------------------
    # TEST EVENTS
    # -----------------------------------------------------

    for event in sample_events:

        event_date = event["date"]

        event_markets = event[
            "markets"
        ]

        winner = event[
            "winner"
        ]

        actual_outcome = (
            get_outcome_label(
                winner
            )
        )

        # Retrieve the official daily high from the
        # NWS Daily Climate Report archive.
        actual_high = historical_highs.get(
            event_date
        )

        # Display the actual temperature if available.
        if actual_high is None:

            actual_high_display = (
                "Unavailable"
            )

        else:

            actual_high_display = (
                f"{actual_high:.1f}°F"
            )

        row = [
            event_date,
            actual_outcome,
            actual_high_display,
        ]

        # Test the same event against every
        # historical GFS snapshot.
        for config in run_configs:

            # Get the short name for this model run.
            #
            # Examples:
            #
            # "Prev 12Z"
            # "Prev 18Z"
            # "Day 00Z"
            # "Day 06Z"
            label = config[
                "label"
            ]

            forecast_high = (
                get_gfs_run_high(
                    event_date,
                    run_date_offset=(
                        config[
                            "date_offset"
                        ]
                    ),
                    run_hour=(
                        config[
                            "hour"
                        ]
                    ),
                )
            )

            if forecast_high is None:

                row.append(
                    "Unavailable"
                )

                continue

            # -------------------------------------------------
            # ACTUAL TEMPERATURE ERROR
            # -------------------------------------------------

            if actual_high is not None:

                # Positive means GFS forecast too warm.
                #
                # Negative means GFS forecast too cool.
                signed_error = (
                    forecast_high
                    - actual_high
                )

                # Absolute error ignores direction.
                #
                # Example:
                #
                # Forecast = 88°F
                # Actual   = 85°F
                #
                # Error = +3°F
                # Absolute error = 3°F
                absolute_error = abs(
                    signed_error
                )

                results[
                    label
                ][
                    "temperature_tested"
                ] += 1

                results[
                    label
                ][
                    "absolute_error_sum"
                ] += absolute_error

                results[
                    label
                ][
                    "signed_error_sum"
                ] += signed_error

            predicted_market = (
                get_predicted_market(
                    event_markets,
                    forecast_high,
                )
            )

            if predicted_market is None:

                row.append(
                    f"{forecast_high:.1f}°F ?"
                )

                continue

            is_correct = (
                predicted_market.get(
                    "ticker"
                )
                == winner.get(
                    "ticker"
                )
            )

            results[
                label
            ][
                "tested"
            ] += 1

            if is_correct:

                results[
                    label
                ][
                    "correct"
                ] += 1

            row.append(
                (
                    f"{forecast_high:.1f}°F "
                    f"{'YES' if is_correct else 'NO'}"
                )
            )

        table.add_row(
            *row
        )

    # -----------------------------------------------------
    # DISPLAY
    # -----------------------------------------------------

    console.print()
    console.print(table)

    console.print(
        "\n[bold]"
        "GFS Run Accuracy"
        "[/bold]"
    )

    for config in run_configs:

        label = config[
            "label"
        ]

        tested = results[
            label
        ][
            "tested"
        ]

        correct = results[
            label
        ][
            "correct"
        ]

        if tested == 0:

            console.print(
                f"{label}: no usable events"
            )

            continue

        accuracy = (
            correct
            / tested
        )

        temperature_tested = (
            results[
                label
            ][
                "temperature_tested"
            ]
        )

        # If official NWS temperatures were available,
        # calculate mean absolute error and signed bias.
        if temperature_tested > 0:

            mae = (
                results[
                    label
                ][
                    "absolute_error_sum"
                ]
                / temperature_tested
            )

            bias = (
                results[
                    label
                ][
                    "signed_error_sum"
                ]
                / temperature_tested
            )

            console.print(
                f"{label}: "
                f"Bucket "
                f"{correct}/{tested} "
                f"([bold]"
                f"{accuracy * 100:.1f}%"
                f"[/bold])"
                f" | MAE "
                f"[bold]"
                f"{mae:.2f}°F"
                f"[/bold]"
                f" | Bias "
                f"[bold]"
                f"{bias:+.2f}°F"
                f"[/bold]"
            )

        else:

            console.print(
                f"{label}: "
                f"Bucket "
                f"{correct}/{tested} "
                f"([bold]"
                f"{accuracy * 100:.1f}%"
                f"[/bold])"
                f" | No NWS temperatures"
            )

    console.print(
        "\n[dim]"
        "Bias: positive = forecast too warm; "
        "negative = forecast too cool."
        "[/dim]"
    ) 

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

    # Compare several exact historical GFS runs
    # using the same 10 Kalshi events.
    display_gfs_run_comparison(
        markets,
        event_limit=10,
    )


if __name__ == "__main__":
    main()
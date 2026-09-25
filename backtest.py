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

from rich.console import Console
from rich.table import Table

from historical_weather import (
    get_gfs_run_high,
)
from nws import get_historical_knyc_highs
from kalshi.client import KalshiClient, get_historical_markets_for_series
from research.weather.resolution import (
    get_event_date,
    get_outcome_label,
    get_winning_market,
    group_markets_by_event,
    is_range_bucket_event,
    temperature_matches_outcome,
)


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

SERIES_TICKER = "KXHIGHNY"

console = Console()


# ---------------------------------------------------------
# HISTORICAL KALSHI DATA
# ---------------------------------------------------------

def get_historical_markets() -> list[dict]:
    """
    Retrieve archived NYC daily-high markets.

    Uses the shared Kalshi client for pagination,
    retry, and rate limiting. Behavior for KXHIGHNY
    remains series-filtered historical markets only.
    """

    client = KalshiClient(
        progress=lambda message: console.print(message, style="dim"),
    )

    try:
        return get_historical_markets_for_series(
            SERIES_TICKER,
            client=client,
        )
    except Exception as error:
        console.print(
            "[bold red]"
            "Could not retrieve historical "
            f"Kalshi markets: {error}"
            "[/bold red]"
        )
        return []


# ---------------------------------------------------------
# PREDICTED CONTRACT
# ---------------------------------------------------------

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
            market=market,
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

def display_day_00z_full_backtest(
    markets: list[dict],
) -> None:
    """
    Test the event-day 00Z GFS forecast across every
    usable historical Kalshi event supported by the
    Open-Meteo Single Runs archive.

    This is a WEATHER FORECAST skill test.

    It does NOT yet test whether a trading strategy
    would have made money.
    """

    grouped_events = (
        group_markets_by_event(
            markets
        )
    )

    usable_events = []

    # -----------------------------------------------------
    # BUILD THE HISTORICAL SAMPLE
    # -----------------------------------------------------

    for (
        event_ticker,
        event_markets,
    ) in grouped_events.items():

        # Only use modern mutually exclusive
        # temperature-bucket events.
        if not is_range_bucket_event(
            event_markets
        ):
            continue

        event_date = get_event_date(
            event_ticker
        )

        if event_date is None:
            continue

        # Open-Meteo's exact Single Runs archive
        # starts in this part of our historical dataset.
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

    # Oldest first makes the test easier to reason about
    # chronologically.
    usable_events.sort(
        key=lambda event: (
            event["date"]
        )
    )

    # -----------------------------------------------------
    # LOAD OFFICIAL NWS DAILY HIGHS
    # -----------------------------------------------------

    needed_years = sorted(
        {
            int(
                event["date"][:4]
            )
            for event in usable_events
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
    # STATISTICS
    # -----------------------------------------------------

    forecast_count = 0
    bucket_tested = 0
    bucket_correct = 0

    absolute_error_sum = 0.0
    signed_error_sum = 0.0
    squared_error_sum = 0.0

    within_1_degree = 0
    within_2_degrees = 0
    within_3_degrees = 0

    error_records = []

    # -----------------------------------------------------
    # WALK-FORWARD BIAS CALIBRATION
    # -----------------------------------------------------

    # We require at least 20 completed historical
    # forecasts before allowing the model to correct
    # itself.
    #
    # Those first 20 dates are TRAINING HISTORY only.
    #
    # Date 21 is the first date that can be tested using
    # a bias learned exclusively from earlier information.
    walk_forward_min_history = 20

    # Store the raw GFS signed errors that were actually
    # known BEFORE each future prediction.
    #
    # Positive:
    #     GFS was too warm.
    #
    # Negative:
    #     GFS was too cool.
    past_signed_errors = []

    # Number of dates on which we had enough earlier
    # history to make a bias-adjusted prediction.
    walk_forward_count = 0

    # Raw GFS statistics measured ONLY on the same dates
    # used by the walk-forward test.
    #
    # This gives us a fair apples-to-apples comparison.
    walk_raw_absolute_error_sum = 0.0
    walk_raw_signed_error_sum = 0.0
    walk_raw_squared_error_sum = 0.0

    walk_raw_bucket_tested = 0
    walk_raw_bucket_correct = 0

    # Bias-adjusted GFS statistics.
    walk_adjusted_absolute_error_sum = 0.0
    walk_adjusted_signed_error_sum = 0.0
    walk_adjusted_squared_error_sum = 0.0

    walk_adjusted_bucket_tested = 0
    walk_adjusted_bucket_correct = 0

    # Keep track of the bias corrections we actually used.
    #
    # This lets us see whether the learned correction
    # stayed stable or moved around over time.
    walk_bias_corrections = []

    total_events = len(
        usable_events
    )

    console.print()

    console.print(
        "[bold]"
        "Testing full Day 00Z historical sample..."
        "[/bold]"
    )

    console.print(
        f"Usable events: "
        f"[bold]{total_events}[/bold]"
    )

    # -----------------------------------------------------
    # TEST EACH DATE
    # -----------------------------------------------------

    for index, event in enumerate(
        usable_events,
        start=1,
    ):

        event_date = event[
            "date"
        ]

        event_markets = event[
            "markets"
        ]

        winner = event[
            "winner"
        ]

        # Show occasional progress without printing
        # one line for every single API request.
        if (
            index == 1
            or index % 10 == 0
            or index == total_events
        ):

            console.print(
                f"Testing "
                f"{index}/{total_events}: "
                f"{event_date}",
                style="dim",
            )

        # Event-day 00Z GFS run.
        forecast_high = (
            get_gfs_run_high(
                event_date,
                run_date_offset=0,
                run_hour=0,
            )
        )

        if forecast_high is None:
            continue

        actual_high = (
            historical_highs.get(
                event_date
            )
        )

        if actual_high is None:
            continue

        forecast_count += 1

        # ---------------------------------------------
        # TEMPERATURE ERROR
        # ---------------------------------------------

        signed_error = (
            forecast_high
            - actual_high
        )

        absolute_error = abs(
            signed_error
        )

        squared_error = (
            signed_error ** 2
        )

        absolute_error_sum += (
            absolute_error
        )

        signed_error_sum += (
            signed_error
        )

        squared_error_sum += (
            squared_error
        )

        if absolute_error <= 1.0:
            within_1_degree += 1

        if absolute_error <= 2.0:
            within_2_degrees += 1

        if absolute_error <= 3.0:
            within_3_degrees += 1

        # ---------------------------------------------
        # KALSHI BUCKET ACCURACY
        # ---------------------------------------------

        predicted_market = (
            get_predicted_market(
                event_markets,
                forecast_high,
            )
        )

        actual_outcome = (
            get_outcome_label(
                winner
            )
        )

        predicted_outcome = (
            "No matching bucket"
        )

        is_correct = False

        if predicted_market is not None:

            bucket_tested += 1

            predicted_outcome = (
                get_outcome_label(
                    predicted_market
                )
            )

            is_correct = (
                predicted_market.get(
                    "ticker"
                )
                == winner.get(
                    "ticker"
                )
            )

            if is_correct:
                bucket_correct += 1

        # -------------------------------------------------
        # WALK-FORWARD BIAS CORRECTION
        # -------------------------------------------------

        # IMPORTANT:
        #
        # We calculate today's correction BEFORE adding
        # today's error to past_signed_errors.
        #
        # That means today's prediction can only learn
        # from dates that happened earlier.
        if (
            len(
                past_signed_errors
            )
            >= walk_forward_min_history
        ):

            # Average signed GFS error observed BEFORE today.
            #
            # Example:
            #
            # Previous GFS forecasts averaged:
            #
            #     +2.4°F too warm
            #
            # Then:
            #
            #     raw forecast = 85.0°F
            #
            # becomes:
            #
            #     adjusted forecast = 82.6°F
            learned_bias = (
                sum(
                    past_signed_errors
                )
                / len(
                    past_signed_errors
                )
            )

            adjusted_forecast = (
                forecast_high
                - learned_bias
            )

            walk_forward_count += 1

            walk_bias_corrections.append(
                learned_bias
            )

            # ---------------------------------------------
            # RAW FORECAST ON WALK-FORWARD TEST DATES
            # ---------------------------------------------

            walk_raw_absolute_error_sum += (
                absolute_error
            )

            walk_raw_signed_error_sum += (
                signed_error
            )

            walk_raw_squared_error_sum += (
                squared_error
            )

            if predicted_market is not None:

                walk_raw_bucket_tested += 1

                if is_correct:

                    walk_raw_bucket_correct += 1

            # ---------------------------------------------
            # ADJUSTED FORECAST ERROR
            # ---------------------------------------------

            adjusted_signed_error = (
                adjusted_forecast
                - actual_high
            )

            adjusted_absolute_error = abs(
                adjusted_signed_error
            )

            adjusted_squared_error = (
                adjusted_signed_error ** 2
            )

            walk_adjusted_absolute_error_sum += (
                adjusted_absolute_error
            )

            walk_adjusted_signed_error_sum += (
                adjusted_signed_error
            )

            walk_adjusted_squared_error_sum += (
                adjusted_squared_error
            )

            # ---------------------------------------------
            # ADJUSTED KALSHI BUCKET
            # ---------------------------------------------

            adjusted_market = (
                get_predicted_market(
                    event_markets,
                    adjusted_forecast,
                )
            )

            if adjusted_market is not None:

                walk_adjusted_bucket_tested += 1

                adjusted_is_correct = (
                    adjusted_market.get(
                        "ticker"
                    )
                    == winner.get(
                        "ticker"
                    )
                )

                if adjusted_is_correct:

                    walk_adjusted_bucket_correct += 1

        # -------------------------------------------------
        # UPDATE HISTORY FOR TOMORROW
        # -------------------------------------------------

        # Only NOW do we reveal today's actual GFS error
        # to the calibration system.
        #
        # Tomorrow may learn from it.
        #
        # Today was not allowed to.
        past_signed_errors.append(
            signed_error
        )

        # Save the individual result so we can later
        # inspect the largest forecast misses.
        error_records.append(
            {
                "date": event_date,
                "forecast": forecast_high,
                "actual": actual_high,
                "signed_error": signed_error,
                "absolute_error": absolute_error,
                "predicted_bucket": predicted_outcome,
                "actual_bucket": actual_outcome,
                "correct": is_correct,
            }
        )

    # -----------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------

    console.print()

    console.print(
        "[bold]"
        "Day 00Z Full Historical Results"
        "[/bold]"
    )

    console.print(
        f"Usable Kalshi events: "
        f"[bold]{total_events}[/bold]"
    )

    console.print(
        f"Events with GFS + NWS data: "
        f"[bold]{forecast_count}[/bold]"
    )

    if forecast_count == 0:

        console.print(
            "[yellow]"
            "No complete forecast/observation pairs."
            "[/yellow]"
        )

        return

    mae = (
        absolute_error_sum
        / forecast_count
    )

    bias = (
        signed_error_sum
        / forecast_count
    )

    rmse = (
        squared_error_sum
        / forecast_count
    ) ** 0.5

    within_1_rate = (
        within_1_degree
        / forecast_count
    )

    within_2_rate = (
        within_2_degrees
        / forecast_count
    )

    within_3_rate = (
        within_3_degrees
        / forecast_count
    )

    console.print(
        f"MAE: "
        f"[bold]{mae:.2f}°F[/bold]"
    )

    console.print(
        f"Bias: "
        f"[bold]{bias:+.2f}°F[/bold]"
    )

    console.print(
        f"RMSE: "
        f"[bold]{rmse:.2f}°F[/bold]"
    )

    console.print(
        f"Within 1°F: "
        f"[bold]"
        f"{within_1_degree}/{forecast_count} "
        f"({within_1_rate * 100:.1f}%)"
        f"[/bold]"
    )

    console.print(
        f"Within 2°F: "
        f"[bold]"
        f"{within_2_degrees}/{forecast_count} "
        f"({within_2_rate * 100:.1f}%)"
        f"[/bold]"
    )

    console.print(
        f"Within 3°F: "
        f"[bold]"
        f"{within_3_degrees}/{forecast_count} "
        f"({within_3_rate * 100:.1f}%)"
        f"[/bold]"
    )

    if bucket_tested > 0:

        bucket_accuracy = (
            bucket_correct
            / bucket_tested
        )

        console.print(
            f"Exact Kalshi bucket accuracy: "
            f"[bold]"
            f"{bucket_correct}/{bucket_tested} "
            f"({bucket_accuracy * 100:.1f}%)"
            f"[/bold]"
        )

        # -----------------------------------------------------
    # WALK-FORWARD CALIBRATION RESULTS
    # -----------------------------------------------------

    console.print()

    console.print(
        "[bold]"
        "Walk-Forward Bias Calibration"
        "[/bold]"
    )

    console.print(
        f"Minimum training history: "
        f"[bold]"
        f"{walk_forward_min_history} days"
        f"[/bold]"
    )

    console.print(
        f"Out-of-sample test dates: "
        f"[bold]"
        f"{walk_forward_count}"
        f"[/bold]"
    )

    if walk_forward_count > 0:

        # ---------------------------------------------
        # RAW METRICS
        # ---------------------------------------------

        walk_raw_mae = (
            walk_raw_absolute_error_sum
            / walk_forward_count
        )

        walk_raw_bias = (
            walk_raw_signed_error_sum
            / walk_forward_count
        )

        walk_raw_rmse = (
            walk_raw_squared_error_sum
            / walk_forward_count
        ) ** 0.5

        # ---------------------------------------------
        # ADJUSTED METRICS
        # ---------------------------------------------

        walk_adjusted_mae = (
            walk_adjusted_absolute_error_sum
            / walk_forward_count
        )

        walk_adjusted_bias = (
            walk_adjusted_signed_error_sum
            / walk_forward_count
        )

        walk_adjusted_rmse = (
            walk_adjusted_squared_error_sum
            / walk_forward_count
        ) ** 0.5

        # Average bias correction actually applied
        # during the out-of-sample test.
        average_learned_bias = (
            sum(
                walk_bias_corrections
            )
            / len(
                walk_bias_corrections
            )
        )

        console.print(
            f"Average learned correction: "
            f"[bold]"
            f"{average_learned_bias:+.2f}°F"
            f"[/bold]"
        )

        comparison_table = Table(
            title=(
                "Raw vs Walk-Forward Adjusted GFS"
            )
        )

        comparison_table.add_column(
            "Metric"
        )

        comparison_table.add_column(
            "Raw GFS",
            justify="right",
        )

        comparison_table.add_column(
            "Adjusted",
            justify="right",
        )

        comparison_table.add_row(
            "MAE",
            f"{walk_raw_mae:.2f}°F",
            f"{walk_adjusted_mae:.2f}°F",
        )

        comparison_table.add_row(
            "Bias",
            f"{walk_raw_bias:+.2f}°F",
            f"{walk_adjusted_bias:+.2f}°F",
        )

        comparison_table.add_row(
            "RMSE",
            f"{walk_raw_rmse:.2f}°F",
            f"{walk_adjusted_rmse:.2f}°F",
        )

        # ---------------------------------------------
        # BUCKET ACCURACY
        # ---------------------------------------------

        if (
            walk_raw_bucket_tested > 0
            and walk_adjusted_bucket_tested > 0
        ):

            walk_raw_bucket_accuracy = (
                walk_raw_bucket_correct
                / walk_raw_bucket_tested
            )

            walk_adjusted_bucket_accuracy = (
                walk_adjusted_bucket_correct
                / walk_adjusted_bucket_tested
            )

            comparison_table.add_row(
                "Exact bucket",
                (
                    f"{walk_raw_bucket_correct}/"
                    f"{walk_raw_bucket_tested} "
                    f"("
                    f"{walk_raw_bucket_accuracy * 100:.1f}%"
                    f")"
                ),
                (
                    f"{walk_adjusted_bucket_correct}/"
                    f"{walk_adjusted_bucket_tested} "
                    f"("
                    f"{walk_adjusted_bucket_accuracy * 100:.1f}%"
                    f")"
                ),
            )

        console.print()

        console.print(
            comparison_table
        )

    # -----------------------------------------------------
    # WORST FORECAST MISSES
    # ----------------------------------------------------

    error_records.sort(
        key=lambda result: (
            result[
                "absolute_error"
            ]
        ),
        reverse=True,
    )

    worst_table = Table(
        title=(
            "10 Largest Day 00Z Forecast Errors"
        )
    )

    worst_table.add_column(
        "Date",
        no_wrap=True,
    )

    worst_table.add_column(
        "GFS",
        justify="right",
    )

    worst_table.add_column(
        "Actual",
        justify="right",
    )

    worst_table.add_column(
        "Error",
        justify="right",
    )

    worst_table.add_column(
        "Actual Bucket",
        no_wrap=True,
    )

    for result in error_records[:10]:

        worst_table.add_row(
            result["date"],
            f"{result['forecast']:.1f}°F",
            f"{result['actual']:.1f}°F",
            f"{result['signed_error']:+.1f}°F",
            result["actual_bucket"],
        )

    console.print()
    console.print(worst_table)

    console.print(
        "\n[dim]"
        "Bias: positive = GFS too warm; "
        "negative = GFS too cool."
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

       # Run the event-day 00Z GFS forecast across
    # the full exact-run historical sample.
    display_day_00z_full_backtest(
        markets
    )


if __name__ == "__main__":
    main()
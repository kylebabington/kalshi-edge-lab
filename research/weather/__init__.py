"""External weather probability research for KXHIGHNY.

This package estimates outcome probabilities from weather information
only. It must not use Kalshi bid/ask/mid/result as model features.
"""

from research.weather.models import (
    GFS_PUBLICATION_LATENCY,
    MIN_N_MONTH,
    MIN_N_RUN,
    MIN_N_SEASON,
    MIN_REQUIRED_EDGE,
    MIN_RUN_HISTORY,
    STANDARDIZED_RUNS,
    WeatherPrediction,
    WeatherTradeEvaluation,
)
from research.weather.resolution import (
    TemperatureBucket,
    WeatherResolution,
    detect_settlement_source_regime,
    get_event_date,
    get_outcome_label,
    get_winning_market,
    group_markets_by_event,
    is_range_bucket_event,
    parse_temperature_bucket,
    temperature_matches_bucket,
)

__all__ = [
    "GFS_PUBLICATION_LATENCY",
    "MIN_N_MONTH",
    "MIN_N_RUN",
    "MIN_N_SEASON",
    "MIN_REQUIRED_EDGE",
    "MIN_RUN_HISTORY",
    "STANDARDIZED_RUNS",
    "TemperatureBucket",
    "WeatherPrediction",
    "WeatherResolution",
    "WeatherTradeEvaluation",
    "detect_settlement_source_regime",
    "get_event_date",
    "get_outcome_label",
    "get_winning_market",
    "group_markets_by_event",
    "is_range_bucket_event",
    "parse_temperature_bucket",
    "temperature_matches_bucket",
]

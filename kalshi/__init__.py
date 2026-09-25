"""Kalshi package exports."""

from kalshi.client import KalshiAPIError, KalshiClient, encode_path_segment
from kalshi.fees import (
    FeeResolutionError,
    FeeSchedule,
    UnsupportedFeeError,
    compute_taker_fee,
    quadratic_taker_fee,
    resolve_fee_schedule,
)

__all__ = [
    "KalshiClient",
    "KalshiAPIError",
    "FeeResolutionError",
    "FeeSchedule",
    "UnsupportedFeeError",
    "compute_taker_fee",
    "encode_path_segment",
    "quadratic_taker_fee",
    "resolve_fee_schedule",
]

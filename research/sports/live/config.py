"""Frozen rules of the prospective capture protocol ``sports_phase2_capture_v2``.

The collector records the *actual* Phase 2 candidates (``sports_phase2_v1``): the Phase 1 v2 primary
model with its frozen parameters, the frozen calibration and blend maps, and the Kalshi market
reference under the historical candle rule. Nothing here is refit or tuned. Every value is copied
into the capture protocol when it is frozen; editing this file changes the live code hash and the
frozen protocol then refuses to record (bump the protocol version instead).
"""

from __future__ import annotations

from .. import models as M
from ..core import REPO_ROOT
from ..phase2 import config as P2

PROTOCOL = "sports_phase2_capture_v2"
PARENT_PROTOCOL = P2.PROTOCOL                  # sports_phase2_v1 (preserved unchanged)
PHASE1_PROTOCOL = P2.PHASE1_PROTOCOL           # sports_phase1_v2
SCHEMA = "sports_phase2_capture_v2_record"

CAPTURE_DIR = REPO_ROOT / "data" / "sports" / "phase2" / "capture_v2"
EVIDENCE_ROOT = REPO_ROOT / "data" / "cache" / "sports" / "raw_capture_v2"

HORIZON_MIN = M.HORIZON_MIN                    # scheduled cutoff = source start - 60 min
WINDOW_MIN = P2.CAPTURE["window_minutes_before_cutoff"]   # window = [cutoff - 20 min, cutoff]
LIVE_T_END = "2100-01-01T00:00:00Z"            # widens the Phase 1 test window's end only (T_TEST unchanged)

MODES = {
    "window": "WINDOW_CAPTURE: prediction_as_of inside [cutoff - 20 min, cutoff]; the actual horizon is "
              "recorded (not an exact T-60 observation). Never writes EARLY_SNAPSHOT records.",
    "early": "EARLY_SNAPSHOT: development record finalized before the window opens; never a checkpoint "
             "record and never MISSED.",
}
STATUSES = {
    "WINDOW_CAPTURE": "accepted checkpoint capture (first attempt satisfying every recording rule)",
    "MISSED": "accepted checkpoint record written only after the window expired without an accepted capture",
    "EARLY_SNAPSHOT": "accepted early development snapshot (first per event)",
    "FAILED_ATTEMPT": "attempt that broke a recording rule (network error, incomplete pagination, pin failure, "
                      "timing); never occupies the accepted slot; another attempt may follow inside the window",
    "DUPLICATE_ATTEMPT": "attempt after an accepted record exists; the accepted record is never replaced",
}
RECORDING_RULES = [
    "capture and parent protocol pins, Phase 1/Phase 2/live code hashes and every prediction dependency "
    "(normalized history, crosswalks, inventory) match the frozen capture protocol",
    "milestone discovery and every market/history page set paginated to completion",
    "every evidence request returned HTTP 200 and its bytes are saved",
    "every evidence retrieval completed at or before prediction_as_of",
    "every result used by a predictor was released (frozen Phase 1 release rule) at or before prediction_as_of",
    "window mode: cutoff - 20 min <= prediction_as_of <= finalized_at <= cutoff",
    "early mode: finalized_at < cutoff - 20 min",
    "each of the four candidates carries a probability or an explicit unavailability reason",
]

# Kalshi discovery (public endpoints only)
MILESTONE_MIN_START = "2026-10-01T00:00:00Z"   # before the frozen Phase 1 history ends (2026-10-07)
PAGE_LIMITS = {"milestones": 500, "markets": 1000}
MAX_PAGES = 200
WINDOW_DISCOVERY_H = (-2.0, 1.75)              # milestone start relative to now (window mode; 25 min slack)
EARLY_LOOKAHEAD_H = {"default": 6.0, "max": 48.0}
HISTORY_OVERLAP_DAYS = 2                       # re-read source results from (last frozen result - 2 days)
NETWORK = {"min_interval_s": 0.5, "max_retries": 2, "timeout_s": 45.0, "concurrency": 1}

# Market candidate: exactly the frozen historical rule, evaluated at prediction_as_of
QUOTE = {**P2.QUOTE, "reference_time": "prediction_as_of (not the scheduled cutoff)",
         "request": "hourly candles over [request_time - 24 h, request_time]; a quote is used only if the "
                    "latest candle ended at or before prediction_as_of; if an hour boundary passes between "
                    "the candle request and prediction_as_of the market candidate is unavailable",
         "diagnostics": "current yes bid/ask from the market listing are saved separately and never used "
                        "as a candidate or blend input"}
MARKET_FAMILIES = P2.MARKET_FAMILIES
CONTRACT_RULES = P2.CONTRACT_RULES

# Live adapters. Source keys follow research.sports.competitions.
ADAPTERS = {
    "espn_team": {"status": "supported", "comparability": "exact",
                  "linking": "Kalshi milestone home/away -> frozen participant crosswalk -> ESPN scoreboard game "
                             "(Phase 1 match_games rule)",
                  "history": "frozen normalized events + ESPN scoreboards from the last frozen result - 2 days"},
    "espn_fight": {"status": "supported", "comparability": "exact",
                   "linking": "Kalshi milestone fighters -> frozen fighter crosswalk -> ESPN fight within 36 h",
                   "history": "frozen normalized fights + ESPN scoreboards from the last frozen result - 2 days"},
    "espn_golf": {"status": "supported", "comparability": "field_assumption_differs",
                  "linking": "Kalshi tournament milestone -> frozen golfer crosswalk -> ESPN event within 3 days "
                             "with >= 70% field overlap",
                  "history": "frozen normalized tournaments + ESPN scoreboards",
                  "field": "prediction-time ESPN listed competitors (no outcome fields). Phase 1 used the field "
                           "listed in the source *result* (late withdrawals included); live and historical "
                           "forecasts are therefore not exactly comparable. If no field is listed by "
                           "prediction_as_of, the frozen predictor is unavailable (never reconstructed from results)."},
    "kalshi_only": {"status": "supported", "comparability": "exact",
                    "linking": "two-market two-participant Kalshi event with a milestone start",
                    "history": "frozen normalized events + settled Kalshi markets since the freeze "
                               "(available_at = settlement time)"},
    "jolpica": {"status": "unsupported",
                "reason": "the frozen F1 field model needs the race entrant list; Jolpica publishes entrants only "
                          "with results, so no prediction-time field source exists"},
}
COMPARABILITY = {
    "exact": "same frozen predictor, parameters and input semantics as the historical forecasts",
    "field_assumption_differs": "prediction-time field instead of the historical result field; not exactly "
                                "comparable with the Phase 1/Phase 2 historical forecasts",
}

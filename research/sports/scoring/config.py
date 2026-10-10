"""Rules for scoring accepted ``sports_phase2_capture_v2`` WINDOW_CAPTURE records against Kalshi settlements.

The capture package, its protocol, records and evidence are read only. Labels come only from a market's
confirmed final settlement (status ``finalized``/``settled``, the existing Phase 1 rule); nothing is
inferred from game scores or expected settlement times. Metric formulas are the frozen Phase 1 evaluator's.
"""

from __future__ import annotations

from ..core import REPO_ROOT
from ..live import config as LC

PROTOCOL = "sports_capture_v2_scoring_v1"
SCHEMA = "sports_capture_v2_score"
SOURCE_PROTOCOL = LC.PROTOCOL

SCORE_DIR = REPO_ROOT / "data" / "sports" / "phase2" / "capture_v2_scores"
EVIDENCE_ROOT = REPO_ROOT / "data" / "cache" / "sports" / "raw_settlement_v1"
REPORT_CSV = REPO_ROOT / "docs" / "research" / "sports_capture_v2_pilot_scores.csv"

SCORED_STATUS = "WINDOW_CAPTURE"
FINAL_MARKET_STATUSES = ("finalized", "settled")

# Settlement states. FINAL states are written once; the others are run diagnostics and are retried.
STATES = {
    "SETTLED_YES": "final status, result yes, settlement value 1",
    "SETTLED_NO": "final status, result no, settlement value 0",
    "NONBINARY": "final status with a scalar / fair-price result; recorded verbatim, no binary label",
    "VOID_OR_CANCELLED": "final status with a void or cancelled result; recorded verbatim, no binary label",
    "PENDING": "market not yet in a confirmed final status (e.g. active, closed, determined, disputed)",
    "INCONSISTENT": "status, result and settlement value conflict (or a final market has no result); "
                    "no binary label is assigned",
    "CONFLICTING_DUPLICATE": "the ticker appeared more than once in the settlement evidence with different "
                             "status / result / settlement value",
    "NOT_FOUND": "ticker absent from the completely paginated event listing",
    "LOOKUP_FAILED": "network error, non-200 response or incomplete pagination",
}
FINAL_STATES = ("SETTLED_YES", "SETTLED_NO", "NONBINARY", "VOID_OR_CANCELLED")
BINARY = {"SETTLED_YES": 1, "SETTLED_NO": 0}
VOID_RESULTS = ("void", "voided", "cancelled", "canceled")

PAIRS = [("frozen", "market"), ("calibrated", "frozen"), ("blend", "frozen"), ("blend", "market")]
GROUP_KEYS = ["sport", "competition", "family", "cohort"]

PILOT_NOTE = ("operational pilot: manual, availability-based captures; descriptive only. Not a representative "
              "sample and not an independent confirmation of historical findings. RESEARCH_ONLY / NO_BET.")

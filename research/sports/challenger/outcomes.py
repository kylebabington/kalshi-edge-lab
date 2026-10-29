"""Settlement observation derivation for isolated ITF challenger capture.

Observations are immutable. Status is derived from all applicable terminal evidence,
not from last-write-wins. Missing/failed/pending cannot erase a terminal result.
A v2 writer may observe a v1 prediction without relabeling that prediction as v2.
"""

from __future__ import annotations

import json

from ..core import sha256_file
from ..scoring import config as SC
from . import records as R
from .constants import (
    ACTIVE_CAPTURE_PROTOCOL, CAPTURE_DIR, CHALLENGER_CAPTURE_V1, CHALLENGER_CAPTURE_V1_PATH,
    CHALLENGER_CAPTURE_V2, CHALLENGER_CAPTURE_V2_PATH,
)


# Existing settlement-state vocabulary (research.sports.scoring.config).
RETRYABLE_STATES = frozenset({"PENDING", "LOOKUP_FAILED", "NOT_FOUND", "INCONSISTENT"})
TERMINAL_STATES = frozenset(SC.FINAL_STATES)  # SETTLED_YES, SETTLED_NO, NONBINARY, VOID_OR_CANCELLED
REVIEW_STATES = frozenset({"CONFLICTING", "CONFLICTING_DUPLICATE"})
BINARY_Y = dict(SC.BINARY)
WRAPPER_PROTOCOL_PATHS = {
    CHALLENGER_CAPTURE_V1: CHALLENGER_CAPTURE_V1_PATH,
    CHALLENGER_CAPTURE_V2: CHALLENGER_CAPTURE_V2_PATH,
}


def _ts(value) -> str:
    return "" if value is None else str(value)


def observation_sort_key(obs: dict) -> tuple:
    """Deterministic listing order. Not used to pick a winner among terminals."""
    return (_ts(obs.get("observed_at")), str(obs.get("attempt_id") or ""))


def wrapper_protocol_path(protocol_id: str | None):
    return WRAPPER_PROTOCOL_PATHS.get(protocol_id)


def verify_prediction_against_own_protocol(pred: dict) -> list[str]:
    """Compare a record to the protocol file it names. Never uses today's wrapper code hash."""
    problems = []
    pid = pred.get("protocol")
    path = wrapper_protocol_path(pid)
    if path is None or not path.is_file():
        return [f"unknown_or_missing_wrapper_protocol:{pid}"]
    frozen = json.loads(path.read_text(encoding="utf-8"))
    pins = frozen.get("pins") or {}
    if pred.get("capture_code_hash") != pins.get("capture_code_hash"):
        problems.append("capture_code_hash does not match the prediction's own frozen wrapper protocol")
    if pred.get("protocol_hash") and pred.get("protocol_hash") != sha256_file(path):
        problems.append("protocol_hash does not match the prediction's own protocol file")
    if pred.get("challenger_code_hash") != pins.get("challenger_code_hash"):
        problems.append("challenger_code_hash does not match the prediction's own wrapper protocol")
    return problems


def hash_matches_prediction(obs: dict, pred: dict) -> bool:
    want = pred.get("record_sha256")
    got = obs.get("prediction_record_sha256")
    return bool(want) and got == want


def legacy_as_observation(rec: dict, pred: dict) -> dict:
    """View of a v1 settlement file. ``settled_at`` stays the legacy lookup timestamp."""
    return {
        "schema": rec.get("schema") or R.SCHEMA_SETTLEMENT,
        "attempt_id": "legacy_v1",
        "legacy": True,
        "prediction_record_sha256": rec.get("prediction_record_sha256"),
        "prediction_protocol_id": pred.get("protocol"),
        "prediction_protocol_hash": pred.get("protocol_hash"),
        "observation_writer_protocol_id": rec.get("protocol"),
        "observation_writer_protocol_hash": rec.get("protocol_hash"),
        "observed_at": rec.get("settled_at"),
        "legacy_settled_at": rec.get("settled_at"),
        "contracts": rec.get("contracts") or [],
        "lookup_failed": rec.get("lookup_failed"),
        "note": "legacy_settled_at is the v1 lookup clock, not an exchange settlement timestamp",
    }


def load_observations(comp_key: str, cluster: str, pred: dict) -> tuple[list[dict], list[dict]]:
    """Return (usable, quarantined). Never mutates files. Does not relabel the prediction."""
    usable, quarantined = [], []
    legacy_path = R.settlement_path(comp_key, cluster)
    rec, st = R.inspect_record(legacy_path)
    if st == "ok" and rec is not None:
        obs = legacy_as_observation(rec, pred)
        (usable if hash_matches_prediction(obs, pred) else quarantined).append(obs)
    elif st in ("incomplete", "checksum_mismatch"):
        quarantined.append({"attempt_id": "legacy_v1", "reason": st, "path": str(legacy_path)})
    root = CAPTURE_DIR / "settlement_observations" / comp_key / R.safe(cluster)
    for path, rec in R.list_complete(root):
        if hash_matches_prediction(rec, pred):
            usable.append(rec)
        else:
            quarantined.append({**rec, "quarantine_reason": "prediction_hash_mismatch", "path": str(path)})
    usable.sort(key=observation_sort_key)
    return usable, quarantined


def _states_for_ticker(observations: list[dict], ticker: str) -> list[str]:
    out = []
    for obs in observations:
        for row in obs.get("contracts") or []:
            if row.get("ticker") == ticker and row.get("state"):
                out.append(row["state"])
    return out


def derive_ticker(states: list[str]) -> dict:
    """Combine all observations of one ticker. Order does not pick among terminals."""
    terminals = [s for s in states if s in TERMINAL_STATES]
    review = [s for s in states if s in REVIEW_STATES]
    unique_terminal = sorted(set(terminals))
    if review or len(unique_terminal) > 1:
        return {
            "state": "CONFLICTING",
            "y": None,
            "scoreable": False,
            "retryable": False,
            "terminal_states": unique_terminal,
            "review_states": sorted(set(review)),
        }
    if len(unique_terminal) == 1:
        state = unique_terminal[0]
        return {
            "state": state,
            "y": BINARY_Y.get(state),
            "scoreable": state in BINARY_Y,
            "retryable": False,
            "terminal_states": unique_terminal,
            "review_states": [],
        }
    retryable = [s for s in states if s in RETRYABLE_STATES]
    if retryable:
        return {
            "state": retryable[-1],
            "y": None,
            "scoreable": False,
            "retryable": True,
            "terminal_states": [],
            "review_states": [],
        }
    if not states:
        return {"state": "MISSING", "y": None, "scoreable": False, "retryable": True,
                "terminal_states": [], "review_states": []}
    return {"state": states[-1], "y": None, "scoreable": False, "retryable": True,
            "terminal_states": [], "review_states": []}


def derive_event(pred: dict, observations: list[dict] | None = None,
                 quarantined: list[dict] | None = None) -> dict:
    if observations is None or quarantined is None:
        observations, quarantined = load_observations(pred["competition"], pred["cluster"], pred)
    tickers = [c.get("ticker") for c in pred.get("contracts") or [] if c.get("ticker")]
    contracts = {}
    for t in tickers:
        contracts[t] = derive_ticker(_states_for_ticker(observations, t))
    unresolved = [t for t, d in contracts.items() if d.get("retryable")]
    conflicting = [t for t, d in contracts.items() if d.get("state") == "CONFLICTING"]
    scoreable = [t for t, d in contracts.items() if d.get("scoreable")]
    return {
        "prediction_record_sha256": pred.get("record_sha256"),
        "prediction_protocol_id": pred.get("protocol"),
        "contracts": contracts,
        "unresolved_tickers": unresolved,
        "conflicting_tickers": conflicting,
        "scoreable_tickers": scoreable,
        "retry_event": bool(unresolved),
        "n_observations": len(observations),
        "n_quarantined": len(quarantined),
        "quarantined": quarantined,
    }


def build_observation(*, pred: dict, attempt_id: str, observed_at: str, writer_protocol: dict,
                      writer_protocol_hash: str, contracts: list[dict], evidence: list,
                      lookup_failed, evidence_root: str) -> dict:
    """v2 writer observation. Prediction protocol fields are copied, never replaced."""
    return {
        "schema": R.SCHEMA_OBSERVATION,
        "attempt_id": attempt_id,
        "competition": pred["competition"],
        "cluster": pred["cluster"],
        "prediction_record_sha256": pred.get("record_sha256"),
        "prediction_protocol_id": pred.get("protocol"),
        "prediction_protocol_hash": pred.get("protocol_hash"),
        "observation_writer_protocol_id": writer_protocol.get("protocol") or ACTIVE_CAPTURE_PROTOCOL,
        "observation_writer_protocol_hash": writer_protocol_hash,
        "observed_at": observed_at,
        "contracts": contracts,
        "evidence": evidence,
        "lookup_failed": lookup_failed,
        "evidence_root": evidence_root,
        "note": "Settlement observations supply outcomes only. They do not establish capture-time eligibility.",
        "research_only": True,
        "no_bet": True,
    }


def exchange_settled_at(market: dict | None):
    """Exchange settlement timestamp from the market payload, if present. Never the lookup clock."""
    if not isinstance(market, dict):
        return None
    return market.get("settlement_ts") or market.get("settlement_time")

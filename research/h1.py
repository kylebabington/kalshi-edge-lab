"""H1 pre-registered hypothesis: cheap YES ask → evaluate executable NO."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from research.efficiency import summarize_side
from research.prices import parse_ts


H1_REGISTRATION_PATH = Path(__file__).resolve().parent / "hypotheses" / "h1.json"
H1_YES_ASK_THRESHOLD = Decimal("0.20")

# Evidence classes for H1 confirmation accounting.
H1_DEVELOPMENT = "development"
H1_RETROSPECTIVE_HOLDOUT = "retrospective_holdout"
H1_PROSPECTIVE = "prospective"


def load_h1_registration(path: Path | None = None) -> dict[str, Any]:
    reg_path = path or H1_REGISTRATION_PATH
    with reg_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("hypothesis_id") != "H1":
        raise ValueError("H1 registration hypothesis_id mismatch")
    if not payload.get("threshold_locked"):
        raise ValueError("H1 threshold must be locked")
    return payload


def h1_metadata(registration: dict[str, Any] | None = None) -> dict[str, Any]:
    reg = registration or load_h1_registration()
    return {
        "hypothesis_id": reg["hypothesis_id"],
        "registered_at": reg["registered_at"],
        "rule": reg["rule"],
        "threshold_locked": bool(reg["threshold_locked"]),
        "yes_ask_threshold": str(reg.get("yes_ask_threshold") or H1_YES_ASK_THRESHOLD),
        "development_event_count": len(reg.get("development_event_tickers") or []),
        "development_market_count": len(reg.get("development_market_tickers") or []),
    }


def development_event_set(registration: dict[str, Any] | None = None) -> set[str]:
    reg = registration or load_h1_registration()
    return {str(e) for e in (reg.get("development_event_tickers") or []) if e}


def _registered_at_dt(registration: dict[str, Any]) -> datetime:
    text = str(registration["registered_at"]).strip()
    # Date-only registration: exclusive end of calendar day UTC for "after".
    if len(text) == 10:
        return datetime.fromisoformat(text + "T23:59:59+00:00")
    parsed = parse_ts(text)
    if parsed is None:
        raise ValueError(f"Invalid H1 registered_at: {text}")
    return parsed


def classify_h1_evidence(
    obs: dict,
    *,
    registration: dict[str, Any] | None = None,
    development_events: set[str] | None = None,
) -> str:
    """
    Label an observation for H1 confirmation accounting.

    - development: event inspected in the H1-origin run (never confirmation)
    - prospective: close/signal after registration, and not development
    - retrospective_holdout: historical, not previously inspected
    """
    reg = registration or load_h1_registration()
    dev = development_events if development_events is not None else development_event_set(reg)
    event = obs.get("event_ticker")
    if event and str(event) in dev:
        return H1_DEVELOPMENT

    registered_at = _registered_at_dt(reg)
    signal_ts = parse_ts(obs.get("close_time") or obs.get("target_entry_ts"))
    if signal_ts is not None and signal_ts > registered_at:
        return H1_PROSPECTIVE
    return H1_RETROSPECTIVE_HOLDOUT


def h1_signal_observations(observations: list[dict]) -> list[dict]:
    """Observations matching locked H1 entry rule (executable YES ask <= 0.20)."""
    threshold = H1_YES_ASK_THRESHOLD
    out = []
    for obs in observations:
        ask = Decimal(str(obs.get("yes_ask", obs.get("yes_entry", "1"))))
        if ask <= threshold:
            out.append(obs)
    return out


def summarize_h1(
    observations: list[dict],
    *,
    registration: dict[str, Any] | None = None,
    spread_filters: tuple[Decimal | float | str, ...] = (None, "0.20", "0.10", "0.05"),
) -> dict[str, Any]:
    """
    Report H1 with development excluded from confirmation slices.

    Confirmation evidence = retrospective_holdout + prospective only.
    """
    reg = registration or load_h1_registration()
    dev_events = development_event_set(reg)
    signals = h1_signal_observations(observations)

    by_class: dict[str, list[dict]] = {
        H1_DEVELOPMENT: [],
        H1_RETROSPECTIVE_HOLDOUT: [],
        H1_PROSPECTIVE: [],
    }
    for obs in signals:
        label = classify_h1_evidence(
            obs, registration=reg, development_events=dev_events
        )
        by_class[label].append(obs)

    confirmation = by_class[H1_RETROSPECTIVE_HOLDOUT] + by_class[H1_PROSPECTIVE]

    def _spread_rows(subset: list[dict]) -> list[dict]:
        rows = []
        for limit in spread_filters:
            if limit is None:
                filtered = subset
                label = "all"
            else:
                lim = Decimal(str(limit))
                filtered = [
                    o for o in subset if Decimal(str(o.get("spread", "1"))) <= lim
                ]
                label = f"spread_le_{limit}"
            yes_summary = summarize_side(filtered, side="YES")
            no_summary = summarize_side(filtered, side="NO")
            rows.append(
                {
                    "spread_filter": label,
                    "cheap_yes": yes_summary,
                    "opposite_no": no_summary,
                }
            )
        return rows

    return {
        "metadata": h1_metadata(reg),
        "all_signals": {
            "cheap_yes": summarize_side(signals, side="YES"),
            "opposite_no": summarize_side(signals, side="NO"),
            "n_signals": len(signals),
        },
        "by_evidence_class": {
            name: {
                "cheap_yes": summarize_side(rows, side="YES"),
                "opposite_no": summarize_side(rows, side="NO"),
                "n_signals": len(rows),
                "unique_events": len(
                    {o.get("event_ticker") for o in rows if o.get("event_ticker")}
                ),
            }
            for name, rows in by_class.items()
        },
        "confirmation": {
            "note": (
                "Excludes H1 development events from the origin 1,000-market run. "
                "Includes retrospective holdout + prospective only."
            ),
            "cheap_yes": summarize_side(confirmation, side="YES"),
            "opposite_no": summarize_side(confirmation, side="NO"),
            "n_signals": len(confirmation),
            "spread_filters": _spread_rows(confirmation),
            "by_class": {
                H1_RETROSPECTIVE_HOLDOUT: {
                    "opposite_no": summarize_side(
                        by_class[H1_RETROSPECTIVE_HOLDOUT], side="NO"
                    ),
                    "n_signals": len(by_class[H1_RETROSPECTIVE_HOLDOUT]),
                },
                H1_PROSPECTIVE: {
                    "opposite_no": summarize_side(by_class[H1_PROSPECTIVE], side="NO"),
                    "n_signals": len(by_class[H1_PROSPECTIVE]),
                },
            },
        },
    }

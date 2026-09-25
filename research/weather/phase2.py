"""Phase 2 settlement-regime catalog, KNYC↔CLINYC pairs, and audits."""

from __future__ import annotations

import csv
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from kalshi import cache
from kalshi.client import KalshiClient
from nws import get_historical_knyc_highs
from research.weather.calibration import CALIBRATION_DIR, ensure_weather_cache_dirs
from research.weather.clinyc import (
    audit_expiration_values,
    fetch_settled_kxhighny_markets,
    get_kalshi_settlement_temperature,
    normalize_expiration_value,
)
from research.weather.models import (
    REGIME_CONFLICTING,
    REGIME_NWS_CLI_KNYC,
    REGIME_UNKNOWN,
    REGIME_WEATHER_COMPANY_CLINYC,
    SERIES_TICKER,
)
from research.weather.resolution import (
    build_weather_resolution,
    get_event_date,
    get_winning_market,
    group_markets_by_event,
    is_range_bucket_event,
    parse_temperature_bucket,
    temperature_matches_bucket,
)


REGIMES_CSV = cache.RESULTS_ROOT / "kxhighny_settlement_regimes.csv"
PAIRS_CSV = CALIBRATION_DIR / "knyc_clinyc_pairs.csv"
CLINYC_AUDIT_PATH = cache.RESULTS_ROOT / "clinyc_payload_audit.json"
TWC_AUDIT_PATH = cache.RESULTS_ROOT / "twc_source_audit.json"
PAIRS_REPORT_PATH = cache.RESULTS_ROOT / "knyc_clinyc_difference_report.json"

REGIME_CSV_FIELDS = [
    "event_ticker",
    "target_date",
    "regime",
    "rules_source_text",
    "numeric_outcome_available",
    "outcome_source",
    "warnings",
]

PAIR_CSV_FIELDS = [
    "event_ticker",
    "target_date",
    "knyc_high_f",
    "clinyc_high_f",
    "difference_f",
    "absolute_difference_f",
    "exact_integer_match",
    "same_integer",  # alias retained for Phase 2 compatibility
    "within_1f",
    "within_2f",
    "same_kalshi_bucket",
    "settlement_source_regime",
    "regime",  # alias retained
    "clinyc_source",
    "knyc_source",
    "evidence_class",
    "confidence",
    "knyc_provenance",
    "clinyc_provenance",
]


def _sanitize_payload(obj: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key.lower() in {"authorization", "api_key", "token", "password"}:
                out[key] = "[redacted]"
            else:
                out[key] = _sanitize_payload(value, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [_sanitize_payload(v, depth=depth + 1) for v in obj[:20]]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def run_clinyc_payload_audit(
    *,
    event_tickers: list[str] | None = None,
    client: KalshiClient | None = None,
) -> dict[str, Any]:
    """Inspect event/market payloads for numeric settlement temperature fields."""
    ensure_weather_cache_dirs()
    cache.ensure_dirs()
    client = client or KalshiClient()
    tickers = event_tickers or [
        "KXHIGHNY-26SEP22",
        "KXHIGHNY-26SEP21",
        "KXHIGHNY-26SEP20",
        "KXHIGHNY-26JUL13",
    ]

    settled = fetch_settled_kxhighny_markets(client=client)
    grouped = group_markets_by_event(settled)

    examples: list[dict[str, Any]] = []
    api_numeric = False
    for ticker in tickers:
        markets = grouped.get(ticker) or []
        event_meta: dict[str, Any] = {}
        try:
            event_meta = client.get_event(ticker, with_nested_markets=True)
            if isinstance(event_meta, dict) and event_meta.get("markets"):
                markets = markets or list(event_meta["markets"])
        except Exception as error:  # noqa: BLE001
            event_meta = {"_error": str(error)}

        sample_market = markets[0] if markets else {}
        audit = audit_expiration_values(markets) if markets else None
        if audit and audit.agreement:
            api_numeric = True

        field_presence = {
            "expiration_value": any(
                m.get("expiration_value") not in (None, "") for m in markets
            ),
            "settlement_value_dollars": any(
                m.get("settlement_value_dollars") is not None for m in markets
            ),
            "result": any(m.get("result") for m in markets),
            "rules_primary": bool(sample_market.get("rules_primary")),
            "settlement_sources_on_event": bool(
                isinstance(event_meta, dict) and event_meta.get("settlement_sources")
            ),
            "product_metadata_on_event": bool(
                isinstance(event_meta, dict) and event_meta.get("product_metadata")
            ),
        }
        examples.append(
            {
                "event_ticker": ticker,
                "market_count": len(markets),
                "field_presence": field_presence,
                "expiration_audit": audit.to_dict() if audit else None,
                "sample_market_fields": _sanitize_payload(
                    {
                        k: sample_market.get(k)
                        for k in (
                            "ticker",
                            "status",
                            "result",
                            "expiration_value",
                            "settlement_value_dollars",
                            "rules_primary",
                            "strike_type",
                            "yes_sub_title",
                        )
                        if k in sample_market or True
                    }
                ),
                "event_meta_keys": sorted(event_meta.keys())
                if isinstance(event_meta, dict)
                else [],
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exact_numeric_outcome_available_via_api": api_numeric,
        "preferred_field": "expiration_value",
        "settlement_value_dollars_note": (
            "settlement_value_dollars is the $0/$1 contract payout, "
            "not the underlying weather temperature"
        ),
        "event_level_agreement_required": True,
        "examples": examples,
        "conclusions": [
            "Use event-level unanimous numeric expiration_value as settlement temperature.",
            "Reject conflicting / malformed / non-final markets.",
            "Do not treat settlement_value_dollars as the weather outcome.",
        ],
    }
    cache.write_json(CLINYC_AUDIT_PATH, report)
    return report


def run_twc_source_audit() -> dict[str, Any]:
    """Discovery-only Weather Company API audit — no credentials purchased."""
    ensure_weather_cache_dirs()
    cache.ensure_dirs()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": [
            {
                "candidate_API": "Historical Conditions Daily Summary",
                "authentication_requirement": "IBM Weather Company API key required",
                "location_identifier_options": [
                    "lat/lon",
                    "place ID",
                    "ICAO/IATA (unclear for CLINYC)",
                ],
                "date_coverage": "vendor-documented historical depth; not verified here",
                "exact_CLINYC_availability": "unknown without credentials",
                "test_request_result": "skipped — no API key in environment",
                "usefulness": (
                    "potentially useful for independent CLINYC series, "
                    "but Kalshi expiration_value is preferred ground truth"
                ),
            },
            {
                "candidate_API": "Historical Data Analytical Tooling",
                "authentication_requirement": "commercial / enterprise",
                "location_identifier_options": ["vendor place IDs"],
                "date_coverage": "unknown",
                "exact_CLINYC_availability": "unknown",
                "test_request_result": "skipped — no purchase / credentials",
                "usefulness": "not usable for Phase 2 without paid access",
            },
            {
                "candidate_API": "weather.com/kalshi public page",
                "authentication_requirement": "none for browser view",
                "location_identifier_options": ["CLINYC label in Kalshi rules"],
                "date_coverage": "current / recent display only",
                "exact_CLINYC_availability": "display convenience — not authoritative archive",
                "test_request_result": "not scraped in Phase 2 primary path",
                "usefulness": "secondary validation only; Kalshi expiration_value preferred",
            },
        ],
        "environment_has_twc_api_key": False,
        "recommendation": (
            "Do not purchase TWC access for Phase 2. Recover CLINYC settlement "
            "temperatures from Kalshi market expiration_value with event-level agreement."
        ),
    }
    cache.write_json(TWC_AUDIT_PATH, report)
    return report


def build_settlement_regimes_csv(
    *,
    client: KalshiClient | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    ensure_weather_cache_dirs()
    cache.ensure_dirs()
    log = progress or (lambda _m: None)
    client = client or KalshiClient()
    markets = fetch_settled_kxhighny_markets(client=client, progress=log)
    grouped = group_markets_by_event(markets)

    # Classify from per-event market rules only. Do NOT apply the current
    # series settlement_sources (often Weather Company) to historical events.
    rows: list[dict[str, Any]] = []
    for event_ticker, event_markets in sorted(
        grouped.items(), key=lambda item: get_event_date(item[0]) or ""
    ):
        if not is_range_bucket_event(event_markets):
            continue
        target_date = get_event_date(event_ticker) or ""
        resolution = build_weather_resolution(
            event_ticker=event_ticker,
            markets=event_markets,
            series_meta=None,
            event_meta=None,
        )
        audit = audit_expiration_values(event_markets)
        rules_text = str(event_markets[0].get("rules_primary") or "")[:240]
        rows.append(
            {
                "event_ticker": event_ticker,
                "target_date": target_date,
                "regime": resolution.settlement_source_regime,
                "rules_source_text": rules_text.replace("\n", " "),
                "numeric_outcome_available": str(audit.agreement).lower(),
                "outcome_source": (
                    "expiration_value" if audit.agreement else audit.resolution_status
                ),
                "warnings": "; ".join(resolution.warnings + audit.warnings),
            }
        )

    REGIMES_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REGIMES_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGIME_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    nws_dates = [
        r["target_date"]
        for r in rows
        if r["regime"] == REGIME_NWS_CLI_KNYC and r["target_date"]
    ]
    twc_dates = [
        r["target_date"]
        for r in rows
        if r["regime"] == REGIME_WEATHER_COMPANY_CLINYC and r["target_date"]
    ]
    conflicting = [r for r in rows if r["regime"] == REGIME_CONFLICTING]
    unknown = [r for r in rows if r["regime"] == REGIME_UNKNOWN]

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "path": str(REGIMES_CSV),
        "event_count": len(rows),
        "earliest_verified_twc_event": min(twc_dates) if twc_dates else None,
        "latest_verified_nws_event": max(nws_dates) if nws_dates else None,
        "nws_count": len(nws_dates),
        "twc_count": len(twc_dates),
        "conflicting_count": len(conflicting),
        "unknown_count": len(unknown),
        "overlap_or_gap": {
            "nws_end": max(nws_dates) if nws_dates else None,
            "twc_start": min(twc_dates) if twc_dates else None,
            "gap_or_overlap_days": None,
        },
    }
    if nws_dates and twc_dates:
        from datetime import date

        nws_end = date.fromisoformat(max(nws_dates))
        twc_start = date.fromisoformat(min(twc_dates))
        summary["overlap_or_gap"]["gap_or_overlap_days"] = (twc_start - nws_end).days

    cache.write_json(
        cache.RESULTS_ROOT / "kxhighny_settlement_regimes_summary.json",
        summary,
    )
    log(
        f"Regimes CSV: {len(rows)} events; "
        f"earliest TWC={summary['earliest_verified_twc_event']}; "
        f"latest NWS={summary['latest_verified_nws_event']}"
    )
    return summary


def same_kalshi_bucket(
    knyc_high: float,
    clinyc_high: float,
    markets: list[dict[str, Any]],
) -> tuple[bool, bool]:
    """Return (same_bucket, source_changes_winner)."""
    buckets = [b for m in markets if (b := parse_temperature_bucket(m)) is not None]
    if not buckets:
        return False, False
    knyc_labels = {b.label for b in buckets if b.contains(knyc_high)}
    clinyc_labels = {b.label for b in buckets if b.contains(clinyc_high)}
    same = bool(knyc_labels & clinyc_labels) and knyc_labels == clinyc_labels
    changes = knyc_labels != clinyc_labels
    return same, changes


def load_pairs_csv(path: Path | None = None) -> list[dict[str, Any]]:
    """Load KNYC↔CLINYC pairs CSV (empty list if missing)."""
    csv_path = path or PAIRS_CSV
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_knyc_clinyc_pairs(
    *,
    client: KalshiClient | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    ensure_weather_cache_dirs()
    cache.ensure_dirs()
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    log = progress or (lambda _m: None)
    client = client or KalshiClient()
    markets = fetch_settled_kxhighny_markets(client=client, progress=log)
    grouped = group_markets_by_event(markets)

    from research.weather.transfer import (
        assign_evidence_class,
        load_or_register_hypothesis,
    )

    hypothesis = load_or_register_hypothesis()

    years: set[int] = set()
    for event_ticker in grouped:
        d = get_event_date(event_ticker)
        if d:
            years.add(int(d[:4]))
    knyc_highs: dict[str, float] = {}
    for year in sorted(years):
        knyc_highs.update(get_historical_knyc_highs(year))

    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for event_ticker, event_markets in sorted(
        grouped.items(), key=lambda item: get_event_date(item[0]) or ""
    ):
        if not is_range_bucket_event(event_markets):
            continue
        target_date = get_event_date(event_ticker)
        if not target_date:
            continue
        # Canonical one row per event/date — skip duplicates.
        key = (event_ticker, target_date)
        if key in seen:
            continue
        seen.add(key)

        knyc = knyc_highs.get(target_date)
        obs = get_kalshi_settlement_temperature(
            event_ticker,
            client=client,
            settled_markets=markets,
        )
        # Source independence: never synthesize a missing side from the other.
        if knyc is None or obs is None or obs.settlement_temperature_f is None:
            continue
        clinyc = float(obs.settlement_temperature_f)
        # Resolve regime from market rules (not series metadata / not date).
        resolution = build_weather_resolution(
            event_ticker=event_ticker,
            markets=event_markets,
            series_meta=None,
            event_meta=None,
        )
        regime = resolution.settlement_source_regime
        diff = clinyc - float(knyc)  # CLINYC - KNYC (sign convention)
        same_int = int(round(clinyc)) == int(round(float(knyc)))
        abs_diff = abs(diff)
        same_bucket, _changes = same_kalshi_bucket(float(knyc), clinyc, event_markets)
        evidence = assign_evidence_class(target_date, hypothesis=hypothesis)
        pairs.append(
            {
                "event_ticker": event_ticker,
                "target_date": target_date,
                "knyc_high_f": float(knyc),
                "clinyc_high_f": clinyc,
                "difference_f": diff,
                "absolute_difference_f": abs_diff,
                "exact_integer_match": str(same_int).lower(),
                "same_integer": str(same_int).lower(),
                "within_1f": str(abs_diff <= 1.0 + 1e-9).lower(),
                "within_2f": str(abs_diff <= 2.0 + 1e-9).lower(),
                "same_kalshi_bucket": str(same_bucket).lower(),
                "settlement_source_regime": regime,
                "regime": regime,
                "clinyc_source": "kalshi_expiration_value",
                "knyc_source": "iem_nws_cli",
                "evidence_class": evidence,
                "confidence": obs.parsing_confidence,
                "knyc_provenance": f"iem_nws_cli:KNYC:{target_date}",
                "clinyc_provenance": (
                    f"kalshi_expiration_value:{event_ticker}:"
                    f"{obs.raw_cache_path or ''}"
                ),
            }
        )

    with PAIRS_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PAIR_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(pairs)

    twc_pairs = [
        p for p in pairs if p["regime"] == REGIME_WEATHER_COMPANY_CLINYC
    ]
    twc_winner_changes = sum(
        1 for p in twc_pairs if str(p["same_kalshi_bucket"]).lower() != "true"
    )
    report = summarize_pairs(twc_pairs, winner_changes=twc_winner_changes)
    report["all_regimes_paired_N"] = len(pairs)
    report["nws_paired_N"] = sum(
        1 for p in pairs if p["regime"] == REGIME_NWS_CLI_KNYC
    )
    report["twc_paired_N"] = len(twc_pairs)
    report["development_n"] = sum(
        1 for p in twc_pairs if p.get("evidence_class") == "development"
    )
    report["prospective_n"] = sum(
        1 for p in twc_pairs if p.get("evidence_class") == "prospective"
    )
    report["note"] = (
        "Primary difference stats use weather_company_clinyc rows only. "
        "NWS-era expiration_value should match KNYC and is reported separately. "
        "difference_f = CLINYC - KNYC. "
        "evidence_class is frozen against the Phase 3 hypothesis registration."
    )
    if report["nws_paired_N"]:
        nws_pairs = [p for p in pairs if p["regime"] == REGIME_NWS_CLI_KNYC]
        report["nws_era_sanity"] = summarize_pairs(nws_pairs)
    cache.write_json(PAIRS_REPORT_PATH, report)
    log(f"Wrote {len(pairs)} pairs ({len(twc_pairs)} TWC) -> {PAIRS_CSV}")
    return report


def summarize_pairs(
    pairs: list[dict[str, Any]],
    *,
    winner_changes: int | None = None,
) -> dict[str, Any]:
    if not pairs:
        return {
            "N": 0,
            "note": "No paired days recovered yet",
            "direct_clinyc_dataset_exists": False,
            "direct_clinyc_operationally_eligible": False,
            "direct_clinyc_calibration_feasible": False,  # legacy alias
        }

    diffs = [float(p["difference_f"]) for p in pairs]
    abs_diffs = [abs(d) for d in diffs]
    n = len(diffs)
    hist = Counter(int(round(d)) for d in diffs)
    same_int = sum(
        1
        for p in pairs
        if str(p.get("exact_integer_match", p.get("same_integer"))).lower() == "true"
    )
    same_bucket = sum(
        1 for p in pairs if str(p["same_kalshi_bucket"]).lower() == "true"
    )
    within_1 = sum(1 for d in abs_diffs if d <= 1.0 + 1e-9)
    within_2 = sum(1 for d in abs_diffs if d <= 2.0 + 1e-9)
    if winner_changes is None:
        winner_changes = n - same_bucket

    mean_diff = statistics.fmean(diffs)
    median_diff = statistics.median(diffs)
    mae = statistics.fmean(abs_diffs)
    rmse = math.sqrt(statistics.fmean(d * d for d in diffs))
    std = statistics.pstdev(diffs) if n > 1 else 0.0

    # Classify discrepancy shape (exploratory).
    exact_pct = same_int / n
    if exact_pct >= 0.9 and mae < 0.2:
        shape = "A_almost_always_0"
    elif abs(mean_diff) >= 0.75 and std < 0.75:
        shape = "B_stable_fixed_offset"
    elif mae <= 1.25 and std <= 1.5:
        shape = "C_random_pm1_noise"
    else:
        shape = "D_materially_larger_unstable"

    from research.weather.models import (
        MIN_N_MONTH,
        MIN_N_RUN,
        MIN_N_SEASON,
        MIN_RUN_HISTORY,
    )

    # Dataset exists iff we have paired CLINYC outcomes.
    dataset_exists = n > 0
    # Operational eligibility requires hierarchical same-run pools — NOT merely
    # N >= MIN_RUN_HISTORY. Pair count alone cannot satisfy MIN_N_RUN=80.
    # Without per-run residual rows here, report operationally_eligible=False
    # whenever N < MIN_N_RUN (conservative; detailed check in transfer module).
    operationally_eligible = False  # pairs ≠ residual pools; see eligibility module

    return {
        "N": n,
        "mean_difference": mean_diff,
        "median_difference": median_diff,
        "MAE": mae,
        "RMSE": rmse,
        "standard_deviation": std,
        "exact_same_integer_pct": exact_pct,
        "within_1F_pct": within_1 / n,
        "within_2F_pct": within_2 / n,
        "same_kalshi_bucket_pct": same_bucket / n,
        "source_choice_changes_winner_count": winner_changes,
        "difference_histogram_rounded_F": {
            str(k): hist[k] for k in sorted(hist)
        },
        "discrepancy_shape": shape,
        "direct_clinyc_dataset_exists": dataset_exists,
        "direct_clinyc_operationally_eligible": operationally_eligible,
        # Legacy alias — must NOT imply operational usability.
        "direct_clinyc_calibration_feasible": False,
        "feasibility_note": (
            f"Paired CLINYC dataset N={n} (dataset_exists={dataset_exists}). "
            f"Operational eligibility still requires same-run hierarchical "
            f"pools: MIN_N_MONTH={MIN_N_MONTH}, MIN_N_SEASON={MIN_N_SEASON}, "
            f"MIN_N_RUN={MIN_N_RUN}, MIN_RUN_HISTORY={MIN_RUN_HISTORY}. "
            f"N>={MIN_RUN_HISTORY} alone is NOT sufficient."
        ),
        "min_run_history_threshold": MIN_RUN_HISTORY,
        "pairs_csv": str(PAIRS_CSV),
    }


def run_phase2_research(*, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Execute Phase 2 recovery pipeline (explicit CLI / research job)."""
    log = progress or (lambda _m: None)
    client = KalshiClient(progress=log)
    audit = run_clinyc_payload_audit(client=client)
    log(f"exact_numeric_outcome_available_via_api={audit['exact_numeric_outcome_available_via_api']}")
    twc = run_twc_source_audit()
    regimes = build_settlement_regimes_csv(client=client, progress=log)
    pairs = build_knyc_clinyc_pairs(client=client, progress=log)
    return {
        "clinyc_payload_audit": audit,
        "twc_source_audit": twc,
        "regimes": regimes,
        "pairs": pairs,
    }

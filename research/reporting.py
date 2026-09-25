"""Rich console reporting and CSV output."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from kalshi import cache
from kalshi.historical import InventoryAudit
from research.sampling import (
    dominance_tops,
    inventory_diagnostics,
    settlement_year,
)


console = Console(legacy_windows=False, force_terminal=True)


def print_inventory_audit(
    audit: InventoryAudit,
    usable_markets: list[dict] | None = None,
    stream_diagnostics: dict | None = None,
) -> None:
    usable = list(usable_markets or [])
    stream = stream_diagnostics or getattr(audit, "_stream_diagnostics", None) or {}
    if stream:
        diag = {
            "inventory_date_range": stream.get("inventory_date_range", "n/a"),
            "inventory_years": stream.get("inventory_years", []),
            "inventory_categories": stream.get(
                "inventory_categories", dict(audit.categories.most_common())
            ),
            "inventory_unique_series": stream.get("inventory_unique_series", 0),
            "inventory_unique_events": stream.get("inventory_unique_events", 0),
        }
        by_year = Counter(stream.get("markets_by_year") or {})
        by_category = Counter(diag["inventory_categories"])
        top_event = stream.get("largest_event") or ("n/a", 0)
        top_series = stream.get("largest_series") or ("n/a", 0)
        top_day = stream.get("largest_day") or ("n/a", 0)
    elif usable:
        diag = inventory_diagnostics(usable)
        by_year = Counter(
            year for year in (settlement_year(m) for m in usable) if year is not None
        )
        by_category = Counter(m.get("category") or "Unknown" for m in usable)
        top_event, top_series, top_day = dominance_tops(usable)
    else:
        diag = {
            "inventory_date_range": "n/a",
            "inventory_years": [],
            "inventory_categories": dict(audit.categories.most_common()),
            "inventory_unique_series": 0,
            "inventory_unique_events": 0,
        }
        by_year = Counter()
        by_category = Counter(diag["inventory_categories"])
        top_event, top_series, top_day = ("n/a", 0), ("n/a", 0), ("n/a", 0)

    named_exclusion_reasons = {
        "excluded_combo",
        "nonstandard_settlement",
        "missing_close_time",
        "missing_result",
    }
    unique_excluded = int(
        stream.get("unique_excluded")
        if stream.get("unique_excluded") is not None
        else getattr(audit, "unique_excluded", 0)
        or sum(audit.skip_reasons.values())
        or (
            audit.excluded_combos
            + audit.nonstandard_settlement
            + audit.missing_close_time
            + audit.missing_result
        )
    )
    other_exclusions = sum(
        count
        for reason, count in audit.skip_reasons.items()
        if reason not in named_exclusion_reasons
    )
    recon_valid = stream.get("count_reconciliation_valid")
    if recon_valid is None:
        recon_valid = audit.combined_unique == audit.usable + unique_excluded
    integrity_valid = stream.get("inventory_integrity_valid")
    if integrity_valid is None:
        integrity_valid = getattr(audit, "inventory_integrity_valid", None)

    console.print()
    console.rule("KALSHI INVENTORY AUDIT")
    console.print(f"Raw historical markets:          {audit.historical_count:,}")
    console.print(f"Raw recent settled markets:      {audit.recent_count:,}")
    console.print(f"Combined unique markets:         {audit.combined_unique:,}")
    console.print(f"Unique excluded markets:         {unique_excluded:,}")
    console.print(f"Usable binary markets:            {audit.usable:,}")
    console.print(
        f"Count reconciliation:             "
        f"{'valid' if recon_valid else 'INVALID'}"
    )
    console.print()
    console.print(f"Excluded combos:                 {audit.excluded_combos:,}")
    console.print(f"Excluded nonstandard settlements: {audit.nonstandard_settlement:,}")
    console.print(f"Missing close time:              {audit.missing_close_time:,}")
    console.print(f"Missing result:                  {audit.missing_result:,}")
    console.print(f"Other exclusions:                {other_exclusions:,}")
    console.print()
    console.print(f"Date range:                      {diag.get('inventory_date_range', 'n/a')}")
    years = diag.get("inventory_years") or []
    console.print(
        f"Years represented:                {', '.join(str(y) for y in years) or 'n/a'}"
    )
    console.print(
        f"Categories:                       {len(diag.get('inventory_categories') or {}):,}"
    )
    console.print(
        f"Unique series:                    {diag.get('inventory_unique_series', 0):,}"
    )
    console.print(
        f"Unique events:                    {diag.get('inventory_unique_events', 0):,}"
    )
    required_missing = stream.get("required_field_missing_counts") or {}
    optional_missing = stream.get("optional_field_missing_counts") or {}
    console.print()
    console.print(
        f"Required field missing:           "
        f"{sum(int(v) for v in required_missing.values()):,}"
    )
    for key, count in sorted(required_missing.items()):
        if count:
            console.print(f"  {key:<30} {int(count):,}")
    console.print(
        f"Missing open_time:                "
        f"{int(stream.get('missing_open_time') or optional_missing.get('open_time') or 0):,}"
    )
    console.print()
    console.print(
        f"inventory_schema_version:         "
        f"{stream.get('inventory_schema_version') or 'n/a'}"
    )
    console.print(
        f"inventory_integrity_valid:        {integrity_valid}"
    )
    console.print()
    console.print("Markets by year:")
    if by_year:
        for year, count in sorted(by_year.items()):
            console.print(f"  {year} {count:,}")
    else:
        console.print("  n/a")
    console.print()
    console.print("Markets by category:")
    if by_category:
        for category, count in by_category.most_common():
            console.print(f"  {category:<24} {count:,}")
    else:
        console.print("  n/a")
    console.print()
    console.print(f"Largest event:                   {top_event[0]} ({top_event[1]:,})")
    console.print(f"Largest series:                  {top_series[0]} ({top_series[1]:,})")
    console.print(f"Largest calendar day:            {top_day[0]} ({top_day[1]:,})")
    if audit.cutoff:
        console.print()
        console.print(
            f"Cutoff market_settled_ts:        {audit.cutoff.get('market_settled_ts')}"
        )

def print_recent_settled_diagnostics(diag: dict) -> None:
    console.print()
    console.rule("RECENT SETTLED INVENTORY")
    console.print(f"pages cached:          {diag.get('pages_cached', 0):,}")
    console.print(f"markets retrieved:     {diag.get('markets_retrieved', 0):,}")
    console.print(f"first close date:      {diag.get('first_close_date') or 'n/a'}")
    console.print(f"last close date:       {diag.get('last_close_date') or 'n/a'}")
    console.print(f"last cursor:           {diag.get('last_cursor') or 'n/a'}")
    console.print(f"complete:              {diag.get('complete')}")
    console.print(f"truncated_by_limit:    {diag.get('truncated_by_limit')}")
    console.print(f"resume_unsupported:    {diag.get('resume_unsupported')}")
    console.print(f"seeded_from_legacy:    {diag.get('seeded_from_legacy')}")


def print_future_close_audit(
    summary: dict,
    examples: list[dict],
    *,
    path=None,
) -> None:
    console.print()
    console.rule("FUTURE CLOSE AUDIT")
    console.print(f"inventory build time:  {summary.get('inventory_build_time')}")
    console.print(f"future-close rows:     {summary.get('future_close_rows', 0):,}")
    console.print(f"unique events:         {summary.get('unique_events', 0):,}")
    console.print(f"can_close_early=true:  {summary.get('can_close_early_true', 0):,}")
    console.print(f"can_close_early=false: {summary.get('can_close_early_false', 0):,}")
    console.print(f"min future close:      {summary.get('min_future_close') or 'n/a'}")
    console.print(f"max future close:      {summary.get('max_future_close') or 'n/a'}")
    console.print(
        f"settled_before_close:  {summary.get('settled_before_close_count', 0):,} "
        f"({100 * float(summary.get('settled_before_close_share') or 0):.1f}%)"
    )
    console.print(
        "median seconds settlement before close: "
        f"{summary.get('median_seconds_settlement_before_close')}"
    )
    console.print(
        "mean seconds settlement before close: "
        f"{summary.get('mean_seconds_settlement_before_close')}"
    )
    if path is not None:
        console.print(f"wrote:                 {path}")
    console.print()
    console.print("First 20 examples:")
    for row in examples:
        console.print(
            f"  {row.get('ticker')} close={row.get('close_time')} "
            f"settled={row.get('settlement_ts')} "
            f"early={row.get('can_close_early')} "
            f"settled_before_close={row.get('settled_before_close')}"
        )


def print_sampling_diagnostics(diag: dict) -> None:
    mode = diag.get("sampling_mode") or "n/a"
    console.print()
    console.rule("SAMPLING DIAGNOSTICS")
    console.print(f"SAMPLING MODE:                 {mode}")
    console.print()
    console.print(f"Selected markets:              {diag.get('selected_markets', 0):,}")
    console.print(f"Unique events:                 {diag.get('selected_unique_events', 0):,}")
    console.print(f"Unique series:                 {diag.get('selected_unique_series', 0):,}")
    console.print(
        f"Unique close dates:             {diag.get('selected_unique_close_dates', 0):,}"
    )
    console.print()
    console.print("Markets per event:")
    console.print(f"  median:                      {diag.get('markets_per_event_median', 0):.1f}")
    console.print(f"  95th percentile:             {diag.get('markets_per_event_p95', 0):.1f}")
    console.print(f"  max:                         {diag.get('markets_per_event_max', 0):,}")
    console.print()
    console.print("Counts by year:")
    for year, count in (diag.get("selected_by_year") or {}).items():
        console.print(f"  {year}: {count:,}")
    console.print("Counts by category:")
    for category, count in (diag.get("selected_by_category") or {}).items():
        console.print(f"  {category:<24} {count:,}")
    console.print("Counts by year x category:")
    for key, count in (diag.get("selected_by_year_category") or {}).items():
        console.print(f"  {key:<32} {count:,}")
    console.print()
    console.print(
        f"Largest calendar day:           {diag.get('largest_calendar_day', 'n/a')} "
        f"({diag.get('largest_single_day_market_count', 0):,})"
    )
    console.print(
        f"Largest event:                  {diag.get('largest_event', 'n/a')} "
        f"({diag.get('largest_event_market_count', 0):,})"
    )
    console.print(
        f"Largest series:                 {diag.get('largest_series', 'n/a')} "
        f"({diag.get('largest_series_market_count', 0):,})"
    )
    console.print()
    console.print(f"{'Category':<16} {'Inventory %':>12} {'Sample %':>12}")
    inv_share = diag.get("category_inventory_share") or {}
    samp_share = diag.get("category_sample_share") or {}
    for category in sorted(set(inv_share) | set(samp_share)):
        console.print(
            f"{category:<16} {100 * float(inv_share.get(category, 0)):12.1f}% "
            f"{100 * float(samp_share.get(category, 0)):12.1f}%"
        )
    console.print()
    console.print(f"Inventory markets:             {diag.get('inventory_markets', 0):,}")
    console.print(f"Inventory date range:          {diag.get('inventory_date_range', 'n/a')}")
    console.print(f"Sampling seed:                 {diag.get('seed', 'n/a')}")
    console.print(
        f"Max markets/event:             {diag.get('max_markets_per_event', 'n/a')}"
    )
    console.print(
        f"Max markets/series-date:       {diag.get('max_markets_per_series_date', 'n/a')}"
    )


def _print_bucket_table(rows: list[dict], *, mode: str) -> None:
    table = Table(show_header=True, header_style="bold")
    if mode == "executable":
        cols = ("Price", "N", "Win%", "Avg entry", "Gap", "Net ROI")
    else:
        cols = ("Midquote", "N", "Win%", "Avg mid", "Cal gap", "Net ROI*")
    for col in cols:
        table.add_column(col)
    for row in rows:
        if row["n"] == 0:
            continue
        if mode == "executable":
            table.add_row(
                row["bucket"],
                f"{row['n']:,}",
                f"{row['win_rate']*100:.1f}%",
                f"{row.get('avg_executable_entry', row.get('avg_entry', 0))*100:.1f}%",
                f"{row.get('entry_gap', row.get('calibration_gap', 0))*100:+.1f}pp",
                f"{row['roi']*100:+.1f}%",
            )
        else:
            table.add_row(
                row["bucket"],
                f"{row['n']:,}",
                f"{row['win_rate']*100:.1f}%",
                f"{row.get('avg_midquote', 0)*100:.1f}%",
                f"{row.get('calibration_gap', 0)*100:+.1f}pp",
                f"{row['roi']*100:+.1f}%",
            )
    console.print(table)
    if mode != "executable":
        console.print(
            "[dim]* Net ROI still uses executable taker prices on the same rows; "
            "midquote is the calibration belief proxy only.[/dim]"
        )


def print_horizon_coverage(rows: list[dict]) -> None:
    console.print()
    console.rule("HORIZON COVERAGE")
    table = Table(show_header=True, header_style="bold")
    cols = (
        "Horizon",
        "Attempted",
        "Usable",
        "Events",
        "Before open",
        "Missing while open",
        "Stale",
        "Bad bid",
        "Bad ask",
        "Fee",
        "Other",
        "OK",
    )
    for col in cols:
        table.add_column(col)
    for row in rows:
        rejected = sum(
            int(row.get(k, 0))
            for k in (
                "horizon_before_market_open",
                "candle_missing_despite_market_being_open",
                "stale_candle",
                "missing_non_executable_bid",
                "missing_non_executable_ask",
                "unresolved_fee",
                "unsupported_fee",
                "other_rejection",
            )
        )
        ok = int(row["markets_attempted"]) == int(row["usable_observations"]) + rejected
        fee_total = int(row.get("unresolved_fee", 0)) + int(
            row.get("unsupported_fee", 0)
        )
        table.add_row(
            str(row["horizon"]),
            f"{row['markets_attempted']:,}",
            f"{row['usable_observations']:,}",
            f"{row['unique_events']:,}",
            f"{row.get('horizon_before_market_open', 0):,}",
            f"{row.get('candle_missing_despite_market_being_open', 0):,}",
            f"{row.get('stale_candle', 0):,}",
            f"{row.get('missing_non_executable_bid', 0):,}",
            f"{row.get('missing_non_executable_ask', 0):,}",
            f"{fee_total:,}",
            f"{row.get('other_rejection', 0):,}",
            "yes" if ok else "NO",
        )
    console.print(table)


def print_lifespan_report(rows: list[dict]) -> None:
    console.print()
    console.rule("MARKET LIFESPAN")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Duration")
    table.add_column("Markets")
    for row in rows:
        table.add_row(row["bucket"], f"{row['n']:,}")
    console.print(table)


def print_extreme_quote_audit(rows: list[dict], *, limit: int = 20) -> None:
    console.print()
    console.rule("EXTREME QUOTE AUDIT")
    console.print(f"Extreme cases: {len(rows):,} (showing first {min(limit, len(rows))})")
    table = Table(show_header=True, header_style="bold")
    for col in (
        "Ticker",
        "Event",
        "Result",
        "YES ask",
        "Bid",
        "Spread",
        "Mid",
        "Last",
        "Category",
    ):
        table.add_column(col)
    for row in rows[:limit]:
        table.add_row(
            str(row.get("ticker") or ""),
            str(row.get("event_ticker") or ""),
            str(row.get("result") or ""),
            str(row.get("yes_ask") or ""),
            str(row.get("yes_bid") or ""),
            str(row.get("spread") or ""),
            str(row.get("midquote") or ""),
            str(row.get("last_trade") or ""),
            str(row.get("category") or ""),
        )
    console.print(table)


def print_h1_report(h1: dict, *, min_observations: int = 100) -> None:
    console.print()
    console.rule("H1 RESEARCH STATUS")
    meta = h1.get("metadata") or {}
    console.print(f"hypothesis_id:     {meta.get('hypothesis_id')}")
    console.print(f"registered_at:     {meta.get('registered_at')}")
    console.print(f"rule:              {meta.get('rule')}")
    console.print(f"threshold_locked:  {meta.get('threshold_locked')}")
    console.print(
        f"development events/markets locked out: "
        f"{meta.get('development_event_count', 0):,} / "
        f"{meta.get('development_market_count', 0):,}"
    )

    by_class = h1.get("by_evidence_class") or {}
    console.print()
    console.print("Evidence classes (H1 signals: yes_ask <= 0.20):")
    for name, label in (
        ("development", "DEVELOPMENT (not confirmation)"),
        ("retrospective_holdout", "RETROSPECTIVE HOLDOUT"),
        ("prospective", "PROSPECTIVE"),
    ):
        block = by_class.get(name) or {}
        no = block.get("opposite_no") or {}
        n = int(block.get("n_signals") or 0)
        roi = (no.get("roi") or 0) * 100
        if n < min_observations:
            console.print(
                f"  {label}: N={n:,} events={block.get('unique_events', 0):,} "
                f"[descriptive only] Raw NO ROI={roi:+.1f}% "
                f"[{no.get('sample_label')}]"
            )
        else:
            console.print(
                f"  {label}: N={n:,} "
                f"events={block.get('unique_events', 0):,} "
                f"NO ROI={roi:+.1f}% "
                f"[{no.get('sample_label')}]"
            )

    conf = h1.get("confirmation") or {}
    console.print()
    console.print("CONFIRMATION (excludes development):")
    console.print(conf.get("note", ""))
    yes = conf.get("cheap_yes") or {}
    no = conf.get("opposite_no") or {}
    for label, block in (("Cheap YES", yes), ("Opposite NO", no)):
        n = int(block.get("n") or 0)
        roi = (block.get("roi") or 0) * 100
        if n < min_observations:
            console.print(
                f"  {label}: N={n:,} events={block.get('unique_events', 0):,} "
                f"[descriptive only] Raw ROI={roi:+.1f}%"
            )
        else:
            console.print(
                f"  {label}: N={n:,} events={block.get('unique_events', 0):,} "
                f"ROI={roi:+.1f}%"
                + (
                    f" event-weighted ROI={block.get('event_weighted_roi', 0)*100:+.1f}%"
                    if "event_weighted_roi" in block
                    else ""
                )
            )
    for row in conf.get("spread_filters") or []:
        no_row = row.get("opposite_no") or {}
        filt = str(row.get("spread_filter"))
        n = int(no_row.get("n") or 0)
        roi = (no_row.get("roi") or 0) * 100
        if n < min_observations:
            console.print(
                f"  filter={filt}: Opposite NO N={n:,} "
                f"[descriptive only] Raw ROI={roi:+.1f}%"
            )
        else:
            console.print(
                f"  filter={filt}: Opposite NO "
                f"N={n:,} ROI={roi:+.1f}%"
            )


def print_efficiency_report(
    *,
    dataset: dict,
    executable_bucket_rows: list[dict],
    midquote_bucket_rows: list[dict],
    tight_spread_calibrations: list[dict],
    favorite_longshot: dict,
    h1_report: dict | None,
    category_rows: list[dict],
    period_rows: list[dict],
    period_bounds: dict,
    strategy_rows: list[dict],
    spread_profit_rows: list[dict],
    liquidity_meta: dict,
    run_stats: dict,
    horizon_coverage: list[dict] | None = None,
    lifespan_rows: list[dict] | None = None,
    extreme_rows: list[dict] | None = None,
) -> None:
    console.print()
    console.rule("KALSHI EDGE LAB — MARKET EFFICIENCY BACKTEST")
    console.print()
    console.print(f"Settled markets found:      {dataset.get('settled_found', 0):,}")
    console.print(f"Usable markets:             {dataset.get('usable_markets', 0):,}")
    console.print()

    selected = dataset.get("selected_sample") or {}
    if selected:
        console.print("SELECTED SAMPLE")
        console.print(f"markets:       {selected.get('markets', dataset.get('sample_markets', 0)):,}")
        console.print(f"events:        {selected.get('events', 0):,}")
        console.print(f"series:        {selected.get('series', 0):,}")
        console.print(f"categories:    {selected.get('categories', 0):,}")
        console.print(f"date range:    {selected.get('date_range', 'n/a')}")
        years = selected.get("year_counts") or {}
        console.print(
            "years:         "
            + (", ".join(f"{y}:{n}" for y, n in years.items()) or "n/a")
        )
        console.print()

    usable = dataset.get("final_usable_observations") or {}
    console.print("FINAL USABLE OBSERVATIONS")
    console.print(f"observations:  {usable.get('observations', dataset.get('observations', 0)):,}")
    console.print(f"unique markets:{usable.get('unique_markets', 0):,}")
    console.print(f"events:        {usable.get('events', dataset.get('unique_events', 0)):,}")
    console.print(f"series:        {usable.get('series', 0):,}")
    console.print(f"categories:    {usable.get('categories', dataset.get('n_categories', 0)):,}")
    console.print(f"date range:    {usable.get('date_range', dataset.get('date_range', 'n/a'))}")
    years = usable.get("year_counts") or {}
    console.print(
        "years:         "
        + (", ".join(f"{y}:{n}" for y, n in years.items()) or "n/a")
    )
    console.print(
        f"All-horizon observations:    {dataset.get('all_horizon_observations', 0):,}"
    )
    console.print(f"Report horizon:              {dataset.get('report_horizon', 'n/a')}")

    overall = dataset.get("overall_summary") or {}
    min_obs = int(dataset.get("min_observations") or 100)
    n_primary = int(usable.get("observations") or dataset.get("observations") or 0)
    if overall:
        roi = overall.get("roi", 0) * 100
        event_roi = overall.get("event_weighted_roi", 0) * 100
        if n_primary < min_obs:
            console.print()
            console.print("Primary-horizon sample insufficient for performance inference")
            console.print(f"N={n_primary}, minimum required={min_obs}")
            console.print(f"Raw descriptive ROI: {roi:+.1f}%")
            console.print(f"Raw descriptive event-weighted ROI: {event_roi:+.1f}%")
        else:
            console.print(f"Market-weighted YES ROI:   {roi:+.1f}%")
            console.print(f"Event-weighted YES ROI:    {event_roi:+.1f}%")
    console.print()
    console.print("Execution model:            Taker / historical bid-ask close")
    console.print("Primary size:               1 contract")
    console.print(
        "Fees:                        Time-aware fee_changes; "
        "series metadata only if series has no fee_change history "
        "(source=series_metadata_no_fee_change_history)"
    )
    console.print()
    console.print(
        "[dim]Sensitivity sizes 10/100 use top-of-book price assumption; "
        "historical depth unavailable.[/dim]"
    )

    console.print()
    console.rule("EXECUTABLE ENTRY PRICE RESULTS — primary horizon subset")
    console.print(
        "[dim]Bucketed by executable YES ask. Not market implied probability.[/dim]"
    )
    _print_bucket_table(executable_bucket_rows, mode="executable")

    console.print()
    console.rule("MARKET CALIBRATION — midquote")
    console.print(
        "[dim]Belief proxy = (yes_bid + yes_ask) / 2. "
        "Wide-spread asks are not treated as probabilities.[/dim]"
    )
    _print_bucket_table(midquote_bucket_rows, mode="calibration")

    for block in tight_spread_calibrations:
        console.print()
        console.rule(
            f"MARKET CALIBRATION — midquote, spread <= {block['max_spread']:.2f}"
        )
        console.print(f"Observations in subset: {block.get('n', 0):,}")
        _print_bucket_table(block.get("buckets") or [], mode="calibration")

    console.print()
    console.rule("FAVORITE / LONGSHOT TEST")
    min_obs = int(dataset.get("min_observations") or 100)
    for key, label in (
        ("cheap_yes", "Cheap YES (<=20¢)"),
        ("expensive_yes", "Expensive YES (>=80¢)"),
        ("opposite_no_when_yes_cheap", "Opposite-side NO when YES cheap"),
        ("opposite_no_when_yes_expensive", "Opposite-side NO when YES expensive"),
    ):
        s = favorite_longshot.get(key) or {}
        n = int(s.get("n") or 0)
        descriptive = " [descriptive only]" if n < min_obs else ""
        console.print(
            f"{label}: N={n:,} events={s.get('unique_events', 0):,} "
            f"win={s.get('win_rate', 0)*100:.1f}% "
            f"avg_entry={s.get('avg_entry', 0):.3f} "
            f"gap={s.get('calibration_gap', 0)*100:+.1f}pp "
            f"ROI={s.get('roi', 0)*100:+.1f}% "
            f"event-wt ROI={s.get('event_weighted_roi', 0)*100:+.1f}% "
            f"[{s.get('sample_label')}]{descriptive}"
        )

    if h1_report:
        print_h1_report(
            h1_report,
            min_observations=int(dataset.get("min_observations") or 100),
        )

    console.print()
    console.rule("SPREAD-FILTERED TAKER PROFITABILITY (BUY YES)")
    sp = Table(show_header=True, header_style="bold")
    for col in ("Filter", "Obs", "Events", "ROI", "Event-wt ROI"):
        sp.add_column(col)
    for row in spread_profit_rows:
        sp.add_row(
            str(row.get("spread_filter")),
            f"{row.get('n', 0):,}",
            f"{row.get('unique_events', 0):,}",
            f"{row.get('roi', 0)*100:+.1f}%",
            f"{row.get('event_weighted_roi', 0)*100:+.1f}%",
        )
    console.print(sp)

    console.print()
    console.rule("CATEGORY RESULTS (BUY YES)")
    cat_table = Table(show_header=True, header_style="bold")
    for col in ("Category", "Obs", "Events", "Win%", "Net ROI", "Event-wt ROI", "Avg spread"):
        cat_table.add_column(col)
    for row in category_rows:
        cat_table.add_row(
            str(row["category"]),
            f"{row['observations']:,}",
            f"{row['unique_events']:,}",
            f"{row['win_rate']*100:.1f}%",
            f"{row['roi']*100:+.1f}%",
            f"{row.get('event_weighted_roi', 0)*100:+.1f}%",
            f"{row['avg_spread']:.3f}",
        )
    console.print(cat_table)

    console.print()
    console.rule("OUT-OF-SAMPLE RESULTS")
    if not period_bounds.get("oos_available"):
        console.print("OUT-OF-SAMPLE ANALYSIS NOT AVAILABLE")
        console.print(
            f"Reason: {period_bounds.get('oos_unavailable_reason') or 'insufficient temporal coverage'}"
        )
        years = period_bounds.get("years_present") or []
        console.print(
            f"Years present in sample: {', '.join(str(y) for y in years) or 'none'}"
        )
    else:
        console.print(
            "Boundaries: "
            f"Discovery <= {period_bounds.get('discovery_max_year')}; "
            f"Validation = {period_bounds.get('validation_year')}; "
            f"OOS >= {period_bounds.get('oos_min_year')}"
            + (" (adapted)" if period_bounds.get("adapted") else "")
        )
        oos_table = Table(show_header=True, header_style="bold")
        for col in ("Period", "N", "Events", "ROI", "Event-wt ROI", "95% ROI CI", "Sample"):
            oos_table.add_column(col)
        for row in period_rows:
            oos_table.add_row(
                row["period"],
                f"{row['n']:,}",
                f"{row['unique_events']:,}",
                f"{row['roi']*100:+.1f}%",
                f"{row.get('event_weighted_roi', 0)*100:+.1f}%",
                f"{row.get('roi_ci_low', 0)*100:+.1f}% to {row.get('roi_ci_high', 0)*100:+.1f}%",
                row.get("sample_label", ""),
            )
        console.print(oos_table)

    if strategy_rows:
        console.print()
        console.rule("STRATEGY SUMMARY (min sample enforced)")
        st = Table(show_header=True, header_style="bold")
        for col in ("Strategy", "N", "Events", "ROI", "Sample"):
            st.add_column(col)
        for row in strategy_rows:
            st.add_row(
                row["strategy"],
                f"{row['n']:,}",
                f"{row['unique_events']:,}",
                f"{row['roi']*100:+.1f}%",
                row["sample_label"],
            )
        console.print(st)

    if lifespan_rows is not None:
        print_lifespan_report(lifespan_rows)
    if horizon_coverage is not None:
        print_horizon_coverage(horizon_coverage)
    if extreme_rows is not None:
        print_extreme_quote_audit(extreme_rows)

    console.print()
    console.rule("LIQUIDITY / SPREAD DEFINITIONS")
    console.print(liquidity_meta.get("volume_definition", ""))
    console.print(liquidity_meta.get("spread_definition", ""))

    console.print()
    console.rule("RUN STATS")
    for key, value in run_stats.items():
        console.print(f"{key}: {value}")

    if dataset.get("observations", 0) == 0:
        console.print()
        console.print("[bold yellow]NO OBSERVATIONS PRODUCED[/bold yellow]")
    elif all(r.get("roi", 0) <= 0 for r in strategy_rows) if strategy_rows else False:
        console.print()
        console.print(
            "[bold]NO SYSTEMATIC PROFITABLE EDGE FOUND[/bold] "
            "(under primary 1-contract taker assumptions)"
        )


def write_csv(path: Path, rows: list[dict]) -> None:
    cache.ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _csv_value(v) for k, v in row.items()})


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return str(value)
    return value


def save_results(
    *,
    observations: list[dict],
    price_bucket_summary: list[dict],
    midquote_calibration: list[dict],
    category_summary_rows: list[dict],
    yearly_summary_rows: list[dict],
    strategy_summary_rows: list[dict],
    extreme_quote_rows: list[dict],
    horizon_coverage_rows: list[dict],
) -> Path:
    cache.ensure_dirs()
    write_csv(cache.RESULTS_ROOT / "observations.csv", observations)
    write_csv(cache.RESULTS_ROOT / "price_bucket_summary.csv", price_bucket_summary)
    write_csv(cache.RESULTS_ROOT / "midquote_calibration.csv", midquote_calibration)
    write_csv(cache.RESULTS_ROOT / "category_summary.csv", category_summary_rows)
    write_csv(cache.RESULTS_ROOT / "yearly_summary.csv", yearly_summary_rows)
    write_csv(cache.RESULTS_ROOT / "strategy_summary.csv", strategy_summary_rows)
    write_csv(cache.RESULTS_ROOT / "extreme_quote_audit.csv", extreme_quote_rows)
    write_csv(cache.RESULTS_ROOT / "horizon_coverage.csv", horizon_coverage_rows)
    return cache.RESULTS_ROOT

"""Offline fee coverage report using disk fee cache (no candle/ROI rerun)."""

from __future__ import annotations

import json

from kalshi import cache
from kalshi.client import KalshiClient
from kalshi.fee_index import FeeIndex
from research.candle_audit import summarize_unsupported_fees
from research.fee_audit import (
    fee_coverage_funnel_path,
    load_fee_audit_rows,
    load_observation_rows,
    missing_historical_report_path,
    reclassify_series_metadata_failures,
    reconcile_frozen_sample_horizons,
    replay_fee_coverage,
    summarize_missing_historical,
    write_csv,
)


def main() -> None:
    client = KalshiClient(progress=lambda _m: None)
    idx = FeeIndex(client=client, refresh=False, progress=lambda _m: None)
    audit = load_fee_audit_rows()
    obs = load_observation_rows()
    print("audit_rows", len(audit), "obs", len(obs))
    print("prior_rejection", dict(summarize_unsupported_fees(audit)["rejection_class"]))

    print("Reclassifying series_metadata failures...")
    causes = reclassify_series_metadata_failures(audit, idx)
    print("CAUSES", dict(causes))
    print("cause_total", sum(causes.values()))
    meta_series = {
        r.get("series_ticker")
        for r in audit
        if "incomplete series fee metadata" in str(r.get("message") or "").lower()
        or r.get("rejection_class")
        in ("series_metadata_failure", "unresolved_fee_metadata")
    }
    print("unique_series_metadata_failures", len(meta_series))

    print("Replaying coverage...")
    cov = replay_fee_coverage(
        fee_index=idx,
        audit_rows=audit,
        observation_rows=obs,
    )
    print("FUNNEL")
    for key, value in cov["funnel"].most_common():
        print(f"  {key}: {value}")
    print("recoverable", cov["recoverable_observations"])
    print("still_unresolved", cov["still_unresolved"])
    print("still_unsupported", cov["still_unsupported"])
    print("unique_markets_resolved", cov["unique_markets_resolved"])
    print("unique_markets_unresolved", cov["unique_markets_unresolved"])

    miss = summarize_missing_historical(cov["missing_historical_details"])
    print("MISSING_HIST_SERIES", len(miss))
    for row in miss:
        print(
            f"  {row['series']}: n={row['n_observations']} "
            f"entry=[{row.get('entry_ts_min')} .. {row.get('entry_ts_max')}] "
            f"changes={row.get('fee_change_row_count')} "
            f"earliest={row.get('earliest_known_fee_rule')}"
        )

    write_csv(
        missing_historical_report_path(),
        miss,
        (
            "series",
            "n_observations",
            "entry_ts_min",
            "entry_ts_max",
            "fee_change_history_available",
            "fee_change_row_count",
            "earliest_known_fee_rule",
            "why",
        ),
    )
    write_csv(
        fee_coverage_funnel_path(),
        [{"bucket": k, "observations": v} for k, v in cov["funnel"].most_common()],
        ("bucket", "observations"),
    )

    recon = reconcile_frozen_sample_horizons(selected_sample=5000)
    print("RECONCILE", recon["reconcile_ok"], recon["exclusion_breakdown"])
    print("pre_horizon_markets", recon.get("pre_horizon_markets"))

    ok = 0
    total = 0
    for path in (cache.CACHE_ROOT / "fees").glob("*.json"):
        if path.name == "fee_changes.json":
            continue
        total += 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("fee_change_http_status") == 200
            or payload.get("resolution_status") == "ok"
        ):
            ok += 1
    print("fee_changes_lookup_success", ok, "/", total)
    print("DONE")


if __name__ == "__main__":
    main()

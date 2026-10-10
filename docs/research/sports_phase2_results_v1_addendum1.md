# Sports Phase 2 (sports_phase2_v1): addendum 1, the k_kxttmatch exception

RESEARCH_ONLY / NO_BET. This addendum corrects one summary statement. It does not change any frozen artifact. `sports_phase2_results_v1.md`, its CSVs, journals, metrics and the `sports_phase2_v1` protocol stay as frozen (their hashes are pinned by `sports_phase2_v1.artifacts.json` and `sports_phase2_v1.docs_portable_v1.json`).

## Correction

The Phase 2 delivery summary said the frozen Phase 1 model was never classified better than the pre-cutoff Kalshi market in a sufficient comparison. That was not exact. The frozen results contain one exception:

| competition | family | cohort | comparison | stratum | clusters | d_brier (contract = event-equal) [95% CI] | d_log_loss |
|---|---|---|---|---|---|---|---|
| k_kxttmatch | game_winner | kalshi_settlement_only | frozen-market | phase2_sample | 117 | -0.0172 [-0.0267, -0.0075] | -0.0347 |

Differences are frozen - market, so a negative value means the frozen model had the lower loss. The preserved Phase 1 v1 sample for the same competition has 25 clusters (below the 50-cluster minimum). It is descriptive only (d_brier -0.0331) and is not classified.

## How to read it

- It is 1 "better" among 170 sufficient competition-level frozen-market comparisons (phase2_sample: strict 46, exploratory 32, Kalshi-settlement-only 69; phase1_v1_sample: strict 21, exploratory 2). The rest are 95 unclear and 74 worse.
- The classes come from per-comparison 95% cluster-bootstrap intervals and are exploratory and unadjusted for multiple comparisons. At 170 comparisons, a handful of intervals excluding zero in either direction are expected by chance. This single result should not be read as evidence of an edge.
- The cohort is Kalshi-settlement-only (labels from Kalshi settlement; no independent source reconciliation). 2026 had already been examined in Phase 1, so the comparison is retrospective.
- The market reference is a sparse hourly candle mid, not an executable price. Nothing here supports trading or profitability claims.

## Unchanged conclusions

- frozen - market: 1 better / 95 unclear / 74 worse over all sufficient competition-level groups. No other group showed the frozen model clearly beating the market reference.
- calibrated - frozen: 0 better / 3 unclear / 2 worse.
- blend - frozen: 4 better (both samples), which reflects the weight the blend puts on the market. blend - market: 0 better / 2 unclear / 2 worse.
- Calibration maps exist for 5 groups and blend maps for 3 (all `ncaaf`). No thresholds were relaxed and there was no new historical tuning.

# Sports Phase 1: sports_phase1_v1 → sports_phase1_v2 comparison

RESEARCH_ONLY / NO_BET. The 2026 test period was already examined in sports_phase1_v1; v2 is a retrospective evaluation correction, not a newly untouched holdout. Forecasts are unchanged (journals identical apart from the protocol field); differences come only from the evaluation corrections below. Interval-based better/unclear/worse classes are exploratory and unadjusted for multiple comparisons. Beating naive forecasts and beating market reference forecasts are reported separately. RESEARCH_ONLY / NO_BET: no profitability or executable-edge claims.

## What changed

1. Reconciliation: sports_phase1_v1 admitted series with fewer than 30 pre-test contracts using all settled contracts, including the test period. v2 admits only pre-test-reconciled series to the strict cohort; the others are a separately labelled exploratory cohort.
2. Market benchmark: the sports_phase1_v1 sample is preserved exactly, but a comparison needs at least 50 unique matched clusters after all quote filters, measured on strict-cohort clusters with strict-only losses and CIs. Kalshi-settlement-only comparisons are descriptive.
3. Weighting: event-equal metrics are reported alongside contract-weighted ones.
4. Interpretation: classes are labelled exploratory and unadjusted; naive and market comparisons are separate.

Prediction journals identical to sports_phase1_v1: 313 of 313 evaluated competitions.

## Headline vs naive (contract-weighted classes, competition × family)

| Family | sports_phase1_v1 independent (b/u/w) | v2 strict (b/u/w) | v2 exploratory (b/u/w) |
|---|---|---|---|
| field_finish | 2 / 0 / 0 | 1 / 1 / 0 | 2 / 0 / 0 |
| game_winner | 16 / 7 / 0 | 15 / 6 / 0 | 1 / 1 / 0 |
| score_props | 2 / 14 / 0 | 0 / 5 / 0 | 2 / 9 / 0 |
| segment | 6 / 11 / 1 | 0 / 0 / 0 | 7 / 10 / 1 |
| spread | 11 / 11 / 0 | 6 / 6 / 0 | 5 / 5 / 0 |
| team_total | 3 / 8 / 0 | 0 / 1 / 0 | 3 / 7 / 0 |
| total | 5 / 17 / 0 | 3 / 9 / 0 | 2 / 8 / 0 |

| Family | sports_phase1_v1 Kalshi-only (b/u/w) | v2 Kalshi-settlement-only (b/u/w) |
|---|---|---|
| game_winner | 48 / 24 / 0 | 48 / 24 / 0 |

Event-equal weighting changes the class of 4 classified competition × family × cohort groups (see the weighting table in the results document).

## Market benchmark

* sports_phase1_v1: 93 competitions classified regardless of matched size, mixed cohorts: model better 1, unclear 65, Kalshi better 27.
* sports_phase1_v2: 21 strict comparisons with ≥ 50 matched clusters: model better 0, unclear 16, Kalshi better 5. 72 Kalshi-settlement-only comparisons are descriptive (frozen 40-game cap); 2 independent-source comparisons are INSUFFICIENT on strict clusters.
* 72 sports_phase1_v1 comparisons had fewer than 50 matched events; `k_kxttmatch` had 25.

## Coverage status changes (all 3,871 series)

| sports_phase1_v1 status | sports_phase1_v2 status | Series |
|---|---|---|
| evaluated | exploratory_evaluated | 225 |
| evaluated | kalshi_settlement_only_evaluated | 131 |
| evaluated | strict_evaluated | 55 |
| excluded | excluded_no_reconciled_contracts | 4 |
| excluded | excluded_spec | 251 |
| excluded | exploratory_evaluated | 13 |
| no test-period contracts | no_test_contracts | 154 |
| not attempted | not_attempted_supported_sport | 2915 |
| not attempted | not_attempted_unsupported_sport | 123 |


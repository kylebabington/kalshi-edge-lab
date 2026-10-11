# First finalized NCAAF scoring verification (2026-10-11T12:19Z)

RESEARCH_ONLY / NO_BET. This is a verification of the existing scoring pipeline (`sports_capture_v2_scoring_v1`) on the first settled NCAAF window captures. No models, protocols, capture code, records or evidence were changed. Results are an operational pilot and descriptive only. Many strikes and segments come from one game, so contracts are not independent observations; the event counts are the relevant sample size.

## Pipeline runs

- **`settle`:** 475 newly finalized scores (137 yes, 338 no) plus 503 `already_finalized`, so 978 finalized in total.
  - 2 contracts are still PENDING: one Liiga game (`k_kxliigagame`), market status `active`. They remain pending.
- **`verify-scores`:** 978 OK, 0 refused.
- **NCAAF:** all 461 contracts of the 10 captured games (run 20261010T154238574921Z) are final. Raw market fields: 130 `finalized / yes / 1.0000` and 331 `finalized / no / 0.0000`. There were no void, nonbinary, inconsistent or duplicate states.

## Independent checks on the 461 NCAAF scores

The checks were done with a separate script that computes metrics with its own formulas and does not import the evaluator. It found no problems apart from the floating-point note on identity calibration below.

- **Hashes:**
  - every `score_sha256` recomputed;
  - each source record passed `check_record`, and its path and `record_sha256` match the score;
  - all 10 source records passed full verification: pins, prediction dependencies, raw prediction evidence and timing;
  - every score's candidates equal the source record's contract candidates exactly;
  - the settlement evidence bytes re-hash, and each label matches the raw `finalized` result.
- **Metrics:** Brier \((p - y)^2\) on the original p and log loss on \(\mathrm{clip}(p, 10^{-4})\) were recomputed per candidate and per pair, overall and by family, in contract-weighted and event-equal views. All 36 report rows match, with identical contract and event counts and values within 1e-12.
- **Identity calibration (game winner, a 0, b 1):** calibrated equals frozen on 20 of 20 contracts to machine precision. 17 are bit-identical and 3 differ by one unit in the last place (at most 5.6e-17), because the identity map is applied numerically as sigmoid(0 + 1 * logit(p)). Brier differences are at most 1.1e-16, and the calibrated - frozen difference is 0.0000.
- **Total blends (w 0, c 0):** all 10 market probabilities are inside the clip range. Blend equals market on 10 of 10 (to 1e-12), and the Brier and log-loss scores are identical (largest difference 5.6e-17). blend - market = +0.0000.
- **Spread blends (w 0, c -0.1032):** not expected to equal the market, and none did: 0 of 20. blend - market probability ranged from -0.0256 to -0.0064.
- **Common contracts:** for every pair, overall and in every family, the contract count used by the report equals the intersection of labelled tickers with a probability for both candidates, and the paired values match the independent recomputation.
- **Idempotency:** re-running `settle` left all 978 finalized score files byte-identical (0 changed, 0 new).

## NCAAF by family (labelled contracts / unique events)

| family | frozen | calibrated | market | blend |
|---|---|---|---|---|
| game_winner | 20 / 10 | 20 / 10 | 10 / 10 | 10 / 10 |
| spread | 167 / 10 | 167 / 10 | 20 / 10 | 20 / 10 |
| total | 126 / 10 | 126 / 10 | 10 / 10 | 10 / 10 |
| team_total | 48 / 2 | - | 4 / 2 | - |
| segment | 100 / 10 | - | - | - |

Descriptive Brier (contract-weighted / event-equal):

| family | frozen | calibrated | market | blend |
|---|---|---|---|---|
| game_winner | 0.2111 / 0.2111 | 0.2111 / 0.2111 | 0.1990 / 0.1990 | 0.1983 / 0.1983 |
| spread | 0.1637 / 0.1620 | 0.1663 / 0.1631 | 0.1746 / 0.1746 | 0.1753 / 0.1753 |
| total | 0.2284 / 0.2280 | 0.2184 / 0.2195 | 0.2210 / 0.2210 | 0.2210 / 0.2210 |
| team_total | 0.2010 / 0.2010 | - | 0.2216 / 0.2216 | - |
| segment | 0.2190 / 0.1847 | - | - | - |

Paired Brier differences on common contracts (contracts / events; contract-weighted, event-equal in parentheses):

| family | frozen - market | calibrated - frozen | blend - frozen | blend - market |
|---|---|---|---|---|
| game_winner | 10 / 10: +0.0121 | 20 / 10: -0.0000 | 10 / 10: -0.0127 | 10 / 10: -0.0007 |
| spread | 20 / 10: -0.0107 | 167 / 10: +0.0026 (+0.0012) | 20 / 10: +0.0114 | 20 / 10: +0.0007 |
| total | 10 / 10: +0.0387 | 126 / 10: -0.0100 (-0.0085) | 10 / 10: -0.0387 | 10 / 10: +0.0000 |
| team_total | 4 / 2: +0.0251 | - | - | - |

With 10 games (2 for team totals), none of these differences supports a conclusion. They are reported only to show that the pipeline handles all four candidates.

## All captured competitions (pilot CSV regenerated)

There are 978 labelled contracts in 43 events; one more event is pending.

| candidate or pair | contracts / events | Brier (event-equal) |
|---|---|---|
| frozen | 978 / 43 | 0.1830 (0.1925) |
| calibrated | 313 / 10 | 0.1901 (0.1894) |
| market | 156 / 43 | 0.1860 (0.1893) |
| blend | 40 / 10 | 0.1925 (0.1925) |
| frozen - market | 156 / 43 | +0.0082 (+0.0123) |

Calibrated and blend exist only for NCAAF. Per-group values are in `sports_capture_v2_pilot_scores.csv`.

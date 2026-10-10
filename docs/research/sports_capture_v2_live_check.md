# `sports_phase2_capture_v2`: manual live check (2026-10-10)

RESEARCH_ONLY / NO_BET. Two all-competition window runs were made right after the protocol was frozen (2026-10-10T12:27:49Z), followed by one NCAAF run for the fitted-candidate check. Records and raw evidence are local (`data/sports/phase2/capture_v2/`, `data/cache/sports/raw_capture_v2/`) and not committed. Settlement scoring is in the last section and in `sports_capture_v2_scoring.md`.

## Runs

| run id | started (UTC) | discovery | WINDOW_CAPTURE | MISSED | DUPLICATE_ATTEMPT | other per-cluster statuses |
|---|---|---|---|---|---|---|
| 20261010T122851133298Z | 12:28:51 | 7,311 milestones, 403 events, 61 competitions | 8 | 17 | 0 | started_not_recorded 39, window_not_open 26, no_linked_contracts 94 |
| 20261010T124155035259Z | 12:41:55 | 7,311 milestones, 409 events, 61 competitions | 26 | 0 | 3 | accepted_exists 22, started_not_recorded 35, window_not_open 1, no_linked_contracts 95 |

- Run problems (pin, pagination or context failures): 0 in both runs. No FAILED_ATTEMPT records.
- The 17 MISSED records are events whose window had already expired, with no accepted capture, when the first run started (the collector was not running earlier). They were not backfilled.
- The 3 DUPLICATE_ATTEMPT records are events accepted in run 1 that were still inside their window during run 2. The accepted records were not replaced.
- `no_linked_contracts` counts discovered Kalshi events for which the adapter linked no eligible contract (for example, events outside the supported families or shapes, or with unmapped participants). Per-event reasons are in the run summaries.

## Accepted window captures (34)

- espn_team: bundesliga 5, saudi 1, eflc 7, epl 4.
- kalshi_only: k_kxatpchallengerdoubles 2, k_kxatpchallengermatch 2, k_kxlklgame 1, k_kxbblgame 1, k_kxdartsmatch 1, k_kxkhlgame 2, k_kxkorisliigagame 1, k_kxliigagame 5, k_kxpremmatch 1, k_kxttstarmatch 1.
- Actual horizons were between 60.07 and 79.17 minutes, and every record satisfied `window_open <= prediction_as_of <= finalized_at <= scheduled_cutoff`.
- Candidates: the frozen candidate was available for every contract. The market candidate was available for the game-winner contracts with a qualifying candle. Calibrated and blend were unavailable in all captured groups because no fitted map exists for them (the only blend maps are `ncaaf`; calibration also exists for `k_kxatpmatch` and `k_kxwtamatch`). No NCAAF, ATP-main or WTA-main event was inside its window during either run (one `k_kxatpmatch` event was MISSED).

Example (bundesliga espn:401884773, cutoff 12:30:00Z, `prediction_as_of` 12:29:40.83Z, finalized 12:29:41.10Z; 1,261 results used, 33 future events hidden, latest release used 2026-10-09T21:30Z), contract `KXBUNDESLIGAGAME-26OCT10PADVFB-PAD`:

| candidate | value |
|---|---|
| frozen | 0.3781 (score model; Phase 1 v2 primary for soccer game winners, not Elo) |
| calibrated | unavailable: training support below threshold |
| market | 0.195 (candle mid) |
| blend | unavailable: matched training support below threshold |
| diagnostics | yes bid 0.19 / ask 0.20 (listing snapshot, not a candidate) |

## Offline replay

`python -m research.sports.live.run replay --all` covered 54 records after the first two runs (34 WINDOW_CAPTURE, 17 MISSED, 3 DUPLICATE_ATTEMPT): 54 OK, 0 refused or mismatched. After the NCAAF run it covered 85 records: 85 OK. Every candidate probability was reproduced from saved evidence bytes only, after checking the record checksum, the global and dependency pins, and the evidence hashes.

## NCAAF fitted-candidate check

The run (`capture --mode window --competitions ncaaf`, run id 20261010T154238574921Z) started at 15:42:38Z, after the 14:40Z and 15:10Z windows had expired. It wrote 21 MISSED records for those games (none was captured or backfilled) and accepted 10 WINDOW_CAPTURE records for kickoffs at 16:45 and 17:00Z. Horizons were 74.09 to 74.47 min. No problems or failed attempts occurred.

`python -m research.sports.scoring.run fitted-check --competitions ncaaf` verified all 10 records: checksum, pins, dependencies, raw evidence hashes and timing, then offline replay OK. It found no problems.

| family | contracts | calibrated available | pinned calibration | calibrated = frozen | blend available | pinned blend | zero model weight | zero intercept | blend = market |
|---|---|---|---|---|---|---|---|---|---|
| game_winner | 20 | 20 | identity (a 0, b 1) | 20 / 20 (identity map) | 10 | w 0.3, c 0 | 0 | 10 | 0 / 10 |
| spread | 167 | 167 | intercept (a 0.1597) | 0 / 167 | 20 | w 0, c -0.1032 | 20 | 0 | 0 / 20 |
| total | 126 | 126 | platt (a -0.1150, b 0.9043) | 0 / 126 | 10 | w 0, c 0 | 10 | 10 | 10 / 10 |
| team_total | 48 | 0 (no map) | - | - | 0 | - | - | - | - |
| segment | 100 | 0 (no map) | - | - | 0 (no market by scope) | - | - | - | - |

- Every calibrated and blend value equals the pinned `sports_phase2_v1` map applied to the recorded frozen and market probabilities exactly.
- Game-winner calibration is the identity, so calibrated legitimately equals frozen.
- Spread has zero model weight but a nonzero intercept: blend = sigmoid(-0.1032 + logit(market)), which is not the market.
- Total has zero weight and zero intercept, so blend equals the market (inside the 1e-4 clip range).
- Every market candidate came from the latest hourly candle ended by `prediction_as_of`, the 15:00Z candle, aged about 46 min (under the 3 h limit), with a two-sided book; p is the bid/ask mid.

Example (Rice at East Carolina, cutoff 16:00Z, `prediction_as_of` 15:45:31.98Z, candle ended 15:00Z):

| contract | frozen | calibrated | market | blend |
|---|---|---|---|---|
| KXNCAAFGAME-...-ECU | 0.8479 | 0.8479 (identity) | 0.775 | 0.7992 (w 0.3) |
| KXNCAAFSPREAD-...-ECU12 | 0.6014 | 0.6390 | 0.465 | 0.4394 (w 0, c -0.1032) |
| KXNCAAFTOTAL-...-52 | 0.4576 | 0.4332 | 0.385 | 0.385 (w 0, c 0) |

## Settlement scoring (as of 2026-10-10T15:51Z)

- **Coverage:** 44 eligible WINDOW_CAPTURE records (44 events, 980 contracts). Excluded: 38 MISSED, 3 DUPLICATE_ATTEMPT.
- **Settled so far:** 212 contracts were confirmed final (86 yes, 126 no) in 11 events: 9 soccer, 1 tennis, 1 table tennis. There were no void, nonbinary, inconsistent or failed lookups.
- **Pending:** 768 contracts were not final in the latest run, including every NCAAF contract (games still in progress).
- **Idempotency on real data:** re-running `settle` left all 209 earlier scores byte-identical and added 3 newly settled ones. `verify-scores`: all OK.
- **No calibrated or blend probabilities are scored yet.** The settled events have no fitted maps, and the NCAAF games are not settled. Missing inputs among labelled contracts:
  - calibrated: 212 "training support below threshold";
  - market: 106 segment (out of scope), 6 score props (out of scope), 63 not selected by the per-event contract rule;
  - blend: those same 112 out-of-scope contracts, plus 100 "matched support below threshold".
- **Descriptive point estimates** (operational pilot, not representative, no classification):
  - frozen: 212 contracts and 11 events, Brier 0.1747 (event-equal 0.1967).
  - frozen - market on 37 common contracts in 7 events: Brier +0.0110 (event-equal +0.0416) and log loss +0.0366 (event-equal +0.0978), i.e. the market was more accurate on these contracts.
  - Groups by sport, competition, family and cohort are in `sports_capture_v2_pilot_scores.csv`.

Re-run `settle` and then `report` after the NCAAF games settle (tonight UTC) to score the calibrated and blend candidates.

## First NCAAF scoring check (2026-10-10T16:51Z): pending

`settle`, `verify-scores` and `report --no-write` were run with the existing pipeline. Nothing in the code, models, protocols or records was changed.

- **NCAAF:** all 461 contracts in the 10 captured games are PENDING. The saved settlement evidence shows market status `active` with an empty result for every one. No NCAAF score is finalized, so the check stopped here: no waiting, no assigned outcomes, no backfill. The calibrated and blend verification (identity game winners, zero-weight zero-intercept totals, nonzero-intercept spreads, and the common-contract sets) remains pending until these markets reach a confirmed final status.
- **Other competitions:**
  - 503 contracts finalized (192 yes, 311 no) in 26 events, with 16 more contracts pending;
  - `verify-scores`: 503 OK, 0 refused;
  - a repeat `settle` left all 503 finalized scores byte-identical (0 changed, 0 new).
- **Labelled contracts and unique events by family** (frozen candidate; market in parentheses):

| family | contracts | events |
|---|---|---|
| game_winner | 48 (19) | 19 (19) |
| spread | 68 (34) | 17 (17) |
| total | 102 (34) | 17 (17) |
| team_total | 46 (18) | 9 (9) |
| segment | 222 (-) | 17 (-) |
| score_props | 17 (-) | 17 (-) |

- **frozen - market on the 105 common contracts in 26 events:** Brier +0.0111 (event-equal +0.0271) and log loss +0.0288 (event-equal +0.0623). Descriptive only. Many strikes and segments come from the same game, so contract counts overstate the number of independent observations; the event counts are the relevant sample size.
- **Not yet scored:** no labelled contract has a calibrated or blend probability (none of the settled competitions has a fitted map).

`sports_capture_v2_pilot_scores.csv` was regenerated from these 503 scores.

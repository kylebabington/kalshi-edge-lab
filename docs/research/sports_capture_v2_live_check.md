# `sports_phase2_capture_v2`: manual live check (2026-10-10)

RESEARCH_ONLY / NO_BET. These two manual window runs were made right after the protocol was frozen (2026-10-10T12:27:49Z). Records and raw evidence are local (`data/sports/phase2/capture_v2/`, `data/cache/sports/raw_capture_v2/`) and not committed. No outcomes are evaluated here.

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

`python -m research.sports.live.run replay --all` covered 54 records (34 WINDOW_CAPTURE, 17 MISSED, 3 DUPLICATE_ATTEMPT): 54 OK, 0 refused or mismatched. Every candidate probability was reproduced from saved evidence bytes only, after checking the record checksum, the global and dependency pins, and the evidence hashes.

## Still pending

A live window with a fitted calibration or blend map (NCAAF, ATP or WTA main tour) has not been captured. To capture one, run `capture --mode window --competitions ncaaf` during [start - 80 min, start - 60 min] of an NCAAF game. On 2026-10-10 the first such window opens at 14:40Z.

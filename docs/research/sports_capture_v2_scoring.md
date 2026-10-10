# Settlement scoring of `sports_phase2_capture_v2` captures (`sports_capture_v2_scoring_v1`)

RESEARCH_ONLY / NO_BET. `research/sports/scoring/` scores accepted window captures against confirmed Kalshi settlements. The capture package, its frozen protocol, its records and its raw evidence are only read, never changed. There are no new models, no tuning, no thresholds and no scheduled jobs.

**Operational pilot.** Captures are manual and availability-based (whatever was inside its window when the collector was run). Results are descriptive only. They are not a representative sample and not an independent confirmation of the historical Phase 1 / Phase 2 findings.

## What is scored

- Only records in `accepted/checkpoint/` with status `WINDOW_CAPTURE` (mode `window`).
- EARLY_SNAPSHOT, MISSED, DUPLICATE_ATTEMPT and FAILED_ATTEMPT records never enter scoring or comparisons. They are counted as exclusions.
- Each contract uses its original ticker and the candidate probabilities copied verbatim from the record. An unavailable candidate stays unavailable, with its original reason.

## Verification before the first score of a record

1. The record checksum (`record_sha256`).
2. The pins: Phase 1, Phase 2 and live code hashes, plus the capture protocol bytes.
3. The prediction dependencies (normalized history, crosswalks, inventory), re-hashed from disk.
4. The raw prediction evidence bytes, re-hashed.
5. Timing: `window_open <= prediction_as_of <= finalized_at <= scheduled_cutoff`.
6. Every candidate is either a finite probability in [0, 1] or an explicit unavailability reason. Invalid probabilities refuse the record; they are not corrected.

Any failure gives `SOURCE_REFUSED` for that run and no score.

## Settlement states

Settlements come from completely paginated `GET /markets?event_ticker=...` listings. Raw bytes, URL, params, `requested_at`/`received_at` and sha256 are saved under `data/cache/sports/raw_settlement_v1/<run_id>/`. The settlement evidence hashes are checked again before a score is written.

| state | rule | final | label |
|---|---|---|---|
| SETTLED_YES | status `finalized`/`settled`, result `yes`, settlement value 1 | yes | 1 |
| SETTLED_NO | status `finalized`/`settled`, result `no`, settlement value 0 | yes | 0 |
| NONBINARY | final status, result `scalar` (fair-price value recorded verbatim) | yes | none |
| VOID_OR_CANCELLED | final status, result void / cancelled | yes | none |
| PENDING | any other status (`active`, `closed`, `determined`, `disputed`, ...), even if a result is shown | no | - |
| INCONSISTENT | final status with no result, a yes/no result whose value is missing or does not match, a value outside [0, 1], or an unknown result | no | none |
| CONFLICTING_DUPLICATE | the ticker appears more than once with different settlement fields | no | none |
| NOT_FOUND | ticker absent from a complete listing | no | - |
| LOOKUP_FAILED | network error, non-200 response or incomplete pagination | no | - |

Labels are never inferred from game scores or expected settlement times. Non-final states are run diagnostics only (`runs/<run_id>.json`) and are retried by later runs.

## Write-once scores

`data/sports/phase2/capture_v2_scores/finalized/<competition>/<cluster>/<ticker>.json` is created exclusively. It contains:
- the source record path and `record_sha256`;
- the candidates verbatim;
- the settlement state and the raw market fields;
- the label (binary states only);
- the settlement evidence refs;
- `score_sha256`.

On later runs an existing score is never rewritten. Its checksum and source identity (path and record hash) are checked before it is counted as `already_finalized`. Otherwise the run reports `SCORE_REFUSED`. `verify-scores` also re-hashes the settlement evidence bytes.

## Metrics

These are the frozen Phase 1 evaluator formulas (`evaluate._losses` / `metrics`):
- Brier: \((p - y)^2\) on the original probability.
- Log loss: computed on \(\mathrm{clip}(p, 10^{-4})\). Clipping applies only here.
- Event key: `competition|cluster` (globally unique). All contracts of one event form one cluster.
- Views: contract-weighted, and event-equal (the mean of per-event means).

Paired differences (frozen-market, calibrated-frozen, blend-frozen, blend-market) use only contracts where both candidates have a probability. They are reported as point estimates by sport, competition, family and cohort, without classification.

## Commands

Run from the repo root (`.venv\Scripts\python.exe`):

```text
python -m research.sports.scoring.run settle                       # fetch settlements, finalize scores once
python -m research.sports.scoring.run verify-scores                # checksums, source identity, settlement evidence
python -m research.sports.scoring.run report                       # writes docs/research/sports_capture_v2_pilot_scores.csv
python -m research.sports.scoring.run report --no-write            # print only
python -m research.sports.scoring.run fitted-check --competitions ncaaf
```

`settle` may be repeated at any time: finalized scores stay byte-identical, and pending contracts are retried.

## Fitted-candidate check

`fitted-check` verifies each record fully (as above, plus offline replay) and then checks each contract:
- **Calibrated:** must equal `apply_calibration(pinned params, frozen)` exactly. An identity map (a = 0, b = 1) is reported because it legitimately equals the frozen candidate.
- **Blend:** must equal `apply_blend(pinned params, frozen, market)` exactly. A model weight of w = 0 gives sigmoid(c + logit(clip(market))). That equals the market only when the intercept c = 0 as well, and only inside the 1e-4 clip range. Zero weight and zero intercept are reported separately.
- **Market:** the latest candle must have ended at or before `prediction_as_of`, with age = `int(prediction_as_of)` - candle end, at most 3 h. The book must be two-sided, and p must be the bid/ask mid.

Pinned NCAAF maps (`sports_phase2_v1`):

| family | calibration | blend |
|---|---|---|
| game_winner | identity (a 0, b 1) | w 0.3, c 0 |
| spread | intercept (a 0.1597, b 1) | w 0, c -0.1032 (not equal to market) |
| total | platt (a -0.1150, b 0.9043) | w 0, c 0 (equals market inside the clip range) |

## Live results

See `sports_capture_v2_live_check.md` for the NCAAF fitted check and the current settlement coverage. The CSV `sports_capture_v2_pilot_scores.csv` holds the per-group metrics.

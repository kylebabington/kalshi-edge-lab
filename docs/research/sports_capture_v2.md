# Sports prospective capture: `sports_phase2_capture_v2`

RESEARCH_ONLY / NO_BET. This is a manual collector that records the actual `sports_phase2_v1` candidates for upcoming Kalshi sports events before they start. It does not place orders, schedule jobs, refit models, tune thresholds or use paid services. Weather code, data and scheduling are untouched.

## What changed and what is preserved

- New package `research/sports/live/` and frozen protocol `research/sports/protocols/sports_phase2_capture_v2.json`.
- It supersedes `research/sports/phase2/capture.py`, which recorded Elo-only forecasts. That file is unchanged and still covered by the `sports_phase2_v1` code hash. Its records, if any, are not comparable with v2 records.
- `sports_phase1_v1`, `sports_phase1_v2` and `sports_phase2_v1` code, protocols, journals, metrics and reports are unchanged. Reproduction of the historical results is at commit `70835fb` and still passes from this commit (`verify` stages below).
- The conclusion correction for `sports_phase2_v1` (the k_kxttmatch exception) is in `sports_phase2_results_v1_addendum1.md`. The frozen report itself is not edited.

## Candidates per contract

Every recorded contract carries four candidates, each with a probability or an explicit `unavailable` reason. Elo is never substituted silently.

| candidate | definition |
|---|---|
| frozen | Phase 1 v2 primary model (`primary_row`: Elo for game winners when it applies, otherwise the score/field model) with frozen `sports_phase1_v2` parameters |
| calibrated | frozen probability through the fitted `sports_phase2_v1` calibration map for that competition and family |
| market | Kalshi mid from the latest ended hourly candle at or before `prediction_as_of`, under the frozen 3 h age and two-sided rules |
| blend | fitted `sports_phase2_v1` blend map applied to frozen and market |

Current bid/ask from the market listing are saved under `diagnostics` only and never used as a candidate or blend input.

## Timing

- `scheduled_cutoff` = source start - 60 min. The capture window is [cutoff - 20 min, cutoff].
- `prediction_as_of` is read from the clock immediately after the last evidence response (the candles) was received. It is stored separately from the cutoff, together with the actual horizon (start - `prediction_as_of`). Records are labelled `WINDOW_CAPTURE`, not exact T-60 observations.
- Every evidence retrieval and every assumed result release (frozen Phase 1 release rule) is at or before `prediction_as_of`. Ratings are never advanced to a future cutoff.
- As-of view: events released after `prediction_as_of` are kept for schedule and pregame fields, but every outcome field is cleared: team scores, winners, period scores, `completed` and extra-time flags, fight winners and methods (including the winner flag inside fighter records), golf positions, `finished` and `won` flags, and Kalshi-only winners. Tests mutate those fields on future events and confirm that predictions stay identical.
- Window mode requires cutoff - 20 min <= `prediction_as_of` <= `finalized_at` <= cutoff and never writes early snapshots. Early mode requires `finalized_at` < window open and never writes checkpoint records or MISSED. Early snapshots live in their own directory and are never backfilled into checkpoints.

## Attempts, acceptance and idempotency

- `accepted/checkpoint/<comp>/<cluster>.json` and `accepted/early/<comp>/<cluster>.json` are created exclusively. The first attempt that satisfies every recording rule is accepted, whatever its probabilities, and it is never replaced.
- Network errors, non-200 responses, incomplete pagination, pin failures, build failures and late finalization produce `FAILED_ATTEMPT` records under `attempts/<run_id>/`. They never occupy the accepted slot, so another attempt can run inside the window.
- Attempts after acceptance produce `DUPLICATE_ATTEMPT` records.
- `MISSED` is written to the checkpoint slot only in window mode, after the cutoff, when no accepted capture exists (the event has not started, or earlier attempts failed).
- Each record has a `record_sha256` over its canonical JSON.

Paths: records are under `data/sports/phase2/capture_v2/`, raw evidence bytes under `data/cache/sports/raw_capture_v2/<run_id>/`. Both are local and not committed.

## Pins and replay

Before writing, a run checks:
- the capture and parent protocol bytes;
- the Phase 1, Phase 2 and live code hashes;
- the dependencies of each competition: normalized `events.jsonl` and `contracts.jsonl`, the participant crosswalk, and `docs/research/sports_inventory_v1.csv`.

Structured-target mappings are not used by the live predictors and are not pinned. A mismatch writes a `FAILED_ATTEMPT` diagnostic and no accepted record.

`replay` refuses unless the record checksum, every global and dependency pin (re-hashed from disk, so a changed crosswalk refuses) and every evidence body hash match. It then recomputes everything offline from the saved bytes only (requests that were not recorded are refused) and compares contracts, market picks, the as-of summary and all four candidates exactly.

## Adapters

| source | status | comparability | notes |
|---|---|---|---|
| espn_team | supported | exact | Kalshi milestone -> frozen crosswalk -> ESPN game (Phase 1 matching rule) |
| espn_fight | supported | exact | fighters -> frozen crosswalk -> ESPN fight within 36 h |
| espn_golf | supported | field_assumption_differs | prediction-time listed field; Phase 1 used the field in the source result. Not exactly comparable. ESPN usually lists only the current event's field; if none is listed by `prediction_as_of` the frozen candidate is unavailable and is never reconstructed from results |
| kalshi_only | supported | exact | two-market two-participant events; history adds settled Kalshi markets (available at settlement) |
| jolpica (F1) | unsupported | - | the frozen field model needs the entrant list, and Jolpica publishes it only with results |

Crosswalks are read-only. An unmapped participant makes that contract's frozen candidate unavailable.

## Availability matrix (offline)

`run matrix` writes `sports_capture_v2_matrix.csv` (competition x family) and `sports_capture_v2_series_matrix.csv` (all 3,871 inventory series):

- 313 competitions: 285 kalshi_only, 25 espn_team, 1 espn_fight, 1 espn_golf, 1 jolpica (unsupported).
- 416 competition x family groups. The frozen candidate is available in 414, calibrated in 5, market in 373 and blend in 3.
- Fitted maps: ncaaf game_winner, spread and total have calibration and blend maps. k_kxatpmatch and k_kxwtamatch game_winner have calibration only (blend support below threshold).
- Series: 574 supported, 259 unsupported (mostly no payoff parser, e.g. SCORE, FTTS, 1HSCORE, 2HBTTS), 3,038 unattempted (never in the Phase 1 scope).

Live availability per event also depends on market listings, crosswalk coverage and candle presence, and is recorded per contract.

## Commands

Run all commands from the repo root with the project venv (`.venv\Scripts\python.exe`):

```text
python -m research.sports.live.run matrix                    # offline availability matrix (add --no-write to only print)
python -m research.sports.live.run verify                    # pins check against the frozen capture protocol
python -m research.sports.live.run upcoming --hours 4        # list upcoming windows (milestones only)
python -m research.sports.live.run capture --mode window [--competitions ncaaf,epl]
python -m research.sports.live.run capture --mode early --lookahead-hours 6
python -m research.sports.live.run replay --all              # or --record <path>
```

A window run discovers events whose milestone start is within [now - 2 h, now + 1.75 h]. It captures those whose window is open, writes MISSED for expired ones, and skips the rest. To capture an event, run it during [start - 80 min, start - 60 min]. Network use is serial with 0.5 s spacing.

Preservation checks:

```text
python -m research.sports.run verify --protocol sports_phase1_v1
python -m research.sports.run verify --protocol sports_phase1_v2
python -m research.sports.phase2.run verify
python -m pytest
```

## Live verification

See `sports_capture_v2_live_check.md` for the manual window runs made at freeze time and their offline replay.

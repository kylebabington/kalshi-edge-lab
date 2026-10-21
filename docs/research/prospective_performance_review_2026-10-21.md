# Prospective performance review — Weather Phase 7 and Sports capture v2

**As of 2026-10-21T20:38:39.935499Z.** RESEARCH_ONLY / NO_BET. No prices, trades, promotion, early stopping, or profitability claims.

Weather Phase 7 and Sports capture v2 are separate studies. Numbers below come from a one-shot filtered snapshot of saved records and locally written scores. Collectors, models, protocols, and scoring code were not modified.

Companions: [`prospective_performance_review_2026-10-21_manifest.json`](prospective_performance_review_2026-10-21_manifest.json), [`_weather.json`](prospective_performance_review_2026-10-21_weather.json), [`_sports.csv`](prospective_performance_review_2026-10-21_sports.csv), [`_inputs.tsv.gz`](prospective_performance_review_2026-10-21_inputs.tsv.gz).

## Cutoff and filtering

`T = 2026-10-21T20:38:39.935499Z` was recorded before any copy. Live directories were then read once. Reporters ran only against the copied snapshot (`Phase7Paths.root` and sports `CAPTURE_DIR` / `SCORE_DIR` redirected). They did not reload live directories.

| Artifact | Inclusion rule |
|---|---|
| Weather receipts | `created_at <= T` |
| Weather records | `prediction_as_of` (capture finalization) `<= T` |
| Weather final scores | `scored_at <= T` and the matching record is in the snapshot |
| Weather pending files | `first_checked_at <= T` |
| Sports `WINDOW_CAPTURE` | `finalized_at <= T` |
| Sports `MISSED` | `written_at <= T` |
| Sports attempts | `written_at <= T` |
| Sports capture/score runs | `finished_at <= T` |
| Sports finalized scores | `scored_at <= T` and `source_record` is in the snapshot |

A Kalshi settlement time `<= T` is not enough if this machine wrote the score after `T`. File mtime was never used as a substitute. Missing availability timestamps were reported and the artifact was excluded.

**Included:** 42,177 snapshot files (listing sha256 `53abb22116a0a4194c92097975387914a02d4747ed95a42f4c38a4edce1a51c8`). **Excluded:** 3 — one `FAILED_ATTEMPT` with no `written_at`, and two sports collector-cycle lines with `finished_at > T` (the live jsonl grew after the captured prefix). **Missing timestamps:** 20 weather-log lines without a leading timestamp (not kept in the prefix) plus the attempt above.

Pinned inputs, re-hashed after copy: weather protocol `cc33e71d…948ae2`, frozen pool `eb4b6e71…33c4bb`, sports capture protocol `6fce0fac…fb41ad`. Immutable copies matched. Append-only logs may grow after the captured prefix; those extra bytes are not in this review.

---

## Weather Phase 7

Registered study: 60 target dates (2026-10-03 through 2026-12-01), CLINYC settlement, paired GFS + HRRR + shadow. Primary contrast is pooled-intraday multiclass Brier of `research_shadow − research_gfs`, date-clustered bootstrap n=1000 seed=42. Minimum 30 unique paired intraday dates. Below that the protocol label is `INSUFFICIENT`. The collection window is still open, so this cannot be a final assessment.

### Coverage

As of T, 19 of 60 dates are scheduled at each checkpoint; 41 remain future. Receipts in the snapshot: 65 `CAPTURED`, 30 `MISSED` (`window_elapsed_without_capture`). Records: 65. Final scores: 63. Pending: 2 (`2026-10-21` `d0_1200` and `d0_1500`, reason `event markets unavailable`).

| Checkpoint | Scheduled | Captured | Missed | Awaiting | Future | Paired valid |
|---|---:|---:|---:|---:|---:|---:|
| dminus1_1800 | 19 | 15 | 4 | 0 | 41 | 15 |
| d0_0600 | 19 | 11 | 8 | 0 | 41 | 11 |
| d0_0900 | 19 | 13 | 6 | 0 | 41 | 0 |
| d0_1200 | 19 | 13 | 6 | 0 | 41 | 13 |
| d0_1500 | 19 | 13 | 6 | 0 | 41 | 13 |

Unique target dates with a record: 18. Unique target dates with a final score: 17. Bucket contracts on included records: 390 (multiclass scoring is one Brier per event-checkpoint, not per bucket).

**`d0_0900` frozen cohort rule:** the checkpoint stays in coverage. All 13 captures are `calibration_INSUFFICIENT_HISTORY` for research GFS (deterministic given the frozen pool size) and `selected_run_horizon_short_null_padded` for HRRR. Shadow is therefore unavailable. No older run is substituted. There is no paired `d0_0900` comparison.

### Misses: documented incidents vs unattributed gaps

Thirty `MISSED` receipts. Sleep, memory pressure, or network outage is stated only where a prior documented incident or a recorded cycle failure exists. A missing cycle-log line is a logging/collection gap, not proof the host was asleep or offline.

| Attribution | Checkpoints |
|---|---|
| Documented sleep on battery ([operational misses note](phase7_operational_misses_note.md), 2026-10-06) | 2026-10-03 `d0_1200`/`d0_1500`; 2026-10-05 `d0_1200`/`d0_1500`; 2026-10-06 `d0_1200` (5) |
| Documented internet outage, machine awake (same note); cycle exit 21 in window | 2026-10-06 `d0_0600` (1) |
| Recorded collector failure (cycle exit 21 in window) | 2026-10-12 `d0_0600`, `d0_0900` (2) |
| Cycle ran in window with exit 0; miss still unexplained | 2026-10-06 `d0_1500`; 2026-10-07 `dminus1_1800`/`d0_0600`/`d0_0900` (4) |
| Unattributed collection gap (no cycle log line in the window) | remaining 18, including all of 2026-10-13 and several on 2026-10-08, 12, 15, 16, 20, 21 |

Parsed weather cycles in the log prefix: 342, of which 9 had a non-zero exit. Those non-zero exits are collector failures only when they fall in a capture window, as above.

### Forecast performance (descriptive; label `INSUFFICIENT`)

Primary cohort: 50 paired eligible checkpoints, of which **35 intraday on 15 unique target dates** (minimum 30). Label **`INSUFFICIENT`**. The registered study continues; this is not a stopping or promotion decision.

Pooled-intraday `research_shadow − research_gfs` Brier: **−0.0275** (n=35, 15 dates), 95% date-clustered CI **[−0.0923, 0.0268]**. The interval includes zero. Lower is better for the shadow.

| Scope | n (checkpoints) | dates | Shadow − GFS Brier | 95% CI |
|---|---:|---:|---:|---|
| pooled intraday (primary) | 35 | 15 | −0.0275 | [−0.0923, 0.0268] |
| dminus1_1800 | 15 | 15 | +0.0244 | [−0.0142, 0.0705] |
| d0_0600 | 11 | 11 | +0.0008 | [−0.0588, 0.0549] |
| d0_0900 | 0 | 0 | — | no paired scores |
| d0_1200 | 12 | 12 | −0.0380 | [−0.1105, 0.0274] |
| d0_1500 | 12 | 12 | −0.0430 | [−0.1172, 0.0203] |

Descriptive scores on the 50 paired eligible checkpoints (includes overnight; not the primary contrast):

| Stream | n | Brier | Log loss |
|---|---:|---:|---:|
| research_gfs | 50 | 0.7176 | 2.0261 |
| research_hrrr | 50 | 0.7420 | 2.0058 |
| research_shadow | 50 | 0.7057 | 1.9861 |
| legacy_incumbent (reference only) | 50 | 0.6982 | 1.3412 |

No new significance gate is applied. Legacy incumbent uses different inputs (`inputs_differ_from_research`) and is not in the primary contrast.

**Identity.** On every record where GFS, HRRR, and shadow are all OK (52), stored shadow probabilities equal `0.5 * P_gfs + 0.5 * P_hrrr` exactly (max abs diff 0).

**What is unresolved.** Fifteen paired dates cannot support the registered comparison. `d0_0900` contributes no pairs. Missed checkpoints shrink the captured set. Two 2026-10-21 checkpoints are pending because event markets were unavailable. Nothing here changes the protocol, calibration thresholds, or shadow-only rule.

---

## Sports capture v2

Availability-based operational pilot (manual and scheduled). Descriptive point estimates only: no bootstrap, no better/worse class, no Phase 1/2 sufficiency gate. Collection mode is how the run started, not a different forecast. Results tables are not split by mode.

### Coverage denominators

`WINDOW_CAPTURE` is an accepted prediction that is eligible for scoring. It is not the set of all events whose windows occurred.

From included capture-run summaries (1,433 runs with `finished_at <= T`):

| Count | Unique events (`competition\|cluster`) | Event-rows in runs |
|---|---:|---:|
| Events appearing in an included run | 6,962 | — |
| `window_not_open` (discovered, window not yet open) | 3,008 | 8,500 |
| `WINDOW_CAPTURE` | 3,139 | 3,139 |
| `MISSED` (recorded after cutoff, no accepted capture) | 492 | 492 |
| `started_not_recorded` (event already started; no MISSED written) | 333 | 883 |
| `no_linked_contracts` | 2,985 | 36,170 |
| `accepted_exists` / `DUPLICATE_ATTEMPT` | 2,901 / 1,342 | 27,775 / 3,065 |
| `FAILED_ATTEMPT` in run summaries | 1 | 1 |

Accepted checkpoint files in the snapshot: **3,139** `WINDOW_CAPTURE` (3,139 events, 34,017 ok contracts) and **492** `MISSED`. Attempts: 3,065 `DUPLICATE_ATTEMPT` and 2 `FAILED_ATTEMPT` (a third attempt was excluded for missing `written_at`).

The total eligible universe of sports events whose 20-minute windows occurred in this period **cannot be established** from saved records. Discovery is a rolling window around each run. Windows that closed while undiscovered, or that produced `started_not_recorded`, never become a complete miss list. **No capture rate is computed from accepted records alone.**

Scored (confirmed binary settlement, `scored_at <= T`): **33,678 contracts / 2,987 events**. Finalized non-binary: 74. Latest included settlement run (`20261021T180349424755Z`, finished 18:30:16Z) still listed 239 `PENDING`. Pending diagnostics are from that run, not a new fetch at T.

### Collection mode, sport, family

| Mode | WINDOW_CAPTURE events | MISSED events | Labelled contracts / events |
|---|---:|---:|---:|
| automatic | 3,084 | 415 | 32,373 / 2,933 |
| manual_pilot | 55 | 77 | 1,305 / 54 |
| collector_manual | 0 | 0 | 0 / 0 |

Labelled contracts vs events by sport (contract-weighted totals are dominated by Football segment markets):

| Sport | Labelled contracts | Labelled events |
|---|---:|---:|
| Football | 20,001 | 154 |
| Soccer | 5,358 | 186 |
| Tennis | 2,960 | 1,480 |
| Hockey | 2,888 | 222 |
| Basketball | 800 | 262 |
| Esports | 712 | 356 |
| Baseball | 328 | 13 |
| Volleyball | 166 | 83 |
| Cricket | 168 | 84 |
| Darts | 140 | 70 |
| Table Tennis | 120 | 60 |
| MMA | 35 | 16 |
| Rugby | 2 | 1 |

| Family | Labelled contracts | Labelled events |
|---|---:|---:|
| segment | 15,605 | 365 |
| game_winner | 6,092 | 2,964 |
| spread | 4,301 | 396 |
| total | 4,022 | 396 |
| team_total | 3,469 | 262 |
| score_props | 189 | 189 |

88 competitions have at least one labelled contract; the CSV lists each.

Candidate availability on the 34,017 eligible ok contracts: frozen 34,017; calibrated 4,988; market 4,841; blend 483. Market is mostly unavailable on segment and score-prop families (frozen scope: no new historical quotes) and on contracts the frozen per-event rule did not select. Calibrated/blend maps exist only for a few competition×family groups.

Missed windows are recorded absences, not reconstructed forecasts. `not_started_unexplained` on the scheduler, if present in live audits, is unexplained — not proof of host sleep. This review does not attribute sports MISSED rows to sleep.

### Forecast performance (common contracts only)

Event-equal weighting is primary. Standalone candidate averages use different samples and are not comparisons. Sign: candidate minus reference; negative means lower error for the candidate.

| Comparison | Contracts | Events | Brier (contract) | Brier (event-equal) | Log loss (contract) | Log loss (event-equal) |
|---|---:|---:|---:|---:|---:|---:|
| frozen (standalone) | 33,678 | 2,987 | 0.1782 | 0.2261 | 0.5307 | 0.6420 |
| calibrated (standalone) | 4,978 | 260 | 0.1897 | 0.2159 | 0.5624 | 0.6227 |
| market (standalone) | 4,695 | 2,828 | 0.1998 | 0.1991 | 0.5805 | 0.5780 |
| blend (standalone) | 483 | 123 | 0.2045 | 0.2052 | 0.5968 | 0.5983 |
| frozen − market | 4,695 | 2,828 | +0.0218 | **+0.0321** | +0.0517 | +0.0758 |
| calibrated − frozen | 4,978 | 260 | +0.0014 | **+0.0022** | +0.0033 | +0.0063 |
| blend − frozen | 483 | 123 | −0.0031 | **−0.0009** | −0.0052 | +0.0002 |
| blend − market | 483 | 123 | −0.0007 | **−0.0009** | −0.0011 | −0.0016 |

On the 2,828 events where both frozen and market have a probability, frozen has **higher** event-equal Brier than the market reference (+0.0321). Calibrated is slightly higher-error than frozen on the 260 events where both exist. Blend vs frozen/market on 123 events is near zero. These are descriptive pilot numbers, not a classification.

Family paired frozen−market rows are in the CSV. Market quotes are absent for segment and score_props (scope), so those families do not enter the frozen−market pair. That is a coverage fact, not a selected subgroup.

### Identity / reproduction

Stored probabilities were not changed to force equality. Identity calibration (`a=0, b=1`) and zero-weight/zero-intercept blends may still differ by rounding or the frozen `1e-4` clip used in log loss.

On labelled scores with both probabilities: calibrated vs frozen — 286 exact equal, 83 within `1e-4` but not exact, 4,609 differ by more than `1e-4` (n=4,978). Blend vs market — 106 exact, 18 within `1e-4`, 359 differ by more than `1e-4` (n=483). Most calibrated and blend values are not copies of the reference.

### What is unresolved

Market, calibrated, and blend are missing on most contracts, so most events cannot support those pairs. The captured set is whatever was in-window while this host ran; it is not a designed sample. Football segment contracts dominate contract-weighted totals, which is why event-equal is the primary view. The pilot remains availability-based; it is not independent confirmation of historical Phase 1/2 results.

---

## Independent checks

Weather Brier and shadow−GFS deltas were recomputed from stored bucket probabilities and the winning bucket on five primary-cohort checkpoints (`2026-10-03 d0_0600`, `2026-10-08 d0_1500`, `2026-10-11 d0_1500`, `2026-10-17 d0_0600`, `2026-10-19 d0_1500`). All five matched the stored scores.

Sports frozen−market Brier and log-loss differences were recomputed with the frozen Phase 1 `_losses` helper on five common contracts and on the full 4,695-contract common set. The full-set means matched the reporter: contract Brier +0.0217721291030025, event-equal Brier +0.032064331771587344.

---

## Test-count appendix (697 vs 719)

This is a pytest reporting difference, not a research result.

Current collection, this tree, `.venv\Scripts\python.exe -m pytest tests --collect-only -q`: **749 tests collected**.

The same command with `--ignore=tests/test_candle_coverage_audit.py --ignore=tests/test_inventory_integrity.py --ignore=tests/test_inventory_only.py --ignore=tests/test_recent_sampling_audit.py`: **697 tests collected**.

Those four files still exist. Collected separately they are 16 + 14 + 10 + 12 = **52** items. `749 − 52 = 697`. **697 is the selected suite with those 52 tests excluded, not the full suite.**

`git log --diff-filter=D -- tests/` is empty: no test file has been deleted. `tests/test_explore.py` and `tests/test_activity.py` were added after the hub work that reported 719 (`c3b8fcb`, `80793cf`). `719 + 16 + 14 = 749`. Frontend Vitest is a separate suite and is not in these counts.

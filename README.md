# Kalshi Edge Lab

Research tools for Kalshi prediction markets.

This repository has three conceptual layers:

1. **NYC Weather External Probability Research** — estimate `KXHIGHNY` outcome probabilities from external weather information only (GFS residuals, GEFS, observations), with calibrated uncertainty
2. **Whole-Kalshi Market Efficiency Research** — historical price-calibration / taker-efficiency study across Kalshi markets
3. **Shared Historical Execution Infrastructure** — inventory, candles, executable ask, fee resolution, no-lookahead selection (`kalshi/`, `research/prices.py`, `efficiency_backtest.py`)

The weather system answers: *What probability should external weather evidence assign?*

The efficiency system answers: *What prices were actually executable on Kalshi?*

The eventual strategy combines them. Phase 1 does **not** optimize weather parameters against trading ROI and does **not** place orders.

Neither track uses account credentials. Public market data only.

---

## NYC Weather External Probability Research (Phase 1)

```powershell
.\.venv\Scripts\python.exe weather_model.py --build-calibration
.\.venv\Scripts\python.exe weather_model.py --backtest
.\.venv\Scripts\python.exe weather_model.py --live
.\.venv\Scripts\python.exe weather_model.py --phase2-clinyc
```

Also still available:

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe backtest.py
```

### Separation (mandatory)

```text
WEATHER FORECASTER          TRADING EVALUATOR
external weather only  →    forecast probability
P(bucket YES)               + Kalshi executable price + fee
                            → YES / NO / NO_BET
```

`WeatherPrediction` contains **no** Kalshi bid/ask/mid/result fields.

Phase 1 trade evaluation defaults to `RESEARCH_ONLY` / `NO_BET`.

### Residual convention

```text
residual_f = actual_high_f - forecast_high_f
possible_actual = current_forecast + historical_residual
```

Do not reuse `backtest.py` signed errors (`forecast - actual`) without converting the sign.

### Settlement-source regimes

Historical calibration (through mid-2026 event rules) uses:

```text
nws_cli_knyc  — NWS Climatological Report (Daily), Central Park / KNYC
```

Current open markets often use:

```text
weather_company_clinyc  — The Weather Company, CLINYC
```

If `calibration_target_regime != live_target_regime` and no transfer calibration exists, live mode labels probabilities:

```text
EXPERIMENTAL — SETTLEMENT SOURCE MISMATCH
```

and does **not** claim TWC settlement calibration from NWS residuals.

### GFS run policy

Report previous-day 12Z / 18Z and event-day 00Z / 06Z **independently**.

Do not pick the historically best Brier run and call it OOS.

Operational live forecasting uses the latest exact GFS run with:

```text
run_init + GFS_PUBLICATION_LATENCY <= prediction_as_of
```

(latency is an explicit conservative constant, currently 6 hours).

Residual pools never mix different GFS runs. Same-run hierarchy only:

```text
month + same run → season + same run → same-run global → insufficient_history
```

Burn-in: `MIN_RUN_HISTORY = 20` prior same-run residuals before an OOS calibrated prediction.

### Package layout

```text
research/weather/   — resolution, calibration, probability, replay, reporting
weather_model.py    — CLI
weather.py / nws.py / historical_weather.py  — reused fetchers (not rewritten)
```

Caches: `data/cache/weather/` (with fallback read of root `gfs_run_cache.json`).
Calibration CSV: `data/weather/calibration/gfs_errors.csv`.

Phase 2 CLINYC recovery uses series-scoped settled markets only
(`GET /markets?series_ticker=KXHIGHNY&status=settled` + historical series fetch),
cached under `data/cache/weather/clinyc/`. It does **not** rebuild the full recent inventory.

Settlement temperatures prefer event-level unanimous numeric `expiration_value`
(not `settlement_value_dollars`). Conflicting values are rejected.

### Phase 6 — historical KNYC as-of observations + operational replay

```bash
python weather_model.py --phase6-obs-replay            # uses caches when present
python weather_model.py --phase6-obs-replay --refresh-phase6-cache
```

Reads the Phase 5 operational CSVs (read-only; run selection and publication
latency unchanged) and adds what was knowable at each checkpoint.

- **Observation source:** Iowa Environmental Mesonet ASOS/METAR archive
  (`asos.py`, station `NYC` = KNYC), routine + SPECI reports, UTC timestamps.
  Raw monthly CSVs cached in `data/cache/weather/knyc_obs_iem/` with
  retrieval metadata. Never derived from the CLI daily high.
- **Availability assumption (frozen):** IEM gives no publication time, so a
  report counts as available 20 minutes after its observation time. The :51
  routine report is therefore never usable at the next :00 checkpoint.
- **Observation window:** the NWS CLI climate day that settles `nws_cli_knyc`
  events — midnight Local Standard Time (UTC−5) all year, so during EDT it runs
  01:00 EDT → 00:59 EDT and 00:xx EDT reports belong to the previous day.
  The version-C model window uses the same climate-day bounds
  (`climate_day_bounds_utc`).
- **Trust rules:** newest usable report ≤ 120 min old; no gap > 180 min
  (including climate-day start → first report). Wrong-station,
  missing/invalid, out-of-range, conflicting-duplicate and not-yet-available
  reports are rejected with counts.
- **Model window:** the hourly series of each already-selected GFS/HRRR run is
  fetched from Open-Meteo Single Runs (UTC) and cached in
  `data/cache/weather/single_runs_hourly/`. `model_remaining_day_high_f` is the
  max forecast at hourly valid times in `[checkpoint, next midnight EST)` — the
  rest of the same CLI climate day the observations use, so during EDT it
  includes 00:00 EDT of the next calendar date (end exclusive). Every expected
  hour must be present with a value, otherwise the row is `REPLAY_UNAVAILABLE`
  (final timestamp beyond the end of the series →
  `run_horizon_ends_before_end_of_target_date`; only a contiguous null-padded
  tail after the last real value → `selected_run_horizon_short_null_padded`;
  a missing/null hour followed by later real values →
  `missing_or_null_hours_in_remaining_window`). There is no partial max and no
  fallback to the Phase 5 daily max.
- `projected_final_high_f = max(observed_high_so_far_f, model_remaining_day_high_f)`.

Three versions, each recalibrated walk-forward on its own residuals and
scored on the identical eligible cohort (both models FULL in version C):

| Version | Model value | Observations |
|---|---|---|
| A | Phase 5 full-run max | none |
| B (diagnostic bridge) | Phase 5 full-run max | as-of |
| C (Phase 6 primary) | remaining-day max | as-of |

A→B is the effect of adding observations under the old window; B→C is the
effect of correcting the window. Replay labels are per row:
`FULL_OPERATIONAL_REPLAY` only for version-C rows with trusted observations and
full remaining-day coverage; `OBS_BRIDGE_DIAGNOSTIC` for B; `MODEL_ONLY_REPLAY`
when observations are missing/untrustworthy; `REPLAY_UNAVAILABLE` when the run
lacks remaining-day coverage. Phase 5's `HISTORICAL_ASOF_OBS_AVAILABLE` stays `False`.

Coverage (Phase 5 dates 2026-04-02 → 2026-07-25, 114 events): 3,642 KNYC
reports, 0 rejected; all 4 intraday checkpoints OK on all 114 dates.

**v2 vs v1.** v1 ended the version-C model window at the next
America/New_York midnight, which during EDT dropped the final 00:00 EDT hour
of the CLI climate day (and for `dminus1_1800` included the target date's
00:00 EDT hour, which belongs to the previous climate day). v2 fixes this.
Every version-C row (all dates are in EDT) gained the final hour; the 226
`dminus1_1800` rows also lost the leading hour (same 24-hour count). The added
hour never became the max; 8 `dminus1_1800` maxima fell (4 GFS, 4 HRRR) from
dropping the leading hour. 53 HRRR `d0_0900` rows (all selected 09Z runs,
18-hour horizon ending 23:00 EDT; the Open-Meteo payload pads later
timestamps with nulls) are now `REPLAY_UNAVAILABLE`, so `d0_0900` eligibility
fell 114 → 61 dates and its common cohort 44 → 4 (too small to interpret).
Other checkpoints are unchanged. Version C pooled-intraday shadow − GFS Brier:
v1 −0.047 [−0.101, +0.008] (n=176) → v2 −0.049 [−0.109, +0.008] (n=136); the
interval still includes zero. Full diff:
`python -m research.weather.phase6_window_diff` →
`data/results/phase6_window_fix_diff_v2.json`.

**v2_1 (current) vs v2 — diagnostic reason only.** The 53 HRRR `d0_0900`
rows change from `missing_or_null_hours_in_remaining_window` to
`selected_run_horizon_short_null_padded`; values, eligibility, cohorts, scores
and CIs are identical. v2_1 was migrated from the v2 artifacts (no downloads,
no rescoring) by `python -m research.weather.phase6_v2_1_migration`, which
verifies the exact planned changes and that v1/v2 files are byte-for-byte
unchanged. A read-only audit (`python -m research.weather.phase6_coverage_audit`)
shows each of those rows selected an 18-hour 09Z run; the one newer
latency-eligible init (10Z) is not represented in the local Phase 5 cache, and
an older 06Z extended run covering the window was eligible but not newest.
Summary: [`docs/research/phase6_v2_1_summary.md`](docs/research/phase6_v2_1_summary.md)
(`python -m research.weather.phase6_summary`); per-row audit:
[`docs/research/phase6_v2_1_hrrr_d0_0900_coverage_audit.csv`](docs/research/phase6_v2_1_hrrr_d0_0900_coverage_audit.csv).

**Version B (closed).** The v2.1 `remaining_window_reason` relabel is
accepted for version B too. The 53 matching `OBS_BRIDGE_DIAGNOSTIC` rows
change only that diagnostic column. Version B replay behavior and numerical
results are unchanged. Historical artifacts are not regenerated.

Outputs (`_v2_1`): `data/weather/calibration/{gfs,hrrr}_operational_replay_obs_v2_1.csv`
(C), `..._obs_bridge_v2_1.csv` (B), `knyc_asof_observations_v2_1.csv`,
`knyc_iem_observations_v2_1.csv` (tracked); `data/results/phase6_obs_coverage_v2_1.json`,
`phase6_operational_comparison_v2_1.json`, `phase6_shadow_evaluation_v2_1.json`,
`phase6_methodology_v2_1.json` (local only; SHA-256 in the summary). The `_v1`
(pre-window-fix) and `_v2` files are kept byte-for-byte unchanged (v1 result
JSONs also copied to `data/results/phase6_v1_backup/`).
Phase 5 artifacts and the frozen shadow hypothesis are not modified.
RESEARCH_ONLY / NO_BET; the shadow stays shadow-only and CLINYC transfer
status is unchanged. A confidence interval excluding zero is not validation.

### Phase 7 — prospective validation (frozen protocol)

The protocol is in `research/weather/hypotheses/phase7_prospective_protocol_v1.json`.
It is write-once and covers 60 consecutive target climate dates, starting with
the first full date after registration. There is no early stopping. Each date
has 5 checkpoints, each with a 30-minute capture window. The primary comparison
is the pooled intraday (`d0_0600/0900/1200/1500`) shadow − research-GFS
multiclass Brier, with a 95% bootstrap clustered by date. Fewer than 30 paired
dates is labeled `INSUFFICIENT`. The cohort is CLINYC-regime events scored on a
confirmed CLINYC settlement. The calibration regime is `nws_cli_knyc`; the
transfer is experimental and does not establish identity. Other regimes are
reported separately.

- **Timing.** `scheduled_checkpoint_at` is nominal, and the evidence cutoff
  equals that instant. `prediction_as_of` is the actual time of finalization.
  Every fetch records its start, completion and receipt times. Anything
  finalized outside the window is kept as a diagnostic and receives a `MISSED`
  receipt.
- **Selection.** Run selection uses the Phase 5 predicate: the newest
  latency-eligible run with a non-null target-date high. An incomplete
  selected run makes that component unavailable; the code never falls back to
  an older complete run. Every attempted candidate keeps its raw evidence and a
  fetch-log line, including failures.
- **Calibration.** The version-C pools are frozen in
  `data/weather/phase7/frozen_pools_v1.csv` (SHA-256 pinned). No prospective
  appends.
- **Method pinning.** Source hashes are pinned for the window, observation,
  selection, probability, calibration, shadow and scoring functions, plus the
  constants. Drift refuses primary capture.
- **Integrity vs working tree.** Capture is gated only by the pinned hashes.
  `method_integrity_ok` means every method group and constant matches.
  `calibration_integrity_ok` means the frozen pool and the pinned v2.1 source
  CSVs match. A mismatch refuses capture and writes a `MISSED` receipt naming
  the hash. Each record also stores Git state for diagnostics only:
  `working_tree_dirty` and `working_tree_changed_paths` cover every tracked
  change, while `code_dirty` and `code_dirty_paths` cover only `.py`, `.ps1` and
  `requirements*.txt`. The hourly cycle appends to the tracked
  `data/weather/calibration/knyc_clinyc_pairs.csv` (CLINYC transfer
  experiment). That makes the working tree dirty, but it is not a code change
  and does not affect capture.
- **Scoring.** Missing settlement stays pending and is retried. A final score
  is write-once and is only written after a confirmed settlement. The outcomes
  ledger is idempotent.
- **`d0_0900` calibration (deterministic).** The frozen `d0_0900` pools have
  61 rows per model, below the global minimum of 80. Every date is therefore
  `CALIBRATION_INSUFFICIENT_DETERMINISTIC` for the whole collection period.
  This follows from the frozen pools alone. The checkpoint stays in coverage
  reporting.
- **`d0_0900` HRRR horizon (diagnosed per run).** The protocol's
  `hrrr_horizon_preflight` expects the selected 09Z run (18 h) to end before
  the climate day does. That expectation comes from observed Open-Meteo
  behavior; it does not prove what every future run will return. Each live
  capture's horizon status comes from the run it actually selected:
  `research_hrrr.window` in the record, the saved payload, and the fetch log.
  An older run is never substituted.

```powershell
.venv\Scripts\python weather_model.py --phase7-preflight   # read-only
.venv\Scripts\python weather_model.py --phase7-register    # write-once
.venv\Scripts\python weather_model.py --phase7-report      # read-only
.venv\Scripts\python weather_model.py --phase7-dry-run     # live fetch, never cohort
.venv\Scripts\python weather_model.py --phase7-reproduce data\weather\phase7\records\...json
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_weather_task.ps1
```

Scheduler: the task `KalshiEdgeLab-WeatherProspective` runs hourly at :05 local
time. The machine must be on ET, which the script verifies and never changes.
It runs `scripts\weather_prospective_task.ps1`, which takes an atomic lock
recording the owner PID and start time. Logs go to `data\logs\prospective\`.
Overlapping starts are ignored. Catch-up runs reconcile `MISSED` receipts with
no backfill. The task runs only while the user is logged on (Interactive
logon). Snapshots, evidence and logs under `data/weather/phase7/` and
`data/logs/` stay local. SHADOW ONLY; RESEARCH_ONLY / NO_BET.

#### Phase 7 operations

The host must stay powered (on AC; on battery the laptop sleeps after 10
minutes), awake, connected to the network, and logged in. The task uses an
Interactive logon with no stored password and does not wake the machine. A
checkpoint whose 30-minute window passes while the laptop is asleep, offline,
logged off or shut down is `MISSED`.

```powershell
# Progress report (read-only): integrity flags, working-tree state, coverage, missed reasons
.venv\Scripts\python weather_model.py --phase7-report
.venv\Scripts\python weather_model.py --phase7-report --json

# Scheduler status, lock, recent log
Get-ScheduledTaskInfo -TaskName KalshiEdgeLab-WeatherProspective | Format-List LastRunTime,LastTaskResult,NextRunTime,NumberOfMissedRuns
Test-Path data\logs\prospective\cycle.lock
Get-Content data\logs\prospective\$((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')).log -Tail 40

# Reproduce a saved record offline (exit 0 = identical, 4 = mismatch)
.venv\Scripts\python weather_model.py --phase7-reproduce data\weather\phase7\records\2026-10-03\dminus1_1800.json

# Missed captures and their reasons; refused captures keep diagnostics
Get-ChildItem data\weather\phase7\receipts -Recurse -Filter *.json | Select-String '"status": "MISSED"' -List
Get-ChildItem data\weather\phase7\diagnostics -Recurse -Filter *.json
```

In a record, `prediction_as_of` is the actual finalization time.
`evidence_cutoff_utc` is the scheduled checkpoint, which is not a prediction
timestamp. Compare `protocol_sha256` and `frozen_pool.sha256` with the full
SHA-256 values in the protocol file, not with abbreviations.

**Resuming after downtime.** Wake or log in and reconnect. `StartWhenAvailable`
fires one catch-up run, which writes `MISSED` receipts
(`window_elapsed_without_capture`) for every elapsed checkpoint. Collection
continues at the next open window. Never backfill: do not reconstruct missed
checkpoints from historical downloads, do not edit or delete receipts, do not
re-register the protocol, and do not use `--phase7-dry-run` as a substitute
(it is never part of the cohort). Afterwards, check with `--phase7-report`.

---

## Research UI (FastAPI + React)

```text
Python research engine
        ↓
   FastAPI layer (api/)
        ↓
   React frontend (frontend/)
```

- Python owns probability calculations, settlement parsing, and (later) fee/execution math
- React owns presentation only
- UI cannot generate trading recommendations yet (`RESEARCH_ONLY` / `NO_BET`)
- `GET /api/weather/model/summary` reads existing artifacts only — never rebuilds calibration/backtest

### Backend

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn api.app:app --reload
```

API base: `http://localhost:8000`

- `GET /api/health`
- `GET /api/weather/live` — may fetch current open markets / GFS / GEFS / observations
- `GET /api/weather/events/{event_ticker}`
- `GET /api/weather/model/summary` — read-only artifacts (`model_summary_available`)

CORS allows only `http://localhost:5173`.

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Optional mock mode (explicit; never silent fallback):

```powershell
$env:VITE_USE_MOCK="true"; npm run dev
```

### Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm test
```

---

## NYC weather (legacy CLIs)

```powershell
python main.py
python backtest.py
```

- `main.py` — live open `KXHIGHNY` markets vs Open-Meteo / NWS
- `backtest.py` — historical settled NYC high-temp markets vs GFS Day-00Z skill

These continue to work independently of the efficiency study.

---

## Market Efficiency Research

### Purpose

Ask whether Kalshi prices show repeatable, historically exploitable biases after accounting for:

- executable bid/ask (not last trade)
- fees (time-aware)
- liquidity / spread
- time to resolution
- out-of-sample performance

This is **research**, not guaranteed trading profit.

> Historical profitability does not prove future profitability.

> A price-calibration bias is not necessarily tradable after spread and fees.

### Data sources

- Production public API: `https://external-api.kalshi.com/trade-api/v2`
- Settled inventory: `GET /historical/markets` + `GET /markets?status=settled` (deduped by ticker)
- Cutoff routing: `GET /historical/cutoff`
- Candles:
  - Historical tier: `GET /historical/markets/{ticker}/candlesticks`
  - Recent tier: `GET /series/{series_ticker}/markets/{ticker}/candlesticks`
- Fees: `GET /series/fee_changes?show_historical=true` (not today's metadata alone)
- Categories / series metadata: `GET /series/{series_ticker}`, `GET /events/{event_ticker}`

### Execution-price assumptions

- Primary model: **1-contract taker**
- YES entry = candle `yes_ask.close`
- NO entry = `1 - yes_bid.close`
- Horizons: 7d, 48h, 24h, 6h, 1h before `close_time`
- Candle selection: latest `end_period_ts <= target_time` (no look-ahead)
- Last trade may be stored for comparison but is **not** the primary execution price

Sensitivity sizes (10 / 100 contracts) may be computed, but are labeled:

> top-of-book price assumption; historical depth unavailable

Candlesticks do **not** prove that 100 contracts were available at the top of book.

### Fee assumptions

- Quadratic taker fee: `round_up(M × 0.07 × C × P × (1 − P))` with centicent rounding
- Fee type/multiplier resolved from historical fee-change history at the simulated entry timestamp
- If a series has **no** rows in `fee_changes`, fall back to series metadata with source `series_metadata_no_history` (documented assumption)
- If fee_changes exist for the series but none cover the entry timestamp, the market is skipped (do not apply a later schedule to earlier trades)
- Event-level fee overrides take precedence when present
- Unsupported / unknown fee schedules are skipped (not treated as zero)

### Settlement rules

- Primary analysis includes only normal binary `result` values: `yes` or `no`
- Scalar / voided / fractional / malformed settlements are skipped as `nonstandard_settlement`
- Non-`yes` is **never** automatically treated as a NO win

### Cache behavior

Cached under `data/cache/` (gitignored):

- inventory (historical + recent)
- events / series
- fee changes
- per-ticker hourly candles

Interrupted downloads resume from cache. Use `--refresh` to force re-download.

Results are written to `data/results/` (gitignored).

### Commands

```powershell
# Recommended first run
python efficiency_backtest.py --smoke

# Bounded run
python efficiency_backtest.py --max-markets 1000

# Full inventory (large download — do not start casually)
python efficiency_backtest.py --full

# Force refresh cache
python efficiency_backtest.py --smoke --refresh
```

### Output files

- `data/results/observations.csv`
- `data/results/price_bucket_summary.csv`
- `data/results/category_summary.csv`
- `data/results/yearly_summary.csv`
- `data/results/strategy_summary.csv`

### Interpreting results

- ROI = net profit / capital risked (fees included), not profit per contract alone
- Sample-size labels describe N only (`insufficient` / `weak evidence` / `preliminary` / `stronger sample`)
- Bootstrap ROI intervals resample **events** where possible to reduce correlated-contract overcounting
- Discovery / validation / out-of-sample periods are chronological; do not treat discovery performance as proof

A successful research outcome can be:

```text
NO SYSTEMATIC PROFITABLE EDGE FOUND
```

### Known limitations

1. Top-of-book candle prices ≠ guaranteed fill size
2. Derived NO ask may differ from a live NO ask quote
3. Incomplete fee-change history causes markets to be skipped
4. Hourly candles are coarse vs minute microstructure
5. Correlated contracts within an event inflate naive confidence if event structure is ignored
6. Past edge ≠ future edge

### Tests

```powershell
python -m pytest -q
```

---

## Integrity rules

1. Never use settlement information when choosing an entry
2. Never use a candle after the simulated entry time
3. Never substitute last price for executable ask in the primary taker test
4. Include fees
5. Include bid/ask spread
6. Do not assume maker fills
7. Do not optimize and test on the same period
8. Do not silently remove losing markets
9. Do not call tiny samples an edge
10. Do not change methodology because a result is unprofitable
11. Keep raw observations auditable
12. If the data does not support an edge, report that honestly

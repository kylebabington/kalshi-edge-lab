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
- **Evidence bytes.** Each saved response is the UTF-8 encoding of the decoded
  response text. These are exactly the bytes hashed into `response_sha256`, not
  the original HTTP body bytes. `--phase7-reproduce` reports `evidence_integrity`
  for every referenced file: a raw-byte match, a CRLF-normalized match, and an
  explicit status for a missing file or missing hash. Mismatches are printed to
  stderr. The exit code still reflects prediction reproduction only. IEM files
  saved before 2026-10-03 18:30 ET have Windows CRLF line endings and match only
  after CRLF normalization; see
  `docs/research/phase7_evidence_line_endings_note.md`.
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

# Reproduce a saved record offline (exit 0 = identical, 4 = mismatch; evidence hash failures go to stderr)
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
.\.venv\Scripts\python.exe -m uvicorn api.app:app --reload --reload-dir api --reload-dir research
```

Without `watchfiles` installed, `--reload` polls every `.py` file under the reload directories several
times a second. Limiting it to `api` and `research` keeps it out of `.venv` and the large `data/` tree,
which otherwise uses a full CPU core and slows the API.

API base: `http://localhost:8000`

- `GET /api/health`
- `GET /api/weather/live` — may fetch current open markets / GFS / GEFS / observations
- `GET /api/weather/events/{event_ticker}`
- `GET /api/weather/model/summary` — read-only artifacts (`model_summary_available`). Status fields
  never report success without evidence (changed semantics; record kinds are under "Research Hub" below):
  - `research_status.weather_phase_1|2|4_evidence` are `UNKNOWN` (no tracked completion record exists;
    previously hardcoded `COMPLETE`). `weather_phase_3_transfer` is `VALIDATED` / `EXPERIMENTAL` from the
    transfer artifact, or `UNAVAILABLE` when the artifact is missing (previously `EXPERIMENTAL`).
    Policy values (`SHADOW_ONLY`, `DISABLED`, `BLOCKED`) are unchanged.
  - `multi_source_evidence` values are `CONFIGURED` or `PRESENT` (a supporting file exists; validity not
    checked); never `ACTIVE`/`LIVE`. The last saved fetch outcome per source is in
    `multi_source_evidence_details[source].operations`.
  - `prospective_journal.settled_events_scored` counts distinct events among score files (previously a
    copy of the score-file count). `settlement_transfer.stale` is `null` (no freshness rule) and
    `last_updated` comes from the artifact. `prospective_validation.scored_events` is `null` when its
    summary artifact is missing; `experimental_transfer_status` comes from the transfer artifact or is
    `unknown`.
  - `research_status_details` / `multi_source_evidence_details` carry structured status records.
- `GET /api/weather/saved-events?status=&q=&sort=&page=&page_size=` — one item per event from saved
  snapshots and score files. `status` is `all|upcoming|awaiting_settlement|settled`; `sort` is
  `default|target_asc|target_desc|capture_desc|settlement_desc`. Settlement has two separate levels:
  `event_settlement` (confirmed by any valid score, `conflict` when scores disagree) and
  `displayed_snapshot_score` for the latest snapshot only (`scored|not_scored|unavailable|invalid`). An
  older scored snapshot never marks the latest one as scored.
- `GET /api/weather/saved-events/{event_ticker}` — every saved snapshot for one event with its own score
  state; 404 when nothing is saved
- `GET /api/weather/collector` — prospective task health from cycle end lines in the task log (last
  successful cycle, failures) kept separate from forecast freshness (latest saved snapshot, latest score,
  missed checkpoints) and a passive scheduler query
- `GET /api/weather/metrics-context` — sample sizes and data periods: prospective snapshot scores vs the
  historical operational replay

The saved-weather endpoints only read files; they never fetch upstream data or create directories.

CORS allows only `http://localhost:5173`.

Sports (read-only; every handler reads saved artifacts and never fetches upstream, captures, settles,
writes reports or starts the collector):

The sports handlers keep parsed records and scores, and the titles and market descriptions read from
saved evidence, in memory. Each value is reused only while every file it was built from has the same size
and modification time, so new, changed or removed files are re-read and their checksums are checked
again. Counts, filters, sorting and paging use record and score metadata only; titles and participants
are filled in for the rows on the returned page (or for every event when `q` is set). At startup a
background thread parses records and scores and reads the title and market evidence used by search; it
pauses between events while requests are running.

- `GET /api/sports/summary` — counts, candidate availability, last capture, collector brief, counts by
  collection mode
- `GET /api/sports/events?sport=&competition=&family=&cohort=&status=&collection=&q=&sort=&page=&page_size=` —
  `sort` is `default|start_asc|start_desc|capture_desc|settlement_desc` (presentation order only; items
  without the timestamp go last); each item's `settlement.last_settled_at` is the latest Kalshi
  settlement time recorded in its scores; `status` is `all|captured|upcoming|awaiting_settlement|settled|missed`; `collection` is
  `automatic|collector_manual|manual_pilot` (see
  [operational amendment 1](docs/research/sports_phase2_capture_v2_ops_amendment_1.md))
- `GET /api/sports/events/{event_id}` — `event_id` is `competition|cluster` (URL-encode it), one event with
  all of its contracts
- `GET /api/sports/results?sport=&competition=&family=&cohort=&weighting=event|contract`
- `GET /api/sports/coverage?sport=&competition=&family=&status=&q=&page=&page_size=`
- `GET /api/sports/historical?protocol=&level=&key=&family=&cohort=&comparison=&page=&page_size=`
- `GET /api/sports/collector` — status files plus a passive `schtasks /Query` of the capture task
  (`KalshiEdgeLab-SportsProspective`, every 5 minutes) and the separate backup task
  (`KalshiEdgeLab-SportsBackup`, 15-minute checks, hourly snapshots); `unknown` when unreadable; cycle
  failures and deadline misses reported separately; last private sports-state backup result (snapshot,
  upload, pending commit, lock duration)

UI routes: `/sports` (tabs Events, Results, Coverage, Historical Research; filters live in the URL) and
`/sports/events/:eventId`. Which local artifacts populate each view, and how the 5-minute sports collector
is operated, is in [docs/research/sports_collector_v1.md](docs/research/sports_collector_v1.md).
`/api/sports/results` includes a `period` (capture and settlement time range), historical protocols
include their test or evaluation `period`, and `/api/sports/collector` adds `history` from the full cycle
log.

### UI routes

| Route | What it shows |
| --- | --- |
| `/` | Overview: separate Weather and Sports sections, each with collector health, saved-forecast freshness, and upcoming / pending settlement / recently settled cards. Every list loads on its own, so a slow or failed request never blocks another. |
| `/explore`, `/explore/events/:eventTicker` | Market Explorer: Kalshi events across categories from the local explorer cache, with Refresh markets, lifecycle/freshness/lookup fields, and model support vs saved-forecast coverage shown separately. Quotes are labelled **Latest retrieved quotes**. |
| `/markets` | Weather events: **Saved forecasts** tab (search, status, sort, pagination in the URL) and **Open markets** tab (current market data from the slow live endpoint). |
| `/markets/:eventTicker` | Open-market detail when the event is still open; otherwise the saved-forecast view with event settlement and per-snapshot scores. |
| `/sports`, `/sports/events/:eventId` | Sports browsing with plain-language labels and sort by start, capture or settlement time. |
| `/watchlist` | Starred events, filterable by `?category=weather`, `sports`, or `kalshi`. |
| `/research` | Research Hub: Weather and Sports experiment catalog (filters `q`, `category`, `designation`, `mode`, `status` in the URL) and the weather model summary. Each list loads on its own. |
| `/research/:experimentId` | One experiment (`weather.*` or `sports.*`): question, candidates, reference, cohort rules, limitations, historical replay / manual pilot / automatic collection, tracked vs local sources, read-only frozen settings and personal notes. |
| `/research/notebook` | Personal notebook: all notes and questions, filters, JSON export and import with a merge preview. |
| `/opportunities`, `/opportunities/:ticker` | Current weather and sports markets ranked by attention score, with edge, EV, fees, warnings and manual AI research. |

Collector health uses successful cycle times only (stale after 2 × cadence + 5 minutes; a missing
timestamp is "unknown", never healthy). Forecast age is shown separately and never affects health. Times
are shown in the browser's time zone with its abbreviation; weather target dates are New York dates.
Detail pages return to the list with its filters, sort and page.

Results views explain Brier, log loss, event-equal weighting and paired differences inline, show sample
size and data period beside each metric, and badge each result as prospective (saved before the outcome)
or historical research (backtest / replay). Standalone per-candidate tables are labelled as not
comparable across rows; there are no rankings.

**Watchlist storage.** Stars live only in this browser under the localStorage key
`kalshi-edge-lab.watchlist.v1` as `{version: 1, entries: [{id, category, key, label, added_at}]}`. IDs are
`weather:<event_ticker>`, `sports:<event_id>`, or `kalshi:<event_ticker>`, so identical keys in different categories never collide.
Explorer stars prefer weather/sports keys when known; notebook notes keep the target they were created with and the UI resolves aliases when displaying.
Invalid entries are dropped; unreadable data is copied to `kalshi-edge-lab.watchlist.v1.unreadable-backup`
before it is replaced. If storage is blocked or full, the list is kept in memory for the tab and the page
says "Changes won't persist". Changes sync across tabs. Each entry is looked up again through the
read-only API; an entry whose event is no longer found stays on the list with an explanation until you
remove it.

### Market Explorer

- `GET /api/explore/summary` — cached event counts, categories, series-metadata freshness, latest refresh state
- `GET /api/explore/events` — paginated explorer cards (`category`, `q`, `lifecycle`, `coverage`, `quote`, `sort`, `page`, `page_size`)
- `GET /api/explore/events/{event_ticker}` — one cached event with markets, rules, coverage, and latest retrieved quotes
- `POST /api/explore/refresh` — start an open-event crawl + watched/non-final lookups (body may list `watched_event_tickers`); GET never starts a crawl
- `GET /api/explore/refresh` — refresh job status (pages, events, lookup attempted vs refreshed)

Cache lives under `data/cache/explore/`. Lifecycle, retrieval freshness, and lookup outcome are separate fields.
`model_supported` and `saved_forecast` are separate; a supported sports series without a capture is not labelled “No model available”.

### Research Hub

- `GET /api/research/experiments?category=weather|sports` — catalog cards for registered experiments
  and frozen protocols (`category` is required, so the two lists load independently)
- `GET /api/research/experiments/{experiment_id}` — one experiment with its detail sections; 404 when the
  ID is not registered

Both endpoints only read tracked protocols, hypotheses and docs plus local result files (cached by size
and modification time). They never fetch upstream, run models, write reports or verify integrity. Sample
counts come from file counts, tracked summary CSVs and the already cached sports layer; while the sports
index is still loading after startup, the sports capture card says so and its counts are `null`.

Every status is a record `{kind, state, label, basis, source, as_of, reason}` (`research/status_records.py`):

| kind | states |
| --- | --- |
| implementation | `implemented` (tracked implementation files) or `unknown` |
| evidence | `present` (file exists; validity not checked), `missing`, `invalid` (unparseable), `not_checked` |
| operations | `recent_success`, `stale`, `failed`, `unknown`, `not_applicable` |
| integrity | `verified` only when a recorded verification ran; catalog cards always report `not_checked` |
| policy / configuration | intentional states such as `RESEARCH_ONLY`, `NO_BET`, `SHADOW_ONLY`, `CONFIGURED` |

Missing or unreadable support is shown as unknown or unavailable with its reason, never as success. Cards
label results as a tracked summary (in git), a local artifact (this machine only) or a live page.

**Personal notebook.** Notes and questions with tags, attached to an experiment or to a weather or sports
event, live only in this browser under `kalshi-edge-lab.notebook.v1` as `{version: 1, entries: [{id, kind:
note|question, origin: personal|ai_commentary, text, tags, target, created_at, updated_at}]}`. `target` is
`{type: "experiment", id}` or `{type: "event", category, key}`. Storage handling matches the watchlist
(invalid entries dropped, unreadable data copied to `.unreadable-backup`, in-memory fallback with a
warning, cross-tab sync). Notes are **not** included in collector backups, are never sent to the API and
are not research evidence. `ai_commentary` marks text you pasted from an AI tool; the hub never calls a
model. Export writes `{format: "kalshi-edge-lab.notebook", version: 1, exported_at, entries}`. Import
validates the file and previews new, identical, conflicting and invalid entries; merge only adds new IDs
and keeps your version on conflicts; replacing the whole notebook requires an explicit confirmation.

### Opportunities

`research/opportunities/` turns saved weather snapshots and saved sports captures into one `Opportunity`
per contract. It reads only existing artifacts (no upstream fetches) and does not import the AI layer;
the direction is raw data → opportunity/quality engine → AI eligibility/research.

- **Edge** is model probability minus the executable price on the better side (YES at the ask, NO at
  `1 − bid`). **EV before fees** is shown separately from **EV after fees**.
- **Fees** come from the cached series fee schedule (`data/cache/fees/`). When it is missing, fees and
  after-fee EV are `null` and a `fees unavailable` warning is shown; fees are never assumed to be zero.
- **`attention_score`** (0–100) ranks which markets deserve a closer look. It is **not a probability,
  not expected value and not a recommendation**. Every point is listed with its reason (edge, after-fee
  EV, confidence, calibration, agreement, freshness, minus caution penalties).
- **Quality**: `OK`, `CAUTION` (methodology uncertainty lowers the score and is shown as a warning) or
  `BLOCKED` (invalid or missing prediction, stale data, no quote, failed integrity/status checks,
  blocking source warnings). Blocked markets score 0, stay in the API, and are hidden in the UI unless
  "Show blocked" is checked. Non-OK weather predictions are kept and marked blocked, not dropped.
- Weather LOW confidence caused by the unvalidated KNYC → CLINYC settlement transfer is a caution, not a
  block; the transfer warning is always shown.

Endpoints:

- `GET /api/opportunities?category=&min_confidence=&min_edge=&freshness=&quality=&eligibility=&ai_status=&include_blocked=&page=&page_size=`
- `GET /api/opportunities/{ticker}` — opportunity, the evidence the AI would see, cached research state
- `POST /api/opportunities/{ticker}/research?force=false` — manual AI research (see below)
- `GET /api/ai/status` — provider, model, reachability

Thresholds can be changed with `OPPORTUNITY_WEATHER_FRESH_S`, `OPPORTUNITY_WEATHER_MAX_AGE_S`,
`OPPORTUNITY_SPORTS_FRESH_S`, `OPPORTUNITY_SPORTS_MAX_AGE_S`, `OPPORTUNITY_WIDE_SPREAD`,
`OPPORTUNITY_SPORTS_CANDIDATE_DISAGREEMENT`, `OPPORTUNITY_MIN_WEATHER_SAMPLE` and
`OPPORTUNITY_BLOCKING_WARNING_PATTERNS` (comma-separated, case-insensitive).

### AI Research Setup

AI research is optional and explains supplied evidence only. The statistical model stays the source of
truth for probability, edge, EV, confidence, calibration, fees and freshness. **AI research does not
modify the statistical model probability.** The AI never places trades, never recommends bets, and is
instructed not to invent injuries, weather, news, odds or prices. V1 evidence is limited to data already
in this repo (model output, saved quotes, forecasts, observations, NWS discussion, contract rules); there
is no web search, news or injury feed.

Every output is validated JSON (summary, supporting/contradictory evidence, risk factors, important
changes, unknowns, `research_confidence`), with each statement labelled FACT, INFERENCE or UNKNOWN. A FACT
without valid evidence ids is relabelled UNKNOWN. Output that claims the weather settlement transfer is
validated is rejected.

Install and run Ollama on Windows:

```powershell
winget install Ollama.Ollama      # or the installer from https://ollama.com/download
ollama pull qwen3:8b
ollama serve                      # skip if the Ollama tray app is already running
```

Settings (process environment, or a `.env` file in the repo root; process environment wins):

```text
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
AI_TIMEOUT_S=180
AI_NUM_CTX=8192             # Ollama's default 4096 silently truncates the ~3k-token prompt
AI_MAX_OUTPUT_TOKENS=1500   # hard stop; an answer cut off here is reported as an error
AI_MIN_EDGE=0.05            # markets below this edge are not eligible for AI research
AI_RESEARCH_MAX_AGE_S=21600 # cached research older than this is shown as stale
AI_AUTO_RESEARCH=false      # off by default; when true, top AI_AUTO_TOP_N eligible markets
AI_AUTO_TOP_N=5
```

Speed depends on hardware. With a GPU a research call takes seconds; on CPU only (check `ollama ps`, column
PROCESSOR) a 4B model took about 5 minutes per call here and an 8B model is slower, so raise
`AI_TIMEOUT_S` (for example `900`) or use a smaller model such as `OLLAMA_MODEL=qwen3:4b-instruct`.

Set `AI_PROVIDER=none` to disable AI entirely; everything else keeps working. If Ollama is not running,
times out, or the model is missing, the research endpoint returns `status: "unavailable"` with the reason
(for a missing model: `ollama pull <model>`) and the page keeps working.

Research is manual: open an opportunity and press **Research with AI** (or **Refresh AI Research**).
Results are cached under `data/cache/ai_research/` keyed by a fingerprint of the probability, quote,
timestamps, warnings, evidence, provider, model and prompt version. A cached result is reused while the
fingerprint matches and it is younger than `AI_RESEARCH_MAX_AGE_S`; otherwise it is shown as stale (with
a "data changed" flag when the inputs differ) until refreshed. Research never runs on page load.

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
npm run lint
npm run build   # tsc -b + vite build
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

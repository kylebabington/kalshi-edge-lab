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

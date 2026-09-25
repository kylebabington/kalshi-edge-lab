# Kalshi Edge Lab

Research tools for Kalshi prediction markets.

This repository currently contains two related but separate tracks:

1. **NYC weather research** — live market display and historical GFS forecast skill for `KXHIGHNY`
2. **Market efficiency research** — whole-Kalshi historical price-calibration / taker-efficiency study

Neither track places orders or uses account credentials. Public market data only.

---

## NYC weather (existing)

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

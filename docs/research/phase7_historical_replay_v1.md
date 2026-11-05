# Phase 7 historical recovery (separate from the frozen prospective protocol)

SHADOW ONLY / RESEARCH_ONLY / NO_BET.

This study reconstructs missed Phase 7 checkpoints from archives. It is **not**
the Phase 7 prospective cohort. It never modifies the frozen protocol, receipts,
prediction records, original evidence, scores, or primary evaluation.

Outputs live only under `data/weather/phase7_historical_replay/`.

## What is frozen and untouched

- `research/weather/hypotheses/phase7_prospective_protocol_v1.json`
- `data/weather/phase7/records|receipts|evidence|scores|diagnostics|dry_run`
- `data/weather/phase7/fetch_log.jsonl` and `outcomes_ledger.jsonl`
- `data/weather/phase7/frozen_pools_v1.csv`
- `--phase7-report` primary evaluation

A missed checkpoint stays `MISSED` with
`note: "not reconstructed from historical downloads"`.

## Priority

- **Primary recovery:** `d0_0600`, `d0_1200`, `d0_1500`
- **Secondary:** `dminus1_1800`
- **Structurally unavailable:** `d0_0900` stays unpaired wherever the frozen
  method requires `CALIBRATION_INSUFFICIENT_DETERMINISTIC` or an incomplete
  selected HRRR run. An older complete HRRR run is never substituted.

## As-of classes

| Class | Meaning |
|---|---|
| `as_of_rule_gated` | Run init + frozen latency, or obs time + assumed 20 min lag |
| `archive_retrieved_now` | Open-Meteo / IEM payload downloaded at reconstruction time |
| `post_settlement_market` | Kalshi historical settled markets (buckets/event id) |
| `current_open_unverified_as_of` | Current open markets when historical settled data are absent |
| `unverifiable_live_availability` | In-window fetch success and live evidence bytes cannot be proven |

## Commands

```powershell
.venv\Scripts\python weather_model.py --phase7-historical-replay-inventory
.venv\Scripts\python weather_model.py --phase7-historical-replay-probe
.venv\Scripts\python weather_model.py --phase7-historical-replay-report
# Full reconstruction is blocked unless the probe set archives_retrievable=true.
.venv\Scripts\python weather_model.py --phase7-historical-replay
```

Inventory and probe may write the replay tree. They never write under
`data/weather/phase7/`.

## Agreement

Captured checkpoints are reconstructed from **separately retrieved** archive
data (not the live evidence files) and compared on selected runs, probability
vectors, observation completeness, and paired validity. Mismatches are reported;
they are not written back into Phase 7.

## Limitations

See `research/weather/hypotheses/phase7_historical_replay_v1.json`. Reconstructed
scores are labeled `HISTORICAL_REPLAY_DIAGNOSTIC` and are not a protocol
assessment.

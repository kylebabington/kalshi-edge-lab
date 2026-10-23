# ITF singles challenger capture v1

**As of 2026-10-23T01:27:20Z.** RESEARCH_ONLY / NO_BET. Descriptive only: no profitability, promotion, or executable-edge claim.

Isolated manual capture for the frozen ITF Elo challenger (`k=48`, `carry=0`) on `k_kxitfmatch` and `k_kxitfwmatch`. The frozen helper, study protocol, live capture_v2 records, scheduled collector, scoring, and weather files were not modified.

Capture wrapper protocol: [`research/sports/challenger/protocol_itf_singles_capture_v1.json`](../../research/sports/challenger/protocol_itf_singles_capture_v1.json) frozen_at `2026-10-23T01:20:11.801041Z`. Parent challenger freeze remains `2026-10-23T00:34:18.095413Z`.

## Commands

```
python -m research.sports.challenger.capture freeze
python -m research.sports.challenger.capture verify
python -m research.sports.challenger.capture upcoming [--hours 24]
python -m research.sports.challenger.capture dry-run
python -m research.sports.challenger.capture window
python -m research.sports.challenger.capture settle
python -m research.sports.challenger.capture report
```

`freeze` writes the wrapper protocol once and pins capture-code, report-code, challenger, live, and Phase 1/2 hashes. `window` refuses if those pins no longer match. There is no scheduler hook. Missed windows are not back-filled.

## Dry-run writes

Dry-run creates **no** prediction, exclusion, or settlement records.

Discovery uses the same byte-exact `Fetcher` evidence cache as a genuine run. Those cache writes go to `data/cache/sports/raw_challenger_itf_v1/dry-run/<run_id>/` (gitignored). Do not describe dry-run as “no writes.”

## Evidence and as-of history

1. Complete the bounded fetches (milestones, event markets, settled history, candles).
2. Then freeze `prediction_as_of`. Filter result **release** times and **local receipt** times against that timestamp. Use the same timestamp as the market-quote cutoff.
3. Men’s and women’s pools stay separate.
4. Call the unchanged helper as `CH.predict(events, queries, params)` with queries `(key, participant_uuid, opponent_uuid, start_timestamp)`. `k=48` and `k=24` run on that identical filtered list.
5. The helper internally queries at start−60 minutes. If a remaining row would apply a release from `(prediction_as_of, start−60]`, the event is stopped with an explicit unavailable/exclusion reason. The helper is not patched.
6. Immediately before persist, read the clock again. If `recorded_at` would fall outside `[start−80min, start−60min]`, write an exclusion. `recorded_at` is never backdated.

Horizons on each record:

- `horizon_minutes = (start − prediction_as_of) / 60`
- `recorded_horizon_minutes = (start − recorded_at) / 60`
- `nominal_horizon_minutes = 60`

A 60–80 minute capture is not labelled `horizon_minutes=60`.

Frozen pinned history files are treated as already received once their hashes match the frozen pins. Live settled-market pages use their evidence `received_at`.

## Immutable records

Predictions live under `data/sports/challenger/itf_singles_v1/predictions/` (gitignored), not `capture_v2`. Publication is a complete temporary file, fsync, then atomic no-overwrite link (`O_EXCL` fallback). Incomplete or checksum-mismatched files are detected and are not treated as captures; retries refuse to overwrite them. Settlements use the same rule in a separate directory.

Each prediction stores candidate id, frozen protocol hash, challenger and capture code hashes, imported dependency hashes, event identity, ticker, participant/opponent UUIDs, contract direction, the three horizon fields, evidence hashes and receipt times, history counts, challenger p, paired-frozen p, market p, candle timestamp and age, and missing-input reasons.

`paired_frozen_p` is k=24 on the same as-of history. It is not assumed equal to a live `capture_v2` frozen p; a live value is copied only when that accepted record exists, and `live_frozen_matches_paired` is set only after a numeric check.

## Reporting

```
python -m research.sports.challenger.capture report
```

Include a prediction in prospective metrics only when:

- `prediction_as_of` and `recorded_at` are after the challenger freeze,
- the record is inside the 60–80 minute window,
- capture-time evidence shows the outcome was not yet available.

A later `settle` fetch does **not** prove unavailability at capture time. Eligibility is `verified`, `excluded`, or `cannot_verify`. Missing essential timing evidence does not enter metrics.

Unknown/void/nonbinary settlements are counted separately and stay out of binary event-equal Brier. Challenger−paired-frozen and challenger−market comparisons use their own common sets and print denominators separately. No promotion, profitability claim, or new significance gate.

## First capture attempt

Dry-run at **2026-10-23T01:21:50Z**: 22 ITF events were in the discovery lookahead; **none** were inside the 60–80 minute window. Evidence cache writes only.

Genuine `window` at **2026-10-23T01:25:33Z**: same blocker. No prediction, exclusion, or settlement record was written. No prospective example was manufactured.

The 02:00Z-start matches had already passed cutoff (01:00Z). The next still-open window starts **2026-10-23T02:10:00Z** (Kalshi start 2026-10-23T03:30:00Z; Janvier vs Karthik and three other `k_kxitfmatch` events).

Exact next command, when that window is open:

```
python -m research.sports.challenger.capture window
```

List later windows with:

```
python -m research.sports.challenger.capture upcoming
```

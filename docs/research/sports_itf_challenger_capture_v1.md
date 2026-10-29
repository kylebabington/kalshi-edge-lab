# ITF singles challenger capture

**As of 2026-10-29T01:02:06Z.** RESEARCH_ONLY / NO_BET. Descriptive only: no profitability, promotion, or executable-edge claim.

Isolated manual capture for the frozen ITF Elo challenger (`k=48`, `carry=0`) on `k_kxitfmatch` and `k_kxitfwmatch`. The frozen helper, study protocol, live `sports_phase2_capture_v2` records, scheduled collector, scoring, and weather files were not modified.

`CAPTURE_PROTOCOL` in `research/sports/challenger/constants.py` remains `sports_phase2_capture_v2` (file hash `6fce0fac0623241f373ec2f277c167030075d2a8af73bf1540681f56c5fb41ad`) so `inputs.py` still resolves the original live capture protocol. Isolated capture uses separate names.

## Wrapper versions

| Wrapper | Protocol id | Protocol file | frozen_at | file sha256 |
|---|---|---|---|---|
| v1 (frozen predecessor) | `sports_itf_singles_challenger_capture_v1` | [`protocol_itf_singles_capture_v1.json`](../../research/sports/challenger/protocol_itf_singles_capture_v1.json) | `2026-10-23T01:20:11.801041Z` | `414f259d1a86e5256c87844147e9b1d094416260ea130fc41d3fb6abd448b0d9` |
| v2 (active CLI) | `sports_itf_singles_challenger_capture_v2` | [`protocol_itf_singles_capture_v2.json`](../../research/sports/challenger/protocol_itf_singles_capture_v2.json) | `2026-10-29T00:56:48.885761Z` | `118822c8ee0285ecd80a3360060be1d0537e132c44a8c1667ae8ba945d097c85` |

Parent challenger freeze remains `2026-10-23T00:34:18.095413Z`. Helper code hash `8ffda605555b1aeb962a6e491975d3c0ff18ff61e7ec2235474c8f798b2a79b3`. Helper protocol hash `ca9c4389b781d4dd204e59fdcf636527bc4e7f0cf726695ffc3f89027a03feeb`.

v2 is a successor wrapper. It does not overwrite v1. Historical v1 predictions are verified against the v1 protocol file, never against today's wrapper code hash. A v2 settlement fetch does not relabel a v1 prediction as v2.

## Commands

```
python -m research.sports.challenger.capture freeze
python -m research.sports.challenger.capture verify
python -m research.sports.challenger.capture upcoming [--hours 24]
python -m research.sports.challenger.capture dry-run
python -m research.sports.challenger.capture window
python -m research.sports.challenger.capture settle
python -m research.sports.challenger.capture report
python -m research.sports.challenger.capture report --output path.json
```

`freeze` writes the **v2** wrapper protocol once and pins capture-code (`history.py`, `records.py`, `capture.py`, `constants.py`, `outcomes.py`), report-code, challenger, live, and Phase 1/2 hashes. It also pins the predecessor v1 protocol hash. `window` / `settle` refuse if those pins no longer match. There is no scheduler hook. Missed windows are not back-filled.

`report` prints only. `--output` is optional. Report does not fetch and does not write prediction or settlement files.

## Dry-run writes

Dry-run creates **no** prediction, exclusion, or settlement records.

Discovery uses the same byte-exact `Fetcher` evidence cache as a genuine run. Those cache writes go to `data/cache/sports/raw_challenger_itf_v1/dry-run/<run_id>/` (gitignored). Do not describe dry-run as “no writes.”

## Evidence and as-of history

Capture-time eligibility is unchanged across wrapper versions. Settlement observations supply outcomes; they do not establish eligibility.

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

Predictions live under `data/sports/challenger/itf_singles_v1/predictions/` (gitignored), not `capture_v2`. Publication is a complete temporary file, fsync, then atomic no-overwrite link (`O_EXCL` fallback). Incomplete or checksum-mismatched files are detected and are not treated as captures; retries refuse to overwrite them.

Each prediction stores candidate id, frozen protocol hash, challenger and capture code hashes, imported dependency hashes, event identity, ticker, participant/opponent UUIDs, contract direction, the three horizon fields, evidence hashes and receipt times, history counts, challenger p, paired-frozen p, market p, candle timestamp and age, and missing-input reasons.

`paired_frozen_p` is k=24 on the same as-of history. It is not assumed equal to a live `capture_v2` frozen p; a live value is copied only when that accepted record exists, and `live_frozen_matches_paired` is set only after a numeric check.

## Settlement observations

v1 `settle()` wrote one exclusive settlement file even when the lookup was pending or failed, then skipped forever. That path is retired. Existing v1 settlement files under `settlements/` are never overwritten.

v2 writes one immutable observation per `attempt_id` under `settlement_observations/`. Retry unresolved contracts. A later response may include already-resolved contracts; those observations are kept too.

Provenance on every observation:

- `prediction_record_sha256`
- `prediction_protocol_id` and `prediction_protocol_hash` copied from the prediction
- `observation_writer_protocol_id` and `observation_writer_protocol_hash` identifying the writer (v2)

A hash mismatch is rejected or quarantined. It is not scored. The prediction is not relabelled.

`observed_at` is the local lookup clock. `attempt_id` plus `observed_at` give deterministic listing order. Conflicts are **not** resolved by order: they are derived from all applicable terminal evidence. Legacy `settled_at` remains the v1 lookup timestamp (`legacy_settled_at`); it is not reinterpreted as an exchange settlement time. Exchange time, if present on the market payload, is stored as `exchange_settled_at` on the contract row.

### Terminal vs retryable

Vocabulary is the existing scoring set in `research.sports.scoring.config`.

- Retryable: `PENDING`, `LOOKUP_FAILED`, `NOT_FOUND`, `INCONSISTENT`, and `MISSING`
- Terminal: `SETTLED_YES`, `SETTLED_NO`, `NONBINARY`, `VOID_OR_CANCELLED`
- Review: `CONFLICTING`, `CONFLICTING_DUPLICATE`

Missing, failed, and pending observations cannot erase a terminal result. Consistent terminal observations retain that result. Incompatible terminal outcomes produce `CONFLICTING` and exclude the affected contract from metrics. `CONFLICTING` is a review state, not an endlessly retried pending result. `settle` fetches only while the event still has unresolved (retryable) contracts.

## Reporting

```
python -m research.sports.challenger.capture report
```

Include a prediction in prospective metrics only when:

- `prediction_as_of` and `recorded_at` are after the challenger freeze,
- the record is inside the 60–80 minute window,
- capture-time evidence shows the outcome was not yet available,
- the prediction’s hashes match **its own** frozen wrapper protocol.

A later `settle` fetch does **not** prove unavailability at capture time. Eligibility is `verified`, `excluded`, or `cannot_verify`. Missing essential timing evidence does not enter metrics.

Unknown/void/nonbinary settlements are counted separately and stay out of binary event-equal Brier. `CONFLICTING` contracts are counted separately and stay out of Brier.

Counts are printed by wrapper version. Combined metrics **do** pool wrapper versions (`combined_pools_wrapper_versions=True`); per-version blocks do not. Challenger−paired-frozen and challenger−market comparisons use their own common sets and print `n_events` and `n_contracts` separately. No promotion, profitability claim, or new significance gate.

## First capture attempt (2026-10-23)

Dry-run at **2026-10-23T01:21:50Z**: 22 ITF events were in the discovery lookahead; **none** were inside the 60–80 minute window. Evidence cache writes only.

Genuine `window` at **2026-10-23T01:25:33Z**: same blocker. No prediction, exclusion, or settlement record was written. No prospective example was manufactured.

The 02:00Z-start matches had already passed cutoff (01:00Z). Zero eligible was operational, not a performance result.

## Current-clock exercise (2026-10-29)

Runtime clock ~`2026-10-29T00:56Z`–`01:02Z`. Pins verified (`verify sports_itf_singles_challenger_capture_v2: OK`). `freeze` reported `protocol_itf_singles_capture_v2.json unchanged`.

An open window `[2026-10-29T00:40:00Z, 2026-10-29T01:00:00Z]` existed for 02:00Z starts. Genuine `window` ran first so that window would not expire:

- `window` 2026-10-29T00:57:48Z–00:58:11Z: `in_window=8 predictions=8 exclusions=0`
- 3 `k_kxitfmatch`, 5 `k_kxitfwmatch`; 16 contracts
- `horizon_minutes` 61.811–62.197 (not labelled 60)
- protocol `sports_itf_singles_challenger_capture_v2`, protocol hash `118822c8ee0285ecd80a3360060be1d0537e132c44a8c1667ae8ba945d097c85`
- `settle` 2026-10-29T00:58:22Z–00:58:27Z: 8 observations, `skipped_resolved=0`, all contract states `PENDING`, no `settled_at` field, prediction hashes matched, writer protocol v2
- `report` (print only): wrapper versions present `['sports_itf_singles_challenger_capture_v2']`; combined pools wrapper versions=`True`; recorded events=8 contracts=16; eligibility verified=8; pending=8; binary=0; conflicting=0; quarantined=0; Brier denominators `n_events=0 n_contracts=0`; `report.json` was not written
- `dry-run` 2026-10-29T00:59:28Z–00:59:56Z: `in_window=8 predictions=0 exclusions=0 dry_run=True`; existing complete predictions were not overwritten; evidence cache only

Zero binary settlements is operational: the matches had not finished. Retry `settle` later. Do not treat pending as a performance result.

`upcoming` at 2026-10-29T01:00:56Z: next still-open window opens **2026-10-29T01:10:00Z / 2026-10-28 21:10:00 EDT (America/Indiana/Indianapolis)** (cutoff 2026-10-29T01:30:00Z / 2026-10-28 21:30:00 EDT; `k_kxitfwmatch` Chen vs Zhang).

Exact next capture command, when that window is open:

```
python -m research.sports.challenger.capture window
```

List later windows with:

```
python -m research.sports.challenger.capture upcoming
```

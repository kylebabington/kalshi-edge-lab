# Phase 7 operational note: evidence line endings (2026-10-03)

## Summary

Before the fix, Phase 7 saved fetched response text with `Path.write_text(..., encoding="utf-8")`.
On Windows that call translates every `"\n"` to `"\r\n"`. The recorded `response_sha256`
was computed beforehand from `response.text.encode("utf-8")`. Any saved response containing
newlines therefore stopped matching its recorded hash at the raw-byte level. The content was
otherwise unchanged.

Only the multi-line IEM KNYC observation CSVs (`iem_knyc.csv`) were affected. The Open-Meteo
Single Runs payloads saved so far are single-line JSON and were unaffected.

## Fix

Evidence is now saved with `path.write_bytes(raw_text.encode("utf-8"))`
(`research/weather/phase7.py:write_evidence_text`). Both fetchers hash exactly that
representation: `fetch_iem_live` and `single_run_hourly.fetch_run_hourly_logged` each compute
`hashlib.sha256(response.text.encode("utf-8"))`. The saved file is the UTF-8 encoding of the
decoded response text, not the original HTTP body bytes.

`--phase7-reproduce` now also reports `evidence_integrity` for every saved response the record
references. For each file it lists `expected_sha256`, `actual_sha256` (from `read_bytes()`),
`raw_bytes_match`, `crlf_normalized_match` (`b"\r\n"` replaced with `b"\n"`) and a status:

- `RAW_MATCH`
- `CRLF_NORMALIZED_MATCH_ONLY`
- `MISMATCH`
- `MISSING_FILE`
- `MISSING_EXPECTED_HASH`

Any non-`RAW_MATCH` file is printed to stderr. The exit code still reflects prediction
reproduction only: 0 means identical, 4 means mismatch.

The changed functions are operational and are not pinned in the protocol's `function_hashes`.
The method fingerprint
(`95b0d991f47f97068b291b93a953a8f6a4eb84099d9288fd2ae6770228e78e6d`), the protocol hash
(`cc33e71db90d0d6c069791892872f8e74d6d9dc7453e6fa5b9e1b3e79f948ae2`), the frozen pool hash
(`eb4b6e716c8b5d24ece9a2597d22a2876175648f2e5584adefe86b71b633c4bb`) and the source CSV
hashes are all unchanged.

## Affected historical records

These are the records captured before the fix. They are kept as written: no record, receipt or
evidence file was edited.

| Record | Prediction reproduction | Raw-byte evidence check | CRLF-normalized check |
|---|---|---|---|
| `2026-10-03/dminus1_1800` | identical | 3/3 RAW_MATCH | n/a |
| `2026-10-03/d0_0600` | identical | 3/4: `iem_knyc.csv` FAILED | `iem_knyc.csv` matched |
| `2026-10-03/d0_0900` | identical | 3/4: `iem_knyc.csv` FAILED | `iem_knyc.csv` matched |
| `2026-10-04/dminus1_1800` | identical | 3/3 RAW_MATCH | n/a |

`d0_0600` `iem_knyc.csv`:

- Recorded hash: `c8ef0d009eae8f8c788f802984ef107cbcb2379d06dfbc26ff840c5d7e575aea`
- Saved-file hash: `c38533afb014bc66d6b8a23564baf9018bfbf855564ccf3493d21e4798b5904d`
- Line endings: 11 CRLF, no bare LF

`d0_0900` `iem_knyc.csv`:

- Recorded hash: `62c16a1240dc6b241f8aed0da44578c099679e47476c2ce0eca1c94d3c4dbf99`
- Saved-file hash: `520f42a3ed4ba008430bfd6debbca1c21f803886bae1c9ed6e08e7a7fc22be6f`
- Line endings: 14 CRLF, no bare LF

To be explicit about each check:

- **Prediction reproduction passed.** Recomputing from saved evidence gives identical
  predictions and probabilities. The CSV is read back in text mode, which undoes the
  translation.
- **The original saved evidence failed raw-byte hash verification.** The bytes on disk differ
  from the bytes that were hashed at fetch time.
- **The CRLF-normalized content matched.** Replacing `\r\n` with `\n` reproduces the recorded
  hash exactly, so the observations are the ones fetched before the cutoff.

This note does not change cohort eligibility, and it does not introduce an exclusion rule.
The dry-run IEM file from 2026-10-02 shows the same translation; dry runs are never part of
the cohort.

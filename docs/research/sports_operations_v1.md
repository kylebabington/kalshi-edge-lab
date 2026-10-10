# Sports research operations (v1)

RESEARCH_ONLY / NO_BET. Operational rules for the sports pipeline. These rules do not change any
frozen protocol, forecast or result.

## Filtered runs never overwrite full protocol results

Phase 1 evaluations restricted with `--competition`, `--sport`, `--family` or `--source` are written
to a separate, deterministic subset directory:

```
data/results/sports_phase1_subsets/<slug>/<protocol>/{scored.jsonl,metrics.json,evaluation_status.json}
```

`<slug>` lists the sorted filter values, followed by the first 10 hex characters of their sha256
(for example `family=total__sport=Soccer__b5341dbc84`). The full-protocol directory
`data/results/sports_phase1/<protocol>/` and its manifest are never touched by a filtered run.
Prediction journals are write-once and are shared: a filtered run may only re-write identical bytes.
The `benchmark`, `report` and `all` stages refuse filters, because they summarize the full set.

```powershell
.venv\Scripts\python.exe -m research.sports.run evaluate --sport Soccer --family total --cache-only
```

The results documents of `sports_phase1_v1` and `sports_phase1_v2` show the earlier form of this command.
At the time, that command overwrote the full results. Those documents are frozen and keep their original
text. Use the command above instead.

## Verification: exact research artifacts, portable documents

```powershell
.venv\Scripts\python.exe -m research.sports.run verify --protocol sports_phase1_v1
.venv\Scripts\python.exe -m research.sports.run verify --protocol sports_phase1_v2
.venv\Scripts\python.exe -m research.sports.phase2.run verify --protocol sports_phase2_v1
```

- **Research artifacts** (protocol JSON, prediction journals, scored rows, numerical results) are
  checked with an exact-byte sha256 against `research/sports/protocols/<protocol>.artifacts.json`.
  They are never normalized. `research/sports/protocols/*.json` is marked `-text` in `.gitattributes`,
  so git never converts protocol or manifest files on checkout.
- **Tracked documents** (`docs/research/...`) are checked with the portable rule `portable_lf_v1`:
  read the bytes, replace every CRLF with LF and change nothing else, then sha256. The hashes are in a
  separate, versioned supplement, `research/sports/protocols/<protocol>.docs_portable_v1.json`.

### Why the supplement exists

The original v1 and v2 manifests were assembled on a Windows checkout. There, git (`core.autocrlf`)
writes the text documents with CRLF line endings, while the repository stores them with LF. An LF
checkout (Linux, or `autocrlf=input/false`) therefore has different bytes for identical content, and
the exact document hashes in the original manifests fail there.

The original manifests are preserved unchanged. The supplements were created on 2026-10-09, and only
after every listed document matched its exact hash in the original manifest. Each supplement records
the sha256 of the manifest it was derived from. Verification fails if that manifest changes or if
the document lists differ.

Both `git clone -c core.autocrlf=true` (CRLF) and `core.autocrlf=false` (LF) checkouts verify. This is
covered by `tests/test_sports_ops.py`.

## Code hashes and line endings

The Phase 1 code hash (`evaluate.CODE_FILES`) and the Phase 2 code hash (`phase2/pipeline.CODE_FILES2`)
are sha256 values over the working-copy bytes of the sports Python files. Those bytes were CRLF when the
protocols were frozen. `.gitattributes` therefore checks out `research/sports/*.py` and
`research/sports/phase2/*.py` with `eol=crlf` on every platform. As a result, a fresh clone reproduces
both pinned hashes, and the cache-only `evaluate` stages accept the frozen protocols.

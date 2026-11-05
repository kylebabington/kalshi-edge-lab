# Storage policy and disk protection (v1)

RESEARCH_ONLY / NO_BET. Operational storage rules for Kalshi Edge Lab. This document does **not** change any frozen experimental protocol, model, prediction journal, or evaluation method.

## Why this exists

The 2026-11-02 sports integrity audit showed that treating sha256-pinned capture bodies under `data/cache/sports/raw_capture_v2/` as a disposable HTTP cache destroyed irreplaceable evidence (Oct 23–29 unrestorable). Disk exhaustion also interrupted collectors. This policy separates **immutable evidence** from **reconstructable caches** and makes cleanup refuse the former by default.

## Classification

| Class | Meaning | Auto-delete? |
|---|---|---|
| `immutable_evidence` | Write-once / sha256-pinned capture, settlement, Phase 7 evidence, normalized history, protocols, prediction journals | **Never** |
| `append_only_logs` | Collector cycles, prospective logs, Phase 7 ledgers | **Never** via cleanup |
| `reconstructable_http_cache` | Regenerable HTTP responses (`data/cache/sports/raw/`, weather model caches, inventory/candles, …) | Eligible after dry-run + confirm |
| `temporary` | `*.tmp`, backup staging, pytest residue under the repo | Eligible after dry-run + confirm |
| `generated_report` | Regenerable results under `data/results/` | Not cleaned by default |
| `unknown` | Anything else | Protected (treated like immutable) |

Immutable sports evidence roots:

- `data/cache/sports/raw_capture_v2/`
- `data/cache/sports/raw_settlement_v1/`
- `data/cache/sports/raw_challenger_itf_v1/`
- `data/cache/sports/evidence_blobs_v1/`
- `data/sports/phase2/capture_v2/` and `capture_v2_scores/`
- `data/sports/normalized/`, `crosswalk/`, `predictions/`

Immutable weather evidence roots:

- `data/weather/phase7/{records,receipts,evidence,scores,pending_scores,diagnostics}/`
- `data/weather/phase7_historical_replay/`
- `data/weather/checkpoints/`, `snapshots/`, `snapshot_scores/`, `calibration/`

## Commands

Run from the repo root with the project venv:

```powershell
.venv\Scripts\python.exe -m research.storage.run policy
.venv\Scripts\python.exe -m research.storage.run health --write
.venv\Scripts\python.exe -m research.storage.run roots
.venv\Scripts\python.exe -m research.storage.run p0-tmp
.venv\Scripts\python.exe -m research.storage.run cleanup
.venv\Scripts\python.exe -m research.storage.run migrate-evidence --batch-size 5
.venv\Scripts\python.exe -m research.storage.run migrate-evidence --execute --batch-size 5
.venv\Scripts\python.exe -m research.storage.run migrate-evidence --retirement-report
.venv\Scripts\python.exe -m research.storage.run cas-stats
.venv\Scripts\python.exe -m research.storage.run savings
.venv\Scripts\python.exe -m research.storage.run report --out data/logs/storage/last_report.json
```

Archive configuration precedence: CLI `--archive-dir` → `KALSHI_ARCHIVE_DIR` →
`archive_dir` in `data/logs/storage/storage_policy.local.json` → unset (clear error).

```powershell
.venv\Scripts\python.exe -m research.storage.run configure-archive D:\kalshi-archive
.venv\Scripts\python.exe -m research.storage.run build-inventory
.venv\Scripts\python.exe -m research.storage.run migrate-evidence --execute --until-done --batch-size 8
.venv\Scripts\python.exe -m research.storage.run verify-archive --mode auto
.venv\Scripts\python.exe -m research.storage.run retirement-proposal
```

Active captures stay on C:; migration writes blobs only to the archive volume. Mode B verification uses the permanent inventory and does **not** require local `.body` files.

### Content-addressed sports evidence (new writes)

New bodies under `raw_capture_v2` / `raw_settlement_v1` / `raw_challenger_itf_v1` are stored once under `data/cache/sports/evidence_blobs_v1/` (gzip when smaller). Per-run `.meta.json` receipts keep timestamps and provenance. Legacy `{key}.body` files remain readable. Dedup never replaces a live network capture — only identical received bytes share a blob.

### Regenerable cache budget

Defaults: **10 GiB** global regenerable cap (`KALSHI_STORAGE_MAX_CACHE_GB`) and **7-day** retention (`KALSHI_STORAGE_CACHE_RETENTION_DAYS`). Immutable evidence is excluded. Optional roots: `KALSHI_DATA_DIR`, `KALSHI_CACHE_DIR`, `KALSHI_ARCHIVE_DIR`.

### Cleanup safety

- **Dry-run is the default.** No files are deleted unless both `--execute` and `--confirm DELETE` are passed.
- Before/after free space is recorded on the plan.
- Every candidate path is listed.
- Paths classified immutable / append-only / unknown are never candidates.
- Path classification is the primary protection (immutable prefixes are never candidates). Pass `--strict-refs` to also scan capture/score records for pinned evidence paths (slower; defense in depth).
- Destructive cleanup **refuses while a sports or weather collector lock is held** (does not interrupt active collection).
- Optional overrides: `data/logs/storage/storage_policy.local.json` (warning/critical bytes, per-cache age and size caps).

```powershell
# Review only
.venv\Scripts\python.exe -m research.storage.run cleanup

# After reviewing the candidate list — and only when collectors are idle:
.venv\Scripts\python.exe -m research.storage.run cleanup --execute --confirm DELETE
```

## Disk-health monitoring

`health` reports free space, warning/critical level, largest reconstructable cache directories (time-budgeted walks), estimated growth bytes/day from `data/logs/storage/disk_usage_history.jsonl`, and collector lock state.

Defaults:

- Warning below **10 GiB** free
- Critical below **2 GiB** free

Sports collector status (`data/logs/sports/collector_status.json`) includes a `disk_health` snapshot. When the level is `critical`, a new sports cycle **skips starting** capture and settlement (exit code 4) so evidence writes are not attempted on a full volume. Weather `post_cycle` also records `disk_health` beside the backup result.

## Cache write safety

Sports `Fetcher` writes body + `.meta.json` through an atomic temp+replace pair (`research.storage.write.write_cache_pair`):

- On disk-full / ENOSPC, temps are removed and `DiskFullError` is raised; no partial body is left as a cache hit.
- Under evidence roots (`raw_capture_v2`, `raw_settlement_v1`, `raw_challenger_itf_v1`), writes are **write-once**: different bytes are refused; identical bytes are a no-op.
- A failed meta replace rolls back the body so `cached()` cannot serve a body without matching meta.

This prevents a single failed cache write from silently corrupting pinned evidence or producing an invalid prediction from partial bytes.

## Recovery procedures

1. **Free space without touching evidence.** Prefer `%TEMP%\pytest-of-kyleb` and repo `*.tmp` / reconstructable caches via dry-run cleanup. Never delete `raw_capture_v2` or `raw_settlement_v1` to make space.
2. **Restore sports capture evidence** only from a legitimate byte-identical source (local `~/.kel/sports-state-backup-v2` clone for runs through 2026-10-22). Copy into the live evidence root; verify sha256 against the capture record; replay one record before bulk settlement.
3. **Do not fabricate** missing historical HTTP bodies. Unrestorable windows stay as records that refuse replay.
4. **Quarantine 0-byte JSON** under attempts/ (move aside) so settlement `scan()` does not crash; document them as truncated originals rather than rewriting capture content.
5. After restore and disk headroom, resume collectors. Re-run `python -m research.storage.run health --write`.

## What this does not change

- Frozen protocols under `research/sports/protocols/` and weather Phase 7 protocol bytes
- Phase 1 / Phase 2 / live capture code hashes for evaluation
- Prediction journals, accepted capture records, finalized scores
- RESEARCH_ONLY / NO_BET / SHADOW ONLY posture

## Implementation map

| Module | Role |
|---|---|
| `research/storage/policy.py` | Path classification |
| `research/storage/config.py` | Retention and thresholds (10 GiB / 7 d defaults) |
| `research/storage/write.py` | Atomic / write-once body+meta writes |
| `research/storage/cas.py` | Content-addressed evidence blobs + legacy/archive read |
| `research/storage/migrate_evidence.py` | Batched historical migrate to archive CAS (no source delete) |
| `research/storage/cas_stats.py` | CAS vs legacy effectiveness measurements |
| `research/storage/roots.py` | `KALSHI_*_DIR` roots + archive availability |

### Evidence consumers (compatibility)

Readers resolve evidence in order: legacy `.body` → local CAS → archive CAS (when `KALSHI_ARCHIVE_DIR` available). Call sites: `Fetcher.cached`, `SavedEvidence`, `replay.check_evidence`, settlement scoring, challenger `inputs`, `service._evidence_body`. Missing archive when only an archive copy remains raises a clear error (never silent success).
| `research/storage/refs.py` | Required evidence path set |
| `research/storage/cleanup.py` | Dry-run default cleanup planner (includes inventory `*.tmp`) |
| `research/storage/health.py` | Disk health assessment |
| `research/storage/run.py` | CLI |
| `tests/test_storage.py` | Cleanup safety, CAS, expiration, disk-full, protected evidence |

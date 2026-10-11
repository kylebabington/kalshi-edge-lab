# Sports UI, read-only API and 5-minute collector (RESEARCH_ONLY / NO_BET)

This document covers operations only. It changes no protocol, model, threshold, capture rule or scoring
rule. Captures still follow the frozen `sports_phase2_capture_v2` protocol and scores the frozen
`sports_capture_v2_scoring_v1` protocol; historical results stay under `sports_phase1_v1`,
`sports_phase1_v2` and `sports_phase2_v1`. No trades, no recommendations.

## Start the UI

```powershell
# API (from the repo root)
.\.venv\Scripts\python.exe -m uvicorn api.app:app --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev          # http://localhost:5173 ; /api is proxied to :8000
```

Mock mode (`$env:VITE_USE_MOCK="true"; npm run dev`) serves fixtures labeled `MOCK FIXTURE`. Normal mode never
falls back to mock data; missing artifacts are shown as missing.

## What populates each view

| View | Local artifacts read (never written) |
| --- | --- |
| Dashboard sports panel, `/sports` Events | `data/sports/phase2/capture_v2/accepted/checkpoint/<comp>/*.json` (accepted WINDOW_CAPTURE and MISSED records), `data/sports/phase2/capture_v2_scores/finalized/**` and the latest `capture_v2_scores/runs/*.json` (pending settlement states) |
| Event titles, contract descriptions | saved evidence of the capture run: `data/cache/sports/raw_capture_v2/<run_id>/kalshi/**` (milestone pages and the event's own market listings, hash-checked). Absent evidence → "unavailable", never refetched |
| `/sports/events/:eventId` | the accepted record, its finalized scores, the latest settlement run, saved evidence (integrity check) and `research/sports/protocols/sports_phase2_capture_v2.json` (pin comparison) |
| Results | finalized scores with a valid `score_sha256`, metrics computed with the frozen evaluator formulas |
| Coverage | tracked `docs/research/sports_capture_v2_series_matrix.csv`, `sports_capture_v2_matrix.csv`, `sports_inventory_v1.csv`, plus availability observed in saved captures |
| Historical Research | tracked `docs/research/sports_phase1_results_v1.csv`, `_v2.csv`, `sports_phase2_results_v1.csv`, checked against the tracked docs manifests; local `data/results/**/metrics.json` checked against the artifact manifests when present ("tracked summary only" otherwise) |
| Collector status | `data/logs/sports/collector_status.json`, `collector_cycles.jsonl`, `cycle.lock` (passive read) and `schtasks /Query` (passive; "unknown" when unreadable) |

Responses never include filesystem paths, credentials or complete evidence payloads. Event ids are
`competition|cluster`: one sports event, many binary contracts.

## The collector

Task `KalshiEdgeLab-SportsProspective` runs `scripts\sports_prospective_task.ps1` every 5 minutes. The
wrapper runs this repo's `.venv\Scripts\python.exe -m research.sports.ops.collector cycle` from the repo
root. One cycle, holding `data\logs\sports\cycle.lock`:

1. Scheduling discovery: one paginated Kalshi milestone listing starting 3 h back. Not evidence; nothing
   is saved.
2. Plan (`research/sports/ops/gate.py`): competitions with a supported, not-yet-accepted milestone whose
   window may be open (Kalshi start ± slack), or whose window closed and still needs one capture run to
   record MISSED. If scheduling discovery is incomplete, the capture runs for every competition.
3. The frozen window capture (`research.sports.live.capture.run("window", ...)`) for exactly those
   competitions, unchanged: its own complete discovery, saved evidence, exact window rule against the
   source start time, exclusive accepted records (an accepted record is never replaced), MISSED when the
   window has passed. Missed forecasts are never reconstructed from later downloads.
4. Settlement check (frozen scoring run) after capture work, at most hourly, deferred while a window is open
   or about to open, for at most 3 hours.

Source history: the frozen capture already reuses its pinned normalized history and refreshes only from
the last frozen result minus two days. The collector does not add a cross-run response cache, because
reusing an earlier run's bytes would misstate evidence timing and break offline replay.

Concurrency and rate limits: manual `research.sports.live.run capture` and
`research.sports.scoring.run settle` take the same lock, so manual and scheduled work never overlap
(a busy lock makes the cycle exit 0 without work; manual commands exit 3). When the holder is the state
backup, which holds the lock for seconds, the cycle first waits up to 60 s for it (`waited_for_backup_s`
in the cycle log) before skipping. A stale lock is removed only
when its owner process is confirmed gone (missing PID, or the PID now belongs to a process with a
different start time); age alone never frees it. The lock is released on failure. The frozen fetcher is
single-flight with 0.5 s spacing per host and at most 2 retries. Kalshi
(`external-api.kalshi.com`) is the only host shared with the weather collector; while the weather cycle
holds `data\logs\prospective\cycle.lock` the sports session spaces Kalshi requests to at least 1 s. The
weather task, wrapper, lock, logs and forecasting method are untouched.

Logs: `data\logs\sports\task_YYYY-MM-DD.log` (wrapper and stderr), `collector_YYYY-MM-DD.log` (cycle and
capture log), `collector_cycles.jsonl` (one JSON line per cycle with plan, capture, settlement, HTTP
statistics, duration and bottleneck), `collector_status.json` (what the UI reads), `collector_state.json`
(gate memory). Exit codes: 0 ok or lock busy, 1 error, 2 capture problem, 3 settlement problem.

Timing: a full-universe window capture took 196–300 s in earlier manual runs; the gate usually narrows a
cycle to the competitions with a window. When a capture run exceeds the headroom (20-min window − 5-min
cadence = 900 s) or the capture records timing failures, the cycle logs `BOTTLENECK` with request counts
and network time. The main cost is the frozen capture's own milestone discovery (paged from the frozen
`minimum_start_date`, growing over time) and serial 0.5 s request spacing. Timing rules are not relaxed.

Collection provenance: scheduled collection began on 2026-10-11 (first scheduled action 14:27:48Z, collector
commit `38f7134`). The frozen protocol's "manual invocation only" wording is no longer accurate; see
[operational amendment 1](sports_phase2_capture_v2_ops_amendment_1.md). Each event's collection mode
(`automatic`, `collector_manual`, `manual_pilot`) is derived from its `run_id` and the cycle log, never
written into records.

Scheduler result codes (`LastTaskResult`, shown decoded in the UI): `0` success; `1`/`2`/`3` the cycle's
own exit codes; `267009` = `0x41301` an instance is running; `267011` = `0x41303` not yet run;
`2147946720` (`-2147020576`) = `0x800710E0` `ERROR_REQUEST_REFUSED`. With `IgnoreNew` the last code is what
the scheduler records when a trigger fires while the previous run is still going; it is also used for unmet
start conditions (battery/idle), which this task does not set. The Task Scheduler operational event log is
disabled on this machine, so `python -m research.sports.ops.audit` reconstructs each trigger's outcome from
the wrapper's start/end lines instead.

### Private sports-state backup

`research/sports/ops/backup.py` keeps a separate private backup of sports state. It is not part of the
weather backup, whose allowlist, ref (`refs/heads/state`), clone and ownership marker are unchanged.

- Destination: the private state remote and SSH command already in `collector.local.json` (read only),
  separate ref `refs/heads/sports-state`, separate clone `%USERPROFILE%\.kel\sports-state-backup`.
- Allowlist (`plan` lists it): capture records (`capture_v2/{accepted,attempts,runs}`), scores
  (`capture_v2_scores/{finalized,runs}`), `research/sports/protocols/*`, every dependency file named by a
  record (checked against the record's sha256), exactly the raw evidence bodies and `.meta.json` files
  referenced by records (`raw_capture_v2`) and scores (`raw_settlement_v1`), and the collector's
  provenance logs (`collector_cycles.jsonl`, `collector_*.log`, `task_*.log`, `collector_status.json`,
  `collector_state.json`). Never: credentials or config, locks, dot/temporary files, dry runs,
  unreferenced cache bodies.
- Storage: `* -text`, `MANIFEST.json` with sha256, size and class per file. Write-once files that change
  locally, append-only logs that shrink, or files that vanish stop the backup instead of being mirrored.
- Coordination: an unlocked warm-up pass copies and hashes everything first; the final pass, which
  defines the snapshot and manifest, holds the sports lock (no capture or settlement writes mid-copy; a busy
  lock skips) and only handles files whose size or modification time changed, so the lock is held for a
  few seconds (2.4 s measured at 5,345 files). Hashes are cached by size and mtime in the clone's `.git`
  directory; `verify-restore` rehashes everything. The push waits while the weather cycle lock is held (the weather backup
  runs inside it), is never forced, and a remote with unknown commits is reported as `conflict`.
- Schedule: the task wrapper runs `backup run --if-due` after each cycle, at below-normal priority with
  single-threaded packing; it pushes at most hourly (or retries sooner after a failure). Its result goes to
  the task log and `data\logs\sports\backup_status.json` and never changes the cycle's exit code.
- Restore check: `verify-restore` clones the ref fresh with `autocrlf=true`/`eol=crlf`, verifies the
  manifest, restores into an empty temporary tree next to a copy of the tracked code, compares every file
  byte-for-byte with this repo, and runs `replay --all` and `verify-scores` inside the isolated tree with
  network access disabled by a dead proxy.

Size: about 440 MB at the first backup (4,585 files; the largest is an 85 MB normalized-history
dependency, under GitHub's 100 MB per-file limit). It grows with evidence, roughly 1–2 MB per captured
event.

### Commands

```powershell
# Register / re-register (current user, Interactive logon, Limited; no password stored)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_sports_task.ps1

Enable-ScheduledTask  -TaskName KalshiEdgeLab-SportsProspective
Disable-ScheduledTask -TaskName KalshiEdgeLab-SportsProspective

# Status
Get-ScheduledTask     -TaskName KalshiEdgeLab-SportsProspective | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName KalshiEdgeLab-SportsProspective | Select-Object LastRunTime, LastTaskResult, NextRunTime
.\.venv\Scripts\python.exe -m research.sports.ops.collector status
Get-Content data\logs\sports\collector_$((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')).log -Tail 30

# Manual run of the exact scheduled action
Start-ScheduledTask -TaskName KalshiEdgeLab-SportsProspective

# Manual cycle in a terminal (same lock), or a dry run (discovery + plan only; writes only data\logs\sports\dry_runs\)
.\.venv\Scripts\python.exe -m research.sports.ops.collector cycle
.\.venv\Scripts\python.exe -m research.sports.ops.collector cycle --dry-run

# Integrity after collection
.\.venv\Scripts\python.exe -m research.sports.live.run verify
.\.venv\Scripts\python.exe -m research.sports.live.run replay --all
.\.venv\Scripts\python.exe -m research.sports.scoring.run verify-scores

# Health audit from existing logs (read only); first results: sports_collector_health_2026-10-11.md
.\.venv\Scripts\python.exe -m research.sports.ops.audit

# Sports-state backup
.\.venv\Scripts\python.exe -m research.sports.ops.backup plan             # what would be backed up
.\.venv\Scripts\python.exe -m research.sports.ops.backup init [--create]  # once per machine
.\.venv\Scripts\python.exe -m research.sports.ops.backup run              # snapshot, commit, push
.\.venv\Scripts\python.exe -m research.sports.ops.backup status
.\.venv\Scripts\python.exe -m research.sports.ops.backup verify-restore   # isolated restore + offline replay
```

### Limits

The task uses the same account and logon approach as the weather task: it runs only while this user is
logged on, does not store a password, does not wake the computer and does not change power settings.
Sleep, hibernation, shutdown, logoff or an internet outage during an event's 20-minute window means no
capture; the next cycle records the event as MISSED. `StartWhenAvailable` starts a late cycle after a
missed trigger, but a late cycle can only record MISSED for windows that already closed. The PowerShell
action uses `-WindowStyle Hidden` like the weather task, which can briefly flash a console window.

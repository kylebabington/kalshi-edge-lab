# sports_phase2_capture_v2 — operational amendment 1: scheduled collection (2026-10-11)

Status: operational amendment. It changes how capture runs are started, not what a capture is. The frozen
protocol file `research/sports/protocols/sports_phase2_capture_v2.json`, the pinned live code
(`research/sports/live/{config,discover,sources,predict,pins,capture,replay}.py`), every saved record and
every score are unchanged. No model, parameter, candidate, window, cutoff, threshold or scoring rule
changed.

## What is no longer accurate

The frozen protocol says `"scheduler": "none (manual invocation only)"` and the docstring of
`research/sports/live/capture.py` says captures are "never scheduled". Both statements are pinned bytes
(changing them would change the protocol hash and the live code hash that every record pins), so they stay
as written, but **they no longer describe how records are collected.** Since 2026-10-11 most capture runs
are started automatically by a Windows scheduled task. Read the protocol's `scheduler` field as "the
protocol itself has no scheduler; runs are started externally". This amendment is the record of the
external starter.

## Scheduled collection

| | |
|---|---|
| Task | `KalshiEdgeLab-SportsProspective` (current user, Interactive logon, Limited run level, no stored password; weather task unchanged) |
| Cadence | every 5 minutes (trigger start boundary 2026-10-11 10:31 local = 14:31Z; `MultipleInstances=IgnoreNew`, `StartWhenAvailable`, 30-minute run limit) |
| Action | `scripts/sports_prospective_task.ps1` → `python -m research.sports.ops.collector cycle --purpose scheduled` |
| Collector commit | `38f7134` (first commit containing the collector, the wrapper and the registration script) |
| Registered | 2026-10-11 ≈14:27Z |
| First scheduled action | 2026-10-11 14:27:48Z (the task's own action, started on demand with `Start-ScheduledTask` right after registration; capture run `20261011T142751509685Z`) |
| First trigger-initiated run | 2026-10-11 14:41:41Z (capture run `20261011T144150296640Z`) |

Each cycle runs the frozen window capture, unchanged, for the competitions its gate selects, and the frozen
settlement scoring at most hourly. A cycle never retries a window after its cutoff and never reconstructs a
missed forecast; the exact window rule, write-once accepted records, evidence capture and offline replay
are the frozen protocol's own.

## Distinguishing manual pilot and automatic captures

Records are not rewritten and carry no new field. The collection mode is derived from the record's existing
`run_id` and the collector's append-only cycle log `data/logs/sports/collector_cycles.jsonl`
(`research/sports/ops/provenance.py`):

| Mode | Rule |
|---|---|
| `automatic` | a collector cycle with purpose `scheduled` started this `run_id` |
| `collector_manual` | a collector cycle started by hand (`collector cycle` without `--purpose scheduled`) |
| `manual_pilot` | no collector cycle started the run (direct `research.sports.live.run capture`) |

Every capture before 14:27:48Z on 2026-10-11 is `manual_pilot`. The capture runs at the time of this
amendment:

| Mode | Capture runs |
|---|---|
| manual_pilot | `20261010T122851133298Z`, `20261010T124155035259Z`, `20261010T154238574921Z`, `20261011T122524791039Z` |
| automatic | every run listed in `collector_cycles.jsonl` with `purpose: scheduled`, starting with `20261011T142751509685Z` |

All settlement runs so far (`capture_v2_scores/runs/`) were started by hand; the collector's first
settlement run will be recorded in the cycle log and classified the same way.

The UI and API report the mode per event (`collection_mode`, filter `?collection=automatic|manual_pilot|
collector_manual`), in event provenance (`provenance.collection`) and as counts on the dashboard
(`/api/sports/summary` → `collection.by_mode`). Results tables are not split by mode: the capture
procedure is identical in both modes, and the pilot is too small for per-mode metrics to mean anything.

Note on MISSED records written by automatic runs: the first automatic cycle reconciled windows that had
already closed before scheduling began (23 MISSED records with cutoffs before 14:27:48Z). Those were not
missed by the collector, but their `run_id` is automatic. `python -m research.sports.ops.audit` separates
them (`expired_windows.cutoff_before_automatic_collection_began`).

## Backup

From the same date, sports state is also backed up privately (`research/sports/ops/backup.py`; see
`docs/research/sports_collector_v1.md`). The backup copies records byte-for-byte and does not alter them.

# Sports collector health — first observation, 2026-10-11

RESEARCH_ONLY / NO_BET. Produced from existing logs by `python -m research.sports.ops.audit` (read-only)
at 2026-10-11T15:58:29Z. **Only 1.4 hours of scheduled operation exist** (first automatic cycle
14:27:48Z, last finished cycle 15:51:36Z); this is not a 24-hour record and says nothing about overnight,
sleep/resume or heavy-slate behavior.

## Cycles (`data/logs/sports/collector_cycles.jsonl`, `task_*.log`)

| | |
|---|---|
| Cycles | 14: 13 ok, 1 skipped (lock busy), 0 errors; every exit code 0 |
| Cycle duration | min 0.1 s, median 87.6 s, p90 370.5 s, max 563.2 s |
| Capture stage | min 14.2 s, median 90.9 s, p90 343.1 s, max 559.8 s |
| Wrapper wall time | median 97.2 s, max 572.5 s (one run still in progress at audit time) |
| Capture problems / timing failures / bottlenecks | 0 / 0 / none |
| HTTP | Kalshi 2196 requests, 1 error; ESPN 35 requests, 0 errors |

The 563 s maximum is the first cycle (14:27:48Z), which worked through a backlog of 59 competitions.

**Lock skip.** The single skip (15:51:36Z) was caused by a *manual* state-backup run that held the sports
lock for 2.4 s from 15:51:34Z. Scheduled backups run after the cycle in the same wrapper and cannot collide
with it. The collector now waits up to 60 s for a backup lock holder before skipping. The next cycle ran
at 15:56Z; no capture window closed in between without a capture (see deadlines).

## Scheduler triggers (task XML + wrapper logs)

The Task Scheduler operational event log is disabled on this machine, so trigger outcomes were derived
from the trigger definition (start 2026-10-11T10:31-04:00, every 5 min, `MultipleInstances=IgnoreNew`)
and the wrapper's start/end lines.

- 17 trigger times: 13 started a run, 4 did not start because an instance was still running
  (14:31 and 14:36 during the first backlog cycle; 15:36 during the 15:33 run; 15:46 during the 15:42
  run). No trigger went unexplained.
- Launch delay from trigger time to wrapper start: median 33.8 s, max 153.1 s (15:31, coinciding with the
  initial 439 MB backup push, since mitigated with low-priority single-threaded packing).
- Result codes are decoded rather than assumed: 0x00041301 `SCHED_S_TASK_RUNNING`; 0x00041303
  `SCHED_S_TASK_HAS_NOT_RUN`; 0x800710E0 `ERROR_REQUEST_REFUSED`. The 0x800710E0 seen at 15:46 coincides
  with the 15:42:25 run still holding the instance, i.e. an `IgnoreNew` refusal, which the wrapper log
  confirms rather than the code alone.

## Records written by automatic runs

| Status | Automatic | Manual pilot |
|---|---|---|
| WINDOW_CAPTURE | 65 | 58 |
| MISSED | 27 | 77 |
| DUPLICATE_ATTEMPT | 32 | 3 |
| FAILED_ATTEMPT | 0 | 0 |

## Deadlines (checked per event, not inferred from the 900 s cycle limit)

Every automatic WINDOW_CAPTURE was checked against its own record: window_open ≤ prediction_as_of ≤
finalized_at ≤ scheduled_cutoff. **0 violations in 65 captures.** Margin between finalization and cutoff:
min 43.6 s, median 951.8 s, max 1143.9 s. Prediction horizon: 60.7–79.1 min before start.

## Expired windows

27 MISSED records were written by automatic runs:

- 23 had cutoffs **before** automatic collection began; the first cycle reconciled them (frozen rule: a
  passed window with no accepted capture is recorded MISSED, never backfilled).
- 4 are genuine misses during operation, all with cutoffs 14:30–14:33Z, inside the 563 s first backlog
  cycle (the only cycle that overlapped their windows):
  `k_kxautbslgame|kalshi:53350c0c…`, `k_kxitfwmatch|kalshi:092c605e…`,
  `k_kxitfwmatch|kalshi:98f6f5f4…`, `k_kxt20match|kalshi:010f7191…`.

No failed attempts were recorded.

## Settlement

No collector settlement check has run yet: it was deferred in all 13 working cycles since 14:37:11Z
because capture windows were continuously open or imminent. The 3-hour bound forces it by 17:37:11Z at
the latest. The latest scores were written by a manual settlement run before scheduling began.

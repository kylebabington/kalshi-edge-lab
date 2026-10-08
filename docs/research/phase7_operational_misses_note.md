# Phase 7 operational note: missed checkpoints and host requirements (2026-10-06)

## Summary

As of 2026-10-06 13:01 ET, six checkpoints have been `MISSED`
(`window_elapsed_without_capture`). Five were caused by the laptop sleeping on battery and one
by a home internet outage. No miss was caused by the cycle lock, the interpreter check, the
protocol or the method. None was backfilled. Every receipt is kept as written.

| Target date | Checkpoint | Cause |
|---|---|---|
| 2026-10-03 | `d0_1200`, `d0_1500` | Sleep on battery |
| 2026-10-05 | `d0_1200`, `d0_1500` | Sleep on battery |
| 2026-10-06 | `d0_0600` | Internet outage (machine awake) |
| 2026-10-06 | `d0_1200` | Sleep on battery |

## Evidence

The Windows `System` log provides the Kernel-Power events (`506`/`507` for Modern Standby,
`42`/`107` for sleep and resume, `105` for AC/DC changes) and Power-Troubleshooter `1`.
Internet capability comes from `Microsoft-Windows-NCSI/Operational` event `4042`. Times below
are ET.

- **2026-10-03 (sleep).** At 11:46 the laptop went off AC and entered Modern Standby. At 14:24
  it hibernated. It woke on AC at 18:08.
- **2026-10-05 (sleep).** At 09:39 Modern Standby began, with the reason "Lid", and the laptop
  went off AC. Network connectivity in standby was "Disconnected". At 12:24 it hibernated
  ("Standby Battery Budget Exceeded"). It woke on AC at 16:55. The cycle log
  (`data/logs/prospective/2026-10-05.log`) has no lines at all between 09:05 and the 16:57
  catch-up run.
- **2026-10-06 `d0_1200` (sleep).** At 11:07:46 Modern Standby began, with the reason "Lid",
  and the laptop went off AC. Standby connectivity was "Disconnected". It woke at 12:38:03,
  again reason "Lid", and was back on AC at 12:38:42. The cycle log has no lines between the
  11:05 run and the 12:39 catch-up run.
- **2026-10-06 `d0_0600` (network).** No sleep events occurred overnight. NCSI shows internet
  capability lost at 23:16:51 on 2026-10-05 ("SuspectDnsProbeFailed"). Wi-Fi stayed associated
  with local-only connectivity until 07:52:44. The 06:05 cycle and its 90 s retry both ran and
  failed with `NameResolutionError` for `external-api.kalshi.com`, so no evidence was fetched.
  The 07:05 cycle then wrote the `MISSED` receipt. The outage lasted beyond the 30-minute
  window, so no retry setting would have recovered this checkpoint. Internet also dropped from
  09:45 to 10:38 that morning, when no checkpoint was due.

The task's configuration made sleep fatal to captures. It has `WakeToRun=False` and
`StartWhenAvailable=True`, and wake timers are disabled on battery. On battery the laptop
sleeps after 10 minutes. On AC it never sleeps.

## Attribution uncertainty

- **Task Scheduler history** (`Microsoft-Windows-TaskScheduler/Operational`) is disabled, so no
  trigger records exist. The sleep attribution rests on two facts that fit each other: the
  power-state transitions and the complete absence of cycle-log lines during each gap. The
  runner logs only after it acquires the lock, so a launch that failed earlier would also
  leave no log trace.
- **Two off-schedule cycles** ran on 2026-10-05, at 17:01 and 21:46. Neither can be attributed
  to the scheduler or to a manual run. Both captured nothing and wrote no `MISSED` receipts.
- **The Oct 6 outage** cannot be pinned down further. The logs show an upstream internet or DNS
  failure while the Wi-Fi link stayed up. They cannot separate a router fault from an ISP
  fault.

## Legacy incumbent: constant intraday forecasts are expected

The legacy incumbent (`research/weather/service.py`) chooses among the four
`STANDARDIZED_RUNS` only: previous-day 12Z, previous-day 18Z, event-day 00Z and event-day 06Z.
Event-day 06Z becomes eligible at 12:00 UTC (08:00 EDT). From then on it is always the selected
run, and both its cached exact-run high and the residual pool are fixed. As a result,
`d0_0900`, `d0_1200` and `d0_1500` produce identical probability vectors and scores, even
though each snapshot is generated independently with its own timestamp, HRRR run and
observations.

On 2026-10-04 all three checkpoints used GFS 06Z at 63.2 °F. The cache entry
`ncep_gfs_global|2026-10-04|2026-10-04T06:00` confirms that value. All three have the
probability-vector hash `7a3216814c14e23a` and the same Brier score, 0.10926. This is expected
behavior, not stale evidence and not a scheduling fault.

The stream is stored as `streams.legacy_incumbent` with `inputs_differ_from_research: true`. It
is reference only: the primary contrast is `research_shadow` minus `research_gfs`, and primary
eligibility does not depend on it.

## "(live GFS fallback)" is a label-only issue

`build_live_event_bundle` appends " (live GFS fallback)" to `forecast_run` whenever a live
deterministic GFS value exists, even when the exact cached run was used. On 2026-10-04 the
selected value (63.2) differs from every live value (62.0, 63.7 and 61.2), so the exact run was
used. Only the label is wrong. The probabilities, scores and Phase 7 records are unaffected.
The legacy code is left unchanged.

## Host requirements: all five daily windows

Each checkpoint is captured by the HH:05 ET run. A checkpoint is `MISSED` if its window closes
while the host is asleep, offline, logged off or shut down.

| Checkpoint | Keep host ready (ET) |
|---|---|
| `d0_0600` | 05:55–06:30 |
| `d0_0900` | 08:55–09:30 |
| `d0_1200` | 11:55–12:30 |
| `d0_1500` | 14:55–15:30 |
| `dminus1_1800` (next day's event) | 17:55–18:30 |

During each window the laptop must be:

- **On AC power.**
- **Awake.** Do not close the lid on battery. Lid close triggered all of the Oct 5 and Oct 6
  sleep misses.
- **Online with working internet,** not just an associated Wi-Fi link.
- **Logged in.** The task uses an Interactive logon.

Battery sleep stays enabled globally, and power settings and scheduler configuration are
unchanged.

**Optional diagnostic.** To attribute future misses directly, enable Task Scheduler history
from an elevated prompt:

```powershell
wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true
```

The rules in the README's "Resuming after downtime" section still apply. Never backfill, edit
receipts, re-register the protocol or extend the collection dates.

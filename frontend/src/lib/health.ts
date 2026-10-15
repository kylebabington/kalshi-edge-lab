import type { SportsCollectorStatus } from '../types/sports'
import type { WeatherCollectorStatus } from '../types/weather'
import { fmtDuration, minutesBetween } from './time'

export type HealthState = 'healthy' | 'degraded' | 'stale' | 'unknown'

export type CollectorHealth = {
  state: HealthState
  minutesSinceSuccess: number | null
  thresholdMinutes: number
  reason: string
}

/**
 * Collector health from successful cycle times only. Forecast or capture age never affects it: a cycle can
 * succeed without anything due to capture. A missing or unreadable timestamp is unknown, never healthy.
 */
export function collectorHealth({
  lastSuccessAt,
  lastCycleOk,
  nowIso,
  cadenceMinutes,
}: {
  lastSuccessAt: string | null | undefined
  lastCycleOk: boolean | null | undefined
  nowIso: string | null | undefined
  cadenceMinutes: number
}): CollectorHealth {
  const thresholdMinutes = 2 * cadenceMinutes + 5
  const age = minutesBetween(lastSuccessAt, nowIso)
  if (age == null) {
    return {
      state: 'unknown',
      minutesSinceSuccess: null,
      thresholdMinutes,
      reason: lastSuccessAt ? 'Successful cycle time is unreadable' : 'No successful cycle time recorded',
    }
  }
  if (age > thresholdMinutes) {
    return {
      state: 'stale',
      minutesSinceSuccess: age,
      thresholdMinutes,
      reason: `Last successful cycle ${fmtDuration(age)} ago; expected every ${cadenceMinutes} min`,
    }
  }
  if (lastCycleOk === false) {
    return {
      state: 'degraded',
      minutesSinceSuccess: age,
      thresholdMinutes,
      reason: `Latest cycle failed; last success ${fmtDuration(age)} ago`,
    }
  }
  return {
    state: 'healthy',
    minutesSinceSuccess: age,
    thresholdMinutes,
    reason: `Last successful cycle ${fmtDuration(age)} ago`,
  }
}

export const HEALTH_LABELS: Record<HealthState, string> = {
  healthy: 'Collector healthy',
  degraded: 'Collector degraded',
  stale: 'Collector stale',
  unknown: 'Collector health unknown',
}

export function weatherHealth(s: WeatherCollectorStatus): CollectorHealth {
  return collectorHealth({
    lastSuccessAt: s.cycles.last_successful_cycle_at,
    lastCycleOk: s.cycles.last_cycle_exit_code == null ? null : s.cycles.last_cycle_exit_code === 0,
    nowIso: s.generated_at,
    cadenceMinutes: s.cadence_minutes,
  })
}

/** The 24 h status file can lack a success that the full cycle log still has, so take the later of the two. */
export function sportsLastSuccess(s: SportsCollectorStatus): string | null {
  const candidates = [s.status.last_successful_cycle_at, s.history?.last_successful_cycle_at].filter(Boolean) as string[]
  return candidates.sort().at(-1) ?? null
}

const BACKUP_FAILURES = new Set(['failed', 'inconsistent_snapshot', 'conflict', 'blocked_oversized', 'push_failed'])

/** `initial` marks a backup that has not run yet on this machine, which is expected right after setup and is not a failure. */
export type BackupHealth = { state: HealthState; label: string; reason: string; initial?: boolean }

/** Backup health is separate from capture health: a failed or slow backup never makes a capture cycle fail. */
export function sportsBackupHealth(s: SportsCollectorStatus): BackupHealth {
  const b = s.backup
  if (!b?.recorded) {
    // With no run recorded, only a task that is visibly missing or disabled counts as unhealthy.
    // Otherwise the first hourly snapshot may simply not have happened yet.
    const task = s.backup_task
    if (task?.observable && task.configured === false) {
      return { state: 'degraded', label: 'Backup unavailable: task not registered', reason: 'No backup run recorded and the backup task is not registered' }
    }
    if (task?.observable && task.enabled === false) {
      return { state: 'degraded', label: 'Backup unavailable: task disabled', reason: 'No backup run recorded and the backup task is disabled' }
    }
    return {
      state: 'unknown',
      initial: true,
      label: 'Backup not run yet',
      reason: 'No backup run recorded on this machine yet; the first snapshot follows the backup task’s first hourly run',
    }
  }
  if (b.paused) return { state: 'degraded', label: 'Backup paused', reason: `Paused${b.paused.reason ? `: ${b.paused.reason}` : ''}` }
  const lastSnap = b.last_snapshot_at ?? (b.snapshot === undefined ? b.at : null) ?? null
  const age = minutesBetween(lastSnap, s.generated_at)
  if (b.backup && BACKUP_FAILURES.has(b.backup)) {
    return { state: 'degraded', label: 'Backup degraded', reason: `Latest backup run: ${b.backup.replace(/_/g, ' ')}` }
  }
  if (b.unpushed) {
    return { state: 'degraded', label: 'Backup upload pending', reason: 'A committed snapshot is waiting to upload; retried every 15 min' }
  }
  if (age == null) return { state: 'unknown', label: 'Backup health unknown', reason: 'No snapshot time recorded' }
  if (age > 180) return { state: 'stale', label: 'Backup stale', reason: `Last snapshot ${fmtDuration(age)} ago; expected hourly` }
  return { state: 'healthy', label: 'Backup healthy', reason: `Last snapshot ${fmtDuration(age)} ago` }
}

export function sportsHealth(s: SportsCollectorStatus): CollectorHealth {
  const lastResult = s.history?.last_cycle_result ?? s.status.last_cycle?.result ?? null
  return collectorHealth({
    lastSuccessAt: sportsLastSuccess(s),
    lastCycleOk: lastResult == null ? null : lastResult === 'ok',
    nowIso: s.generated_at,
    cadenceMinutes: s.cadence_minutes,
  })
}

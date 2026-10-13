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

export function sportsHealth(s: SportsCollectorStatus): CollectorHealth {
  const lastResult = s.history?.last_cycle_result ?? s.status.last_cycle?.result ?? null
  return collectorHealth({
    lastSuccessAt: sportsLastSuccess(s),
    lastCycleOk: lastResult == null ? null : lastResult === 'ok',
    nowIso: s.generated_at,
    cadenceMinutes: s.cadence_minutes,
  })
}

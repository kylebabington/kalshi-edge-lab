import type { EventSettlementState, SnapshotScoreState } from '../types/weather'
import { fmtTargetDate } from './time'

export function weatherTitle(eventTicker: string, targetDate: string | null | undefined): string {
  return `NYC daily high · ${targetDate ? fmtTargetDate(targetDate) : eventTicker}`
}

export function eventSettlementText(s: EventSettlementState): string {
  if (s.state === 'confirmed') return `Confirmed: ${s.winning_bucket ?? 'bucket not recorded'}`
  if (s.state === 'conflict') return `Conflicting scores (${s.reason ?? 'disagree'})`
  return 'Not confirmed yet'
}

export function snapshotScoreText(s: SnapshotScoreState): string {
  if (s.state === 'scored') return 'Scored'
  if (s.state === 'not_scored') return 'Not scored yet'
  return `Unavailable: ${s.reason ?? s.state}`
}

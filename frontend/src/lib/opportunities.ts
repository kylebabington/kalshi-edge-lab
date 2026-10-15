import type { Opportunity } from '../types/opportunities'

/** Probability edge in percentage points; a missing edge is unavailable, never zero. */
export function fmtEdge(edge: number | null | undefined): string {
  if (edge == null || !Number.isFinite(edge)) return 'unavailable'
  const pts = (edge * 100).toFixed(1)
  return edge > 0 ? `+${pts} pt` : `${pts} pt`
}

/** Dollars per $1 contract; null stays "unavailable" so missing fees never look like zero. */
export function fmtUsd(v: number | null | undefined, digits = 4): string {
  if (v == null || !Number.isFinite(v)) return 'unavailable'
  const s = `$${Math.abs(v).toFixed(digits)}`
  return v < 0 ? `-${s}` : v > 0 ? `+${s}` : s
}

export function fmtConfidence(o: Pick<Opportunity, 'confidence_label' | 'confidence_score' | 'category'>): string {
  if (!o.confidence_label) return o.category === 'sports' ? 'no validated metric' : 'unavailable'
  return o.confidence_score == null ? o.confidence_label : `${o.confidence_label} (${o.confidence_score.toFixed(2)})`
}

export const AI_STATUS_LABELS: Record<string, string> = {
  not_researched: 'Not researched',
  researched: 'Researched',
  stale: 'Research stale',
}

export const ELIGIBILITY_LABELS: Record<string, string> = {
  ELIGIBLE: 'AI eligible',
  ELIGIBLE_WITH_CAUTION: 'AI eligible with caution',
  BLOCKED: 'Blocked',
}

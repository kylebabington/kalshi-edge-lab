import type { ExploreEventCard, ExploreEventList, RetrievalFreshness } from '../types/explore'
import { freshnessLabel } from './labels'

/**
 * Cache / market-data freshness from retrieval timestamps — not from list
 * `generated_at` (which is when the API built the response).
 */
export function listRetrievalFreshness(list: ExploreEventList | null | undefined): {
  label: string
  asOf: string | null
  state: string
} {
  if (!list?.events?.length) {
    return { label: 'unknown', asOf: null, state: 'unknown' }
  }
  let best: RetrievalFreshness | null = null
  let bestAt = ''
  for (const e of list.events) {
    const at = e.retrieved_at || e.retrieval_freshness?.as_of || ''
    if (!at) continue
    if (!bestAt || at > bestAt) {
      bestAt = at
      best = e.retrieval_freshness ?? null
    }
  }
  if (!bestAt) return { label: 'unknown', asOf: null, state: 'unknown' }
  const state = best?.state || 'unknown'
  return { label: freshnessLabel(best) || state, asOf: bestAt, state }
}

export function eventRetrievalStamp(e: ExploreEventCard): string | null {
  return e.retrieved_at || e.retrieval_freshness?.as_of || null
}

import { AI_STATUS_LABELS, ELIGIBILITY_LABELS } from '../lib/opportunities'
import type { AIResearchStatus, EligibilityStatus, Freshness } from '../types/opportunities'

export function EligibilityBadge({ status }: { status: EligibilityStatus }) {
  const tone = status === 'ELIGIBLE' ? 'badge-positive' : status === 'BLOCKED' ? 'badge-negative' : 'badge-warning'
  return <span className={`badge ${tone}`}>{ELIGIBILITY_LABELS[status] ?? status}</span>
}

export function FreshnessBadge({ status }: { status: Freshness }) {
  const tone = status === 'fresh' ? 'badge-positive' : status === 'aging' ? 'badge-warning' : 'badge-negative'
  return <span className={`badge ${tone}`}>{status}</span>
}

export function AIStatusBadge({ status }: { status: AIResearchStatus }) {
  const tone = status === 'researched' ? 'badge-info' : status === 'stale' ? 'badge-warning' : ''
  return <span className={`badge ${tone}`}>{AI_STATUS_LABELS[status] ?? status}</span>
}

/** attention_score is a 0-100 ranking of what deserves a look; not a probability, EV or recommendation. */
export function AttentionScore({ score, size = 'sm' }: { score: number; size?: 'sm' | 'lg' }) {
  return (
    <span
      className={`mono font-semibold ${size === 'lg' ? 'text-2xl' : 'text-sm'}`}
      title="Attention score (0-100): how much this market deserves a closer look. Not a probability, not expected value, not a recommendation."
    >
      {score}
      <span className="text-[var(--text-muted)] text-xs font-normal">/100</span>
    </span>
  )
}

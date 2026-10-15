import { fmtLocal } from '../lib/time'
import type { StatusRecord } from '../types/weather'

const KIND_LABELS: Record<string, string> = {
  implementation: 'Implementation',
  evidence: 'Evidence',
  operations: 'Operations',
  integrity: 'Integrity',
  policy: 'Policy',
  configuration: 'Configuration',
}

const STATE_LABELS: Record<string, string> = {
  recent_success: 'recent success',
  not_checked: 'not checked',
  not_applicable: 'not applicable',
}

function badgeClass(record: StatusRecord): string {
  if (record.kind === 'policy' || record.kind === 'configuration') return 'badge badge-info'
  switch (record.state) {
    case 'verified':
    case 'recent_success':
      return 'badge badge-positive'
    case 'failed':
      return 'badge badge-negative'
    case 'present':
    case 'implemented':
    case 'not_applicable':
      return 'badge badge-info'
    default:
      return 'badge badge-warning'
  }
}

function stateText(record: StatusRecord): string {
  return STATE_LABELS[record.state] ?? record.state.replaceAll('_', ' ')
}

/**
 * One status claim with its kind and basis. A missing record renders as unknown, never as success.
 */
export function StatusRecordBadge({
  record,
  showBasis = true,
  showKind = true,
}: {
  record: StatusRecord | null | undefined
  showBasis?: boolean
  showKind?: boolean
}) {
  if (!record) {
    return (
      <span className="inline-flex flex-col gap-0.5">
        <span className="badge badge-warning">unknown</span>
        {showBasis ? <span className="text-xs text-[var(--text-muted)]">No status record was provided.</span> : null}
      </span>
    )
  }
  const kind = KIND_LABELS[record.kind] ?? record.kind
  const reason = record.reason && record.reason !== record.basis ? record.reason : null
  return (
    <span className="inline-flex flex-col gap-0.5 min-w-0">
      <span className={`${badgeClass(record)} w-fit`} title={`${kind}: ${record.state}`}>
        {showKind ? `${kind} · ` : ''}
        {stateText(record)}
      </span>
      {showBasis ? (
        <span className="text-xs text-[var(--text-muted)] break-words">
          {record.basis}
          {reason ? ` · ${reason}` : ''}
          {record.as_of ? ` · as of ${fmtLocal(record.as_of)}` : ''}
          {record.source ? (
            <>
              {' · '}
              <span className="mono break-all">{record.source}</span>
            </>
          ) : null}
        </span>
      ) : null}
    </span>
  )
}

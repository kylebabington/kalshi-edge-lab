import type { ReactNode } from 'react'

export function StatusBadge({ status }: { status: string }) {
  const upper = status.toUpperCase()
  let cls = 'badge badge-info'
  if (
    upper.includes('INSUFFICIENT') ||
    upper.includes('MISMATCH') ||
    upper.includes('UNKNOWN') ||
    upper.includes('BURN') ||
    upper.includes('EXPERIMENTAL') ||
    upper.includes('STALE') ||
    upper.includes('DEGRADED') ||
    upper.includes('UNAVAILABLE')
  ) {
    cls = 'badge badge-warning'
  }
  if (upper.includes('DISABLED') || upper.includes('BLOCKED') || upper.includes('ERROR') || upper.includes('CONFLICT')) {
    cls = 'badge badge-negative'
  }
  if (upper.includes('HEALTHY')) cls = 'badge badge-positive'
  return (
    <span className={cls} title={status}>
      {status.replaceAll('_', ' ')}
    </span>
  )
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="panel text-[var(--text-muted)]">
      <div className="font-medium text-[var(--text)]">{title}</div>
      <p className="mt-1 text-sm">{detail}</p>
    </div>
  )
}

export function ErrorState({ message, title = 'API error', onRetry }: { message: string; title?: string; onRetry?: () => void }) {
  return (
    <div className="panel border-[color-mix(in_oklab,var(--negative)_50%,var(--border))]" role="alert">
      <div className="text-[var(--negative)] font-medium">{title}</div>
      <p className="mt-1 text-sm mono whitespace-pre-wrap break-words">{message}</p>
      {onRetry ? (
        <button type="button" className="pager-button mt-2 text-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  )
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="panel text-[var(--text-muted)]" role="status" aria-live="polite">
      {label}
    </div>
  )
}

export function StaleNotice({ children }: { children: ReactNode }) {
  return (
    <div className="warning-banner text-sm" role="status">
      <span className="font-semibold text-[var(--warning)]">Stale: </span>
      {children}
    </div>
  )
}

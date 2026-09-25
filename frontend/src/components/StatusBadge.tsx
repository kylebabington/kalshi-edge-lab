export function StatusBadge({ status }: { status: string }) {
  const upper = status.toUpperCase()
  let cls = 'badge badge-info'
  if (
    upper.includes('INSUFFICIENT') ||
    upper.includes('MISMATCH') ||
    upper.includes('UNKNOWN') ||
    upper.includes('BURN') ||
    upper.includes('EXPERIMENTAL')
  ) {
    cls = 'badge badge-warning'
  }
  if (upper.includes('DISABLED') || upper.includes('BLOCKED') || upper.includes('ERROR')) {
    cls = 'badge badge-negative'
  }
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

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="panel border-[color-mix(in_oklab,var(--negative)_50%,var(--border))]">
      <div className="text-[var(--negative)] font-medium">API error</div>
      <p className="mt-1 text-sm mono whitespace-pre-wrap">{message}</p>
    </div>
  )
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return <div className="panel text-[var(--text-muted)]">{label}</div>
}

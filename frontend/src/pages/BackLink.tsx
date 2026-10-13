import { Link, useLocation } from 'react-router-dom'

/** Returns to the list the user came from, with its filters, sort and page; falls back to the plain list route. */
export function BackLink({ fallback, label }: { fallback: string; label: string }) {
  const location = useLocation()
  const from = (location.state as { from?: unknown } | null)?.from
  const to = typeof from === 'string' && from.startsWith('/') ? from : fallback
  return (
    <Link to={to} className="text-sm text-[var(--accent)] hover:underline">
      ← {to === fallback ? label : 'Back to list'}
    </Link>
  )
}

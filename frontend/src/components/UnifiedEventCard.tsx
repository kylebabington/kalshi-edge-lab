import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { StarButton } from '../watchlist/StarButton'
import type { WatchCategory } from '../watchlist/store'
import { StatusBadge } from './StatusBadge'

export type DataKind = 'saved' | 'market'

export type EventCardModel = {
  category: WatchCategory
  /** Identifier used by the read-only API and the watchlist. */
  eventKey: string
  href: string
  title: string
  eyebrow: string
  status: string
  dataKind: DataKind
  rows: { label: string; value: ReactNode }[]
}

export function DataKindTag({ kind }: { kind: DataKind }) {
  return kind === 'saved' ? (
    <span className="data-kind data-kind-saved" title="Forecast saved before the outcome; it never changes">
      Saved forecast
    </span>
  ) : (
    <span className="data-kind data-kind-market" title="Fetched from current market and weather sources">
      Current market data
    </span>
  )
}

export function UnifiedEventCard({ model, children }: { model: EventCardModel; children?: ReactNode }) {
  const location = useLocation()
  return (
    <article className="panel h-full flex flex-col gap-3 hover:border-[color-mix(in_oklab,var(--accent)_40%,var(--border))] transition">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-[0.14em] text-[var(--text-muted)]">{model.eyebrow}</div>
          <h3 className="mt-1 text-base font-semibold break-words">
            <Link
              to={model.href}
              state={{ from: location.pathname + location.search }}
              className="hover:underline underline-offset-2"
              aria-label={`Open ${model.title}`}
            >
              {model.title}
            </Link>
          </h3>
        </div>
        <StarButton category={model.category} eventKey={model.eventKey} label={model.title} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={model.status} />
        <DataKindTag kind={model.dataKind} />
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
        {model.rows.map((r) => (
          <div key={r.label} className="min-w-0">
            <dt className="text-[var(--text-muted)] text-xs">{r.label}</dt>
            <dd className="text-xs break-words">{r.value}</dd>
          </div>
        ))}
      </dl>
      {children}
    </article>
  )
}

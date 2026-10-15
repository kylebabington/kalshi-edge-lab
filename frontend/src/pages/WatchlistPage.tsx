import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { eventPath } from '../api/sports'
import { weatherEventPath } from '../api/weather'
import { LiveWeatherCard, SavedWeatherCard, SportsCard } from '../components/eventCards'
import { EmptyState, LoadingState } from '../components/StatusBadge'
import { TimezoneNote } from '../components/TimezoneNote'
import { fmtLocal } from '../lib/time'
import { PersistenceNotice } from '../watchlist/StarButton'
import type { Resolved } from '../watchlist/resolve'
import type { WatchCategory, WatchEntry } from '../watchlist/store'
import { useResolvedWatchlist, type ResolvedRow } from '../watchlist/useResolvedWatchlist'
import { useWatchlist } from '../watchlist/useWatchlist'

const FILTERS: { id: '' | WatchCategory; label: string }[] = [
  { id: '', label: 'All' },
  { id: 'weather', label: 'Weather' },
  { id: 'sports', label: 'Sports' },
]

const SORTS = [
  { id: 'added', label: 'Recently added' },
  { id: 'upcoming', label: 'Upcoming' },
  { id: 'pending', label: 'Pending settlement' },
  { id: 'settled', label: 'Settled' },
] as const

type SortId = (typeof SORTS)[number]['id']

function parseSort(raw: string | null): SortId {
  return SORTS.some((s) => s.id === raw) ? (raw as SortId) : 'added'
}

function viewOf(data: Resolved): string | null {
  if (data.kind === 'sports' || data.kind === 'weather-saved') return data.event.view
  return null
}

function eventTime(row: ResolvedRow): string | null {
  const data = row.data
  if (!data) return null
  if (data.kind === 'sports') return data.event.event_start
  if (data.kind === 'weather-saved') return data.event.target_date
  if (data.kind === 'weather-live') return data.event.target_date ?? data.event.prediction.target_date ?? null
  return null
}

function settlementTime(row: ResolvedRow): string | null {
  const data = row.data
  if (!data) return null
  if (data.kind === 'sports') return data.event.settlement.last_settled_at ?? null
  if (data.kind === 'weather-saved') return data.event.event_settlement.confirmed_at ?? null
  return null
}

/**
 * Mixed weather and sports rows share one order.
 * Live weather has no saved view, so it sorts with upcoming: the market is still open.
 * Sports missed windows match none of upcoming, pending, or settled.
 * Loading, failed, and not-found rows sort after rows with a known status, then by
 * added_at, because their research status is not reliable yet.
 */
function statusRank(sort: SortId, row: ResolvedRow): number {
  if (sort === 'added') return 0
  if (row.loading || row.error || !row.data || row.data.kind === 'not_found') return 2
  if (sort === 'upcoming' && (row.data.kind === 'weather-live' || viewOf(row.data) === 'upcoming')) return 0
  if (sort === 'pending' && viewOf(row.data) === 'awaiting_settlement') return 0
  if (sort === 'settled' && viewOf(row.data) === 'settled') return 0
  return 1
}

function compareRows(sort: SortId, a: ResolvedRow, b: ResolvedRow): number {
  if (sort === 'added') return b.entry.added_at.localeCompare(a.entry.added_at)
  const rank = statusRank(sort, a) - statusRank(sort, b)
  if (rank) return rank
  if (sort === 'upcoming') {
    const byTime = (eventTime(a) ?? '9999').localeCompare(eventTime(b) ?? '9999')
    if (byTime) return byTime
  }
  if (sort === 'settled') {
    const left = settlementTime(a)
    const right = settlementTime(b)
    if (left && right && left !== right) return right.localeCompare(left)
    if (left && !right) return -1
    if (!left && right) return 1
  }
  return b.entry.added_at.localeCompare(a.entry.added_at)
}

export function WatchlistPage() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('category')
  const category: '' | WatchCategory = raw === 'weather' || raw === 'sports' ? raw : ''
  const sort = parseSort(params.get('sort'))
  const { entries, store } = useWatchlist()
  const [confirming, setConfirming] = useState(false)
  const resolved = useResolvedWatchlist(entries)
  const shown = resolved
    .filter((row) => !category || row.entry.category === category)
    .sort((a, b) => compareRows(sort, a, b))
  const count = (c: '' | WatchCategory) => entries.filter((e) => !c || e.category === c).length

  function setCategory(next: '' | WatchCategory) {
    setParams(
      (prev) => {
        const paramsNext = new URLSearchParams(prev)
        if (next) paramsNext.set('category', next)
        else paramsNext.delete('category')
        return paramsNext
      },
      { replace: true },
    )
  }

  function setSort(next: string) {
    setParams(
      (prev) => {
        const paramsNext = new URLSearchParams(prev)
        if (!next || next === 'added') paramsNext.delete('sort')
        else paramsNext.set('sort', next)
        return paramsNext
      },
      { replace: true },
    )
  }

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold break-words">Watchlist</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Events you starred, stored only in this browser. Each entry is looked up again through the read-only API.
        </p>
        <TimezoneNote />
        <PersistenceNotice />
      </header>
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
        <div role="group" aria-label="Filter by category" className="flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.id || 'all'}
              type="button"
              className="pager-button text-sm"
              aria-pressed={category === f.id}
              onClick={() => setCategory(f.id)}
            >
              {f.label} ({count(f.id)})
            </button>
          ))}
        </div>
        <div className="flex flex-col gap-2 w-full sm:w-auto sm:flex-row sm:items-end">
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)] w-full sm:min-w-[12rem]">
            Sort
            <select className="filter-control" value={sort} onChange={(e) => setSort(e.target.value)}>
              {SORTS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          {entries.length ? (
            confirming ? (
              <div role="alertdialog" aria-label="Confirm clearing the watchlist" className="flex flex-wrap items-center gap-2 text-sm max-w-full">
                <span>
                  Remove all {entries.length} {entries.length === 1 ? 'entry' : 'entries'}? This cannot be undone.
                </span>
                <button
                  type="button"
                  className="pager-button text-[var(--negative)]"
                  onClick={() => {
                    store.clear()
                    setConfirming(false)
                  }}
                >
                  Yes, clear watchlist
                </button>
                <button type="button" className="pager-button" onClick={() => setConfirming(false)}>
                  Cancel
                </button>
              </div>
            ) : (
              <button type="button" className="pager-button text-sm whitespace-nowrap" onClick={() => setConfirming(true)}>
                Clear watchlist…
              </button>
            )
          ) : null}
        </div>
      </div>
      {entries.length === 0 ? (
        <EmptyState
          title="Your watchlist is empty"
          detail="Use the star on any weather or sports event card or detail page to add it here."
        />
      ) : shown.length === 0 ? (
        <EmptyState title={`No ${category} events on your watchlist`} detail="Choose another category to see your other entries." />
      ) : (
        <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {shown.map((row) => (
            <li key={row.entry.id}>
              <WatchRow row={row} onRemove={() => store.remove(row.entry.id)} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function WatchRow({ row, onRemove }: { row: ResolvedRow; onRemove: () => void }) {
  const entry: WatchEntry = row.entry
  const r = row.data
  const footer = (
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--text-muted)]">
      <span>Added {fmtLocal(entry.added_at)}</span>
      <button type="button" className="pager-button" onClick={onRemove} aria-label={`Remove ${entry.label} from watchlist`}>
        Remove
      </button>
    </div>
  )
  if (row.loading && !r) {
    return (
      <div className="space-y-2">
        <LoadingState label={`Looking up ${entry.label}…`} />
        {footer}
      </div>
    )
  }
  if (row.error || !r || r.kind === 'not_found') {
    const href = entry.category === 'sports' ? eventPath(entry.key) : weatherEventPath(entry.key)
    return (
      <article className="panel space-y-2 h-full" aria-label={entry.label}>
        <div className="text-xs uppercase tracking-[0.14em] text-[var(--text-muted)]">
          {entry.category === 'sports' ? 'Sports' : 'Weather'} · <span className="mono normal-case tracking-normal">{entry.key}</span>
        </div>
        <h3 className="font-semibold break-words">{entry.label}</h3>
        {row.error ? (
          <div className="text-sm" role="alert">
            <p className="text-[var(--negative)]">Could not check this event: the API request failed.</p>
            <p className="mono text-xs break-words text-[var(--text-muted)]">{row.error}</p>
            <button type="button" className="pager-button mt-2" onClick={row.reload}>
              Retry
            </button>
          </div>
        ) : (
          <div className="text-sm">
            <p className="text-[var(--warning)]">Unavailable</p>
            <p className="text-[var(--text-muted)]">
              {r?.kind === 'not_found' ? r.detail : 'No data returned.'} The saved entry is kept until you remove it.
            </p>
            <Link to={href} className="text-xs text-[var(--accent)] hover:underline">
              Try the detail page →
            </Link>
          </div>
        )}
        {footer}
      </article>
    )
  }
  return (
    <div className="grid h-full grid-rows-[1fr_auto] gap-2">
      {r.kind === 'sports' ? <SportsCard event={r.event} /> : null}
      {r.kind === 'weather-saved' ? <SavedWeatherCard event={r.event} /> : null}
      {r.kind === 'weather-live' ? <LiveWeatherCard event={r.event} /> : null}
      {footer}
    </div>
  )
}

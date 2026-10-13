import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { eventPath } from '../api/sports'
import { weatherEventPath } from '../api/weather'
import { LiveWeatherCard, SavedWeatherCard, SportsCard } from '../components/eventCards'
import { EmptyState, LoadingState } from '../components/StatusBadge'
import { TimezoneNote } from '../components/TimezoneNote'
import { useAsync } from '../components/sports/useAsync'
import { fmtLocal } from '../lib/time'
import { PersistenceNotice } from '../watchlist/StarButton'
import { resolveEntry } from '../watchlist/resolve'
import type { WatchCategory, WatchEntry } from '../watchlist/store'
import { useWatchlist } from '../watchlist/useWatchlist'

const FILTERS: { id: '' | WatchCategory; label: string }[] = [
  { id: '', label: 'All' },
  { id: 'weather', label: 'Weather' },
  { id: 'sports', label: 'Sports' },
]

export function WatchlistPage() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('category')
  const category: '' | WatchCategory = raw === 'weather' || raw === 'sports' ? raw : ''
  const { entries, store } = useWatchlist()
  const [confirming, setConfirming] = useState(false)
  const shown = entries.filter((e) => !category || e.category === category).reverse()
  const count = (c: '' | WatchCategory) => entries.filter((e) => !c || e.category === c).length

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold">Watchlist</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Events you starred, stored only in this browser. Each entry is looked up again through the read-only API;
          nothing here places or recommends trades.
        </p>
        <TimezoneNote />
        <PersistenceNotice />
      </header>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label="Filter by category" className="flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.id || 'all'}
              type="button"
              className="pager-button text-sm"
              aria-pressed={category === f.id}
              onClick={() => setParams(f.id ? { category: f.id } : {}, { replace: true })}
            >
              {f.label} ({count(f.id)})
            </button>
          ))}
        </div>
        {entries.length ? (
          confirming ? (
            <div role="alertdialog" aria-label="Confirm clearing the watchlist" className="flex flex-wrap items-center gap-2 text-sm">
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
            <button type="button" className="pager-button text-sm" onClick={() => setConfirming(true)}>
              Clear watchlist…
            </button>
          )
        ) : null}
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
          {shown.map((e) => (
            <li key={e.id}>
              <WatchRow entry={e} onRemove={() => store.remove(e.id)} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function WatchRow({ entry, onRemove }: { entry: WatchEntry; onRemove: () => void }) {
  const state = useAsync(() => resolveEntry(entry), entry.id)
  const r = state.data
  const footer = (
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--text-muted)]">
      <span>Added {fmtLocal(entry.added_at)}</span>
      <button type="button" className="pager-button" onClick={onRemove} aria-label={`Remove ${entry.label} from watchlist`}>
        Remove
      </button>
    </div>
  )
  if (state.loading && !r) {
    return (
      <div className="space-y-2">
        <LoadingState label={`Looking up ${entry.label}…`} />
        {footer}
      </div>
    )
  }
  if (state.error || !r || r.kind === 'not_found') {
    const href = entry.category === 'sports' ? eventPath(entry.key) : weatherEventPath(entry.key)
    return (
      <article className="panel space-y-2 h-full" aria-label={entry.label}>
        <div className="text-xs uppercase tracking-[0.14em] text-[var(--text-muted)]">
          {entry.category === 'sports' ? 'Sports' : 'Weather'} · <span className="mono normal-case tracking-normal">{entry.key}</span>
        </div>
        <h3 className="font-semibold break-words">{entry.label}</h3>
        {state.error ? (
          <div className="text-sm" role="alert">
            <p className="text-[var(--negative)]">Could not check this event: the API request failed.</p>
            <p className="mono text-xs break-words text-[var(--text-muted)]">{state.error}</p>
            <button type="button" className="pager-button mt-2" onClick={state.reload}>
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
    <div className="space-y-2 h-full">
      {r.kind === 'sports' ? <SportsCard event={r.event} /> : null}
      {r.kind === 'weather-saved' ? <SavedWeatherCard event={r.event} /> : null}
      {r.kind === 'weather-live' ? <LiveWeatherCard event={r.event} /> : null}
      {footer}
    </div>
  )
}

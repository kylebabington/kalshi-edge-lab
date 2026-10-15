import { useState } from 'react'
import { Link } from 'react-router-dom'
import { exploreEventPath, fetchExploreEvents } from '../api/explore'
import { EmptyState, ErrorState, LoadingState } from '../components/StatusBadge'
import { Pager } from '../components/sports/FilterBar'
import { useAsync } from '../components/sports/useAsync'
import { listRetrievalFreshness } from '../explore/listFreshness'
import { fmtLocal } from '../lib/time'
import { exploreHref, filtersKey } from './filters'
import type { SavedSearch } from './store'

const PAGE_SIZE = 5

/**
 * One dashboard pin. Request key includes search id, validated filters, and page
 * so filter changes (including cross-tab) invalidate in-flight responses via useAsync.
 */
export function PinnedSearchPanel({ search }: { search: SavedSearch }) {
  const fKey = filtersKey(search.filters)
  const identity = `${search.id}|${fKey}`
  const [page, setPage] = useState(1)
  const [pageIdentity, setPageIdentity] = useState(identity)
  if (pageIdentity !== identity) {
    setPageIdentity(identity)
    setPage(1)
  }

  const requestKey = `${identity}|${page}`
  const state = useAsync(
    () =>
      fetchExploreEvents({
        ...search.filters,
        page,
        page_size: PAGE_SIZE,
      }),
    requestKey,
  )

  const fresh = listRetrievalFreshness(state.data)
  const href = exploreHref(search.filters)

  return (
    <section className="panel space-y-3" aria-label={`Pinned search ${search.name}`} data-testid="pinned-search-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold break-words">{search.name}</h3>
        <Link to={href} className="text-sm text-[var(--accent)] hover:underline">
          Open in Explorer →
        </Link>
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Market-data freshness: {fresh.label}
        {fresh.asOf ? ` · latest retrieval ${fmtLocal(fresh.asOf)}` : ' · no retrieval timestamps on this page'}
        {state.data?.partial_crawl ? ' · partial crawl' : ''}
      </p>
      <p className="text-xs text-[var(--text-muted)]">
        Response built at {state.data?.generated_at ? fmtLocal(state.data.generated_at) : '—'} (API query time, not market refresh).
      </p>

      {state.loading && !state.data ? <LoadingState label={`Loading “${search.name}”…`} /> : null}
      {state.error ? <ErrorState message={state.error} onRetry={state.reload} /> : null}
      {state.data && !state.error ? (
        state.data.total === 0 ? (
          <EmptyState
            title="No matching events"
            detail="This saved search has no events in the explorer cache for these filters. Results are not substituted from other searches."
          />
        ) : (
          <>
            <ul className="space-y-2">
              {state.data.events.map((e) => (
                <li key={e.event_ticker} className="text-sm">
                  <Link to={exploreEventPath(e.event_ticker)} className="font-medium hover:underline break-words">
                    {e.title || e.event_ticker}
                  </Link>
                  <div className="text-xs text-[var(--text-muted)] mono">{e.event_ticker}</div>
                </li>
              ))}
            </ul>
            <Pager
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              noun="events"
              onPage={setPage}
            />
          </>
        )
      ) : null}
    </section>
  )
}

export function PinnedSearchesSection({ searches }: { searches: readonly SavedSearch[] }) {
  if (!searches.length) {
    return (
      <section className="space-y-2" aria-labelledby="pinned-searches-heading">
        <h2 id="pinned-searches-heading" className="text-lg font-semibold">
          Pinned searches
        </h2>
        <EmptyState
          title="No pinned searches"
          detail="Pin a saved search from Market Explorer to preview it here. Previews read the local cache only."
        />
      </section>
    )
  }
  return (
    <section className="space-y-4" aria-labelledby="pinned-searches-heading">
      <h2 id="pinned-searches-heading" className="text-lg font-semibold">
        Pinned searches
      </h2>
      <p className="text-sm text-[var(--text-muted)]">
        Each panel loads independently from the explorer cache. Opening a pin never starts Refresh markets.
      </p>
      <ul className="grid gap-4 lg:grid-cols-2">
        {searches.map((s) => (
          <li key={s.id}>
            <PinnedSearchPanel search={s} />
          </li>
        ))}
      </ul>
    </section>
  )
}

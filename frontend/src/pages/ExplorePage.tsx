import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  exploreEventPath,
  fetchExploreEvents,
  fetchExploreRefreshStatus,
  fetchExploreSummary,
  postExploreRefresh,
} from '../api/explore'
import { EmptyState, ErrorState, LoadingState } from '../components/StatusBadge'
import { HelpDisclosure } from '../components/HelpDisclosure'
import { TimezoneNote } from '../components/TimezoneNote'
import { FilterBar, Pager } from '../components/sports/FilterBar'
import { useAsync } from '../components/sports/useAsync'
import { coverageSummary, freshnessLabel, lifecycleLabel } from '../explore/labels'
import {
  resolveWatchRefreshTargets,
  type RefreshTargetsResult,
  type UnresolvedWatch,
} from '../explore/refreshTargets'
import { fmtLocal } from '../lib/time'
import type { ExploreEventCard, ExploreRefreshStatus, ExploreSummary } from '../types/explore'
import { canonicalizeSort, filtersFromSearchParams, filtersToSearchParams, type ExploreSavedFilters } from '../savedSearches/filters'
import { SavedSearchesPanel } from '../savedSearches/SavedSearchesPanel'
import { StarButton } from '../watchlist/StarButton'
import { useWatchlist } from '../watchlist/useWatchlist'

const SORTS = [
  { value: 'retrieved_desc', label: 'Latest retrieved' },
  { value: 'close_asc', label: 'Close time (earliest)' },
  { value: 'volume_desc', label: '24h volume' },
  { value: 'title', label: 'Title' },
]

export function ExplorePage() {
  const [params, setParams] = useSearchParams()
  const setParam = useCallback(
    (updates: Record<string, string>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          for (const [k, v] of Object.entries(updates)) {
            if (k === 'sort' && v) {
              next.set('sort', canonicalizeSort(v))
              continue
            }
            if (v) next.set(k, v)
            else next.delete(k)
          }
          if (!('page' in updates)) next.delete('page')
          return next
        },
        { replace: true },
      )
    },
    [setParams],
  )

  const savedFilters = filtersFromSearchParams(params)
  const filters = {
    ...savedFilters,
    page: Number(params.get('page') ?? '1') || 1,
  }

  const summary = useAsync(fetchExploreSummary, 'explore-summary')
  const list = useAsync(() => fetchExploreEvents({ ...filters, page_size: 40 }), JSON.stringify(filters))
  const { entries } = useWatchlist()

  const openSaved = useCallback(
    (f: ExploreSavedFilters) => {
      // Restore filters only — never start a refresh.
      setParams(filtersToSearchParams(f), { replace: true })
    },
    [setParams],
  )

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold break-words">Market Explorer</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Browse Kalshi events across categories from the local explorer cache. Quotes are the latest values retrieved
          into that cache—not live streaming prices.
        </p>
        <HelpDisclosure title="How refresh works">
          <p>
            Refresh markets crawls currently open events and looks up watched or previously cached non-final events.
            GET requests never start a crawl. Absence from a refresh does not mean an event is closed. Model support and
            saved-forecast availability are separate: a supported sports series without a capture shows as model
            supported with no saved forecast.
          </p>
        </HelpDisclosure>
        <TimezoneNote />
      </header>

      <RefreshPanel watchEntries={entries} summary={summary.data} onDone={() => { summary.reload(); list.reload() }} />

      {summary.data ? <SummaryStrip summary={summary.data} /> : null}

      <SavedSearchesPanel currentFilters={savedFilters} onOpen={openSaved} />

      <FilterBar
        selects={[
          {
            id: 'category',
            label: 'Category',
            value: filters.category,
            options: (summary.data?.categories ?? []).map((c) => ({ value: c.name, label: `${c.name} (${c.event_count})` })),
            allLabel: 'All categories',
          },
          {
            id: 'lifecycle',
            label: 'Lifecycle',
            value: filters.lifecycle,
            options: [
              { value: 'open' },
              { value: 'closed' },
              { value: 'final' },
              { value: 'unknown' },
            ],
          },
          {
            id: 'coverage',
            label: 'Coverage',
            value: filters.coverage,
            options: [
              { value: 'model', label: 'Model supported' },
              { value: 'none', label: 'No model registered' },
            ],
          },
          {
            id: 'sort',
            label: 'Sort',
            value: filters.sort,
            options: SORTS,
            allLabel: undefined,
          },
        ]}
        advancedSelects={[
          {
            id: 'quote',
            label: 'Quote depth',
            value: filters.quote,
            options: [
              { value: 'two_sided', label: 'Two-sided' },
              { value: 'one_sided', label: 'One-sided' },
              { value: 'none', label: 'No quote' },
            ],
          },
        ]}
        onChange={(id, value) => setParam({ [id]: value })}
        search={{ label: 'Search', value: filters.q, placeholder: 'Ticker or title' }}
        onSearch={(q) => setParam({ q })}
        onReset={() => setParams({}, { replace: true })}
      />

      {list.loading && !list.data ? (
        <LoadingState label="Loading explorer events…" />
      ) : list.error ? (
        <ErrorState message={list.error} onRetry={list.reload} />
      ) : list.data && list.data.events.length === 0 ? (
        <EmptyState
          title="No events match these filters"
          detail="Adjust filters or run Refresh markets to crawl currently open Kalshi events into the local cache."
        />
      ) : list.data ? (
        <>
          {list.data.partial_crawl ? (
            <p className="warning-banner text-sm" role="status">
              Partial crawl: the latest refresh did not finish a complete open-event pass. Counts and listings may be incomplete.
            </p>
          ) : null}
          <p className="text-xs text-[var(--text-muted)]">
            Showing {(list.data.page - 1) * list.data.page_size + 1}–
            {Math.min(list.data.page * list.data.page_size, list.data.total)} of {list.data.total}
          </p>
          <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {list.data.events.map((e) => (
              <li key={e.event_ticker}>
                <ExploreCard event={e} />
              </li>
            ))}
          </ul>
          <Pager
            page={list.data.page}
            pages={list.data.pages}
            total={list.data.total}
            noun="events"
            onPage={(page) => setParam({ page: String(page) })}
          />
        </>
      ) : null}
    </div>
  )
}

function SummaryStrip({ summary }: { summary: ExploreSummary }) {
  const meta = summary.series_metadata
  return (
    <section className="panel text-sm space-y-2" aria-label="Explorer summary">
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span>
          <span className="text-[var(--text-muted)]">Events cached:</span> {summary.event_count}
        </span>
        {summary.lifecycle_totals
          ? Object.entries(summary.lifecycle_totals).map(([k, v]) => (
              <span key={k}>
                <span className="text-[var(--text-muted)]">{k}:</span> {v}
              </span>
            ))
          : null}
      </div>
      {meta ? (
        <p className="text-xs text-[var(--text-muted)]">
          Series metadata: {meta.available ? `${meta.count ?? '—'} series` : 'unavailable'}
          {meta.retrieved_at ? ` · retrieved ${fmtLocal(meta.retrieved_at)}` : ''}
          {meta.freshness ? ` · ${meta.freshness}` : ''}
          {meta.degraded ? ' · degraded (previous metadata retained)' : ''}
          {meta.error ? ` · ${String(meta.error).slice(0, 200)}` : ''}
        </p>
      ) : null}
      {summary.notes?.length ? (
        <ul className="text-xs text-[var(--text-muted)] list-disc pl-4">
          {summary.notes.slice(0, 4).map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

function RefreshPanel({
  watchEntries,
  summary,
  onDone,
}: {
  watchEntries: readonly { id: string; category: 'weather' | 'sports' | 'kalshi'; key: string; label: string; added_at: string }[]
  summary: ExploreSummary | null
  onDone: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [status, setStatus] = useState<ExploreRefreshStatus['status'] | null>(
    (summary?.refresh as ExploreRefreshStatus['status'] | undefined) ?? null,
  )
  const [error, setError] = useState<string | null>(null)
  const [targets, setTargets] = useState<RefreshTargetsResult | null>(null)

  useEffect(() => {
    if (summary?.refresh && typeof summary.refresh === 'object') {
      setStatus(summary.refresh as ExploreRefreshStatus['status'])
    }
  }, [summary])

  useEffect(() => {
    const state = status?.state
    if (state !== 'running' && state !== 'starting') return
    let cancelled = false
    const tick = async () => {
      try {
        const next = await fetchExploreRefreshStatus()
        if (cancelled) return
        setStatus(next.status)
        if (next.status.state === 'complete' || next.status.state === 'interrupted' || next.status.state === 'failed' || next.status.state === 'idle') {
          setBusy(false)
          onDone()
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message.slice(0, 400) : String(err).slice(0, 400))
      }
    }
    const id = setInterval(tick, 2000)
    void tick()
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [status?.state, onDone])

  async function start() {
    setBusy(true)
    setResolving(true)
    setError(null)
    setTargets(null)
    let resolved: RefreshTargetsResult
    try {
      resolved = await resolveWatchRefreshTargets(watchEntries)
      setTargets(resolved)
    } catch (err) {
      setBusy(false)
      setResolving(false)
      setError(err instanceof Error ? err.message.slice(0, 400) : String(err).slice(0, 400))
      return
    }
    setResolving(false)
    try {
      const res = await postExploreRefresh(resolved.tickers)
      setStatus(res.status)
      if (res.conflict) {
        setError('A refresh is already running.')
        setBusy(false)
      } else if (res.status.state === 'complete' || res.status.state === 'failed' || res.status.state === 'interrupted') {
        setBusy(false)
        onDone()
      }
    } catch (err) {
      setBusy(false)
      setError(err instanceof Error ? err.message.slice(0, 400) : String(err).slice(0, 400))
    }
  }

  const running = busy || status?.state === 'running' || status?.state === 'starting'

  return (
    <section className="panel space-y-2" aria-label="Refresh markets">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="pager-button text-sm" disabled={running} onClick={() => void start()}>
          {resolving ? 'Resolving watchlist…' : running ? 'Refreshing…' : 'Refresh markets'}
        </button>
        {status?.state ? (
          <span className="text-xs text-[var(--text-muted)]">
            Status: {status.state}
            {status.pages != null ? ` · ${status.pages} pages` : ''}
            {status.events != null ? ` · ${status.events} events` : ''}
            {status.step ? ` · ${status.step}` : ''}
          </span>
        ) : (
          <span className="text-xs text-[var(--text-muted)]">No refresh has been run yet</span>
        )}
      </div>
      {targets ? <TargetSummary result={targets} /> : null}
      {error ? (
        <p className="text-sm text-[var(--negative)]" role="alert">
          {error}
        </p>
      ) : null}
      {status?.error ? (
        <p className="text-sm text-[var(--warning)]" role="status">
          {String(status.error).slice(0, 400)}
        </p>
      ) : null}
    </section>
  )
}

function TargetSummary({ result }: { result: RefreshTargetsResult }) {
  const issues: UnresolvedWatch[] = [...result.unresolved, ...result.overCap]
  return (
    <div className="text-xs text-[var(--text-muted)] space-y-1" role="status">
      <p>
        Watchlist targets for this refresh: <span className="mono">{result.tickers.length}</span> Kalshi ticker
        {result.tickers.length === 1 ? '' : 's'}
        {result.truncated ? ' (list truncated at cap)' : ''}
      </p>
      {issues.length ? (
        <ul className="list-disc pl-4 text-[var(--warning)]">
          {issues.slice(0, 12).map((u) => (
            <li key={`${u.entryId}-${u.reason}`}>
              {u.label} ({u.category}): {u.reason}
            </li>
          ))}
          {issues.length > 12 ? <li>…and {issues.length - 12} more</li> : null}
        </ul>
      ) : null}
    </div>
  )
}

function ExploreCard({ event }: { event: ExploreEventCard }) {
  const title = event.title || event.event_ticker
  const cov = event.coverage
  return (
    <article className="panel space-y-2 h-full flex flex-col" aria-label={title}>
      <div className="flex items-start justify-between gap-2">
        <div className="text-xs uppercase tracking-[0.14em] text-[var(--text-muted)]">
          {event.category || 'Uncategorized'} · <span className="mono normal-case tracking-normal">{event.event_ticker}</span>
        </div>
        <StarButton
          category="kalshi"
          eventKey={event.event_ticker}
          label={title}
          coverage={cov}
          eventTicker={event.event_ticker}
        />
      </div>
      <h3 className="font-semibold break-words">
        <Link to={exploreEventPath(event.event_ticker)} className="hover:underline">
          {title}
        </Link>
      </h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm flex-1">
        <dt className="text-[var(--text-muted)]">Lifecycle</dt>
        <dd>{lifecycleLabel(event.lifecycle)}</dd>
        <dt className="text-[var(--text-muted)]">Retrieved</dt>
        <dd className="mono text-xs">{event.retrieved_at ? fmtLocal(event.retrieved_at) : '—'}</dd>
        <dt className="text-[var(--text-muted)]">Freshness</dt>
        <dd>{freshnessLabel(event.retrieval_freshness)}</dd>
        <dt className="text-[var(--text-muted)]">Coverage</dt>
        <dd>{coverageSummary(cov)}</dd>
        <dt className="text-[var(--text-muted)]">Markets</dt>
        <dd>
          {event.open_contracts ?? 0} open / {event.market_count ?? 0} listed
          {event.markets_complete === false ? ' · incomplete list' : ''}
        </dd>
      </dl>
      <Link to={exploreEventPath(event.event_ticker)} className="text-xs text-[var(--accent)] hover:underline mt-auto">
        Open event →
      </Link>
    </article>
  )
}

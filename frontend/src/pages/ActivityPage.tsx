import { useCallback, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { fetchActivity, fetchActivityIds } from '../api/activity'
import { useActivityRead } from '../activity/useActivityRead'
import { EmptyState, ErrorState, LoadingState } from '../components/StatusBadge'
import { FilterBar, Pager } from '../components/sports/FilterBar'
import { useAsync } from '../components/sports/useAsync'
import { TimezoneNote } from '../components/TimezoneNote'
import { fmtLocal } from '../lib/time'
import type { ActivityItem } from '../types/activity'
import { useWatchlist } from '../watchlist/useWatchlist'

const TYPE_OPTIONS = [
  { value: 'forecast_accepted', label: 'Forecasts' },
  { value: 'settlement_confirmed', label: 'Settlements' },
  { value: 'collector_failure', label: 'Collector failures' },
  { value: 'collector_recovery', label: 'Collector recoveries' },
  { value: 'collector_skipped', label: 'Collector skipped' },
  { value: 'backup_failure', label: 'Backup failures' },
  { value: 'backup_skipped', label: 'Backup skipped' },
  { value: 'backup_ok', label: 'Backup status' },
  { value: 'explore_discovered', label: 'Explorer discoveries' },
  { value: 'explore_status_change', label: 'Status changes' },
  { value: 'explore_quote_change', label: 'Quote changes' },
]

const CATEGORY_OPTIONS = [
  { value: 'weather', label: 'Weather' },
  { value: 'sports', label: 'Sports' },
  { value: 'kalshi', label: 'Kalshi / Explore' },
  { value: 'system', label: 'System' },
]

function typeLabel(type: string): string {
  return TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type.replaceAll('_', ' ')
}

function fmtMaybe(iso: string | null | undefined): string {
  if (!iso) return 'unknown'
  return fmtLocal(iso)
}

function QuotePayload({ item }: { item: ActivityItem }) {
  const p = item.payload
  if (!p?.changes?.length) return null
  return (
    <div className="mt-2 text-xs space-y-1 text-[var(--text-muted)]">
      <div>
        Retrieved {fmtMaybe(p.old_retrieved_at)} → {fmtMaybe(p.new_retrieved_at)}
      </div>
      <ul className="list-disc pl-4">
        {p.changes.map((c) => (
          <li key={c.field}>
            <span className="text-[var(--text)]">{c.field}</span>: {String(c.from ?? '—')} → {String(c.to ?? '—')}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ActivityPage() {
  const [params, setParams] = useSearchParams()
  const { entries, persistent, problem } = useWatchlist()
  const read = useActivityRead()
  const [marking, setMarking] = useState(false)

  const setParam = useCallback(
    (updates: Record<string, string | null>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          for (const [k, v] of Object.entries(updates)) {
            if (v == null || v === '') next.delete(k)
            else next.set(k, v)
          }
          if (!('page' in updates)) next.delete('page')
          return next
        },
        { replace: true },
      )
    },
    [setParams],
  )

  const filters = useMemo(() => {
    const watchlistOnly = params.get('watchlist') === '1'
    const watched = watchlistOnly ? entries.map((e) => e.id).join(',') : undefined
    return {
      category: params.get('category') || undefined,
      type: params.get('type') || undefined,
      q: params.get('q') || undefined,
      watched: watched || undefined,
      page: Number(params.get('page') || '1') || 1,
      page_size: 40,
      watchlist_only: watchlistOnly,
    }
  }, [params, entries])

  const list = useAsync(() => fetchActivity(filters), JSON.stringify(filters))

  async function onMarkAllRead() {
    setMarking(true)
    try {
      const snap = await fetchActivityIds({
        category: filters.category,
        type: filters.type,
        q: filters.q,
        watched: filters.watched,
      })
      read.markAllRead(snap.ids)
    } finally {
      setMarking(false)
    }
  }

  const unreadOnPage =
    list.data?.items.reduce((n, it) => n + (read.isRead(it.id) ? 0 : 1), 0) ?? 0

  return (
    <div className="space-y-4 max-w-4xl">
      <header className="space-y-1">
        <h2 className="text-2xl font-semibold">Activity</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Saved forecasts, settlements, collector and backup status, and Explorer changes from explicit refreshes.
          Observation time is separate from event/source time.
        </p>
        <TimezoneNote />
      </header>

      {(!persistent || !read.persistent || problem || read.problem) && (
        <div className="panel text-sm text-[var(--text-muted)]" role="status">
          {!read.persistent || read.problem ? (
            <p>{read.problem || "Browser storage is unavailable. Read state won't persist after this tab closes."}</p>
          ) : null}
          {!persistent || problem ? <p>{problem || 'Watchlist storage is unavailable in this tab.'}</p> : null}
        </div>
      )}

      <FilterBar
        selects={[
          {
            id: 'category',
            label: 'Category',
            value: filters.category || '',
            options: CATEGORY_OPTIONS,
          },
          {
            id: 'type',
            label: 'Type',
            value: filters.type || '',
            options: TYPE_OPTIONS,
          },
          {
            id: 'watchlist',
            label: 'Watchlist',
            value: filters.watchlist_only ? '1' : '',
            options: [{ value: '1', label: 'Watchlist only' }],
            allLabel: 'All activity',
          },
        ]}
        onChange={(id, value) => setParam({ [id]: value || null })}
        search={{ label: 'Search', value: filters.q || '', placeholder: 'Title, summary, id…' }}
        onSearch={(value) => setParam({ q: value || null })}
        onReset={() => setParams(new URLSearchParams(), { replace: true })}
      >
        <button
          type="button"
          className="pager-button text-sm"
          disabled={marking || !list.data?.total}
          onClick={() => void onMarkAllRead()}
        >
          {marking ? 'Marking…' : 'Mark all read'}
        </button>
        {list.data ? (
          <span className="text-xs text-[var(--text-muted)] self-center">
            {unreadOnPage} unread on this page · {list.data.total} matching
          </span>
        ) : null}
      </FilterBar>

      {list.loading ? <LoadingState label="Loading activity" /> : null}
      {list.error ? <ErrorState message={list.error} onRetry={list.reload} /> : null}
      {list.data && list.data.total === 0 ? (
        <EmptyState
          title="No activity yet"
          detail="Activity appears when forecasts are accepted, settlements confirm, collectors fail or recover, or you run an Explorer refresh."
        />
      ) : null}

      {list.data && list.data.items.length > 0 ? (
        <ul className="space-y-2" aria-label="Activity feed">
          {list.data.items.map((it) => {
            const unread = !read.isRead(it.id)
            return (
              <li
                key={it.id}
                className={`panel ${unread ? 'border-[var(--border)]' : 'opacity-80'}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {unread ? (
                        <span className="badge badge-info" title="Unread">
                          New
                        </span>
                      ) : null}
                      <span className="badge badge-info">{typeLabel(it.type)}</span>
                      <span className="text-xs uppercase tracking-wide text-[var(--text-muted)]">{it.category}</span>
                    </div>
                    <Link to={it.href} className="mt-1 block font-medium text-[var(--text)] hover:underline" onClick={() => read.markRead(it.id)}>
                      {it.title}
                    </Link>
                    <p className="mt-1 text-sm text-[var(--text-muted)]">{it.summary}</p>
                    {it.type === 'explore_quote_change' ? <QuotePayload item={it} /> : null}
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
                      <span>Observed {fmtMaybe(it.observed_at)}</span>
                      <span>Source {fmtMaybe(it.source_at)}</span>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="pager-button text-xs shrink-0"
                    onClick={() => (unread ? read.markRead(it.id) : read.markUnread(it.id))}
                  >
                    {unread ? 'Mark read' : 'Mark unread'}
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      ) : null}

      {list.data && list.data.pages > 1 ? (
        <Pager
          page={list.data.page}
          pages={list.data.pages}
          total={list.data.total}
          noun="items"
          onPage={(page) => setParam({ page: String(page) })}
        />
      ) : null}
    </div>
  )
}

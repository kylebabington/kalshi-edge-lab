import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { fetchLiveWeather, fetchSavedWeatherEvents } from '../api/weather'
import { LiveWeatherCard, SavedWeatherCard } from '../components/eventCards'
import { EmptyState, ErrorState, LoadingState } from '../components/StatusBadge'
import { TimezoneNote } from '../components/TimezoneNote'
import { DataStamp } from '../components/sports/DataStamp'
import { FilterBar, Pager } from '../components/sports/FilterBar'
import { Tabs } from '../components/sports/Tabs'
import { useAsync } from '../components/sports/useAsync'
import { WEATHER_VIEW_LABELS } from '../lib/labels'

const TABS = [
  { id: 'saved', label: 'Saved forecasts' },
  { id: 'open', label: 'Open markets (current data)' },
]

const SORTS = [
  { value: 'target_asc', label: 'Target date (earliest first)' },
  { value: 'target_desc', label: 'Target date (latest first)' },
  { value: 'capture_desc', label: 'Latest forecast saved' },
  { value: 'settlement_desc', label: 'Settlement confirmed (most recent)' },
]

export function MarketsPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'open' ? 'open' : 'saved'
  const setParam = useCallback(
    (updates: Record<string, string>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          for (const [k, v] of Object.entries(updates)) {
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
  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold">Weather events</h2>
        <p className="text-sm text-[var(--text-muted)]">
          NYC daily high (KXHIGHNY). <strong>Saved forecasts</strong> are immutable snapshots recorded before each
          outcome. <strong>Open markets</strong> show current market and weather data fetched on request. Research only.
        </p>
        <TimezoneNote />
      </header>
      <Tabs
        tabs={TABS}
        active={tab}
        label="Weather views"
        idPrefix="weather"
        onChange={(id) => setParams(id === 'saved' ? {} : { tab: id }, { replace: true })}
      />
      <div role="tabpanel" id={`weather-panel-${tab}`} aria-labelledby={`weather-tab-${tab}`} className="space-y-4">
        {tab === 'saved' ? <SavedTab params={params} setParam={setParam} /> : <OpenTab />}
      </div>
    </div>
  )
}

function SavedTab({ params, setParam }: { params: URLSearchParams; setParam: (u: Record<string, string>) => void }) {
  const filters = {
    status: params.get('status') ?? '',
    q: params.get('q') ?? '',
    sort: params.get('sort') ?? '',
    page: Number(params.get('page') ?? '1') || 1,
  }
  const state = useAsync(() => fetchSavedWeatherEvents({ ...filters, page_size: 24 }), JSON.stringify(filters))
  const data = state.data
  const onSearch = useCallback((q: string) => setParam({ q }), [setParam])
  return (
    <>
      <FilterBar
        selects={[
          {
            id: 'status',
            label: 'Status',
            value: filters.status,
            allLabel: 'All saved events',
            options: (['upcoming', 'awaiting_settlement', 'settled'] as const).map((v) => ({
              value: v,
              label: `${WEATHER_VIEW_LABELS[v]}${data ? ` (${data.views[v] ?? 0})` : ''}`,
            })),
          },
          { id: 'sort', label: 'Sort by', value: filters.sort, allLabel: 'Default (upcoming first)', options: SORTS },
        ]}
        onChange={(id, value) => setParam({ [id]: value })}
        search={{ label: 'Search events', value: filters.q, placeholder: 'Ticker or date, e.g. OCT12 or 2026-10' }}
        onSearch={onSearch}
      />
      {data ? <DataStamp generatedAt={data.generated_at} loadedAt={state.loadedAt} source={data.source} /> : null}
      {state.loading ? <LoadingState label="Loading saved weather forecasts…" /> : null}
      {state.error ? <ErrorState message={state.error} onRetry={state.reload} /> : null}
      {data && !state.loading && !state.error ? (
        data.artifacts.snapshots === 'missing' ? (
          <EmptyState
            title="No saved weather forecasts on this machine"
            detail="Saved snapshots appear after the prospective cycle captures a checkpoint."
          />
        ) : data.items.length === 0 ? (
          <EmptyState title="No saved forecasts match these filters" detail="Clear a filter or search term to see more events." />
        ) : (
          <>
            <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {data.items.map((e) => (
                <li key={e.event_ticker}>
                  <SavedWeatherCard event={e} />
                </li>
              ))}
            </ul>
            <Pager page={data.page} pages={data.pages} total={data.total} noun="events" onPage={(p) => setParam({ page: String(p) })} />
          </>
        )
      ) : null}
    </>
  )
}

function OpenTab() {
  const state = useAsync(fetchLiveWeather, 'weather-live')
  const data = state.data
  return (
    <>
      <p className="text-xs text-[var(--text-muted)]">
        Fetched from Kalshi and live weather sources when requested; can take a while. Values change as new data
        arrives and are not saved forecasts.
      </p>
      {data ? <DataStamp generatedAt={data.generated_at} loadedAt={state.loadedAt} source="live sources" /> : null}
      {state.loading ? <LoadingState label="Loading open weather markets (live sources)…" /> : null}
      {state.error ? <ErrorState title="Live weather unavailable" message={state.error} onRetry={state.reload} /> : null}
      {data && !state.loading && !state.error ? (
        data.events.length === 0 ? (
          <EmptyState title="No open KXHIGHNY events" detail="Open markets appear here when Kalshi lists them." />
        ) : (
          <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {data.events.map((e) => (
              <li key={e.event_ticker}>
                <LiveWeatherCard event={e} />
              </li>
            ))}
          </ul>
        )
      ) : null}
    </>
  )
}

import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { fetchSportsCollector, fetchSportsEvents } from '../api/sports'
import { fetchLiveWeather, fetchSavedWeatherEvents, fetchWeatherCollector } from '../api/weather'
import { LiveWeatherCard, SavedWeatherCard, SportsCard } from '../components/eventCards'
import { SportsHealthPanel, WeatherHealthPanel } from '../components/HealthPanels'
import { sportsHealth, weatherHealth } from '../lib/health'
import { EmptyState, ErrorState, LoadingState, StaleNotice, StatusBadge } from '../components/StatusBadge'
import { TimezoneNote } from '../components/TimezoneNote'
import { DataStamp } from '../components/sports/DataStamp'
import { useAsync, type AsyncState } from '../components/sports/useAsync'
import type { SportsEventItem } from '../types/sports'
import type { ModelSummary, SavedWeatherEvent } from '../types/weather'

type Props = {
  summary: ModelSummary | null
  summaryLoading: boolean
  summaryError: string | null
}

const GROUP_SIZE = 6

export function DashboardPage(props: Props) {
  return (
    <div className="space-y-8">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold">Overview</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Weather and sports load separately, so a slow or failed request in one never blocks the other. Research only —
          no bets, no recommendations.
        </p>
        <TimezoneNote />
      </header>
      <WeatherOverview {...props} />
      <SportsOverview />
    </div>
  )
}

function SectionHeader({ id, title, href, linkText, children }: { id: string; title: string; href: string; linkText: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <div>
        <h2 id={id} className="text-lg font-semibold">
          {title}
        </h2>
        {children}
      </div>
      <Link to={href} className="text-sm text-[var(--accent)] hover:underline">
        {linkText} →
      </Link>
    </div>
  )
}

type ListPayload<T> = { items: T[]; total: number; generated_at: string; source?: string }

function EventGroup<T>({
  title,
  description,
  state,
  render,
  empty,
  viewAll,
}: {
  title: string
  description: string
  state: AsyncState<ListPayload<T>>
  render: (item: T) => ReactNode
  empty: string
  viewAll: string
}) {
  const data = state.data
  return (
    <section className="space-y-2" aria-label={title}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">
          {title}
          {data ? <span className="ml-2 text-xs text-[var(--text-muted)] mono">{data.total}</span> : null}
        </h3>
        {data && data.total > data.items.length ? (
          <Link to={viewAll} className="text-xs text-[var(--accent)] hover:underline">
            View all {data.total} →
          </Link>
        ) : null}
      </div>
      <p className="text-xs text-[var(--text-muted)]">{description}</p>
      {state.loading && !data ? <LoadingState label={`Loading ${title.toLowerCase()}…`} /> : null}
      {state.error ? <ErrorState message={state.error} onRetry={state.reload} /> : null}
      {data && !state.error ? (
        data.items.length === 0 ? (
          <EmptyState title={`No ${title.toLowerCase()}`} detail={empty} />
        ) : (
          <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {data.items.map((item, i) => (
              <li key={i}>{render(item)}</li>
            ))}
          </ul>
        )
      ) : null}
    </section>
  )
}

// ---------------------------------------------------------------- weather

function useSavedWeather(status: string, sort: string) {
  return useAsync(() => fetchSavedWeatherEvents({ status, sort, page_size: GROUP_SIZE }), `weather-${status}-${sort}`)
}

function WeatherOverview({ summary, summaryLoading, summaryError }: Props) {
  const collector = useAsync(fetchWeatherCollector, 'weather-collector')
  const upcoming = useSavedWeather('upcoming', 'target_asc')
  const pending = useSavedWeather('awaiting_settlement', 'target_desc')
  const settled = useSavedWeather('settled', 'settlement_desc')
  const live = useAsync(fetchLiveWeather, 'weather-live')
  const health = collector.data ? weatherHealth(collector.data) : null
  const renderSaved = (e: SavedWeatherEvent) => <SavedWeatherCard event={e} />
  const missing = upcoming.data?.artifacts.snapshots === 'missing'
  return (
    <section className="space-y-5" aria-labelledby="weather-overview">
      <SectionHeader id="weather-overview" title="Weather" href="/markets?tab=saved" linkText="Browse weather events">
        <p className="text-xs text-[var(--text-muted)]">NYC daily high (KXHIGHNY). Saved forecasts are immutable snapshots.</p>
      </SectionHeader>
      <WeatherHealthPanel state={collector} />
      {health?.state === 'stale' ? (
        <StaleNotice>
          {health.reason}. Saved forecasts below are still valid records, but new checkpoints may be missing.
        </StaleNotice>
      ) : null}
      {missing ? (
        <EmptyState title="No saved weather forecasts on this machine" detail="Saved snapshots appear after the prospective cycle captures a checkpoint." />
      ) : (
        <>
          <EventGroup
            title="Upcoming captured events"
            description="Target date today or later, with at least one saved forecast."
            state={upcoming}
            render={renderSaved}
            empty="No saved forecast for an event that has not happened yet."
            viewAll="/markets?tab=saved&status=upcoming"
          />
          <EventGroup
            title="Pending settlement"
            description="Target date has passed; no valid score confirms the outcome yet."
            state={pending}
            render={renderSaved}
            empty="Every past event with a saved forecast has a confirmed outcome."
            viewAll="/markets?tab=saved&status=awaiting_settlement"
          />
          <EventGroup
            title="Recently settled"
            description="Outcome confirmed by a valid score. The latest snapshot's own score is shown separately."
            state={settled}
            render={renderSaved}
            empty="No saved forecast has a confirmed outcome yet."
            viewAll="/markets?tab=saved&status=settled&sort=settlement_desc"
          />
        </>
      )}
      {upcoming.data ? <DataStamp generatedAt={upcoming.data.generated_at} loadedAt={upcoming.loadedAt} source={upcoming.data.source} /> : null}
      <section className="space-y-2" aria-label="Open markets">
        <h3 className="font-semibold">Open markets</h3>
        <p className="text-xs text-[var(--text-muted)]">
          Current market data and live weather sources, fetched on request (can be slow). Not a saved forecast.
        </p>
        {live.loading && !live.data ? <LoadingState label="Loading open weather markets (live sources)…" /> : null}
        {live.error ? <ErrorState title="Live weather unavailable" message={live.error} onRetry={live.reload} /> : null}
        {live.data && !live.error ? (
          live.data.events.length === 0 ? (
            <EmptyState title="No open KXHIGHNY events" detail="Open markets appear here when Kalshi lists them." />
          ) : (
            <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {live.data.events.map((e) => (
                <li key={e.event_ticker}>
                  <LiveWeatherCard event={e} />
                </li>
              ))}
            </ul>
          )
        ) : null}
        {live.data ? <DataStamp generatedAt={live.data.generated_at} loadedAt={live.loadedAt} source="live sources" /> : null}
      </section>
      <WeatherModelPanels summary={summary} loading={summaryLoading} error={summaryError} />
    </section>
  )
}

function WeatherModelPanels({ summary, loading, error }: { summary: ModelSummary | null; loading: boolean; error: string | null }) {
  if (loading) return <LoadingState label="Loading weather model artifacts…" />
  if (error) return <ErrorState title="Weather model summary unavailable" message={error} />
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="panel">
        <h3 className="font-semibold">Weather model artifacts</h3>
        {summary?.model_summary_available ? (
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between gap-3">
              <dt className="text-[var(--text-muted)]">Calibration rows</dt>
              <dd className="mono">{String(summary.calibration.row_count ?? 'unknown')}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-[var(--text-muted)]">Usable CLINYC residuals</dt>
              <dd className="mono">{String(summary.calibration.usable_clinyc_residuals ?? 'unknown')}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-[var(--text-muted)]">Transfer status</dt>
              <dd>
                <StatusBadge
                  status={
                    summary.settlement_regime?.transfer_validated
                      ? 'VALIDATED'
                      : String(summary.settlement_transfer?.transfer_status ?? summary.settlement_regime?.transfer_status ?? 'EXPERIMENTAL').toUpperCase()
                  }
                />
              </dd>
            </div>
          </dl>
        ) : (
          <p className="mt-3 text-sm text-[var(--text-muted)]">No calibration or backtest artifacts on this machine.</p>
        )}
      </div>
      <div className="panel">
        <h3 className="font-semibold">Research status</h3>
        <ul className="mt-3 space-y-2 text-sm">
          <li className="flex justify-between gap-3">
            <span>Weather probability model</span>
            <StatusBadge status={summary?.research_status?.weather_phase_1 ?? 'unknown'} />
          </li>
          <li className="flex justify-between gap-3">
            <span>CLINYC transfer validation</span>
            <StatusBadge status={summary?.research_status?.clinyc_target_calibration ?? 'unknown'} />
          </li>
          <li className="flex justify-between gap-3">
            <span>Trade recommendations</span>
            <StatusBadge status="DISABLED" />
          </li>
        </ul>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- sports

function useSportsGroup(status: string, sort: string) {
  return useAsync(() => fetchSportsEvents({ status, sort, page_size: GROUP_SIZE }), `sports-${status}-${sort}`)
}

function SportsOverview() {
  const collector = useAsync(fetchSportsCollector, 'sports-collector')
  const upcoming = useSportsGroup('upcoming', 'start_asc')
  const pending = useSportsGroup('awaiting_settlement', 'start_desc')
  const settled = useSportsGroup('settled', 'settlement_desc')
  const health = collector.data ? sportsHealth(collector.data) : null
  const renderSports = (e: SportsEventItem) => <SportsCard event={e} />
  const missing = upcoming.data?.artifacts.capture_records === 'missing'
  return (
    <section className="space-y-5" aria-labelledby="sports-overview">
      <SectionHeader id="sports-overview" title="Sports" href="/sports" linkText="Browse sports events">
        <p className="text-xs text-[var(--text-muted)]">
          Frozen <span className="mono">sports_phase2_capture_v2</span> candidates captured inside each event's window.
        </p>
      </SectionHeader>
      <SportsHealthPanel state={collector} />
      {health?.state === 'stale' ? (
        <StaleNotice>{health.reason}. Upcoming events may be missing captures until the collector runs again.</StaleNotice>
      ) : null}
      {missing ? (
        <EmptyState title="No saved sports captures on this machine" detail="Captures appear after the collector records an event inside its window." />
      ) : (
        <>
          <EventGroup
            title="Upcoming captured events"
            description="Forecast saved; the event has not started yet."
            state={upcoming}
            render={renderSports}
            empty="No captured event is still upcoming."
            viewAll="/sports?tab=events&status=upcoming&sort=start_asc"
          />
          <EventGroup
            title="Pending settlement"
            description="Started or finished; some captured contracts are not final yet."
            state={pending}
            render={renderSports}
            empty="No captured event is waiting for settlement."
            viewAll="/sports?tab=events&status=awaiting_settlement"
          />
          <EventGroup
            title="Recently settled"
            description="Every captured contract is final, most recent Kalshi settlement first."
            state={settled}
            render={renderSports}
            empty="No captured event has fully settled yet."
            viewAll="/sports?tab=events&status=settled&sort=settlement_desc"
          />
        </>
      )}
      {upcoming.data ? <DataStamp generatedAt={upcoming.data.generated_at} loadedAt={upcoming.loadedAt} source={upcoming.data.source} /> : null}
    </section>
  )
}

import { useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { eventPath, fetchSportsCollector, fetchSportsEvents } from '../api/sports'
import { fetchSavedWeatherEvents, fetchWeatherCollector, weatherEventPath } from '../api/weather'
import { EmptyState, ErrorState, LoadingState, StatusBadge } from '../components/StatusBadge'
import { TimezoneNote } from '../components/TimezoneNote'
import { useAsync, type AsyncState } from '../components/sports/useAsync'
import { PinnedSearchesSection } from '../savedSearches/PinnedSearchPanel'
import { useSavedSearches } from '../savedSearches/useSavedSearches'
import {
  sportsBackupHealth,
  sportsHealth,
  weatherHealth,
  type CollectorHealth,
  type HealthState,
} from '../lib/health'
import { SPORTS_VIEW_LABELS, WEATHER_VIEW_LABELS, sportsEventTitle } from '../lib/labels'
import { fmtLocal, fmtTargetDate } from '../lib/time'
import { weatherTitle } from '../lib/weatherText'
import type { SportsCollectorStatus, SportsEventItem, SportsEventList } from '../types/sports'
import type { SavedWeatherEvent, SavedWeatherEventList, WeatherCollectorStatus } from '../types/weather'

const PREVIEW = 4

type Tone = 'healthy' | 'warning' | 'stale' | 'error'

const TONE_LABEL: Record<Tone, string> = {
  healthy: 'Healthy',
  warning: 'Warning',
  stale: 'Stale',
  error: 'Error',
}

type Attention = { id: string; message: string; href?: string }

type Props = { apiStatus?: string }

function toneOf(state: HealthState): Tone {
  if (state === 'healthy') return 'healthy'
  if (state === 'stale') return 'stale'
  return 'warning'
}

function apiTone(status: string): Tone | 'loading' {
  if (status === 'loading') return 'loading'
  if (status === 'ok' || status === 'mock') return 'healthy'
  if (status === 'down') return 'error'
  return 'warning'
}

function healthLine(name: string, health: CollectorHealth): Attention | null {
  if (health.state === 'healthy') return null
  return { id: `${name}-health`, message: `${name}: ${health.reason}.` }
}

/**
 * Attention is derived from collector health and the unfiltered view counts on the
 * upcoming lists. Lifetime missed-window totals are not listed: only misses in the
 * last 24 hours, missing artifact directories, and events still awaiting settlement.
 */
function buildAttention({
  apiStatus,
  weatherCollector,
  sportsCollector,
  weatherUpcoming,
  weatherSettled,
  sportsUpcoming,
  sportsSettled,
}: {
  apiStatus: string
  weatherCollector: AsyncState<WeatherCollectorStatus>
  sportsCollector: AsyncState<SportsCollectorStatus>
  weatherUpcoming: AsyncState<SavedWeatherEventList>
  weatherSettled: AsyncState<SavedWeatherEventList>
  sportsUpcoming: AsyncState<SportsEventList>
  sportsSettled: AsyncState<SportsEventList>
}): Attention[] {
  const items: Attention[] = []
  const api = apiTone(apiStatus)
  if (api === 'error') items.push({ id: 'api', message: 'API is not responding.' })
  else if (api === 'warning') items.push({ id: 'api', message: `API status is ${apiStatus}.` })

  if (!weatherCollector.loading) {
    if (weatherCollector.error) {
      items.push({ id: 'weather-collector-error', message: `Weather collector status unavailable: ${weatherCollector.error}` })
    } else if (weatherCollector.data) {
      const line = healthLine('Weather collector', weatherHealth(weatherCollector.data))
      if (line) items.push(line)
      const missed = weatherCollector.data.forecast_freshness.missed_checkpoints_24h
      if (missed > 0) {
        items.push({
          id: 'weather-checkpoints',
          message: `${missed} missed weather ${missed === 1 ? 'checkpoint' : 'checkpoints'} in the last 24 hours.`,
          href: '/markets?tab=saved',
        })
      }
    }
  }

  if (!weatherUpcoming.loading) {
    if (weatherUpcoming.error) {
      items.push({ id: 'weather-upcoming-error', message: `Upcoming weather events failed to load: ${weatherUpcoming.error}` })
    } else if (weatherUpcoming.data?.artifacts.snapshots === 'missing') {
      items.push({
        id: 'weather-missing',
        message: 'No saved weather forecasts on this machine.',
        href: '/markets?tab=saved',
      })
    } else if ((weatherUpcoming.data?.views.awaiting_settlement ?? 0) > 0) {
      const n = weatherUpcoming.data!.views.awaiting_settlement
      items.push({
        id: 'weather-pending',
        message: `${n} weather ${n === 1 ? 'event is' : 'events are'} awaiting settlement.`,
        href: '/markets?tab=saved&status=awaiting_settlement',
      })
    }
  }
  if (!weatherSettled.loading && weatherSettled.error) {
    items.push({ id: 'weather-settled-error', message: `Settled weather events failed to load: ${weatherSettled.error}` })
  }

  if (!sportsCollector.loading) {
    if (sportsCollector.error) {
      items.push({ id: 'sports-collector-error', message: `Sports collector status unavailable: ${sportsCollector.error}` })
    } else if (sportsCollector.data) {
      const line = healthLine('Sports collector', sportsHealth(sportsCollector.data))
      if (line) items.push(line)
      const backup = sportsBackupHealth(sportsCollector.data)
      if (backup.state !== 'healthy' && !backup.initial) {
        items.push({ id: 'sports-backup', message: `Sports backup: ${backup.reason}.` })
      }
      const misses = sportsCollector.data.missed_windows.last_24h
      if (misses > 0) {
        items.push({
          id: 'sports-misses',
          message: `${misses} sports deadline ${misses === 1 ? 'miss' : 'misses'} in the last 24 hours.`,
          href: '/sports?tab=events&status=missed',
        })
      }
    }
  }

  if (!sportsUpcoming.loading) {
    if (sportsUpcoming.error) {
      items.push({ id: 'sports-upcoming-error', message: `Upcoming sports events failed to load: ${sportsUpcoming.error}` })
    } else if (sportsUpcoming.data?.artifacts.capture_records === 'missing') {
      items.push({
        id: 'sports-missing',
        message: 'No saved sports captures on this machine.',
        href: '/sports?tab=events',
      })
    } else if ((sportsUpcoming.data?.views.awaiting_settlement ?? 0) > 0) {
      const n = sportsUpcoming.data!.views.awaiting_settlement
      items.push({
        id: 'sports-pending',
        message: `${n} sports ${n === 1 ? 'event is' : 'events are'} awaiting settlement.`,
        href: '/sports?tab=events&status=awaiting_settlement',
      })
    }
  }
  if (!sportsSettled.loading && sportsSettled.error) {
    items.push({ id: 'sports-settled-error', message: `Settled sports events failed to load: ${sportsSettled.error}` })
  }

  return items
}

export function DashboardPage({ apiStatus = 'unknown' }: Props) {
  const { entries: savedEntries } = useSavedSearches()
  const pinned = useMemo(
    () =>
      savedEntries
        .filter((e) => e.pin_order != null)
        .sort((a, b) => (a.pin_order ?? 0) - (b.pin_order ?? 0) || a.name.localeCompare(b.name)),
    [savedEntries],
  )
  const weatherCollector = useAsync(fetchWeatherCollector, 'dash-weather-collector')
  const sportsCollector = useAsync(fetchSportsCollector, 'dash-sports-collector')
  const weatherUpcoming = useAsync(
    () => fetchSavedWeatherEvents({ status: 'upcoming', sort: 'target_asc', page_size: PREVIEW }),
    'dash-weather-upcoming',
  )
  const weatherSettled = useAsync(
    () => fetchSavedWeatherEvents({ status: 'settled', sort: 'settlement_desc', page_size: PREVIEW }),
    'dash-weather-settled',
  )
  const sportsUpcoming = useAsync(
    () => fetchSportsEvents({ status: 'upcoming', sort: 'start_asc', page_size: PREVIEW }),
    'dash-sports-upcoming',
  )
  const sportsSettled = useAsync(
    () => fetchSportsEvents({ status: 'settled', sort: 'settlement_desc', page_size: PREVIEW }),
    'dash-sports-settled',
  )

  const attention = buildAttention({
    apiStatus,
    weatherCollector,
    sportsCollector,
    weatherUpcoming,
    weatherSettled,
    sportsUpcoming,
    sportsSettled,
  })
  const checking =
    weatherCollector.loading ||
    sportsCollector.loading ||
    weatherUpcoming.loading ||
    weatherSettled.loading ||
    sportsUpcoming.loading ||
    sportsSettled.loading

  return (
    <div className="space-y-8">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold break-words">Overview</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Saved weather and sports captures, collector status, pinned Explorer searches, and recent activity.
        </p>
        <TimezoneNote />
      </header>

      <PinnedSearchesSection searches={pinned} />

      <section className="space-y-3" aria-labelledby="system-status">
        <h2 id="system-status" className="text-lg font-semibold">
          System status
        </h2>
        <ul className="grid gap-3 lg:grid-cols-3">
          <StatusCard
            label="API"
            tone={apiTone(apiStatus)}
            detail={apiStatus === 'loading' ? 'Checking API…' : `Reported status: ${apiStatus}`}
          />
          <CollectorCard
            label="Weather collector"
            state={weatherCollector}
            health={weatherCollector.data ? weatherHealth(weatherCollector.data) : null}
          />
          <CollectorCard
            label="Sports collector"
            state={sportsCollector}
            health={sportsCollector.data ? sportsHealth(sportsCollector.data) : null}
          />
        </ul>
      </section>

      <section className="space-y-3" aria-labelledby="needs-attention">
        <h2 id="needs-attention" className="text-lg font-semibold">
          Needs attention
        </h2>
        {attention.length ? (
          <ul className="space-y-2" aria-label="Attention items">
            {attention.map((item) => (
              <li key={item.id} className="panel flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="break-words">{item.message}</span>
                {item.href ? (
                  <Link
                    to={item.href}
                    className="text-[var(--accent)] hover:underline shrink-0 inline-flex items-center min-h-[2.25rem] px-1"
                  >
                    View
                  </Link>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        {checking ? <LoadingState label="Checking collectors and saved events…" /> : null}
        {!checking && attention.length === 0 ? (
          <EmptyState
            title="Nothing needs attention"
            detail="Collectors are current, saved data is available, and nothing is waiting on settlement."
          />
        ) : null}
      </section>

      <section className="space-y-4" aria-labelledby="upcoming-heading">
        <h2 id="upcoming-heading" className="text-lg font-semibold">
          Upcoming
        </h2>
        <CategoryPreview
          title="Weather"
          viewAll="/markets?tab=saved&status=upcoming"
          state={weatherUpcoming}
          missing={weatherUpcoming.data?.artifacts.snapshots === 'missing'}
          missingTitle="No saved weather forecasts on this machine"
          missingDetail="Saved snapshots appear after the prospective cycle captures a checkpoint."
          emptyTitle="No upcoming weather events"
          emptyDetail="No saved forecast for an event that has not happened yet."
          rows={(weatherUpcoming.data?.items ?? []).slice(0, PREVIEW).map((event) => (
            <CompactWeather key={event.event_ticker} event={event} />
          ))}
        />
        <CategoryPreview
          title="Sports"
          viewAll="/sports?tab=events&status=upcoming&sort=start_asc"
          state={sportsUpcoming}
          missing={sportsUpcoming.data?.artifacts.capture_records === 'missing'}
          missingTitle="No saved sports captures on this machine"
          missingDetail="Captures appear after the collector records an event inside its window."
          emptyTitle="No upcoming sports events"
          emptyDetail="No captured event is still upcoming."
          rows={(sportsUpcoming.data?.items ?? []).slice(0, PREVIEW).map((event) => (
            <CompactSports key={event.event_id} event={event} />
          ))}
        />
      </section>

      <section className="space-y-4" aria-labelledby="settled-heading">
        <h2 id="settled-heading" className="text-lg font-semibold">
          Recently settled
        </h2>
        <CategoryPreview
          title="Weather"
          viewAll="/markets?tab=saved&status=settled&sort=settlement_desc"
          state={weatherSettled}
          missing={weatherSettled.data?.artifacts.snapshots === 'missing'}
          missingTitle="No saved weather forecasts on this machine"
          missingDetail="Saved snapshots appear after the prospective cycle captures a checkpoint."
          emptyTitle="No recently settled weather events"
          emptyDetail="No saved forecast has a confirmed outcome yet."
          rows={(weatherSettled.data?.items ?? []).slice(0, PREVIEW).map((event) => (
            <CompactWeather key={event.event_ticker} event={event} />
          ))}
        />
        <CategoryPreview
          title="Sports"
          viewAll="/sports?tab=events&status=settled&sort=settlement_desc"
          state={sportsSettled}
          missing={sportsSettled.data?.artifacts.capture_records === 'missing'}
          missingTitle="No saved sports captures on this machine"
          missingDetail="Captures appear after the collector records an event inside its window."
          emptyTitle="No recently settled sports events"
          emptyDetail="No captured event has fully settled yet."
          rows={(sportsSettled.data?.items ?? []).slice(0, PREVIEW).map((event) => (
            <CompactSports key={event.event_id} event={event} />
          ))}
        />
      </section>
    </div>
  )
}

function StatusCard({ label, tone, detail, onRetry }: { label: string; tone: Tone | 'loading'; detail: string; onRetry?: () => void }) {
  return (
    <li className="panel space-y-2" aria-label={label}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{label}</h3>
        {tone === 'loading' ? (
          <span className="text-xs text-[var(--text-muted)]">Checking…</span>
        ) : (
          <StatusBadge status={TONE_LABEL[tone]} />
        )}
      </div>
      <p className="text-xs text-[var(--text-muted)] break-words">{detail}</p>
      {onRetry ? (
        <button type="button" className="pager-button text-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </li>
  )
}

function CollectorCard<T>({
  label,
  state,
  health,
}: {
  label: string
  state: AsyncState<T>
  health: CollectorHealth | null
}) {
  if (state.loading && !state.data) {
    return <StatusCard label={label} tone="loading" detail="Loading collector status…" />
  }
  if (state.error) {
    return <StatusCard label={label} tone="error" detail={state.error} onRetry={state.reload} />
  }
  if (!health) return <StatusCard label={label} tone="warning" detail="Collector status has not loaded." />
  return <StatusCard label={label} tone={toneOf(health.state)} detail={health.reason} />
}

function CategoryPreview({
  title,
  viewAll,
  state,
  missing,
  missingTitle,
  missingDetail,
  emptyTitle,
  emptyDetail,
  rows,
}: {
  title: string
  viewAll: string
  state: AsyncState<{ items: unknown[] }>
  missing: boolean
  missingTitle: string
  missingDetail: string
  emptyTitle: string
  emptyDetail: string
  rows: ReactNode[]
}) {
  const count = state.data?.items.length ?? 0
  return (
    <div className="space-y-2" aria-label={title}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">{title}</h3>
        <Link
          to={viewAll}
          className="text-sm text-[var(--accent)] hover:underline inline-flex items-center min-h-[2.25rem] px-1"
          aria-label={`View all ${title}`}
        >
          View all
        </Link>
      </div>
      {state.loading && !state.data ? <LoadingState label={`Loading ${title.toLowerCase()}…`} /> : null}
      {state.error ? <ErrorState message={state.error} onRetry={state.reload} /> : null}
      {state.data && !state.error ? (
        missing ? (
          <EmptyState title={missingTitle} detail={missingDetail} />
        ) : count === 0 ? (
          <EmptyState title={emptyTitle} detail={emptyDetail} />
        ) : (
          <ul className="grid gap-3 md:grid-cols-2">{rows}</ul>
        )
      ) : null}
    </div>
  )
}

function CompactWeather({ event }: { event: SavedWeatherEvent }) {
  const title = weatherTitle(event.event_ticker, event.target_date)
  return (
    <CompactRow
      href={weatherEventPath(event.event_ticker)}
      title={title}
      meta={fmtTargetDate(event.target_date)}
      status={WEATHER_VIEW_LABELS[event.view] ?? event.view}
    />
  )
}

function CompactSports({ event }: { event: SportsEventItem }) {
  const title = sportsEventTitle(event)
  return (
    <CompactRow
      href={eventPath(event.event_id)}
      title={title}
      meta={event.event_start ? `Starts ${fmtLocal(event.event_start)}` : 'Start time unavailable'}
      status={SPORTS_VIEW_LABELS[event.view] ?? event.view}
    />
  )
}

function CompactRow({ href, title, meta, status }: { href: string; title: string; meta: string; status: string }) {
  return (
    <li>
      <article className="panel flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <Link to={href} className="font-medium break-words hover:underline" aria-label={`Open ${title}`}>
            {title}
          </Link>
          <p className="text-xs text-[var(--text-muted)] break-words">{meta}</p>
        </div>
        <StatusBadge status={status} />
      </article>
    </li>
  )
}

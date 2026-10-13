import { useParams } from 'react-router-dom'
import { isNotFound } from '../api/client'
import { fetchLiveWeather, fetchSavedWeatherEvent } from '../api/weather'
import { EmptyState, ErrorState, LoadingState } from '../components/StatusBadge'
import { weatherTitle } from '../lib/weatherText'
import { useAsync } from '../components/sports/useAsync'
import { StarButton } from '../watchlist/StarButton'
import { BackLink } from './BackLink'
import { MarketDetailPage } from './MarketDetailPage'
import { SavedWeatherEventView } from './SavedWeatherEventView'

/**
 * Saved forecasts load first (fast, local). Live market data is only requested when the event may still be open
 * (no saved record, or the target date has not passed), so a settled event never waits on live sources.
 */
export function WeatherEventRoute() {
  const { eventTicker = '' } = useParams()
  const saved = useAsync(() => fetchSavedWeatherEvent(eventTicker), `saved-${eventTicker}`)
  const savedMissing = saved.error != null && isNotFound(saved.errorValue)
  const savedDone = !saved.loading
  const needLive = savedDone && (saved.error != null || saved.data?.event.view === 'upcoming')
  const live = useAsync(async () => (needLive ? fetchLiveWeather() : null), `live-${eventTicker}-${needLive}`)
  const open = live.data?.events.find((e) => e.event_ticker === eventTicker) ?? null

  if (!savedDone || (needLive && live.loading)) {
    return <LoadingState label={needLive ? 'Checking open markets (live sources)…' : 'Loading saved forecasts…'} />
  }
  if (open) {
    const title = weatherTitle(open.event_ticker, open.target_date ?? open.prediction.target_date)
    return (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <BackLink fallback="/markets?tab=open" label="Weather events" />
          <StarButton category="weather" eventKey={open.event_ticker} label={title} size="md" />
        </div>
        <MarketDetailPage loading={false} error={null} event={open} />
      </div>
    )
  }
  if (saved.data) return <SavedWeatherEventView data={saved.data} loadedAt={saved.loadedAt} />
  return (
    <div className="space-y-3">
      <BackLink fallback="/markets" label="Weather events" />
      {!savedMissing && saved.error ? (
        <ErrorState title="Saved forecasts unavailable" message={saved.error} onRetry={saved.reload} />
      ) : null}
      {live.error ? <ErrorState title="Live weather unavailable" message={live.error} onRetry={live.reload} /> : null}
      {savedMissing && !live.error ? (
        <EmptyState
          title="Weather event not found"
          detail={`No saved forecast for ${eventTicker} on this machine, and it is not among the currently open markets.`}
        />
      ) : null}
    </div>
  )
}

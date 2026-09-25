import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { fetchHealth, fetchLiveWeather, fetchModelSummary, fetchWeatherEvent } from './api/weather'
import { isMockMode } from './api/client'
import type { LiveWeatherResponse, ModelSummary, WeatherEvent } from './types/weather'
import { DashboardPage } from './pages/DashboardPage'
import { MarketsPage } from './pages/MarketsPage'
import { MarketDetailPage } from './pages/MarketDetailPage'
import { WeatherModelPage } from './pages/WeatherModelPage'
import { ResearchPage } from './pages/ResearchPage'

function MarketDetailRoute({
  live,
  liveError,
}: {
  live: LiveWeatherResponse | null
  liveError: string | null
}) {
  const { eventTicker = '' } = useParams()
  const [event, setEvent] = useState<WeatherEvent | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const fromLive = live?.events.find((e) => e.event_ticker === eventTicker) ?? null
        if (fromLive) {
          if (!cancelled) setEvent(fromLive)
        } else {
          const detail = await fetchWeatherEvent(eventTicker)
          if (!cancelled) setEvent(detail)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [eventTicker, live])

  return (
    <MarketDetailPage
      loading={loading}
      error={error ?? liveError}
      event={event}
    />
  )
}

export default function App() {
  const [live, setLive] = useState<LiveWeatherResponse | null>(null)
  const [summary, setSummary] = useState<ModelSummary | null>(null)
  const [liveLoading, setLiveLoading] = useState(true)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [liveError, setLiveError] = useState<string | null>(null)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const [apiStatus, setApiStatus] = useState('unknown')

  useEffect(() => {
    let cancelled = false
    async function boot() {
      try {
        const health = await fetchHealth()
        if (!cancelled) setApiStatus(health.status)
      } catch {
        if (!cancelled) setApiStatus(isMockMode() ? 'mock' : 'down')
      }

      try {
        const livePayload = await fetchLiveWeather()
        if (!cancelled) setLive(livePayload)
      } catch (err) {
        if (!cancelled) setLiveError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setLiveLoading(false)
      }

      try {
        const summaryPayload = await fetchModelSummary()
        if (!cancelled) setSummary(summaryPayload)
      } catch (err) {
        if (!cancelled) setSummaryError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setSummaryLoading(false)
      }
    }
    void boot()
    return () => {
      cancelled = true
    }
  }, [])

  const events = live?.events ?? []
  const modelStatus = summary?.model_summary_available ? 'artifacts loaded' : 'unavailable'

  return (
    <Routes>
      <Route element={<AppShell apiStatus={apiStatus} modelStatus={modelStatus} />}>
        <Route
          path="/"
          element={
            <DashboardPage
              loading={liveLoading || summaryLoading}
              error={liveError}
              events={events}
              summary={summary}
            />
          }
        />
        <Route
          path="/markets"
          element={<MarketsPage loading={liveLoading} error={liveError} events={events} />}
        />
        <Route
          path="/markets/:eventTicker"
          element={<MarketDetailRoute live={live} liveError={liveError} />}
        />
        <Route
          path="/weather-model"
          element={
            <WeatherModelPage
              loading={summaryLoading}
              error={summaryError}
              summary={summary}
            />
          }
        />
        <Route
          path="/research"
          element={
            <ResearchPage
              loading={summaryLoading}
              error={summaryError}
              summary={summary}
            />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

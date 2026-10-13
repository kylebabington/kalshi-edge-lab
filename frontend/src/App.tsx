import { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { fetchHealth, fetchModelSummary } from './api/weather'
import { isMockMode } from './api/client'
import type { ModelSummary } from './types/weather'
import { DashboardPage } from './pages/DashboardPage'
import { MarketsPage } from './pages/MarketsPage'
import { WeatherEventRoute } from './pages/WeatherEventRoute'
import { WeatherModelPage } from './pages/WeatherModelPage'
import { ResearchPage } from './pages/ResearchPage'
import { SportsPage } from './pages/SportsPage'
import { SportsEventPage } from './pages/SportsEventPage'
import { WatchlistPage } from './pages/WatchlistPage'

export default function App() {
  const [summary, setSummary] = useState<ModelSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const [apiStatus, setApiStatus] = useState('unknown')

  useEffect(() => {
    let cancelled = false
    fetchHealth()
      .then((health) => {
        if (!cancelled) setApiStatus(health.status)
      })
      .catch(() => {
        if (!cancelled) setApiStatus(isMockMode() ? 'mock' : 'down')
      })
    fetchModelSummary()
      .then((payload) => {
        if (!cancelled) setSummary(payload)
      })
      .catch((err: unknown) => {
        if (!cancelled) setSummaryError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setSummaryLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const modelStatus = summaryLoading
    ? 'loading…'
    : summary?.model_summary_available
      ? 'artifacts loaded'
      : 'unavailable'

  return (
    <Routes>
      <Route element={<AppShell apiStatus={apiStatus} modelStatus={modelStatus} />}>
        <Route
          path="/"
          element={<DashboardPage summary={summary} summaryLoading={summaryLoading} summaryError={summaryError} />}
        />
        <Route path="/markets" element={<MarketsPage />} />
        <Route path="/markets/:eventTicker" element={<WeatherEventRoute />} />
        <Route
          path="/weather-model"
          element={<WeatherModelPage loading={summaryLoading} error={summaryError} summary={summary} />}
        />
        <Route
          path="/research"
          element={<ResearchPage loading={summaryLoading} error={summaryError} summary={summary} />}
        />
        <Route path="/sports" element={<SportsPage />} />
        <Route path="/sports/events/:eventId" element={<SportsEventPage />} />
        <Route path="/watchlist" element={<WatchlistPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

import { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { fetchHealth } from './api/weather'
import { isMockMode } from './api/client'
import { AppShell } from './components/AppShell'
import { DashboardPage } from './pages/DashboardPage'
import { MarketsPage } from './pages/MarketsPage'
import { WeatherEventRoute } from './pages/WeatherEventRoute'
import { WeatherModelPage } from './pages/WeatherModelPage'
import { ResearchPage } from './pages/ResearchPage'
import { SportsPage } from './pages/SportsPage'
import { SportsEventPage } from './pages/SportsEventPage'
import { WatchlistPage } from './pages/WatchlistPage'
import { OpportunitiesPage } from './pages/OpportunitiesPage'
import { OpportunityDetailPage } from './pages/OpportunityDetailPage'

export default function App() {
  const [apiStatus, setApiStatus] = useState('loading')

  useEffect(() => {
    let cancelled = false
    fetchHealth()
      .then((health) => {
        if (!cancelled) setApiStatus(health.status)
      })
      .catch(() => {
        if (!cancelled) setApiStatus(isMockMode() ? 'mock' : 'down')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <Routes>
      <Route element={<AppShell apiStatus={apiStatus} />}>
        <Route path="/" element={<DashboardPage apiStatus={apiStatus} />} />
        <Route path="/markets" element={<MarketsPage />} />
        <Route path="/markets/:eventTicker" element={<WeatherEventRoute />} />
        <Route path="/weather-model" element={<WeatherModelPage />} />
        <Route path="/research" element={<ResearchPage />} />
        <Route path="/sports" element={<SportsPage />} />
        <Route path="/sports/events/:eventId" element={<SportsEventPage />} />
        <Route path="/opportunities" element={<OpportunitiesPage />} />
        <Route path="/opportunities/:ticker" element={<OpportunityDetailPage />} />
        <Route path="/watchlist" element={<WatchlistPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

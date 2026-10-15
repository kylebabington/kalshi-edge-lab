import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NotFoundError } from '../api/client'
import * as sportsApi from '../api/sports'
import * as weatherApi from '../api/weather'
import { collectorHealth } from '../lib/health'
import { mockSportsCollector, mockSportsEvents } from '../mocks/sports'
import {
  mockSavedWeatherEvent,
  mockSavedWeatherEvents,
  mockWeatherCollector,
} from '../mocks/weatherSaved'
import { DashboardPage } from '../pages/DashboardPage'
import { MarketsPage } from '../pages/MarketsPage'
import { WeatherEventRoute } from '../pages/WeatherEventRoute'
import { watchlistStore } from '../watchlist/store'

vi.mock('../api/sports', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/sports')>()),
  fetchSportsEvents: vi.fn(),
  fetchSportsCollector: vi.fn(),
}))
vi.mock('../api/weather', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/weather')>()),
  fetchSavedWeatherEvents: vi.fn(),
  fetchSavedWeatherEvent: vi.fn(),
  fetchWeatherCollector: vi.fn(),
  fetchLiveWeather: vi.fn(),
  fetchModelSummary: vi.fn(),
}))

const sports = vi.mocked(sportsApi)
const weather = vi.mocked(weatherApi)
const never = () => new Promise<never>(() => {})

function renderDashboard(apiStatus = 'ok') {
  return render(
    <MemoryRouter>
      <DashboardPage apiStatus={apiStatus} />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  watchlistStore.reload()
  sports.fetchSportsEvents.mockImplementation(async (f) => mockSportsEvents(f))
  sports.fetchSportsCollector.mockResolvedValue(mockSportsCollector)
  weather.fetchSavedWeatherEvents.mockImplementation(async (f) => mockSavedWeatherEvents(f))
  weather.fetchSavedWeatherEvent.mockImplementation(async (t) => {
    const found = mockSavedWeatherEvent(t)
    if (!found) throw new NotFoundError(`404 ${t}`)
    return found
  })
  weather.fetchWeatherCollector.mockResolvedValue(mockWeatherCollector)
  weather.fetchLiveWeather.mockResolvedValue({ generated_at: '2026-10-11T14:00:00Z', mode: 'mock', events: [] })
})

describe('Dashboard sections load independently', () => {
  it('renders sports while every weather request is still pending', async () => {
    weather.fetchSavedWeatherEvents.mockImplementation(never)
    weather.fetchWeatherCollector.mockImplementation(never)
    renderDashboard()
    expect(await screen.findByRole('link', { name: /Open Mock United vs Fixture City/ })).toBeInTheDocument()
    expect(screen.getAllByText('Loading weather…').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Loading collector status…').length).toBeGreaterThan(0)
    expect(screen.queryByText(/Loading open weather markets/)).toBeNull()
    expect(weather.fetchLiveWeather).not.toHaveBeenCalled()
    expect(weather.fetchModelSummary).not.toHaveBeenCalled()
  })

  it('renders weather when sports fails, with a retryable sports error', async () => {
    sports.fetchSportsEvents.mockRejectedValue(new Error('500 sports down'))
    sports.fetchSportsCollector.mockRejectedValue(new Error('500 sports down'))
    renderDashboard()
    expect(await screen.findByRole('link', { name: /Open NYC daily high · .*Jan 1, 2099/ })).toBeInTheDocument()
    expect((await screen.findAllByText('500 sports down')).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: 'Retry' }).length).toBeGreaterThan(0)
  })

  it('does not load live weather or model artifacts', async () => {
    renderDashboard()
    expect(await screen.findByRole('heading', { name: 'System status' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Needs attention' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Upcoming' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Recently settled' })).toBeInTheDocument()
    expect(screen.queryByText(/Weather model artifacts/)).toBeNull()
    expect(screen.queryByText(/Research status/)).toBeNull()
    expect(weather.fetchLiveWeather).not.toHaveBeenCalled()
  })

  it('does not flag a backup that has simply not run yet, but flags a missing backup task', async () => {
    sports.fetchSportsCollector.mockResolvedValue({ ...mockSportsCollector, backup: undefined, backup_task: undefined })
    const { unmount } = renderDashboard()
    const attention = await screen.findByRole('region', { name: 'Needs attention' })
    await waitFor(() => expect(within(attention).queryByText(/Sports collector/)).not.toBeNull())
    expect(within(attention).queryByText(/Sports backup/)).toBeNull()
    unmount()

    sports.fetchSportsCollector.mockResolvedValue({
      ...mockSportsCollector,
      backup: undefined,
      backup_task: {
        observable: true,
        configured: false,
        enabled: 'unknown',
        status: 'not registered',
        next_run_time: '',
        last_run_time: '',
        last_result: '',
      },
    })
    renderDashboard()
    expect(await screen.findByText(/Sports backup: .*backup task is not registered/)).toBeInTheDocument()
  })

  it('shows clear empty states per group', async () => {
    sports.fetchSportsEvents.mockImplementation(async (f) => ({ ...mockSportsEvents(f), items: [], total: 0 }))
    renderDashboard()
    const upcoming = await screen.findByRole('region', { name: 'Upcoming' })
    expect(await within(upcoming).findByText('No upcoming sports events')).toBeInTheDocument()
    const settled = screen.getByRole('region', { name: 'Recently settled' })
    expect(within(settled).getByText('No recently settled sports events')).toBeInTheDocument()
  })
})

describe('Collector health vs forecast freshness', () => {
  it('reports unknown health when no successful cycle timestamp exists', async () => {
    renderDashboard()
    const panel = await screen.findByRole('listitem', { name: 'Weather collector' })
    expect(within(panel).getByText('Warning')).toBeInTheDocument()
    expect(within(panel).getByText(/No successful cycle time recorded/)).toBeInTheDocument()
  })

  it('reports stale health from successful cycle times, not from forecast age', async () => {
    weather.fetchWeatherCollector.mockResolvedValue({
      ...mockWeatherCollector,
      cycles: { ...mockWeatherCollector.cycles, last_successful_cycle_at: '2026-10-11T08:00:00Z', last_cycle_exit_code: 0 },
    })
    renderDashboard()
    const panel = await screen.findByRole('listitem', { name: 'Weather collector' })
    expect(within(panel).getByText('Stale')).toBeInTheDocument()
    expect(within(panel).getByText(/Last successful cycle/)).toBeInTheDocument()
  })

  it('a recent successful cycle is healthy even though the latest saved forecast is weeks old', async () => {
    weather.fetchWeatherCollector.mockResolvedValue({
      ...mockWeatherCollector,
      cycles: { ...mockWeatherCollector.cycles, last_successful_cycle_at: '2026-10-11T13:30:00Z', last_cycle_exit_code: 0 },
      forecast_freshness: { ...mockWeatherCollector.forecast_freshness, last_snapshot_prediction_as_of: '2026-09-20T12:00:00Z' },
    })
    renderDashboard()
    const panel = await screen.findByRole('listitem', { name: 'Weather collector' })
    expect(within(panel).getByText('Healthy')).toBeInTheDocument()
  })

  it('collectorHealth treats invalid timestamps as unknown and recent failures as degraded', () => {
    const base = { nowIso: '2026-10-11T14:00:00Z', cadenceMinutes: 60 }
    expect(collectorHealth({ ...base, lastSuccessAt: null, lastCycleOk: true }).state).toBe('unknown')
    expect(collectorHealth({ ...base, lastSuccessAt: 'garbage', lastCycleOk: true }).state).toBe('unknown')
    expect(collectorHealth({ ...base, lastSuccessAt: '2026-10-11T13:30:00Z', lastCycleOk: false }).state).toBe('degraded')
    expect(collectorHealth({ ...base, lastSuccessAt: '2026-10-11T13:30:00Z', lastCycleOk: true }).state).toBe('healthy')
    expect(collectorHealth({ ...base, nowIso: 'bad', lastSuccessAt: '2026-10-11T13:30:00Z', lastCycleOk: true }).state).toBe('unknown')
  })
})

function renderWeather(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/markets" element={<MarketsPage />} />
        <Route path="/markets/:eventTicker" element={<WeatherEventRoute />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Weather browsing', () => {
  it('passes URL filters and sort to the saved-forecast API', async () => {
    const user = userEvent.setup()
    renderWeather('/markets?status=settled&sort=capture_desc&q=SEP')
    await screen.findByRole('link', { name: /Open NYC daily high · .*Sep 26, 2026/ })
    expect(weather.fetchSavedWeatherEvents).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'settled', sort: 'capture_desc', q: 'SEP', page: 1 }),
    )
    expect(screen.getByLabelText('Status')).toHaveValue('settled')
    const settled = screen.getByRole('link', { name: /Open NYC daily high · .*Sep 26, 2026/ })
    const card = settled.closest('article')!
    expect(within(card).getByText(/Confirmed/)).toBeInTheDocument()
    expect(within(card).getByText(/latest snapshot shown here has no valid score of its own/)).toBeInTheDocument()
    expect(within(card).queryByText(/Brier/)).toBeNull()
    await user.selectOptions(screen.getByLabelText('Sort by'), 'target_asc')
    expect(weather.fetchSavedWeatherEvents).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'target_asc' }))
    expect(weather.fetchLiveWeather).not.toHaveBeenCalled()
  })

  it('labels Saved and Live distinctly and keeps tab=open working', async () => {
    const user = userEvent.setup()
    renderWeather('/markets?tab=open')
    expect(screen.getByRole('tab', { name: 'Live' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('No open KXHIGHNY events')).toBeInTheDocument()
    expect(weather.fetchLiveWeather).toHaveBeenCalled()
    await user.click(screen.getByRole('tab', { name: 'Saved' }))
    await screen.findByRole('link', { name: /Open NYC daily high · .*Sep 26, 2026/ })
    expect(screen.getAllByText('Saved forecast').length).toBeGreaterThan(0)
  })

  it('counts active filters and clears them without leaving Saved', async () => {
    const user = userEvent.setup()
    renderWeather('/markets?status=settled&sort=capture_desc&q=SEP')
    await screen.findByRole('link', { name: /Open NYC daily high · .*Sep 26, 2026/ })
    expect(screen.getByText(/3 filters active/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear all' }))
    await waitFor(() =>
      expect(weather.fetchSavedWeatherEvents).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: '', sort: '', q: '', page: 1 }),
      ),
    )
    expect(screen.getByRole('tab', { name: 'Saved' })).toHaveAttribute('aria-selected', 'true')
  })

  it('keeps saved forecasts available when live weather fails', async () => {
    weather.fetchLiveWeather.mockRejectedValue(new Error('timeout'))
    const user = userEvent.setup()
    renderWeather('/markets?tab=open')
    expect(await screen.findByText('Live weather unavailable')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Saved' }))
    expect(await screen.findAllByRole('link', { name: /Open NYC daily high/ })).not.toHaveLength(0)
  })

  it('keeps detailed collector health on the weather page', async () => {
    const user = userEvent.setup()
    renderWeather('/markets')
    await user.click(screen.getByText('Collector status'))
    // Once in the collapsed summary, once in the panel.
    expect(await screen.findAllByText(/Collector health unknown/i)).toHaveLength(2)
    expect(screen.getByText('Saved forecast freshness')).toBeInTheDocument()
  })

  it('opens a settled saved event without waiting on live sources and returns to the filtered list', async () => {
    const user = userEvent.setup()
    renderWeather('/markets?status=settled&sort=settlement_desc')
    await user.click(await screen.findByRole('link', { name: /Open NYC daily high · .*Sep 26, 2026/ }))
    expect(await screen.findByRole('heading', { name: /NYC daily high · .*Sep 26, 2026/ })).toBeInTheDocument()
    expect(screen.getByText(/Event settlement/)).toBeInTheDocument()
    expect(screen.getAllByText(/Not scored/i).length).toBeGreaterThan(0)
    expect(weather.fetchLiveWeather).not.toHaveBeenCalled()
    await user.click(screen.getByRole('link', { name: /Back to list/ }))
    await screen.findByRole('link', { name: /Open NYC daily high · .*Sep 26, 2026/ })
    expect(screen.getByLabelText('Status')).toHaveValue('settled')
    expect(screen.getByLabelText('Sort by')).toHaveValue('settlement_desc')
  })

  it('shows not-found only after both saved and open markets miss', async () => {
    renderWeather('/markets/KXHIGHNY-77JAN01')
    expect(await screen.findByText('Weather event not found')).toBeInTheDocument()
    expect(weather.fetchLiveWeather).toHaveBeenCalledTimes(1)
  })

  it('stars a saved weather event from its detail page', async () => {
    const user = userEvent.setup()
    renderWeather('/markets/KXHIGHNY-26SEP26')
    await user.click(await screen.findByRole('button', { name: /Add to watchlist: NYC daily high/ }))
    expect(watchlistStore.has('weather:KXHIGHNY-26SEP26')).toBe(true)
    expect(watchlistStore.has('sports:KXHIGHNY-26SEP26')).toBe(false)
  })
})

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NotFoundError } from '../api/client'
import * as sportsApi from '../api/sports'
import * as weatherApi from '../api/weather'
import { collectorHealth } from '../lib/health'
import { mockSportsCollector, mockSportsEvents } from '../mocks/sports'
import { mockModelSummary } from '../mocks/weatherEvent'
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
}))

const sports = vi.mocked(sportsApi)
const weather = vi.mocked(weatherApi)
const never = () => new Promise<never>(() => {})

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage summary={mockModelSummary} summaryLoading={false} summaryError={null} />
    </MemoryRouter>,
  )
}

function section(name: RegExp) {
  return screen.getByRole('region', { name })
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
    weather.fetchLiveWeather.mockImplementation(never)
    renderDashboard()
    expect(await screen.findByRole('link', { name: /Open Mock United vs Fixture City/ })).toBeInTheDocument()
    expect(screen.getByText(/Loading weather collector health/)).toBeInTheDocument()
    expect(screen.getByText(/Loading open weather markets/)).toBeInTheDocument()
  })

  it('renders weather when sports fails, with a retryable sports error', async () => {
    sports.fetchSportsEvents.mockRejectedValue(new Error('500 sports down'))
    sports.fetchSportsCollector.mockRejectedValue(new Error('500 sports down'))
    renderDashboard()
    expect(await screen.findByRole('link', { name: /Open NYC daily high · .*Jan 1, 2099/ })).toBeInTheDocument()
    const sportsSection = section(/^Sports$/)
    expect((await within(sportsSection).findAllByText('500 sports down')).length).toBeGreaterThan(0)
    expect(within(sportsSection).getAllByRole('button', { name: 'Retry' }).length).toBeGreaterThan(0)
  })

  it('a failing live weather request does not hide saved forecasts', async () => {
    weather.fetchLiveWeather.mockRejectedValue(new Error('timeout'))
    renderDashboard()
    expect(await screen.findByText('Live weather unavailable')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open NYC daily high · .*Jan 1, 2099/ })).toBeInTheDocument()
  })

  it('shows clear empty states per group', async () => {
    sports.fetchSportsEvents.mockImplementation(async (f) => ({ ...mockSportsEvents(f), items: [], total: 0 }))
    renderDashboard()
    const sportsSection = section(/^Sports$/)
    expect(await within(sportsSection).findByText('No upcoming captured events')).toBeInTheDocument()
    expect(within(sportsSection).getByText('No recently settled')).toBeInTheDocument()
  })
})

describe('Collector health vs forecast freshness', () => {
  it('reports unknown health when no successful cycle timestamp exists', async () => {
    renderDashboard()
    const panel = await screen.findByRole('region', { name: 'Weather collector' })
    expect(within(panel).getByText(/Collector health unknown/i)).toBeInTheDocument()
    expect(within(panel).getByText('Saved forecast freshness')).toBeInTheDocument()
  })

  it('reports stale health from successful cycle times, not from forecast age', async () => {
    weather.fetchWeatherCollector.mockResolvedValue({
      ...mockWeatherCollector,
      cycles: { ...mockWeatherCollector.cycles, last_successful_cycle_at: '2026-10-11T08:00:00Z', last_cycle_exit_code: 0 },
    })
    renderDashboard()
    const panel = await screen.findByRole('region', { name: 'Weather collector' })
    expect(within(panel).getByText(/Collector stale/i)).toBeInTheDocument()
    expect(screen.getByText(/new checkpoints may be missing/)).toBeInTheDocument()
  })

  it('a recent successful cycle is healthy even though the latest saved forecast is weeks old', async () => {
    weather.fetchWeatherCollector.mockResolvedValue({
      ...mockWeatherCollector,
      cycles: { ...mockWeatherCollector.cycles, last_successful_cycle_at: '2026-10-11T13:30:00Z', last_cycle_exit_code: 0 },
      forecast_freshness: { ...mockWeatherCollector.forecast_freshness, last_snapshot_prediction_as_of: '2026-09-20T12:00:00Z' },
    })
    renderDashboard()
    const panel = await screen.findByRole('region', { name: 'Weather collector' })
    expect(within(panel).getByText(/Collector healthy/i)).toBeInTheDocument()
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

describe('Weather settlement display', () => {
  it('separates confirmed event settlement from the latest snapshot score', async () => {
    renderDashboard()
    const settled = await screen.findByRole('link', { name: /Open NYC daily high · .*Sep 26, 2026/ })
    const card = settled.closest('article')!
    expect(within(card).getByText(/Confirmed/)).toBeInTheDocument()
    expect(within(card).getByText(/latest snapshot shown here has no valid score of its own/)).toBeInTheDocument()
    expect(within(card).queryByText(/Brier/)).toBeNull()
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
    await user.selectOptions(screen.getByLabelText('Sort by'), 'target_asc')
    expect(weather.fetchSavedWeatherEvents).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'target_asc' }))
    expect(weather.fetchLiveWeather).not.toHaveBeenCalled()
  })

  it('labels saved forecasts and current market data distinctly', async () => {
    const user = userEvent.setup()
    renderWeather('/markets')
    await screen.findByRole('link', { name: /Open NYC daily high · .*Sep 26, 2026/ })
    expect(screen.getAllByText('Saved forecast').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('tab', { name: /Open markets/ }))
    expect(await screen.findByText('No open KXHIGHNY events')).toBeInTheDocument()
    expect(weather.fetchLiveWeather).toHaveBeenCalled()
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

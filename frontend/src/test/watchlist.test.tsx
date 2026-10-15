import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NotFoundError } from '../api/client'
import * as sportsApi from '../api/sports'
import * as weatherApi from '../api/weather'
import { mockSportsEventDetail } from '../mocks/sports'
import { mockSavedWeatherEvent } from '../mocks/weatherSaved'
import { WatchlistPage } from '../pages/WatchlistPage'
import { STORAGE_KEY, watchlistStore } from '../watchlist/store'

vi.mock('../api/sports', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/sports')>()),
  fetchSportsEvent: vi.fn(),
}))
vi.mock('../api/weather', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/weather')>()),
  fetchSavedWeatherEvent: vi.fn(),
  fetchLiveWeather: vi.fn(),
}))

const sports = vi.mocked(sportsApi)
const weather = vi.mocked(weatherApi)

function seed(entries: { category: 'weather' | 'sports'; key: string; label: string }[]) {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      version: 1,
      entries: entries.map((e, i) => ({ ...e, id: `${e.category}:${e.key}`, added_at: `2026-10-0${i + 1}T12:00:00Z` })),
    }),
  )
  watchlistStore.reload()
}

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname + loc.search}</div>
}

function renderPage(path = '/watchlist') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/watchlist"
          element={
            <>
              <WatchlistPage />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  watchlistStore.reload()
  sports.fetchSportsEvent.mockImplementation(async (id) => {
    if (id === mockSportsEventDetail.event.event_id) return mockSportsEventDetail
    throw new NotFoundError(`404 ${id}`)
  })
  weather.fetchSavedWeatherEvent.mockImplementation(async (t) => {
    const found = mockSavedWeatherEvent(t)
    if (!found) throw new NotFoundError(`404 ${t}`)
    return found
  })
  weather.fetchLiveWeather.mockResolvedValue({ generated_at: '2026-10-11T14:00:00Z', mode: 'mock', events: [] })
})

describe('Watchlist page', () => {
  it('shows an empty state when nothing is starred', () => {
    renderPage()
    expect(screen.getByText('Your watchlist is empty')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Clear watchlist/ })).toBeNull()
  })

  it('resolves weather and sports entries through the read-only APIs', async () => {
    seed([
      { category: 'weather', key: 'KXHIGHNY-26SEP26', label: 'NYC high Sep 26' },
      { category: 'sports', key: mockSportsEventDetail.event.event_id, label: 'Mock game' },
    ])
    renderPage()
    expect(await screen.findByRole('link', { name: /Open NYC daily high/ })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: /Open Mock State at Example Tech/ })).toBeInTheDocument()
    expect(weather.fetchSavedWeatherEvent).toHaveBeenCalledWith('KXHIGHNY-26SEP26')
    expect(sports.fetchSportsEvent).toHaveBeenCalledWith(mockSportsEventDetail.event.event_id)
    expect(weather.fetchLiveWeather).not.toHaveBeenCalled()
  })

  it('keeps an unavailable entry and explains why', async () => {
    seed([{ category: 'sports', key: 'gone|espn:0', label: 'Old game' }])
    renderPage()
    const card = await screen.findByRole('article', { name: 'Old game' })
    expect(within(card).getByText('Unavailable')).toBeInTheDocument()
    expect(within(card).getByText(/No saved sports capture with this id/)).toBeInTheDocument()
    expect(within(card).getByText(/saved entry is kept until you remove it/)).toBeInTheDocument()
    expect(watchlistStore.has('sports:gone|espn:0')).toBe(true)
  })

  it('checks open markets before calling a weather entry unavailable', async () => {
    seed([{ category: 'weather', key: 'KXHIGHNY-77JAN01', label: 'Unknown weather' }])
    renderPage()
    expect(await screen.findByText(/not among the currently open markets/)).toBeInTheDocument()
    expect(weather.fetchLiveWeather).toHaveBeenCalledTimes(1)
  })

  it('shows an API failure with retry instead of calling the event unavailable', async () => {
    sports.fetchSportsEvent.mockRejectedValueOnce(new Error('502 Bad Gateway'))
    seed([{ category: 'sports', key: mockSportsEventDetail.event.event_id, label: 'Mock game' }])
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText(/Could not check this event/)).toBeInTheDocument()
    expect(screen.queryByText('Unavailable')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('link', { name: /Open Mock State at Example Tech/ })).toBeInTheDocument()
  })

  it('filters by category through the URL', async () => {
    seed([
      { category: 'weather', key: 'KXHIGHNY-26SEP26', label: 'NYC high Sep 26' },
      { category: 'sports', key: 'gone|espn:0', label: 'Old game' },
    ])
    const user = userEvent.setup()
    renderPage('/watchlist?category=sports')
    expect(screen.getByRole('button', { name: 'Sports (1)' })).toHaveAttribute('aria-pressed', 'true')
    expect(await screen.findByRole('article', { name: 'Old game' })).toBeInTheDocument()
    expect(screen.queryByText(/NYC daily high/)).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Weather (1)' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/watchlist?category=weather')
    expect(await screen.findByRole('link', { name: /Open NYC daily high/ })).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Old game' })).toBeNull()
  })

  it('removes a single entry and persists the change', async () => {
    seed([
      { category: 'sports', key: 'gone|espn:0', label: 'Old game' },
      { category: 'weather', key: 'KXHIGHNY-26SEP26', label: 'NYC high Sep 26' },
    ])
    const user = userEvent.setup()
    renderPage()
    const card = await screen.findByRole('article', { name: 'Old game' })
    await user.click(within(card).getByRole('button', { name: 'Remove Old game from watchlist' }))
    expect(screen.queryByRole('article', { name: 'Old game' })).toBeNull()
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)!)
    expect(saved.entries.map((e: { id: string }) => e.id)).toEqual(['weather:KXHIGHNY-26SEP26'])
  })

  it('clears the whole list only after confirmation', async () => {
    seed([
      { category: 'sports', key: 'gone|espn:0', label: 'Old game' },
      { category: 'weather', key: 'KXHIGHNY-26SEP26', label: 'NYC high Sep 26' },
    ])
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Clear watchlist…' }))
    const dialog = screen.getByRole('alertdialog')
    expect(dialog).toHaveTextContent('Remove all 2 entries?')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(watchlistStore.getSnapshot().entries).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: 'Clear watchlist…' }))
    await user.click(screen.getByRole('button', { name: 'Yes, clear watchlist' }))
    expect(screen.getByText('Your watchlist is empty')).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).entries).toEqual([])
  })

  it("warns that changes won't persist when storage writes fail", async () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError')
    })
    try {
      renderPage()
      act(() => watchlistStore.add('weather', 'KXHIGHNY-26SEP26', 'NYC high Sep 26'))
      expect(await screen.findByText(/Changes won't persist/)).toBeInTheDocument()
      expect(await screen.findByRole('link', { name: /Open NYC daily high/ })).toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })

  it('picks up a change made in another tab', async () => {
    renderPage()
    expect(screen.getByText('Your watchlist is empty')).toBeInTheDocument()
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        version: 1,
        entries: [{ id: 'sports:gone|espn:0', category: 'sports', key: 'gone|espn:0', label: 'Old game', added_at: '2026-10-01T00:00:00Z' }],
      }),
    )
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: STORAGE_KEY }))
    })
    expect(await screen.findByRole('article', { name: 'Old game' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Your watchlist is empty')).toBeNull())
  })

  it('sorts by settlement from the URL and keeps category when the sort changes', async () => {
    seed([
      { category: 'weather', key: 'KXHIGHNY-26SEP26', label: 'NYC high Sep 26' },
      { category: 'weather', key: 'KXHIGHNY-99JAN01', label: 'NYC high Jan 1' },
    ])
    const user = userEvent.setup()
    renderPage('/watchlist?sort=settled')
    await waitFor(() => {
      const links = screen.getAllByRole('link', { name: /Open NYC daily high/ })
      expect(links[0]).toHaveAccessibleName(/Sep 26/)
    })
    await user.selectOptions(screen.getByLabelText('Sort'), 'Recently added')
    expect(screen.getByTestId('location')).toHaveTextContent('/watchlist')
    expect(screen.getByTestId('location').textContent).not.toContain('sort=')
    await waitFor(() => {
      const links = screen.getAllByRole('link', { name: /Open NYC daily high/ })
      expect(links[0]).toHaveAccessibleName(/Jan 1/)
    })
    await user.selectOptions(screen.getByLabelText('Sort'), 'Settled')
    await user.click(screen.getByRole('button', { name: 'Weather (2)' }))
    expect(screen.getByTestId('location').textContent).toContain('category=weather')
    expect(screen.getByTestId('location').textContent).toContain('sort=settled')
  })
})

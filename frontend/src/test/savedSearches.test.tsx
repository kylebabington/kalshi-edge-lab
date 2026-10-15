import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ExplorePage } from '../pages/ExplorePage'
import { DashboardPage } from '../pages/DashboardPage'
import { mockSportsCollector, mockSportsEvents } from '../mocks/sports'
import { mockSavedWeatherEvents, mockWeatherCollector } from '../mocks/weatherSaved'
import { STORAGE_KEY, savedSearchesStore } from '../savedSearches/store'
import { watchlistStore } from '../watchlist/store'

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname + loc.search}</div>
}

vi.mock('../api/explore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/explore')>()
  const mocks = await import('../mocks/explore')
  return {
    ...actual,
    fetchExploreSummary: vi.fn(async () => mocks.mockExploreSummary),
    fetchExploreEvents: vi.fn(async (f: { category?: string; page?: number; page_size?: number; q?: string }) => {
      const base = mocks.mockExploreEvents(f)
      if (f.q === 'empty-only') return { ...base, total: 0, pages: 1, events: [] }
      if (f.q === 'fail-panel') throw new Error('panel failed')
      return base
    }),
    fetchExploreRefreshStatus: vi.fn(async () => ({ status: { state: 'idle' } })),
    postExploreRefresh: vi.fn(async () => ({
      accepted: true,
      conflict: false,
      status: { state: 'complete', run_id: 'mock', started_at: '2099-01-01T12:00:00Z', finished_at: '2099-01-01T12:00:00Z' },
    })),
  }
})

vi.mock('../api/weather', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/weather')>()),
  fetchWeatherCollector: vi.fn(async () => mockWeatherCollector),
  fetchSavedWeatherEvents: vi.fn(async (f: object) => mockSavedWeatherEvents(f)),
}))

vi.mock('../api/sports', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/sports')>()),
  fetchSportsCollector: vi.fn(async () => mockSportsCollector),
  fetchSportsEvents: vi.fn(async (f: object) => mockSportsEvents(f)),
}))

beforeEach(() => {
  localStorage.clear()
  savedSearchesStore.reload()
  watchlistStore.reload()
  vi.clearAllMocks()
})

describe('Explore saved searches', () => {
  it('saves current filters with inline name form and opens them without refreshing', async () => {
    const user = userEvent.setup()
    const exploreApi = await import('../api/explore')
    render(
      <MemoryRouter initialEntries={['/explore?lifecycle=open&sort=title_asc']}>
        <Routes>
          <Route
            path="/explore"
            element={
              <>
                <ExplorePage />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Market Explorer' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Save current filters/i }))
    await user.type(screen.getByLabelText('Name for current filters'), 'My open')
    await user.click(screen.getByRole('button', { name: 'Save search' }))
    expect(await screen.findByText('My open')).toBeInTheDocument()
    const saved = savedSearchesStore.getSnapshot().entries[0]
    expect(saved.filters.sort).toBe('title')
    expect(saved.filters.lifecycle).toBe('open')

    await user.click(screen.getByRole('button', { name: /Clear all/i }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/explore'))
    await user.click(screen.getByRole('button', { name: /Open saved search My open/i }))
    await waitFor(() => {
      const loc = screen.getByTestId('location').textContent || ''
      expect(loc).toContain('lifecycle=open')
      expect(loc).toContain('sort=title')
      expect(loc).not.toContain('title_asc')
      expect(loc).not.toContain('page=')
    })
    expect(vi.mocked(exploreApi.postExploreRefresh)).not.toHaveBeenCalled()
  })
})

describe('Dashboard pinned searches', () => {
  it('loads panels independently and keeps empty results empty', async () => {
    const at = '2099-01-01T00:00:00.000Z'
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        version: 1,
        entries: [
          {
            id: 'pin-ok',
            name: 'Politics',
            filters: { category: 'Politics', q: '', lifecycle: '', coverage: '', quote: '', sort: 'retrieved_desc' },
            pin_order: 0,
            created_at: at,
            updated_at: at,
          },
          {
            id: 'pin-empty',
            name: 'Empty',
            filters: { category: '', q: 'empty-only', lifecycle: '', coverage: '', quote: '', sort: 'retrieved_desc' },
            pin_order: 1,
            created_at: at,
            updated_at: at,
          },
          {
            id: 'pin-fail',
            name: 'Broken',
            filters: { category: '', q: 'fail-panel', lifecycle: '', coverage: '', quote: '', sort: 'retrieved_desc' },
            pin_order: 2,
            created_at: at,
            updated_at: at,
          },
        ],
      }),
    )
    savedSearchesStore.reload()

    render(
      <MemoryRouter>
        <DashboardPage apiStatus="ok" />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Pinned searches' })).toBeInTheDocument()
    const panels = await screen.findAllByTestId('pinned-search-panel')
    expect(panels.length).toBe(3)

    await waitFor(() => {
      expect(screen.getByText('No matching events')).toBeInTheDocument()
      expect(screen.getByText(/panel failed/i)).toBeInTheDocument()
    })
    expect(within(panels[0]).queryByText('No matching events')).toBeNull()
    expect(within(panels[0]).queryByText(/panel failed/i)).toBeNull()
    expect(screen.getAllByText(/Market-data freshness/i).length).toBe(3)
    expect(screen.getAllByText(/API query time, not market refresh/i).length).toBe(3)
  })
})

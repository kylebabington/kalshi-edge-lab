import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NotFoundError } from '../api/client'
import { ExploreEventPage } from '../pages/ExploreEventPage'
import { ExplorePage } from '../pages/ExplorePage'
import { STORAGE_KEY, type NotebookEntry, notebookStore } from '../notebook/store'
import { watchlistStore } from '../watchlist/store'

vi.mock('../api/explore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/explore')>()
  const mocks = await import('../mocks/explore')
  return {
    ...actual,
    fetchExploreSummary: vi.fn(async () => mocks.mockExploreSummary),
    fetchExploreEvents: vi.fn(async (f: object) => mocks.mockExploreEvents(f)),
    fetchExploreEvent: vi.fn(async (id: string) => {
      const found = mocks.mockExploreEvent(id)
      if (!found) throw new NotFoundError(`404 ${id}`)
      return found
    }),
    fetchExploreRefreshStatus: vi.fn(async () => ({ status: { state: 'idle', message: 'idle' } })),
    postExploreRefresh: vi.fn(async () => ({
      accepted: true,
      conflict: false,
      status: { state: 'complete', run_id: 'mock', started_at: '2099-01-01T12:00:00Z', finished_at: '2099-01-01T12:00:00Z' },
    })),
  }
})

beforeEach(() => {
  localStorage.clear()
  watchlistStore.reload()
  notebookStore.reload()
  vi.clearAllMocks()
})

function renderExplore(path = '/explore') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/explore" element={<ExplorePage />} />
        <Route path="/explore/events/:eventTicker" element={<ExploreEventPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Explore page', () => {
  it('lists events and separates model support from saved forecast', async () => {
    renderExplore()
    expect(await screen.findByRole('heading', { name: 'Market Explorer' })).toBeInTheDocument()
    expect(await screen.findByText('Mock NFL game winner')).toBeInTheDocument()
    const sportsCard = screen.getByRole('article', { name: 'Mock NFL game winner' })
    expect(within(sportsCard).getByText(/Sports model supported · no saved forecast/)).toBeInTheDocument()
    expect(within(sportsCard).queryByText(/No model available/i)).toBeNull()
    expect(screen.getByRole('button', { name: /Refresh markets/i })).toBeInTheDocument()
  })

  it('stars via preferred identity without inventing a sports capture', async () => {
    const user = userEvent.setup()
    renderExplore()
    const politics = await screen.findByRole('article', { name: 'Mock politics event' })
    await user.click(within(politics).getByRole('button', { name: /Add to watchlist/ }))
    await waitFor(() => {
      expect(watchlistStore.getSnapshot().entries.some((e) => e.id === 'kalshi:KXOTHER-MOCK')).toBe(true)
    })
  })
})

describe('Explore event detail', () => {
  it('labels quotes as latest retrieved and shows coverage fields separately', async () => {
    renderExplore('/explore/events/KXNFLGAME-MOCK')
    expect(await screen.findByRole('heading', { name: 'Mock NFL game winner' })).toBeInTheDocument()
    expect(screen.getByText('Latest retrieved quotes')).toBeInTheDocument()
    expect(screen.getByText('Sports model supported')).toBeInTheDocument()
    expect(screen.getByText('No saved forecast')).toBeInTheDocument()
    expect(screen.queryByText(/No model available/i)).toBeNull()
  })

  it('resolves notes across aliases without moving original targets', async () => {
    const AT = '2099-01-01T00:00:00.000Z'
    const early: NotebookEntry = {
      id: 'n-early',
      kind: 'note',
      origin: 'personal',
      text: 'starred before sports index warmed',
      tags: [],
      target: { type: 'event', category: 'kalshi', key: 'KXNFLGAME-MOCK' },
      created_at: AT,
      updated_at: AT,
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, entries: [early] }))
    notebookStore.reload()

    // Remap mock coverage to include sports_event_id so aliases expand after "warm".
    const exploreApi = await import('../api/explore')
    vi.mocked(exploreApi.fetchExploreEvent).mockResolvedValueOnce({
      ...(await import('../mocks/explore')).mockExploreEvent('KXNFLGAME-MOCK')!,
      coverage: {
        model_supported: 'sports',
        saved_forecast: 'available',
        sports_event_id: 'nfl|mock-cluster',
        saved_forecast_href: '/sports/events/nfl%7Cmock-cluster',
      },
    })

    renderExplore('/explore/events/KXNFLGAME-MOCK')
    expect(await screen.findByText('starred before sports index warmed')).toBeInTheDocument()
    // Original target preserved on the stored entry.
    expect(notebookStore.getSnapshot().entries[0].target).toEqual({
      type: 'event',
      category: 'kalshi',
      key: 'KXNFLGAME-MOCK',
    })
  })
})

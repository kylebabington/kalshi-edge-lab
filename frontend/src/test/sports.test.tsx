import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/sports'
import { AppShell } from '../components/AppShell'
import { SportsSummaryPanel } from '../components/sports/SportsSummaryPanel'
import {
  MOCK_SOURCE,
  mockSportsCollector,
  mockSportsCoverage,
  mockSportsEventDetail,
  mockSportsEvents,
  mockSportsHistorical,
  mockSportsResults,
  mockSportsSummary,
} from '../mocks/sports'
import { SportsEventPage } from '../pages/SportsEventPage'
import { SportsPage } from '../pages/SportsPage'

vi.mock('../api/sports', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/sports')>()
  return {
    ...actual,
    fetchSportsSummary: vi.fn(),
    fetchSportsEvents: vi.fn(),
    fetchSportsEvent: vi.fn(),
    fetchSportsResults: vi.fn(),
    fetchSportsCoverage: vi.fn(),
    fetchSportsHistorical: vi.fn(),
    fetchSportsCollector: vi.fn(),
  }
})

const m = vi.mocked(api)

function renderSports(path = '/sports') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/sports" element={<SportsPage />} />
        <Route path="/sports/events/:eventId" element={<SportsEventPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  m.fetchSportsSummary.mockResolvedValue(mockSportsSummary)
  m.fetchSportsEvents.mockImplementation(async (f) => mockSportsEvents(f))
  m.fetchSportsEvent.mockResolvedValue(mockSportsEventDetail)
  m.fetchSportsResults.mockResolvedValue(mockSportsResults)
  m.fetchSportsCoverage.mockResolvedValue(mockSportsCoverage)
  m.fetchSportsHistorical.mockResolvedValue(mockSportsHistorical)
  m.fetchSportsCollector.mockResolvedValue(mockSportsCollector)
})

describe('Sports events', () => {
  it('shows a loading state, then saved-capture cards with upcoming first', async () => {
    renderSports()
    expect(screen.getByText(/Loading saved captures/i)).toBeInTheDocument()
    const cards = await screen.findAllByRole('link', { name: /^Open / })
    expect(cards[0]).toHaveAccessibleName(/Mock United vs Fixture City/)
    expect(screen.getAllByText(/Saved captures/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/not a live feed/i)).toBeInTheDocument()
    expect(screen.getByText(/Loaded in UI/i)).toBeInTheDocument()
    expect(screen.queryByText(/\bBUY\b|\bSELL\b|best bet/i)).toBeNull()
  })

  it('shows the API error state', async () => {
    m.fetchSportsEvents.mockRejectedValue(new Error('500 Internal Server Error'))
    renderSports()
    expect(await screen.findByText('500 Internal Server Error')).toBeInTheDocument()
    expect(screen.getAllByText(/API error/i).length).toBeGreaterThan(0)
  })

  it('shows an empty state when no captures match', async () => {
    m.fetchSportsEvents.mockImplementation(async (f) => ({ ...mockSportsEvents(f), items: [], total: 0 }))
    renderSports()
    expect(await screen.findByText(/No saved captures match these filters/i)).toBeInTheDocument()
  })

  it('shows a distinct state when capture artifacts are missing', async () => {
    m.fetchSportsEvents.mockImplementation(async (f) => ({
      ...mockSportsEvents(f),
      items: [],
      total: 0,
      artifacts: { ...mockSportsEvents(f).artifacts, capture_records: 'missing' },
    }))
    renderSports()
    expect(await screen.findByText(/No saved sports captures on this machine/i)).toBeInTheDocument()
  })

  it('passes filter and search values to the API', async () => {
    const user = userEvent.setup()
    renderSports()
    await screen.findAllByRole('link', { name: /^Open / })
    await user.selectOptions(screen.getByLabelText('Sport'), 'Soccer')
    await waitFor(() => expect(m.fetchSportsEvents).toHaveBeenLastCalledWith(expect.objectContaining({ sport: 'Soccer' })))
    await user.selectOptions(screen.getByLabelText('Capture / settlement'), 'missed')
    await waitFor(() => expect(m.fetchSportsEvents).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'missed' })))
    await user.type(screen.getByLabelText(/Search participants/i), 'mock{enter}')
    await waitFor(() => expect(m.fetchSportsEvents).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'mock' })))
  })

  it('navigates from a card to the event detail page', async () => {
    const user = userEvent.setup()
    renderSports()
    await user.click(await screen.findByRole('link', { name: /Open Mock State at Example Tech/ }))
    expect(await screen.findByRole('heading', { name: 'Mock State at Example Tech' })).toBeInTheDocument()
    expect(m.fetchSportsEvent).toHaveBeenCalledWith('ncaaf|espn:mock-1')
  })

  it('decodes the scheduler result and shows settlement deferral, lock skips and backup', async () => {
    m.fetchSportsCollector.mockResolvedValue({
      ...mockSportsCollector,
      scheduler: {
        ...mockSportsCollector.scheduler,
        observable: true,
        configured: true,
        enabled: true,
        last_result: '2147946720',
        last_result_decoded: { code: 2147946720, hex: '0x800710E0', name: 'ERROR_REQUEST_REFUSED', meaning: 'refused' },
      },
      status: {
        ...mockSportsCollector.status,
        settlement_deferred_since: '2026-10-11T14:37:11Z',
        settlement_forced_by: '2026-10-11T17:37:11Z',
        skipped_lock_busy_24h: 2,
      },
      backup: { recorded: true, backup: 'pushed', at: '2026-10-11T15:30:42Z', files: 4585, missing_count: 0 },
    })
    renderSports()
    expect(await screen.findByText(/0x800710E0 ERROR_REQUEST_REFUSED/)).toBeInTheDocument()
    expect(screen.getByText(/capture windows open\); runs by/)).toBeInTheDocument()
    expect(screen.getByText('Lock-busy skips (24 h)').nextSibling).toHaveTextContent('2')
    expect(screen.getByText(/4585 files/)).toBeInTheDocument()
  })

  it('filters events by collection mode via the URL', async () => {
    const user = userEvent.setup()
    renderSports()
    await screen.findAllByRole('link', { name: /^Open / })
    await user.selectOptions(screen.getByLabelText('Collection mode'), 'automatic')
    await waitFor(() =>
      expect(m.fetchSportsEvents).toHaveBeenLastCalledWith(expect.objectContaining({ collection: 'automatic' })),
    )
  })

  it('shows collector scheduler state as unknown when unreadable', async () => {
    renderSports()
    expect(await screen.findByText('Scheduler: unknown')).toBeInTheDocument()
    expect(screen.getByText(/never starts a collection/i)).toBeInTheDocument()
  })
})

describe('Sports tabs', () => {
  it('supports keyboard navigation between tabs', async () => {
    const user = userEvent.setup()
    renderSports()
    const events = screen.getByRole('tab', { name: 'Events' })
    expect(events).toHaveAttribute('aria-selected', 'true')
    events.focus()
    await user.keyboard('{ArrowRight}')
    const results = screen.getByRole('tab', { name: 'Results' })
    expect(results).toHaveAttribute('aria-selected', 'true')
    expect(results).toHaveFocus()
    expect(await screen.findByText(/negative value means the candidate had lower error/i)).toBeInTheDocument()
    await user.keyboard('{End}')
    expect(screen.getByRole('tab', { name: 'Historical Research' })).toHaveAttribute('aria-selected', 'true')
  })

  it('results show matched-contract and unique-event counts and switch weighting', async () => {
    const user = userEvent.setup()
    renderSports('/sports?tab=results')
    const paired = await screen.findByRole('table', { name: /paired comparisons on common contracts/i })
    expect(within(paired).getByRole('columnheader', { name: 'Matched contracts' })).toBeInTheDocument()
    expect(within(paired).getByRole('columnheader', { name: 'Unique events' })).toBeInTheDocument()
    expect(within(paired).getByText('-0.0280')).toBeInTheDocument()
    const standalone = screen.getByRole('table', { name: /standalone metrics/i })
    expect(within(standalone).getByRole('rowheader', { name: 'Blend' }).parentElement).toHaveTextContent('1')
    expect(screen.getByText(/No ranking of bets, no executable edge/i)).toBeInTheDocument()
    await user.click(screen.getByLabelText('Contract-weighted'))
    await waitFor(() => expect(m.fetchSportsResults).toHaveBeenLastCalledWith(expect.objectContaining({ weighting: 'contract' })))
  })

  it('coverage shows statuses with exact reasons', async () => {
    renderSports('/sports?tab=coverage')
    expect(await screen.findByText('KXMOCKAWARD')).toBeInTheDocument()
    expect(screen.getByText('awards: voting outcomes without as-of inputs')).toBeInTheDocument()
    expect(screen.getByText(/segment markets: no quotes by scope decision/)).toBeInTheDocument()
  })

  it('historical research is separated from the pilot with protocol labels', async () => {
    renderSports('/sports?tab=historical')
    expect(await screen.findByText(/shown separately from the prospective pilot/i)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Phase 1 · sports_phase1_v2/ })).toBeInTheDocument()
  })
})

describe('Sports event detail', () => {
  it('shows actual unavailable reasons instead of zero', async () => {
    renderSports('/sports/events/ncaaf%7Cespn%3Amock-1')
    await screen.findByRole('heading', { name: 'Mock State at Example Tech' })
    expect(m.fetchSportsEvent).toHaveBeenCalledWith('ncaaf|espn:mock-1')
    expect(screen.getByText('Unavailable: no quote (candle older than 3 h)')).toBeInTheDocument()
    expect(screen.getByText('Unavailable: matched training support below threshold')).toBeInTheDocument()
    expect(screen.queryByText('0.0%')).toBeNull()
  })

  it('explains binary contracts, market reference candles and shows scores after settlement', async () => {
    renderSports('/sports/events/ncaaf%7Cespn%3Amock-1')
    await screen.findByRole('heading', { name: 'Mock State at Example Tech' })
    expect(screen.getByText(/do not sum to one/i)).toBeInTheDocument()
    expect(screen.getByText(/latest hourly Kalshi candle/i)).toBeInTheDocument()
    expect(screen.getAllByText(/candle 2026-10-10 15:00 UTC · age 46 min/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Brier 0\.1444/)).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader', { name: /Listing bid \/ ask \(diagnostic\)/ })).toHaveLength(2)
    expect(screen.getByText('Provenance').closest('details')).not.toHaveAttribute('open')
  })

  it('shows not-found for an unknown event id', async () => {
    m.fetchSportsEvent.mockRejectedValue(new Error('404 Not Found'))
    renderSports('/sports/events/nope')
    expect(await screen.findByText('Event not found')).toBeInTheDocument()
  })
})

describe('Sports navigation and dashboard', () => {
  it('app shell includes a Sports link and scopes the model chip to weather', () => {
    render(
      <MemoryRouter>
        <AppShell apiStatus="ok" modelStatus="artifacts loaded" />
      </MemoryRouter>,
    )
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Sports' })).toHaveAttribute('href', '/sports')
    expect(screen.getByText(/Weather model: artifacts loaded/)).toBeInTheDocument()
  })

  it('dashboard sports summary shows counts and last capture', async () => {
    render(
      <MemoryRouter>
        <SportsSummaryPanel />
      </MemoryRouter>,
    )
    expect(await screen.findByText('Sports — saved captures')).toBeInTheDocument()
    expect(screen.getByText('Pending settlement').nextSibling).toHaveTextContent('1')
    expect(screen.getByText(/Last capture:/)).toBeInTheDocument()
  })

  it('every mock payload is labeled as a mock fixture', () => {
    expect(MOCK_SOURCE).toMatch(/MOCK FIXTURE/)
    for (const payload of [mockSportsSummary, mockSportsEventDetail, mockSportsResults]) {
      expect(payload.source).toBe(MOCK_SOURCE)
      expect(payload.label).toMatch(/MOCK FIXTURE/)
    }
    expect(mockSportsHistorical.label).toMatch(/MOCK FIXTURE/)
    expect(mockSportsCollector.status.note).toMatch(/MOCK FIXTURE/)
  })
})

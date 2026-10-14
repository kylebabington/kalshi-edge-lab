import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/sports'
import { AppShell } from '../components/AppShell'
import { NotFoundError } from '../api/client'
import { watchlistStore } from '../watchlist/store'
import {
  MOCK_SOURCE,
  mockSportsCollector,
  mockSportsCoverage,
  mockSportsEventDetail,
  mockSportsEvents,
  mockSportsHistorical,
  mockSportsResults,
  mockSportsResultsTimeline,
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
    fetchSportsResultsTimeline: vi.fn(),
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
  localStorage.clear()
  watchlistStore.reload()
  m.fetchSportsSummary.mockResolvedValue(mockSportsSummary)
  m.fetchSportsEvents.mockImplementation(async (f) => mockSportsEvents(f))
  m.fetchSportsEvent.mockResolvedValue(mockSportsEventDetail)
  m.fetchSportsResults.mockResolvedValue(mockSportsResults)
  m.fetchSportsResultsTimeline.mockResolvedValue(mockSportsResultsTimeline)
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
      backup: {
        recorded: true,
        backup: 'push_failed',
        snapshot: 'committed',
        push: 'push_failed',
        unpushed: true,
        at: '2026-10-11T15:30:42Z',
        last_snapshot_at: '2026-10-11T15:30:42Z',
        last_snapshot_lock_s: 1.8,
        files: 4585,
        missing_count: 0,
      },
      missed_windows: { total: 4, last_24h: 3, recent: [] },
    })
    renderSports()
    expect(await screen.findByText(/0x800710E0 ERROR_REQUEST_REFUSED/)).toBeInTheDocument()
    expect(screen.getByText(/capture windows open\); runs by/)).toBeInTheDocument()
    expect(screen.getByText('Lock-busy skips (24 h)').nextSibling).toHaveTextContent('2')
    expect(screen.getByText('Capture health')).toBeInTheDocument()
    expect(screen.getByText('Backup health')).toBeInTheDocument()
    expect(screen.getByText('Deadline misses (24 h / total)').nextSibling).toHaveTextContent('3 / 4')
    expect(screen.getByText('Cycle failures (24 h) / consecutive')).toBeInTheDocument()
    expect(screen.getByText(/4585 files/)).toBeInTheDocument()
    expect(screen.getByText('Capture lock held by snapshot').nextSibling).toHaveTextContent('1.8 s')
    expect(screen.getByText('Upload pending').nextSibling).toHaveTextContent(/^yes/)
    expect(screen.getByText('Backup degraded')).toBeInTheDocument()
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

  it('sorts via the URL and shows plain-language filter labels', async () => {
    const user = userEvent.setup()
    renderSports()
    await screen.findAllByRole('link', { name: /^Open / })
    expect(within(screen.getByLabelText('Contract family')).getByRole('option', { name: 'Game winner' })).toBeInTheDocument()
    expect(within(screen.getByLabelText('Contract family')).getByRole('option', { name: 'Point spread' })).toBeInTheDocument()
    expect(within(screen.getByLabelText('League / competition')).getByRole('option', { name: /^Premier League/ })).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Sort by'), 'settlement_desc')
    await waitFor(() =>
      expect(m.fetchSportsEvents).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'settlement_desc' })),
    )
  })

  it('restores filters, sort and page when returning from a detail page', async () => {
    const user = userEvent.setup()
    renderSports('/sports?sport=Football&sort=start_desc')
    await user.click(await screen.findByRole('link', { name: /Open Mock State at Example Tech/ }))
    await screen.findByRole('heading', { name: 'Mock State at Example Tech' })
    await user.click(screen.getByRole('link', { name: /Back to list/ }))
    await screen.findAllByRole('link', { name: /^Open / })
    expect(screen.getByLabelText('Sport')).toHaveValue('Football')
    expect(screen.getByLabelText('Sort by')).toHaveValue('start_desc')
    expect(m.fetchSportsEvents).toHaveBeenLastCalledWith(expect.objectContaining({ sport: 'Football', sort: 'start_desc' }))
  })

  it('stars an event from the list without navigating', async () => {
    const user = userEvent.setup()
    renderSports()
    const star = await screen.findByRole('button', { name: /Add to watchlist: Mock United vs Fixture City/ })
    await user.click(star)
    expect(screen.getByRole('button', { name: /Remove from watchlist: Mock United vs Fixture City/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('tab', { name: 'Events' })).toBeInTheDocument()
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
    const paired = await screen.findByRole('table', { name: /paired differences on common contracts/i })
    expect(within(paired).getByRole('columnheader', { name: 'Matched contracts (n)' })).toBeInTheDocument()
    expect(within(paired).getByRole('columnheader', { name: 'Unique events' })).toBeInTheDocument()
    expect(within(paired).getByText('-0.0280')).toBeInTheDocument()
    const standalone = screen.getByRole('table', { name: /standalone metrics/i })
    expect(within(standalone).getByRole('rowheader', { name: 'Blend' }).parentElement).toHaveTextContent('1')
    expect(screen.getByText(/No ranking of bets, no executable edge/i)).toBeInTheDocument()
    await user.click(screen.getByLabelText('Contract-weighted'))
    await waitFor(() => expect(m.fetchSportsResults).toHaveBeenLastCalledWith(expect.objectContaining({ weighting: 'contract' })))
  })

  it('results explain metrics, label the pilot prospective and show sample size and period', async () => {
    const user = userEvent.setup()
    renderSports('/sports?tab=results')
    await screen.findByRole('table', { name: /paired differences/i })
    expect(screen.getAllByText(/Prospective · saved before outcome/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Data period:/).length).toBeGreaterThan(1)
    expect(screen.getByText(/3 contracts in 1/)).toBeInTheDocument()
    expect(screen.getByText(/rows are not comparable with each other/i)).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'What is Brier?' })[0])
    expect(screen.getByRole('note')).toHaveTextContent(/mean squared gap/i)
    await user.click(screen.getByRole('button', { name: 'What is event-equal weighting?' }))
    expect(screen.getAllByRole('note').some((n) => /every event counts once/i.test(n.textContent ?? ''))).toBe(true)
    expect(screen.queryByText(/best bet|top pick|\bBUY\b/i)).toBeNull()
  })

  it('results over time chart completed events and report other cohorts separately', async () => {
    const user = userEvent.setup()
    renderSports('/sports?tab=results&sport=football')
    const panel = (await screen.findByRole('heading', { name: /Results over time/ })).closest('section')!
    expect(panel).toHaveTextContent('(prospective pilot only)')
    expect(await within(panel).findByText(/2 completed events, 5 matched contracts/)).toBeInTheDocument()
    expect(panel).toHaveTextContent(/partially settled 1 events \(4 contracts\)/)
    expect(panel).toHaveTextContent(/completed without a settlement time 1/)
    expect(panel).toHaveTextContent(/Results table basis \(different cohort\)/)
    const table = within(panel).getByRole('table', { name: /Completed events, most recent first/ })
    const rows = within(table).getAllByRole('row')
    expect(rows[1]).toHaveTextContent('nfl|espn:mock-2')
    expect(rows[1]).toHaveTextContent('-0.0100 (2)')
    expect(m.fetchSportsResultsTimeline).toHaveBeenCalledWith(expect.objectContaining({ pair: 'frozen:market', sport: 'football' }))
    await user.selectOptions(within(panel).getByLabelText('Pair'), 'blend:market')
    await waitFor(() =>
      expect(m.fetchSportsResultsTimeline).toHaveBeenLastCalledWith(expect.objectContaining({ pair: 'blend:market' })),
    )
    await user.selectOptions(within(panel).getByLabelText('Collection'), 'automatic')
    await waitFor(() =>
      expect(m.fetchSportsResultsTimeline).toHaveBeenLastCalledWith(expect.objectContaining({ collection: 'automatic' })),
    )
    expect(panel.textContent).not.toMatch(/winner|better model|profit/i)
  })

  it('historical research carries the historical badge and its period', async () => {
    renderSports('/sports?tab=historical')
    await screen.findByText(/shown separately from the prospective pilot/i)
    expect(screen.getAllByText(/Historical research · backtest \/ replay/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Data period:/).length).toBeGreaterThan(0)
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
    renderSports('/sports/events/ncaaf%7Cespn%3Amock-1?family=spread')
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
    expect(screen.getAllByRole('columnheader', { name: /Listing bid \/ ask \(diagnostic\)/ })).toHaveLength(1)
    expect(screen.getByText('Provenance').closest('details')).not.toHaveAttribute('open')
  })

  it('compares candidates side by side with market differences only on matched contracts', async () => {
    const user = userEvent.setup()
    renderSports('/sports/events/ncaaf%7Cespn%3Amock-1')
    await screen.findByRole('heading', { name: 'Mock State at Example Tech' })
    expect(screen.getByRole('note')).toHaveTextContent(/One saved prediction checkpoint/)
    expect(screen.getByRole('note')).toHaveTextContent(/no time series/i)
    await user.selectOptions(screen.getByLabelText('Contract family'), 'spread')
    const summary = await screen.findByRole('table', { name: /Candidates side by side/ })
    expect(summary).toHaveAccessibleName(/probability differences, not executable edge/)
    const market = within(summary).getByRole('rowheader', { name: 'Market reference' }).parentElement!
    expect(market).toHaveTextContent('1 / 2')
    expect(market).toHaveTextContent('no quote (candle older than 3 h) (1)')
    const frozen = within(summary).getByRole('rowheader', { name: 'Frozen' }).parentElement!
    expect(frozen).toHaveTextContent('0.020')
    const ext7 = screen.getByRole('rowheader', { name: /KXNCAAFSPREAD-MOCK1-EXT7/ }).parentElement!
    expect(within(ext7).getAllByText('not matched')).toHaveLength(3)
    const ext3 = screen.getByRole('rowheader', { name: /KXNCAAFSPREAD-MOCK1-EXT3/ }).parentElement!
    expect(ext3).toHaveTextContent('+0.020')
    expect(ext3).toHaveTextContent('+0.050')
    expect(screen.queryByText(/edge:|\bBUY\b|profit/i)).toBeNull()
  })

  it('paginates large contract families instead of rendering every strike', async () => {
    const base = mockSportsEventDetail.families[1].contracts[1]
    const many = Array.from({ length: 60 }, (_, i) => ({ ...base, ticker: `KXMANY-${String(i).padStart(2, '0')}` }))
    m.fetchSportsEvent.mockResolvedValue({ ...mockSportsEventDetail, families: [{ family: 'spread', contracts: many }] })
    const user = userEvent.setup()
    renderSports('/sports/events/ncaaf%7Cespn%3Amock-1')
    await screen.findByText(/Contracts 1–25 of 60/)
    expect(screen.getAllByRole('rowheader', { name: /KXMANY-/ })).toHaveLength(25)
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText(/Contracts 26–50 of 60/)).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: /KXMANY-25/ }).closest('tr')?.firstElementChild).toHaveTextContent('26')
    expect(screen.queryByRole('rowheader', { name: /KXMANY-00/ })).toBeNull()
  })

  it('shows not-found for an unknown event id', async () => {
    m.fetchSportsEvent.mockRejectedValue(new NotFoundError('404 Not Found'))
    renderSports('/sports/events/nope')
    expect(await screen.findByText('Event not found')).toBeInTheDocument()
  })

  it('treats a server failure on detail as an error with retry, not as not-found', async () => {
    m.fetchSportsEvent.mockRejectedValueOnce(new Error('503 Service Unavailable'))
    const user = userEvent.setup()
    renderSports('/sports/events/ncaaf%7Cespn%3Amock-1')
    expect(await screen.findByText('503 Service Unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Event not found')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('heading', { name: 'Mock State at Example Tech' })).toBeInTheDocument()
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

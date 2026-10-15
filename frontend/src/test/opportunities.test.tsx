import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/opportunities'
import { NotFoundError } from '../api/client'
import { fmtEdge, fmtUsd } from '../lib/opportunities'
import { mockOpportunityDetail, mockOpportunityList, mockResearch } from '../mocks/opportunities'
import { OpportunitiesPage } from '../pages/OpportunitiesPage'
import { OpportunityDetailPage } from '../pages/OpportunityDetailPage'
import { watchlistStore } from '../watchlist/store'

vi.mock('../api/opportunities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/opportunities')>()
  return {
    ...actual,
    fetchOpportunities: vi.fn(),
    fetchOpportunity: vi.fn(),
    researchOpportunity: vi.fn(),
  }
})

const m = vi.mocked(api)
const WEATHER = 'KXHIGHNY-26OCT15-T64'
const SPORTS_OK = 'KXNFLGAME-MOCK-HOME'
const SPORTS_BLOCKED = 'KXNFLGAME-MOCK-AWAY'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/opportunities" element={<OpportunitiesPage />} />
        <Route path="/opportunities/:ticker" element={<OpportunityDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  watchlistStore.reload()
  m.fetchOpportunities.mockImplementation(async (f) => mockOpportunityList(f))
  m.fetchOpportunity.mockImplementation(async (ticker) => {
    const d = mockOpportunityDetail(ticker)
    if (!d) throw new NotFoundError(ticker)
    return d
  })
  m.researchOpportunity.mockImplementation(async (ticker) => mockResearch(ticker))
})

describe('formatters', () => {
  it('never renders missing fees or EV as zero', () => {
    expect(fmtUsd(null)).toBe('unavailable')
    expect(fmtEdge(null)).toBe('unavailable')
    expect(fmtEdge(0.275)).toBe('+27.5 pt')
    expect(fmtUsd(-0.01)).toBe('-$0.0100')
  })
})

describe('OpportunitiesPage', () => {
  it('shows the ranked list first, hides blocked rows and explains attention_score', async () => {
    renderAt('/opportunities')
    const table = await screen.findByRole('table', { name: 'Ranked opportunities' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('NYC high temperature')
    expect(screen.queryByText(/Fixture City - Fixture City/)).toBeNull()
    expect(screen.getByText(/not a probability, not an\s+expected value and not a recommendation/)).toBeInTheDocument()
    expect(m.fetchOpportunities).toHaveBeenCalledWith(expect.objectContaining({ include_blocked: false }))
  })

  it('shows before-fee EV and an unavailable after-fee EV when fees are missing', async () => {
    renderAt('/opportunities')
    const row = (await screen.findByRole('link', { name: /Open Mock United vs Fixture City - Mock United/ })).closest('tr')!
    expect(row).toHaveTextContent('before fees +$0.1100')
    expect(row).toHaveTextContent('after fees unavailable')
  })

  it('can show blocked rows and filter by category', async () => {
    const user = userEvent.setup()
    renderAt('/opportunities')
    await screen.findByRole('table')
    await user.click(screen.getByLabelText('Show blocked (diagnostics)'))
    expect(await screen.findByText(/Fixture City - Fixture City/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Weather' }))
    await waitFor(() => expect(screen.queryByText(/Fixture City - Fixture City/)).toBeNull())
    expect(m.fetchOpportunities).toHaveBeenLastCalledWith(expect.objectContaining({ category: 'weather', include_blocked: true }))
  })

  it('filters to watchlisted events', async () => {
    watchlistStore.add('sports', 'nfl|espn:401', 'Mock game')
    renderAt('/opportunities?watchlist=1')
    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('Mock United vs Fixture City - Mock United')
  })
})

describe('OpportunityDetailPage', () => {
  it('shows the transfer warning, numbers and the AI note for a caution weather market', async () => {
    renderAt(`/opportunities/${WEATHER}`)
    expect(await screen.findByRole('heading', { name: /NYC high temperature/ })).toBeInTheDocument()
    expect(screen.getAllByText(/KNYC -> CLINYC is NOT validated/).length).toBeGreaterThan(0)
    expect(screen.getByText('AI research does not modify the statistical model probability.')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Why the model likes it' })).getByText('-15')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Research with AI' })).toBeInTheDocument()
  })

  it('runs manual research and renders the structured result', async () => {
    const user = userEvent.setup()
    renderAt(`/opportunities/${WEATHER}`)
    await user.click(await screen.findByRole('button', { name: 'Research with AI' }))
    expect(m.researchOpportunity).toHaveBeenCalledWith(WEATHER, false)
    expect(await screen.findByText(/both rate the home side/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh AI Research' })).toBeInTheDocument()
    expect(screen.getByText('Main risks')).toBeInTheDocument()
  })

  it('shows cached research metadata and refreshes with force', async () => {
    const user = userEvent.setup()
    renderAt(`/opportunities/${SPORTS_OK}`)
    expect(await screen.findByText(/both rate the home side/)).toBeInTheDocument()
    expect(screen.getByText('cached')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Refresh AI Research' }))
    expect(m.researchOpportunity).toHaveBeenCalledWith(SPORTS_OK, true)
  })

  it('shows provider failures without breaking the page', async () => {
    const user = userEvent.setup()
    m.researchOpportunity.mockResolvedValueOnce({
      ...mockResearch(WEATHER),
      status: 'unavailable',
      research: null,
      error: { code: 'ollama_unreachable', message: 'Ollama is not reachable at http://localhost:11434' },
    })
    renderAt(`/opportunities/${WEATHER}`)
    await user.click(await screen.findByRole('button', { name: 'Research with AI' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Ollama is not reachable')
    expect(screen.getByRole('region', { name: 'Numbers' })).toBeInTheDocument()
  })

  it('blocks AI research for blocked markets and lists the reasons', async () => {
    renderAt(`/opportunities/${SPORTS_BLOCKED}`)
    expect(await screen.findByText(/Not eligible for AI research/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Research with AI/ })).toBeNull()
    expect(screen.getAllByText('no saved bid/ask quote for this contract').length).toBeGreaterThan(0)
  })

  it('shows a not-found state for unknown tickers', async () => {
    renderAt('/opportunities/NOPE')
    expect(await screen.findByText('No current opportunity')).toBeInTheDocument()
  })
})

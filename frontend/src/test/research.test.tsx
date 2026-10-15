import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ResearchPage } from '../pages/ResearchPage'
import { filterExperiments, periodText } from '../components/research/format'
import { ExperimentDetailPage } from '../pages/ExperimentDetailPage'
import { StatusRecordBadge } from '../components/StatusRecordBadge'
import { mockExperimentCards, mockExperimentDetail, mockExperimentList } from '../mocks/research'
import { mockModelSummary } from '../mocks/weatherEvent'
import { ApiError } from '../api/client'
import * as researchApi from '../api/research'

vi.mock('../api/research', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/research')>()
  return { ...actual, fetchResearchExperiments: vi.fn(), fetchResearchExperiment: vi.fn() }
})

vi.mock('../api/weather', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/weather')>()
  const saved = await import('../mocks/weatherSaved')
  return {
    ...actual,
    fetchWeatherMetricsContext: vi.fn(async () => saved.mockWeatherMetricsContext),
    fetchModelSummary: vi.fn(async () => mockModelSummary),
  }
})

const listMock = vi.mocked(researchApi.fetchResearchExperiments)
const detailMock = vi.mocked(researchApi.fetchResearchExperiment)

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname + loc.search}</div>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/research" element={<ResearchPage />} />
        <Route path="/research/:experimentId" element={<ExperimentDetailPage />} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  listMock.mockImplementation(async (category) => mockExperimentList(category))
  detailMock.mockImplementation(async (id) => {
    const d = mockExperimentDetail(id)
    if (!d) throw new ApiError('Unknown experiment', 404)
    return d
  })
})

describe('Research catalog', () => {
  it('lists weather and sports experiments with designation, version, samples and results', async () => {
    renderAt('/research')
    expect(await screen.findByText('Weather Phase 7: prospective GFS/HRRR shadow validation')).toBeInTheDocument()
    expect(await screen.findByText('Sports Phase 1 v1: frozen models vs naive baselines')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Weather experiments' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Sports experiments' })).toBeInTheDocument()
    const phase7 = screen.getByRole('article', { name: /Weather Phase 7/ })
    expect(within(phase7).getByText('v1')).toBeInTheDocument()
    expect(within(phase7).getByText(/Prospective · saved before outcome/)).toBeInTheDocument()
    expect(within(phase7).getByText('Prediction records')).toBeInTheDocument()
    expect(within(phase7).getAllByText('unavailable').length).toBeGreaterThan(0)
    expect(within(phase7).getByText('records directory not found', { exact: false })).toBeInTheDocument()
    expect(within(phase7).getAllByText(/Integrity · not checked/).length).toBeGreaterThan(0)
    expect(within(phase7).getAllByText(/Operations · unknown/).length).toBeGreaterThan(0)
    expect(within(phase7).getAllByText('RESEARCH ONLY').length).toBeGreaterThan(0)
    const historical = screen.getByRole('article', { name: /Sports Phase 1 v1/ })
    expect(within(historical).getByText(/Historical research/)).toBeInTheDocument()
  })

  it('never shows a successful integrity status on catalog cards', async () => {
    renderAt('/research')
    await screen.findByText('Sports Phase 1 v1: frozen models vs naive baselines')
    for (const article of screen.getAllByRole('article')) {
      expect(article).not.toHaveTextContent(/Integrity · verified/)
      expect(article).not.toHaveTextContent(/\bCOMPLETE\b/)
    }
  })

  it('loads weather and sports independently: one failure leaves the other visible with retry', async () => {
    listMock.mockImplementation(async (category) => {
      if (category === 'weather') throw new Error('weather catalog down')
      return mockExperimentList(category)
    })
    renderAt('/research')
    expect(await screen.findByText('Weather experiments unavailable')).toBeInTheDocument()
    expect(screen.getByText('weather catalog down')).toBeInTheDocument()
    expect(await screen.findByText('Sports Phase 1 v1: frozen models vs naive baselines')).toBeInTheDocument()

    listMock.mockImplementation(async (category) => mockExperimentList(category))
    const alert = screen.getByText('Weather experiments unavailable').closest('[role="alert"]') as HTMLElement
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Weather Phase 7: prospective GFS/HRRR shadow validation')).toBeInTheDocument()
  })

  it('keeps the catalog when the weather model summary fails', async () => {
    const weather = await import('../api/weather')
    vi.mocked(weather.fetchModelSummary).mockRejectedValueOnce(new Error('summary down'))
    renderAt('/research')
    expect(await screen.findByText('Weather model summary unavailable')).toBeInTheDocument()
    expect(await screen.findByText('Weather Phase 7: prospective GFS/HRRR shadow validation')).toBeInTheDocument()
  })

  it('applies category, designation, mode, results and search filters from the URL', async () => {
    renderAt('/research?category=sports&designation=historical')
    expect(await screen.findByText('Sports Phase 1 v1: frozen models vs naive baselines')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Weather experiments' })).toBeNull()
    expect(screen.queryByText('Sports prospective capture (Phase 2 capture v2)')).toBeNull()
    expect(listMock).not.toHaveBeenCalledWith('weather')
  })

  it('writes filter changes to the URL and clears them', async () => {
    const user = userEvent.setup()
    renderAt('/research')
    await screen.findByText('Sports Phase 1 v1: frozen models vs naive baselines')
    await user.selectOptions(screen.getByLabelText('Collection mode'), 'manual_pilot')
    expect(screen.getByTestId('location')).toHaveTextContent('mode=manual_pilot')
    expect(screen.getByText('Sports prospective capture (Phase 2 capture v2)')).toBeInTheDocument()
    expect(screen.queryByText('Sports Phase 1 v1: frozen models vs naive baselines')).toBeNull()
    expect(screen.getByText(/No weather experiments match these filters/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /clear/i }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/research$/)
  })

  it('shows UTC-midnight protocol boundaries as dates, not shifted local times', () => {
    expect(periodText({ label: 'test', start: '2026-01-01T00:00:00Z', end: '2026-10-06T00:00:00+00:00', basis: 'x' })).toBe(
      '2026-01-01 UTC – 2026-10-06 UTC',
    )
    expect(periodText(null, 'no captures')).toBe('unknown (no captures)')
    expect(periodText({ label: 'p', start: '2026-09-26', end: null, basis: 'x' })).toBe('2026-09-26 – ongoing')
  })

  it('filterExperiments matches search text and result availability', () => {
    const base = { q: '', designation: '', mode: '', status: '' }
    expect(filterExperiments(mockExperimentCards, { ...base, q: 'CLINYC' }).map((c) => c.id)).toEqual(['weather.clinyc_transfer_v1'])
    expect(filterExperiments(mockExperimentCards, { ...base, status: 'unavailable' }).map((c) => c.id)).toEqual([
      'weather.phase7_prospective_v1',
    ])
    expect(filterExperiments(mockExperimentCards, { ...base, designation: 'historical' }).map((c) => c.id)).toEqual(['sports.phase1_v1'])
  })

  it('links each card to its detail route', async () => {
    renderAt('/research')
    const link = await screen.findByRole('link', { name: 'Sports Phase 1 v1: frozen models vs naive baselines' })
    expect(link).toHaveAttribute('href', '/research/sports.phase1_v1')
  })
})

describe('Experiment detail', () => {
  it('shows question, candidates, collection modes, sources and read-only settings', async () => {
    renderAt('/research/sports.phase2_capture_v2')
    expect(await screen.findByRole('heading', { name: 'Sports prospective capture (Phase 2 capture v2)' })).toBeInTheDocument()
    expect(screen.getByText(/Mock research question/)).toBeInTheDocument()
    expect(screen.getByText('Candidates')).toBeInTheDocument()
    expect(screen.getByText('Cohort rules')).toBeInTheDocument()
    expect(screen.getByText('Limitations')).toBeInTheDocument()

    const replay = screen.getByTestId('mode-historical_replay')
    expect(within(replay).getByText('not part of this protocol')).toBeInTheDocument()
    expect(within(screen.getByTestId('mode-manual_pilot')).getByText('part of this protocol')).toBeInTheDocument()
    expect(within(screen.getByTestId('mode-automatic_collection')).getByText(/Operations · recent success/)).toBeInTheDocument()

    expect(screen.getByText('Tracked in the repository')).toBeInTheDocument()
    expect(screen.getByText('Local research artifacts')).toBeInTheDocument()
    expect(screen.getByText('research/sports/protocols/mock.json')).toBeInTheDocument()
    expect(screen.getByText('data/results/mock/metrics.json')).toBeInTheDocument()

    const settings = screen.getByText('Show frozen settings').closest('details') as HTMLElement
    expect(settings.querySelector('pre')).toHaveTextContent('"omitted_for_display": true')
    expect(within(settings).queryByRole('textbox')).toBeNull()
    expect(screen.getByRole('heading', { name: 'My notes on this experiment' })).toBeInTheDocument()
  })

  it('shows not found for an unknown id', async () => {
    renderAt('/research/weather.nope')
    expect(await screen.findByText('Experiment not found')).toBeInTheDocument()
  })

  it('shows a retryable error when the request fails', async () => {
    detailMock.mockRejectedValueOnce(new Error('network down'))
    renderAt('/research/sports.phase1_v1')
    expect(await screen.findByText('Experiment unavailable')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: /Sports Phase 1 v1/ })).toBeInTheDocument())
  })
})

describe('StatusRecordBadge', () => {
  it('renders a missing record as unknown, never as success', () => {
    render(<StatusRecordBadge record={null} />)
    expect(screen.getByText('unknown')).toBeInTheDocument()
    expect(screen.getByText(/No status record/)).toBeInTheDocument()
  })

  it('shows basis, reason, source and timestamp', () => {
    render(
      <StatusRecordBadge
        record={{
          kind: 'evidence',
          state: 'present',
          label: 'x',
          basis: 'file present; validity not checked',
          source: 'data/x.csv',
          as_of: '2099-01-01T00:00:00Z',
        }}
      />,
    )
    expect(screen.getByText('Evidence · present')).toBeInTheDocument()
    expect(screen.getByText(/validity not checked/)).toBeInTheDocument()
    expect(screen.getByText('data/x.csv')).toBeInTheDocument()
    expect(screen.getByText(/as of/)).toBeInTheDocument()
  })
})

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MarketDetailPage } from '../pages/MarketDetailPage'
import { ResearchPage } from '../pages/ResearchPage'
import { WeatherModelPage } from '../pages/WeatherModelPage'
import {
  mockModelSummary,
  mockSnapshot,
  mockSnapshotList,
  mockWeatherEvent,
} from '../mocks/weatherEvent'
import { SourceMismatchBanner } from '../components/EventCard'
import { ErrorState } from '../components/StatusBadge'

vi.mock('../api/weather', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/weather')>()
  const saved = await import('../mocks/weatherSaved')
  return {
    ...actual,
    fetchEventSnapshots: vi.fn(async () => mockSnapshotList),
    fetchSnapshot: vi.fn(async () => mockSnapshot),
    fetchWeatherMetricsContext: vi.fn(async () => saved.mockWeatherMetricsContext),
    fetchModelSummary: vi.fn(async () => mockModelSummary),
  }
})

vi.mock('../api/research', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/research')>()
  const mocks = await import('../mocks/research')
  return {
    ...actual,
    fetchResearchExperiments: vi.fn(async (category: 'weather' | 'sports') => mocks.mockExperimentList(category)),
  }
})

describe('UI foundation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('market detail renders probabilities and research-only badge', () => {
    render(
      <MemoryRouter>
        <MarketDetailPage loading={false} error={null} event={mockWeatherEvent} />
      </MemoryRouter>,
    )
    expect(screen.getByText('Probability Distribution')).toBeInTheDocument()
    expect(screen.getAllByText(/Research Only/i).length).toBeGreaterThan(0)
    expect(screen.queryByText(/\bBUY\b/)).toBeNull()
    expect(screen.queryByText(/\bSELL\b/)).toBeNull()
  })

  it('source mismatch / insufficient history warning appears', () => {
    render(<SourceMismatchBanner event={mockWeatherEvent} />)
    expect(screen.getByRole('alert')).toHaveTextContent(
      /INSUFFICIENT TARGET REGIME HISTORY|SETTLEMENT SOURCE MISMATCH/i,
    )
  })

  it('shows API error state', () => {
    render(<ErrorState message="boom" />)
    expect(screen.getByText(/API error/i)).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
  })

  it('mock payload uses first-class insufficient history status', () => {
    expect(mockWeatherEvent.prediction.prediction_status).toBe(
      'INSUFFICIENT_TARGET_REGIME_HISTORY',
    )
    expect(mockWeatherEvent.research_status?.trade_recommendations).toBe('DISABLED')
  })

  it('probability comparison does not invent trade controls', () => {
    render(
      <MemoryRouter>
        <MarketDetailPage loading={false} error={null} event={mockWeatherEvent} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/Probability Comparison/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /buy|sell|trade/i })).toBeNull()
  })

  it('weather model page renders EXPERIMENTAL transfer', async () => {
    render(
      <MemoryRouter>
        <WeatherModelPage />
      </MemoryRouter>,
    )
    expect(await screen.findByText('SETTLEMENT TARGET TRANSFER')).toBeInTheDocument()
    expect(screen.getAllByText('EXPERIMENTAL').length).toBeGreaterThan(0)
    expect(screen.queryByText('VALIDATED')).toBeNull()
  })

  it('evidence board renders source status and freshness', () => {
    render(
      <MemoryRouter>
        <MarketDetailPage loading={false} error={null} event={mockWeatherEvent} />
      </MemoryRouter>,
    )
    expect(screen.getByText('EXTERNAL EVIDENCE')).toBeInTheDocument()
    expect(screen.getByText('CALIBRATED GFS')).toBeInTheDocument()
    expect(screen.getByText('HRRR')).toBeInTheDocument()
    expect(screen.getByText('FORECAST DISCUSSION')).toBeInTheDocument()
    expect(screen.getAllByText(/FRESH|AGING|STALE|UNAVAILABLE|OK/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Research Only/i).length).toBeGreaterThan(0)
    expect(screen.queryByText(/\bBUY\b/)).toBeNull()
    expect(screen.queryByText(/\bSELL\b/)).toBeNull()
  })

  it('forecast evolution section present without inventing trade controls', () => {
    render(
      <MemoryRouter>
        <MarketDetailPage loading={false} error={null} event={mockWeatherEvent} />
      </MemoryRouter>,
    )
    expect(screen.getByText('Forecast Evolution')).toBeInTheDocument()
    expect(screen.getByText(/Real snapshots only/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /buy|sell|trade/i })).toBeNull()
  })

  it('As Of historical view uses one coherent snapshot with age-at-snapshot freshness', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <MarketDetailPage loading={false} error={null} event={mockWeatherEvent} />
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.getByText(/As Of inspector/i)).toBeInTheDocument()
    })
    const snapButtons = screen.getAllByRole('button').filter((b) =>
      /2026|SEP|9\/26|26\/9/i.test(b.textContent || ''),
    )
    expect(snapButtons.length).toBeGreaterThan(0)
    await user.click(snapButtons[0])
    await waitFor(() => {
      expect(screen.getAllByText(/immutable snapshot/i).length).toBeGreaterThan(0)
      expect(screen.getAllByText(/age at snapshot/i).length).toBeGreaterThan(0)
    })
    // Snapshot HRRR high differs from live mock — must show snapshot value.
    expect(screen.getByText(/58\.5/)).toBeInTheDocument()
    // Unavailable NWS from snapshot must be visible.
    expect(screen.getAllByText(/UNAVAILABLE/i).length).toBeGreaterThan(0)
    expect(screen.queryByText(/\bBUY\b/)).toBeNull()
  })

  it('research page shows data sources and CLINYC progress', async () => {
    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    )
    expect(await screen.findByText('Data sources')).toBeInTheDocument()
    expect(screen.getByText(/PROSPECTIVE JOURNAL/i)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'CLINYC_TRANSFER_V1' })).toBeInTheDocument()
    expect(screen.getByText(/0\s*\/\s*20/)).toBeInTheDocument()
    expect(screen.queryByText(/\bBUY\b/)).toBeNull()
  })

  it('research page never presents a configured source as active or a phase as complete', async () => {
    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    )
    await screen.findByText('Data sources')
    const summary = document.getElementById('weather-model-summary')!
    expect(summary).not.toHaveTextContent(/\bACTIVE\b/i)
    expect(summary).not.toHaveTextContent(/\bCOMPLETE\b/)
    expect(summary).not.toHaveTextContent(/\bLIVE\b/)
    expect(screen.getAllByText('UNKNOWN').length).toBeGreaterThan(0)
    expect(screen.getAllByText('CONFIGURED').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/No tracked completion record exists for this phase/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Operations · failed/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Evidence · missing/).length).toBeGreaterThan(0)
  })

  it('research page shows unknown, not defaults, when summary fields are absent', async () => {
    const weather = await import('../api/weather')
    vi.mocked(weather.fetchModelSummary).mockResolvedValueOnce({
      ...mockModelSummary,
      research_status: undefined,
      research_status_details: undefined,
      multi_source_evidence: undefined,
      multi_source_evidence_details: undefined,
      prospective_journal: undefined,
      settlement_transfer: { available: false },
      prospective_validation: undefined,
    })
    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/Research status: unknown/)).toBeInTheDocument()
    expect(screen.getByText(/Data source status: unknown/)).toBeInTheDocument()
    expect(screen.getByText(/Prospective journal counts: unavailable/)).toBeInTheDocument()
    expect(screen.getByText(/unavailable\s*\/\s*unavailable/)).toBeInTheDocument()
    const summary = document.getElementById('weather-model-summary')!
    expect(summary).not.toHaveTextContent(/\bCOMPLETE\b/)
    expect(summary).not.toHaveTextContent(/0\s*\/\s*20/)
    expect(summary).not.toHaveTextContent(/SHADOW ONLY/)
  })

  it('research page shows Phase 5 prospective validation and SHADOW ONLY', async () => {
    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/PROSPECTIVE WEATHER VALIDATION/i)).toBeInTheDocument()
    expect(screen.getAllByText(/SHADOW ONLY/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/d0_1200/i)).toBeInTheDocument()
    expect(screen.getByText(/1\s*\/\s*1/)).toBeInTheDocument()
  })

  it('research page separates prospective snapshot scores from the historical shadow replay', async () => {
    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    )
    expect(await screen.findAllByText(/Prospective · saved before outcome/)).not.toHaveLength(0)
    expect(await screen.findByText(/1 scored snapshots in 1/)).toBeInTheDocument()
    expect(screen.getAllByText(/Data period:/).length).toBeGreaterThan(0)
  })

  it('weather model page labels backtest metrics as historical with their period', async () => {
    render(
      <MemoryRouter>
        <WeatherModelPage />
      </MemoryRouter>,
    )
    expect(await screen.findAllByText(/Historical research · backtest \/ replay/)).not.toHaveLength(0)
    expect(screen.getAllByText(/calibration dataset/).length).toBeGreaterThan(0)
  })

  it('market detail shows shadow labeled and keeps incumbent probabilities', () => {
    render(
      <MemoryRouter>
        <MarketDetailPage loading={false} error={null} event={mockWeatherEvent} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/SHADOW MODELS/i)).toBeInTheDocument()
    expect(screen.getByText(/SHADOW — NOT USED FOR DECISION/i)).toBeInTheDocument()
    expect(screen.getByText(/Probability Distribution/i)).toBeInTheDocument()
    // Incumbent 60–61 is 42%; shadow is 38% — both visible, main chart remains incumbent.
    expect(screen.getAllByText(/42%/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/\bBUY\b/)).toBeNull()
    expect(screen.queryByText(/\bSELL\b/)).toBeNull()
  })
})

void vi

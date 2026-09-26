import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DashboardPage } from '../pages/DashboardPage'
import { MarketDetailPage } from '../pages/MarketDetailPage'
import { ResearchPage } from '../pages/ResearchPage'
import { WeatherModelPage } from '../pages/WeatherModelPage'
import {
  mockLiveWeather,
  mockModelSummary,
  mockSnapshot,
  mockSnapshotList,
  mockWeatherEvent,
} from '../mocks/weatherEvent'
import { SourceMismatchBanner } from '../components/EventCard'
import { ErrorState } from '../components/StatusBadge'

vi.mock('../api/weather', () => ({
  fetchEventSnapshots: vi.fn(async () => mockSnapshotList),
  fetchSnapshot: vi.fn(async () => mockSnapshot),
}))

describe('UI foundation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('dashboard renders events', () => {
    render(
      <MemoryRouter>
        <DashboardPage
          loading={false}
          error={null}
          events={mockLiveWeather.events}
          summary={mockModelSummary}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText('KXHIGHNY-26SEP26')).toBeInTheDocument()
    expect(screen.getByText(/Current Weather Markets/i)).toBeInTheDocument()
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
    expect(mockWeatherEvent.research_status.trade_recommendations).toBe('DISABLED')
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

  it('weather model page renders EXPERIMENTAL transfer', () => {
    render(
      <MemoryRouter>
        <WeatherModelPage loading={false} error={null} summary={mockModelSummary} />
      </MemoryRouter>,
    )
    expect(screen.getByText('SETTLEMENT TARGET TRANSFER')).toBeInTheDocument()
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

  it('research page shows Phase 4 multi-source and CLINYC progress', () => {
    render(
      <MemoryRouter>
        <ResearchPage loading={false} error={null} summary={mockModelSummary} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/MULTI-SOURCE EVIDENCE/i)).toBeInTheDocument()
    expect(screen.getByText(/PROSPECTIVE JOURNAL/i)).toBeInTheDocument()
    expect(screen.getByText(/CLINYC_TRANSFER_V1/i)).toBeInTheDocument()
    expect(screen.getByText(/0\s*\/\s*20/)).toBeInTheDocument()
    expect(screen.queryByText(/\bBUY\b/)).toBeNull()
  })

  it('research page shows Phase 5 prospective validation and SHADOW ONLY', () => {
    render(
      <MemoryRouter>
        <ResearchPage loading={false} error={null} summary={mockModelSummary} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/PROSPECTIVE WEATHER VALIDATION/i)).toBeInTheDocument()
    expect(screen.getAllByText(/SHADOW ONLY/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/d0_1200/i)).toBeInTheDocument()
    expect(screen.getByText(/1\s*\/\s*1/)).toBeInTheDocument()
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

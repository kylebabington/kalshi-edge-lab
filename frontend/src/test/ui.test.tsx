import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { DashboardPage } from '../pages/DashboardPage'
import { MarketDetailPage } from '../pages/MarketDetailPage'
import { WeatherModelPage } from '../pages/WeatherModelPage'
import { mockLiveWeather, mockModelSummary, mockWeatherEvent } from '../mocks/weatherEvent'
import { SourceMismatchBanner } from '../components/EventCard'
import { ErrorState } from '../components/StatusBadge'

describe('UI foundation', () => {
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
    expect(mockModelSummary.research_status?.live_recommendations).toBe('DISABLED')
  })

  it('probability comparison does not invent trade controls', () => {
    render(
      <MemoryRouter>
        <MarketDetailPage loading={false} error={null} event={mockWeatherEvent} />
      </MemoryRouter>,
    )
    expect(screen.getByText('Probability Comparison')).toBeInTheDocument()
    expect(screen.getByText(/Raw probability difference/i)).toBeInTheDocument()
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

  it('weather model page distinguishes direct dataset from calibrated model', () => {
    render(
      <MemoryRouter>
        <WeatherModelPage loading={false} error={null} summary={mockModelSummary} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/Direct CLINYC dataset/i)).toBeInTheDocument()
    expect(screen.getByText('AVAILABLE')).toBeInTheDocument()
    expect(screen.getByText(/Direct CLINYC calibrated prediction/i)).toBeInTheDocument()
    expect(screen.getByText('INSUFFICIENT HISTORY')).toBeInTheDocument()
  })
})

// Keep vi imported for future env stubs without unused-var lint noise in some configs.
void vi

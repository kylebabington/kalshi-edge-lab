import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { PredictionHistory } from '../components/PredictionHistory'
import { compareFamily, diffVsMarket, paginate } from '../lib/candidates'
import { bucketOptionLabel, defaultBucket, historyPoints } from '../lib/weatherHistory'
import { mockSportsEventDetail } from '../mocks/sports'
import { mockSavedWeatherEvent, mockWeatherBuckets } from '../mocks/weatherSaved'
import type { SavedWeatherEventDetail } from '../types/weather'

const detail = mockSavedWeatherEvent('KXHIGHNY-26SEP26') as SavedWeatherEventDetail
const [B60, B62] = mockWeatherBuckets.map((b) => b.id)

function Search() {
  return <output data-testid="search">{useLocation().search}</output>
}

function renderHistory(data: SavedWeatherEventDetail, path = '/w') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/w"
          element={
            <>
              <PredictionHistory data={data} />
              <Search />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

describe('weather prediction history', () => {
  it('plots only valid saved probabilities, in capture order, without filling gaps', () => {
    expect(historyPoints(detail.snapshots, B60).map((p) => p.p)).toEqual([0.42])
    expect(historyPoints(detail.snapshots, B62).map((p) => p.p)).toEqual([0.31])
    expect(historyPoints(detail.snapshots, null)).toEqual([])
    expect(defaultBucket(detail)).toBe(B60)
  })

  it('labels same-label buckets with different saved definitions as separate series', () => {
    const a = { ...mockWeatherBuckets[0], definition_variants: 2 }
    const b = { ...a, id: 'other', ticker: 'KXHIGHNY-26SEP26-B60.6', lower_f: 61 }
    expect(bucketOptionLabel(a)).not.toBe(bucketOptionLabel(b))
    expect(bucketOptionLabel(mockWeatherBuckets[0])).toBe('60° to 61°')
  })

  it('lists every snapshot chronologically with checkpoint, run, observations and unavailable values', () => {
    renderHistory(detail)
    const table = screen.getByRole('table', { name: /Saved snapshots in capture order/ })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('dminus1_1800')
    expect(rows[0]).toHaveTextContent('unavailable (invalid saved value)')
    expect(rows[0]).toHaveTextContent('latest time unknown')
    expect(rows[0]).toHaveTextContent('2 time unknown')
    expect(rows[1]).toHaveTextContent('42.0%')
    expect(rows[1]).toHaveTextContent('event-day 06Z')
    expect(rows[1]).toHaveTextContent('GEFS 31 members')
    expect(rows[1]).toHaveTextContent('in the target climate day')
    expect(rows[1]).toHaveTextContent('11 in day · 1 outside')
    expect(screen.getByText(/1 of 2 saved snapshots plotted/)).toBeInTheDocument()
    expect(screen.getByText(/fixed-EST climate day/)).toBeInTheDocument()
    expect(screen.getByText(/not market prices/)).toBeInTheDocument()
  })

  it('keeps the selected bucket in the URL', async () => {
    const user = userEvent.setup()
    renderHistory(detail, `/w?bucket=${encodeURIComponent(B62)}`)
    const select = screen.getByLabelText('Bucket') as HTMLSelectElement
    expect(select.value).toBe(B62)
    const rows = within(screen.getByRole('table', { name: /Saved snapshots/ })).getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('not in this snapshot')
    await user.selectOptions(select, B60)
    expect(new URLSearchParams(screen.getByTestId('search').textContent ?? '').get('bucket')).toBe(B60)
  })

  it('shows an unavailable state when no snapshot has bucket probabilities', () => {
    renderHistory({ ...detail, buckets: [] })
    expect(screen.getByText(/no saved snapshot for this event has bucket probabilities/i)).toBeInTheDocument()
  })
})

describe('candidate comparison library', () => {
  const spread = mockSportsEventDetail.families[1].contracts

  it('differences need both the candidate and the market reference on the same contract', () => {
    expect(diffVsMarket(spread[0], 'frozen')).toBeNull()
    expect(diffVsMarket(spread[1], 'frozen')).toBeCloseTo(0.02, 10)
    expect(diffVsMarket(spread[1], 'blend')).toBeNull()
  })

  it('counts matched contracts per candidate and tallies unavailable reasons', () => {
    const cmp = compareFamily(spread)
    expect(cmp.predicted).toBe(2)
    expect(cmp.matched).toMatchObject({ frozen: 1, calibrated: 1, blend: 0, market: 1 })
    expect(cmp.meanAbsDiff.frozen).toBeCloseTo(0.02, 10)
    expect(cmp.meanAbsDiff.blend).toBeNull()
    expect(cmp.unavailable.blend.map((u) => u.contracts).reduce((a, b) => a + b, 0)).toBe(2)
    expect(cmp.unavailable.market).toEqual([{ reason: 'no quote (candle older than 3 h)', contracts: 1 }])
  })

  it('paginates and clamps pages', () => {
    const items = Array.from({ length: 51 }, (_, i) => i)
    expect(paginate(items, 1, 25)).toMatchObject({ page: 1, pages: 3 })
    expect(paginate(items, 3, 25).items).toEqual([50])
    expect(paginate(items, 99, 25).page).toBe(3)
    expect(paginate([], 1, 25)).toMatchObject({ items: [], page: 1, pages: 1 })
  })
})

import { describe, expect, it } from 'vitest'
import { aliasNoteTargets, aliasWatchIds, isStarred, preferredWatch } from '../explore/identity'
import { coverageSummary, modelSupportLabel, savedForecastLabel } from '../explore/labels'
import type { ExploreCoverage } from '../types/explore'
import { watchId } from '../watchlist/store'

describe('explore identity', () => {
  it('prefers weather key for KXHIGHNY without moving other categories', () => {
    const cov: ExploreCoverage = { model_supported: 'weather', saved_forecast: 'unavailable' }
    expect(preferredWatch('KXHIGHNY-26OCT15', cov)).toEqual({ category: 'weather', key: 'KXHIGHNY-26OCT15' })
    expect(aliasWatchIds('KXHIGHNY-26OCT15', cov)).toContain(watchId('kalshi', 'KXHIGHNY-26OCT15'))
    expect(aliasWatchIds('KXHIGHNY-26OCT15', cov)).toContain(watchId('weather', 'KXHIGHNY-26OCT15'))
  })

  it('keeps kalshi preferred before sports index warms, then adds sports alias after mapping', () => {
    const pending: ExploreCoverage = {
      model_supported: 'sports',
      saved_forecast: 'lookup_pending',
      sports_event_id: null,
    }
    expect(preferredWatch('KXNFLGAME-MOCK', pending)).toEqual({ category: 'kalshi', key: 'KXNFLGAME-MOCK' })
    expect(aliasNoteTargets('KXNFLGAME-MOCK', pending)).toEqual([{ type: 'event', category: 'kalshi', key: 'KXNFLGAME-MOCK' }])

    const mapped: ExploreCoverage = {
      model_supported: 'sports',
      saved_forecast: 'available',
      sports_event_id: 'nfl|mock-cluster',
    }
    expect(preferredWatch('KXNFLGAME-MOCK', mapped)).toEqual({ category: 'sports', key: 'nfl|mock-cluster' })
    const aliases = aliasNoteTargets('KXNFLGAME-MOCK', mapped)
    expect(aliases).toContainEqual({ type: 'event', category: 'kalshi', key: 'KXNFLGAME-MOCK' })
    expect(aliases).toContainEqual({ type: 'event', category: 'sports', key: 'nfl|mock-cluster' })
    // One sports event_id can map to several Kalshi tickers; aliases are per ticker, not 1:1 forced.
    expect(aliasWatchIds('KXNFLGAME-OTHER', mapped)).toContain(watchId('sports', 'nfl|mock-cluster'))
    expect(aliasWatchIds('KXNFLGAME-OTHER', mapped)).toContain(watchId('kalshi', 'KXNFLGAME-OTHER'))
  })

  it('treats any alias as starred without requiring the preferred id', () => {
    const mapped: ExploreCoverage = {
      model_supported: 'sports',
      saved_forecast: 'available',
      sports_event_id: 'nfl|mock-cluster',
    }
    expect(isStarred([{ id: watchId('kalshi', 'KXNFLGAME-MOCK') }], 'KXNFLGAME-MOCK', mapped)).toBe(true)
    expect(isStarred([{ id: watchId('sports', 'nfl|mock-cluster') }], 'KXNFLGAME-MOCK', mapped)).toBe(true)
    expect(isStarred([{ id: watchId('kalshi', 'OTHER') }], 'KXNFLGAME-MOCK', mapped)).toBe(false)
  })
})

describe('coverage labels', () => {
  it('does not say no model available for supported sports without a capture', () => {
    const cov: ExploreCoverage = {
      model_supported: 'sports',
      saved_forecast: 'unavailable',
      saved_forecast_basis: 'Supported series, but no captured sports event',
    }
    expect(modelSupportLabel(cov)).toMatch(/Sports model supported/)
    expect(savedForecastLabel(cov)).toBe('No saved forecast')
    expect(coverageSummary(cov)).toBe('Sports model supported · no saved forecast')
    expect(coverageSummary(cov).toLowerCase()).not.toContain('no model available')
  })

  it('preserves lookup_pending while sports index is loading', () => {
    const cov: ExploreCoverage = { model_supported: 'sports', saved_forecast: 'lookup_pending' }
    expect(savedForecastLabel(cov)).toBe('Saved forecast lookup pending')
    expect(coverageSummary(cov)).toContain('lookup pending')
  })
})

import { describe, expect, it, vi } from 'vitest'
import { NotFoundError } from '../api/client'
import { MAX_WATCHED_TICKERS, resolveWatchRefreshTargets } from '../explore/refreshTargets'
import type { WatchEntry } from '../watchlist/store'

function entry(over: Partial<WatchEntry> & Pick<WatchEntry, 'category' | 'key'>): WatchEntry {
  return {
    id: `${over.category}:${over.key}`,
    label: over.label ?? over.key,
    added_at: '2099-01-01T00:00:00Z',
    ...over,
  }
}

describe('resolveWatchRefreshTargets', () => {
  it('includes weather and kalshi keys and expands sports tickers with dedupe', async () => {
    const result = await resolveWatchRefreshTargets(
      [
        entry({ category: 'weather', key: 'KXHIGHNY-1' }),
        entry({ category: 'kalshi', key: 'KXHIGHNY-1' }),
        entry({ category: 'kalshi', key: 'KXOTHER-1' }),
        entry({ category: 'sports', key: 'nfl|a' }),
      ],
      {
        fetchSports: async () => ({ event: { kalshi_event_tickers: ['KXHIGHNY-1', 'KXNFL-GAME', 'KXNFL-SPREAD'] } }),
      },
    )
    expect(result.tickers).toEqual(['KXHIGHNY-1', 'KXOTHER-1', 'KXNFL-GAME', 'KXNFL-SPREAD'])
    expect(result.unresolved).toHaveLength(0)
  })

  it('records sports not-found and empty ticker lists without dropping other targets', async () => {
    const result = await resolveWatchRefreshTargets(
      [
        entry({ category: 'kalshi', key: 'KEEP' }),
        entry({ category: 'sports', key: 'missing', label: 'Missing' }),
        entry({ category: 'sports', key: 'empty', label: 'Empty' }),
      ],
      {
        fetchSports: async (id) => {
          if (id === 'missing') throw new NotFoundError('404')
          return { event: { kalshi_event_tickers: [] } }
        },
      },
    )
    expect(result.tickers).toEqual(['KEEP'])
    expect(result.unresolved.map((u) => u.key).sort()).toEqual(['empty', 'missing'])
  })

  it('times out a slow sports entry and still returns resolved tickers', async () => {
    const result = await resolveWatchRefreshTargets(
      [entry({ category: 'kalshi', key: 'FAST' }), entry({ category: 'sports', key: 'slow', label: 'Slow' })],
      {
        timeoutMs: 30,
        concurrency: 2,
        fetchSports: () => new Promise(() => {}),
      },
    )
    expect(result.tickers).toEqual(['FAST'])
    expect(result.unresolved[0]?.reason).toMatch(/Timed out/)
  })

  it('truncates at the 500-ticker cap and reports over-cap entries', async () => {
    const many = Array.from({ length: MAX_WATCHED_TICKERS + 3 }, (_, i) =>
      entry({ category: 'kalshi', key: `T-${i}` }),
    )
    const result = await resolveWatchRefreshTargets(many, {
      fetchSports: async () => ({ event: { kalshi_event_tickers: [] } }),
    })
    expect(result.tickers).toHaveLength(MAX_WATCHED_TICKERS)
    expect(result.truncated).toBe(true)
    expect(result.overCap.length).toBeGreaterThan(0)
  })

  it('skips sports expansion when direct tickers already filled the cap', async () => {
    const many = Array.from({ length: MAX_WATCHED_TICKERS }, (_, i) => entry({ category: 'weather', key: `W-${i}` }))
    const sports = entry({ category: 'sports', key: 'nfl|x', label: 'Game' })
    const fetchSports = vi.fn(async () => ({ event: { kalshi_event_tickers: ['SHOULD-NOT'] } }))
    const result = await resolveWatchRefreshTargets([...many, sports], { fetchSports, maxTickers: MAX_WATCHED_TICKERS })
    expect(fetchSports).not.toHaveBeenCalled()
    expect(result.tickers).toHaveLength(MAX_WATCHED_TICKERS)
    expect(result.overCap.some((o) => o.key === 'nfl|x')).toBe(true)
  })
})

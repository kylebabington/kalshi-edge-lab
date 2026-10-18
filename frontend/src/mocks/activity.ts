import type { ActivityFilters, ActivityIds, ActivityItem, ActivityList, ActivitySummary } from '../types/activity'

const ITEMS: ActivityItem[] = [
  {
    id: 'explore.discovered:KXHIGHNY-26OCT18:2026-10-17T12:00:00Z',
    type: 'explore_discovered',
    category: 'kalshi',
    observed_at: '2026-10-17T12:00:00Z',
    source_at: '2026-10-17T12:00:00Z',
    title: 'Discovered KXHIGHNY-26OCT18',
    summary: 'First seen by this app during explore refresh.',
    href: '/explore/events/KXHIGHNY-26OCT18',
    subject: { kind: 'event_ticker', key: 'KXHIGHNY-26OCT18', aliases: ['kalshi:KXHIGHNY-26OCT18', 'weather:KXHIGHNY-26OCT18'] },
  },
  {
    id: 'explore.quote:KXHIGHNY-26OCT18:KXHIGHNY-26OCT18-T70:2026-10-17T14:00:00Z',
    type: 'explore_quote_change',
    category: 'kalshi',
    observed_at: '2026-10-17T14:00:00Z',
    source_at: '2026-10-17T14:00:00Z',
    title: 'Quote changed: T70',
    summary: 'KXHIGHNY-26OCT18-T70: 2 field(s) changed.',
    href: '/explore/events/KXHIGHNY-26OCT18',
    subject: { kind: 'event_ticker', key: 'KXHIGHNY-26OCT18', aliases: ['kalshi:KXHIGHNY-26OCT18'] },
    payload: {
      market_ticker: 'KXHIGHNY-26OCT18-T70',
      old_retrieved_at: '2026-10-17T12:00:00Z',
      new_retrieved_at: '2026-10-17T14:00:00Z',
      changes: [
        { field: 'yes_bid', from: 0.4, to: 0.42 },
        { field: 'yes_ask', from: 0.45, to: 0.48 },
      ],
    },
  },
  {
    id: 'weather.forecast:KXHIGHNY-26OCT18__20261017120000Z',
    type: 'forecast_accepted',
    category: 'weather',
    observed_at: '2026-10-17T12:05:00Z',
    source_at: '2026-10-17T12:00:00Z',
    title: 'Forecast accepted: KXHIGHNY-26OCT18',
    summary: 'Snapshot KXHIGHNY-26OCT18__20261017120000Z',
    href: '/markets/KXHIGHNY-26OCT18',
    subject: { kind: 'event_ticker', key: 'KXHIGHNY-26OCT18', aliases: ['weather:KXHIGHNY-26OCT18', 'kalshi:KXHIGHNY-26OCT18'] },
  },
  {
    id: 'collector.failure:sports:2026-10-16T08:00:00Z',
    type: 'collector_failure',
    category: 'system',
    observed_at: '2026-10-16T08:00:00Z',
    source_at: '2026-10-16T08:00:00Z',
    title: 'Sports collector failure',
    summary: 'capture_problem',
    href: '/sports',
    subject: { kind: 'collector', key: 'sports' },
  },
  {
    id: 'collector.recovery:sports:2026-10-16T09:00:00Z',
    type: 'collector_recovery',
    category: 'system',
    observed_at: '2026-10-16T09:00:00Z',
    source_at: '2026-10-16T09:00:00Z',
    title: 'Sports collector recovered',
    summary: 'First successful cycle after one or more failures.',
    href: '/sports',
    subject: { kind: 'collector', key: 'sports' },
  },
]

function filterItems(filters: ActivityFilters): ActivityItem[] {
  const q = (filters.q || '').trim().toLowerCase()
  const watched = new Set((filters.watched || '').split(',').map((s) => s.trim()).filter(Boolean))
  return ITEMS.filter((it) => {
    if (filters.category && it.category !== filters.category) return false
    if (filters.type && it.type !== filters.type) return false
    if (watched.size) {
      const aliases = new Set([...(it.subject?.aliases || []), it.subject?.key ? `${it.subject.kind}:${it.subject.key}` : ''].filter(Boolean))
      if (![...watched].some((w) => aliases.has(w))) return false
    }
    if (q) {
      const blob = `${it.title} ${it.summary} ${it.id}`.toLowerCase()
      if (!blob.includes(q)) return false
    }
    return true
  })
}

export const mockActivitySummary: ActivitySummary = {
  generated_at: '2026-10-17T15:00:00Z',
  index_built_at: '2026-10-17T15:00:00Z',
  mode: 'saved',
  total: ITEMS.length,
  by_type: Object.fromEntries(
    [...new Set(ITEMS.map((i) => i.type))].map((t) => [t, ITEMS.filter((i) => i.type === t).length]),
  ),
  by_category: Object.fromEntries(
    [...new Set(ITEMS.map((i) => i.category))].map((c) => [c, ITEMS.filter((i) => i.category === c).length]),
  ),
  newest_observed_at: '2026-10-17T14:00:00Z',
  notes: [],
}

export function mockActivityList(filters: ActivityFilters = {}): ActivityList {
  const filtered = filterItems(filters)
  const page = Math.max(1, filters.page ?? 1)
  const page_size = Math.min(200, Math.max(1, filters.page_size ?? 40))
  const pages = Math.max(1, Math.ceil(filtered.length / page_size) || 1)
  const start = (page - 1) * page_size
  return {
    generated_at: '2026-10-17T15:00:00Z',
    index_built_at: '2026-10-17T15:00:00Z',
    mode: 'saved',
    page,
    page_size,
    total: filtered.length,
    pages,
    items: filtered.slice(start, start + page_size),
    notes: [],
  }
}

export function mockActivityIds(filters: ActivityFilters = {}): ActivityIds {
  const ids = filterItems(filters).map((i) => i.id)
  return { generated_at: '2026-10-17T15:00:00Z', ids, total: ids.length }
}

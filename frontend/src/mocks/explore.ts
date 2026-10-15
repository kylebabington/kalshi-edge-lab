import type { ExploreEventDetail, ExploreEventList, ExploreSummary } from '../types/explore'

const GENERATED = '2099-01-01T12:00:00Z'

export const mockExploreSummary: ExploreSummary = {
  generated_at: GENERATED,
  mode: 'RESEARCH_ONLY',
  event_count: 3,
  categories: [
    { name: 'Climate and Weather', event_count: 1 },
    { name: 'Sports', event_count: 1 },
    { name: 'Politics', event_count: 1 },
  ],
  lifecycle_totals: { open: 2, unknown: 1 },
  series_metadata: { available: true, retrieved_at: GENERATED, freshness: 'fresh', degraded: false, count: 3 },
  latest_run: {
    run_id: 'mock',
    state: 'complete',
    started_at: GENERATED,
    finished_at: GENERATED,
    steps: { open_crawl: { pages: 1, complete: true, scope: { params: { status: 'open' } } } },
  },
  refresh: { state: 'idle', message: 'No refresh has been run yet' },
  notes: ['GET endpoints read the local explorer cache only.'],
}

const cards = [
  {
    event_ticker: 'KXHIGHNY-26OCT15',
    series_ticker: 'KXHIGHNY',
    title: 'Will the high temp in NYC be 64-65° on Oct 15, 2026?',
    sub_title: '64-65°',
    category: 'Climate and Weather',
    categories: ['Climate and Weather'],
    lifecycle: 'open',
    markets_complete: true,
    listed_open_at: GENERATED,
    retrieved_at: GENERATED,
    open_contracts: 1,
    market_count: 1,
    close_time_min: '2026-10-15T23:00:00Z',
    close_time_max: '2026-10-15T23:00:00Z',
    volume_24h_total: 120,
    quote: { two_sided: 1, one_sided: 0, none: 0 },
    retrieval_freshness: { state: 'fresh', age_seconds: 60, basis: 'Retrieved within 2 h', as_of: GENERATED },
    coverage: {
      model_supported: 'weather',
      model_supported_basis: 'Series KXHIGHNY',
      saved_forecast: 'available',
      saved_forecast_basis: 'Snapshot present',
      saved_forecast_href: '/markets/KXHIGHNY-26OCT15',
    },
  },
  {
    event_ticker: 'KXNFLGAME-MOCK',
    series_ticker: 'KXNFLGAME',
    title: 'Mock NFL game winner',
    category: 'Sports',
    categories: ['Sports'],
    lifecycle: 'open',
    markets_complete: true,
    listed_open_at: GENERATED,
    retrieved_at: GENERATED,
    open_contracts: 1,
    market_count: 2,
    quote: { two_sided: 1, one_sided: 1, none: 0 },
    retrieval_freshness: { state: 'fresh', age_seconds: 120, basis: 'Retrieved within 2 h', as_of: GENERATED },
    coverage: {
      model_supported: 'sports',
      model_supported_basis: 'Supported series',
      saved_forecast: 'unavailable',
      saved_forecast_basis: 'Supported series, but no captured sports event',
      saved_forecast_href: null,
    },
  },
  {
    event_ticker: 'KXOTHER-MOCK',
    series_ticker: 'KXOTHER',
    title: 'Mock politics event',
    category: 'Politics',
    categories: ['Politics'],
    lifecycle: 'open',
    markets_complete: true,
    listed_open_at: GENERATED,
    retrieved_at: GENERATED,
    open_contracts: 1,
    market_count: 1,
    quote: { two_sided: 0, one_sided: 0, none: 1 },
    retrieval_freshness: { state: 'aging', age_seconds: 10000, basis: 'aging', as_of: GENERATED },
    coverage: {
      model_supported: 'none',
      model_supported_basis: 'No model registered',
      saved_forecast: 'unavailable',
      saved_forecast_basis: 'No model is registered for this series',
      saved_forecast_href: null,
    },
  },
]

export function mockExploreEvents(filters: { category?: string; q?: string; page?: number; page_size?: number } = {}): ExploreEventList {
  let events = [...cards]
  if (filters.category) events = events.filter((e) => e.category === filters.category)
  if (filters.q) {
    const q = filters.q.toLowerCase()
    events = events.filter((e) => `${e.title} ${e.event_ticker}`.toLowerCase().includes(q))
  }
  const page = filters.page ?? 1
  const page_size = filters.page_size ?? 50
  const start = (page - 1) * page_size
  return {
    generated_at: GENERATED,
    mode: 'RESEARCH_ONLY',
    page,
    page_size,
    total: events.length,
    pages: Math.max(1, Math.ceil(events.length / page_size)),
    events: events.slice(start, start + page_size),
    partial_crawl: false,
    latest_run_id: 'mock',
  }
}

export function mockExploreEvent(id: string): ExploreEventDetail | null {
  const card = cards.find((c) => c.event_ticker === id)
  if (!card) return null
  return {
    ...card,
    generated_at: GENERATED,
    mode: 'RESEARCH_ONLY',
    settlement_sources: [{ name: 'Example', url: 'https://example.test' }],
    contract_url: 'https://example.test/contract',
    contract_terms_url: 'https://example.test/terms',
    quote_section_label: 'Latest retrieved quotes',
    markets: [
      {
        ticker: `${id}-M1`,
        status: 'active',
        is_open: true,
        yes_bid: 0.4,
        yes_ask: 0.45,
        no_bid: 0.55,
        no_ask: 0.6,
        last_price: 0.42,
        volume_24h: 50,
        open_interest: 20,
        retrieved_at: GENERATED,
        retrieval_freshness: { state: 'fresh', age_seconds: 60, basis: 'within 2h', as_of: GENERATED },
        close_time: card.close_time_min ?? null,
        rules_primary: 'Mock settlement rule.',
      },
    ],
    coverage: card.coverage,
    notes: ['Quotes are the latest values retrieved into the explorer cache.'],
  }
}

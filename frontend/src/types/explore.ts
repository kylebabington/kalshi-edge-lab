export type ExploreCoverage = {
  model_supported: 'weather' | 'sports' | 'none' | string
  model_supported_basis?: string
  saved_forecast: 'available' | 'unavailable' | 'lookup_pending' | string
  saved_forecast_basis?: string
  saved_forecast_href?: string | null
  sports_event_id?: string | null
  research_hrefs?: { label: string; href: string }[]
}

export type RetrievalFreshness = {
  state: string
  age_seconds?: number | null
  basis?: string
  as_of?: string | null
  reason?: string | null
}

export type ExploreEventCard = {
  event_ticker: string
  series_ticker?: string | null
  title?: string | null
  sub_title?: string | null
  category?: string | null
  categories?: string[]
  lifecycle?: string | null
  lifecycle_detail?: { state?: string; basis?: string; reason?: string | null; markets_complete?: boolean }
  markets_complete?: boolean
  listed_open_at?: string | null
  retrieved_at?: string | null
  last_lookup?: { at?: string; outcome?: string; reason?: string | null } | null
  contract_status_counts?: Record<string, number>
  open_contracts?: number
  market_count?: number
  close_time_min?: string | null
  close_time_max?: string | null
  volume_24h_total?: number | null
  quote?: { two_sided?: number; one_sided?: number; none?: number }
  retrieval_freshness?: RetrievalFreshness
  coverage?: ExploreCoverage
}

export type ExploreSummary = {
  generated_at: string
  mode: string
  event_count: number
  categories: { name: string; event_count: number }[]
  lifecycle_totals?: Record<string, number>
  series_metadata?: {
    available?: boolean
    retrieved_at?: string | null
    freshness?: string
    degraded?: boolean
    count?: number | null
    error?: string | null
  }
  latest_run?: Record<string, unknown> | null
  refresh?: Record<string, unknown>
  notes?: string[]
}

export type ExploreEventList = {
  generated_at: string
  mode: string
  page: number
  page_size: number
  total: number
  pages: number
  events: ExploreEventCard[]
  partial_crawl?: boolean
  latest_run_id?: string | null
}

export type ExploreMarket = {
  ticker?: string
  status?: string | null
  is_open?: boolean
  yes_bid?: number | null
  yes_ask?: number | null
  no_bid?: number | null
  no_ask?: number | null
  yes_bid_size?: number | null
  yes_ask_size?: number | null
  last_price?: number | null
  volume?: number | null
  volume_24h?: number | null
  open_interest?: number | null
  open_time?: string | null
  close_time?: string | null
  expiration_time?: string | null
  updated_time?: string | null
  rules_primary?: string | null
  rules_secondary?: string | null
  retrieved_at?: string | null
  retrieval_freshness?: RetrievalFreshness
  not_in_latest_response?: boolean
  yes_sub_title?: string | null
  no_sub_title?: string | null
}

export type ExploreEventDetail = ExploreEventCard & {
  generated_at: string
  mode: string
  series_title?: string | null
  settlement_sources?: { name?: string; url?: string }[]
  contract_url?: string | null
  contract_terms_url?: string | null
  markets: ExploreMarket[]
  markets_complete_basis?: string | null
  quote_section_label?: string
  notes?: string[]
  coverage: ExploreCoverage
}

export type ExploreRefreshStatus = {
  accepted?: boolean
  conflict?: boolean
  status: {
    state?: string
    run_id?: string
    started_at?: string
    finished_at?: string | null
    step?: string
    pages?: number
    events?: number
    lookups?: Record<string, number>
    error?: string | null
    metrics?: Record<string, unknown>
    message?: string
    heartbeat_at?: string
  }
}

export type ExploreEventFilters = {
  category?: string
  q?: string
  contract_status?: string
  lifecycle?: string
  listed_open?: string
  quote?: string
  coverage?: string
  sort?: string
  page?: number
  page_size?: number
}

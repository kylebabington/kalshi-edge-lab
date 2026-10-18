export type ActivityType =
  | 'forecast_accepted'
  | 'settlement_confirmed'
  | 'collector_failure'
  | 'collector_recovery'
  | 'collector_skipped'
  | 'backup_failure'
  | 'backup_skipped'
  | 'backup_ok'
  | 'explore_discovered'
  | 'explore_status_change'
  | 'explore_quote_change'

export type ActivityCategory = 'weather' | 'sports' | 'kalshi' | 'system'

export type ActivitySubject = {
  kind?: string
  key?: string
  aliases?: string[]
}

export type QuoteFieldChange = {
  field: string
  from: unknown
  to: unknown
}

export type ActivityItem = {
  id: string
  type: ActivityType | string
  category: ActivityCategory | string
  observed_at: string | null
  source_at: string | null
  title: string
  summary: string
  href: string
  subject?: ActivitySubject
  payload?: Record<string, unknown> & {
    old_retrieved_at?: string | null
    new_retrieved_at?: string | null
    changes?: QuoteFieldChange[]
    from?: string | null
    to?: string | null
    market_ticker?: string
    event_ticker?: string
  }
}

export type ActivityList = {
  generated_at: string
  index_built_at: string | null
  mode: string
  page: number
  page_size: number
  total: number
  pages: number
  items: ActivityItem[]
  notes: string[]
}

export type ActivitySummary = {
  generated_at: string
  index_built_at: string | null
  mode: string
  total: number
  by_type: Record<string, number>
  by_category: Record<string, number>
  newest_observed_at: string | null
  notes: string[]
}

export type ActivityIds = {
  generated_at: string
  ids: string[]
  total: number
}

export type ActivityFilters = {
  category?: string
  type?: string
  q?: string
  watched?: string
  page?: number
  page_size?: number
  watchlist_only?: boolean
}

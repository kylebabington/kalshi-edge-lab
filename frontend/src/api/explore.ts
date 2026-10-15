import { ApiError, NotFoundError, apiGet, apiPost, isMockMode } from './client'
import type { ExploreEventDetail, ExploreEventFilters, ExploreEventList, ExploreRefreshStatus, ExploreSummary } from '../types/explore'
import { mockExploreEvent, mockExploreEvents, mockExploreSummary } from '../mocks/explore'

function query(params: object): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export async function fetchExploreSummary(): Promise<ExploreSummary> {
  if (isMockMode()) return mockExploreSummary
  return apiGet<ExploreSummary>('/api/explore/summary')
}

export async function fetchExploreEvents(filters: ExploreEventFilters = {}): Promise<ExploreEventList> {
  if (isMockMode()) return mockExploreEvents(filters)
  return apiGet<ExploreEventList>(`/api/explore/events${query(filters)}`)
}

export async function fetchExploreEvent(eventTicker: string): Promise<ExploreEventDetail> {
  if (isMockMode()) {
    const found = mockExploreEvent(eventTicker)
    if (!found) throw new NotFoundError(`404 Unknown event ${eventTicker}`)
    return found
  }
  try {
    return await apiGet<ExploreEventDetail>(`/api/explore/events/${encodeURIComponent(eventTicker)}`)
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) throw new NotFoundError(err.message)
    throw err
  }
}

export async function postExploreRefresh(watchedEventTickers: string[]): Promise<ExploreRefreshStatus> {
  if (isMockMode()) {
    return {
      accepted: true,
      conflict: false,
      status: { state: 'complete', run_id: 'mock', started_at: new Date().toISOString(), finished_at: new Date().toISOString(), step: 'done', pages: 1, events: 3 },
    }
  }
  return apiPost<ExploreRefreshStatus>('/api/explore/refresh', { watched_event_tickers: watchedEventTickers })
}

export async function fetchExploreRefreshStatus(): Promise<ExploreRefreshStatus> {
  if (isMockMode()) return { accepted: false, status: { state: 'idle', message: 'Mock refresh is instantaneous' } }
  return apiGet<ExploreRefreshStatus>('/api/explore/refresh')
}

export function exploreEventPath(eventTicker: string): string {
  return `/explore/events/${encodeURIComponent(eventTicker)}`
}

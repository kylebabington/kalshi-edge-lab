import { apiGet, isMockMode } from './client'
import type { ActivityFilters, ActivityIds, ActivityList, ActivitySummary } from '../types/activity'
import { mockActivityIds, mockActivityList, mockActivitySummary } from '../mocks/activity'

function query(params: object): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export async function fetchActivitySummary(): Promise<ActivitySummary> {
  if (isMockMode()) return mockActivitySummary
  return apiGet<ActivitySummary>('/api/activity/summary')
}

export async function fetchActivity(filters: ActivityFilters = {}): Promise<ActivityList> {
  if (isMockMode()) return mockActivityList(filters)
  const { watchlist_only: _w, ...rest } = filters
  return apiGet<ActivityList>(`/api/activity${query(rest)}`)
}

export async function fetchActivityIds(filters: Omit<ActivityFilters, 'page' | 'page_size' | 'watchlist_only'> = {}): Promise<ActivityIds> {
  if (isMockMode()) return mockActivityIds(filters)
  return apiGet<ActivityIds>(`/api/activity/ids${query(filters)}`)
}

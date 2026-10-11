import { apiGet, isMockMode } from './client'
import type {
  CoverageFilters,
  EventFilters,
  HistoricalFilters,
  ResultFilters,
  SportsCollectorStatus,
  SportsCoverage,
  SportsEventDetail,
  SportsEventList,
  SportsHistorical,
  SportsResults,
  SportsSummary,
} from '../types/sports'
import {
  mockSportsCollector,
  mockSportsCoverage,
  mockSportsEventDetail,
  mockSportsEvents,
  mockSportsHistorical,
  mockSportsResults,
  mockSportsSummary,
} from '../mocks/sports'

function query(params: object): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export async function fetchSportsSummary(): Promise<SportsSummary> {
  if (isMockMode()) return mockSportsSummary
  return apiGet<SportsSummary>('/api/sports/summary')
}

export async function fetchSportsEvents(filters: EventFilters = {}): Promise<SportsEventList> {
  if (isMockMode()) return mockSportsEvents(filters)
  return apiGet<SportsEventList>(`/api/sports/events${query(filters)}`)
}

export async function fetchSportsEvent(eventId: string): Promise<SportsEventDetail> {
  if (isMockMode()) {
    if (eventId === mockSportsEventDetail.event.event_id) return mockSportsEventDetail
    throw new Error(`Mock sports event not found: ${eventId}`)
  }
  return apiGet<SportsEventDetail>(`/api/sports/events/${encodeURIComponent(eventId)}`)
}

export async function fetchSportsResults(filters: ResultFilters = {}): Promise<SportsResults> {
  if (isMockMode()) return { ...mockSportsResults, weighting: filters.weighting ?? 'event' }
  return apiGet<SportsResults>(`/api/sports/results${query(filters)}`)
}

export async function fetchSportsCoverage(filters: CoverageFilters = {}): Promise<SportsCoverage> {
  if (isMockMode()) return mockSportsCoverage
  return apiGet<SportsCoverage>(`/api/sports/coverage${query(filters)}`)
}

export async function fetchSportsHistorical(filters: HistoricalFilters = {}): Promise<SportsHistorical> {
  if (isMockMode()) return mockSportsHistorical
  return apiGet<SportsHistorical>(`/api/sports/historical${query(filters)}`)
}

export async function fetchSportsCollector(): Promise<SportsCollectorStatus> {
  if (isMockMode()) return mockSportsCollector
  return apiGet<SportsCollectorStatus>('/api/sports/collector')
}

export function eventPath(eventId: string): string {
  return `/sports/events/${encodeURIComponent(eventId)}`
}

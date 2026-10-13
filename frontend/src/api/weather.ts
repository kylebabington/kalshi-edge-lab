import { NotFoundError, apiGet, isMockMode } from './client'
import type {
  LiveWeatherResponse,
  ModelSummary,
  PredictionSnapshotMeta,
  SavedWeatherEventDetail,
  SavedWeatherEventList,
  SavedWeatherFilters,
  WeatherCollectorStatus,
  WeatherEvent,
  WeatherMetricsContext,
} from '../types/weather'
import {
  mockLiveWeather,
  mockModelSummary,
  mockSnapshot,
  mockSnapshotList,
  mockWeatherEvent,
} from '../mocks/weatherEvent'
import {
  mockSavedWeatherEvent,
  mockSavedWeatherEvents,
  mockWeatherCollector,
  mockWeatherMetricsContext,
} from '../mocks/weatherSaved'

const LIVE_TTL_MS = 60_000
let liveCache: { at: number; promise: Promise<LiveWeatherResponse> } | null = null

/** The live endpoint fetches upstream data and is slow; pages share one request for a minute. */
export async function fetchLiveWeather(): Promise<LiveWeatherResponse> {
  if (isMockMode()) return mockLiveWeather
  const now = Date.now()
  if (liveCache && now - liveCache.at < LIVE_TTL_MS) return liveCache.promise
  const promise = apiGet<LiveWeatherResponse>('/api/weather/live')
  liveCache = { at: now, promise }
  promise.catch(() => {
    if (liveCache?.promise === promise) liveCache = null
  })
  return promise
}

export async function fetchWeatherEvent(eventTicker: string): Promise<WeatherEvent> {
  if (isMockMode()) {
    if (mockWeatherEvent.event_ticker === eventTicker) return mockWeatherEvent
    const match = mockLiveWeather.events.find((e) => e.event_ticker === eventTicker)
    if (match) return match
    throw new NotFoundError(`Mock event not found: ${eventTicker}`)
  }
  return apiGet<WeatherEvent>(`/api/weather/events/${encodeURIComponent(eventTicker)}`)
}

export async function fetchModelSummary(): Promise<ModelSummary> {
  if (isMockMode()) return mockModelSummary
  return apiGet<ModelSummary>('/api/weather/model/summary')
}

export async function fetchEventSnapshots(
  eventTicker: string,
): Promise<{ event_ticker: string; snapshots: PredictionSnapshotMeta[]; count: number }> {
  if (isMockMode()) {
    if (eventTicker === mockWeatherEvent.event_ticker) return mockSnapshotList
    return { event_ticker: eventTicker, snapshots: [], count: 0 }
  }
  return apiGet(`/api/weather/events/${encodeURIComponent(eventTicker)}/snapshots`)
}

export async function fetchSnapshot(snapshotId: string): Promise<Record<string, unknown>> {
  if (isMockMode()) {
    if (snapshotId === mockSnapshot.snapshot_id) return mockSnapshot
    throw new NotFoundError(`Mock snapshot not found: ${snapshotId}`)
  }
  return apiGet(`/api/weather/snapshots/${encodeURIComponent(snapshotId)}`)
}

function query(params: object): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export async function fetchSavedWeatherEvents(filters: SavedWeatherFilters = {}): Promise<SavedWeatherEventList> {
  if (isMockMode()) return mockSavedWeatherEvents(filters)
  return apiGet<SavedWeatherEventList>(`/api/weather/saved-events${query(filters)}`)
}

export async function fetchSavedWeatherEvent(eventTicker: string): Promise<SavedWeatherEventDetail> {
  if (isMockMode()) {
    const detail = mockSavedWeatherEvent(eventTicker)
    if (detail) return detail
    throw new NotFoundError(`Mock saved weather event not found: ${eventTicker}`)
  }
  return apiGet<SavedWeatherEventDetail>(`/api/weather/saved-events/${encodeURIComponent(eventTicker)}`)
}

export async function fetchWeatherCollector(): Promise<WeatherCollectorStatus> {
  if (isMockMode()) return mockWeatherCollector
  return apiGet<WeatherCollectorStatus>('/api/weather/collector')
}

export async function fetchWeatherMetricsContext(): Promise<WeatherMetricsContext> {
  if (isMockMode()) return mockWeatherMetricsContext
  return apiGet<WeatherMetricsContext>('/api/weather/metrics-context')
}

export async function fetchHealth(): Promise<{ status: string; mode: string }> {
  if (isMockMode()) return { status: 'ok', mode: 'research_only' }
  return apiGet('/api/health')
}

export function weatherEventPath(eventTicker: string): string {
  return `/markets/${encodeURIComponent(eventTicker)}`
}

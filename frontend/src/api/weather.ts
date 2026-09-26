import { apiGet, isMockMode } from './client'
import type {
  LiveWeatherResponse,
  ModelSummary,
  PredictionSnapshotMeta,
  WeatherEvent,
} from '../types/weather'
import {
  mockLiveWeather,
  mockModelSummary,
  mockSnapshot,
  mockSnapshotList,
  mockWeatherEvent,
} from '../mocks/weatherEvent'

export async function fetchLiveWeather(): Promise<LiveWeatherResponse> {
  if (isMockMode()) return mockLiveWeather
  return apiGet<LiveWeatherResponse>('/api/weather/live')
}

export async function fetchWeatherEvent(eventTicker: string): Promise<WeatherEvent> {
  if (isMockMode()) {
    if (mockWeatherEvent.event_ticker === eventTicker) return mockWeatherEvent
    const match = mockLiveWeather.events.find((e) => e.event_ticker === eventTicker)
    if (match) return match
    throw new Error(`Mock event not found: ${eventTicker}`)
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
    throw new Error(`Mock snapshot not found: ${snapshotId}`)
  }
  return apiGet(`/api/weather/snapshots/${encodeURIComponent(snapshotId)}`)
}

export async function fetchHealth(): Promise<{ status: string; mode: string }> {
  if (isMockMode()) return { status: 'ok', mode: 'research_only' }
  return apiGet('/api/health')
}

import { isNotFound } from '../api/client'
import { fetchSportsEvent } from '../api/sports'
import { fetchLiveWeather, fetchSavedWeatherEvent } from '../api/weather'
import type { SportsEventItem } from '../types/sports'
import type { SavedWeatherEvent, WeatherEvent } from '../types/weather'
import type { WatchEntry } from './store'

export type Resolved =
  | { kind: 'sports'; event: SportsEventItem }
  | { kind: 'weather-saved'; event: SavedWeatherEvent }
  | { kind: 'weather-live'; event: WeatherEvent }
  | { kind: 'not_found'; detail: string }

/** Resolve a saved entry through the read-only APIs. A 404 is "not found"; any other failure is re-thrown. */
export async function resolveEntry(entry: WatchEntry): Promise<Resolved> {
  if (entry.category === 'sports') {
    try {
      const detail = await fetchSportsEvent(entry.key)
      return { kind: 'sports', event: detail.event }
    } catch (err) {
      if (isNotFound(err)) return { kind: 'not_found', detail: 'No saved sports capture with this id exists on this machine.' }
      throw err
    }
  }
  try {
    const detail = await fetchSavedWeatherEvent(entry.key)
    return { kind: 'weather-saved', event: detail.event }
  } catch (err) {
    if (!isNotFound(err)) throw err
  }
  const live = await fetchLiveWeather()
  const open = live.events.find((e) => e.event_ticker === entry.key)
  if (open) return { kind: 'weather-live', event: open }
  return {
    kind: 'not_found',
    detail: 'No saved forecast for this event on this machine, and it is not among the currently open markets.',
  }
}

/**
 * Stable watchlist / notebook identities for Explorer events.
 *
 * Aliases must never silently move or overwrite notebook notes. Prefer a
 * domain-specific watch key when known; notes keep whatever target they were
 * created with, and the UI resolves all aliases when displaying.
 */
import type { ExploreCoverage } from '../types/explore'
import type { WatchCategory } from '../watchlist/store'
import { watchId } from '../watchlist/store'
import type { NoteTarget } from '../notebook/store'

export type PreferredWatch = { category: WatchCategory; key: string }

export function preferredWatch(eventTicker: string, coverage?: ExploreCoverage | null): PreferredWatch {
  if (coverage?.model_supported === 'weather' || eventTicker.startsWith('KXHIGHNY-')) {
    return { category: 'weather', key: eventTicker }
  }
  if (coverage?.model_supported === 'sports' && coverage.sports_event_id) {
    return { category: 'sports', key: coverage.sports_event_id }
  }
  return { category: 'kalshi', key: eventTicker }
}

/** All known ids that may refer to the same underlying event for starring/notes display. */
export function aliasWatchIds(eventTicker: string, coverage?: ExploreCoverage | null): string[] {
  const ids = new Set<string>()
  ids.add(watchId('kalshi', eventTicker))
  if (eventTicker.startsWith('KXHIGHNY-') || coverage?.model_supported === 'weather') {
    ids.add(watchId('weather', eventTicker))
  }
  if (coverage?.sports_event_id) {
    ids.add(watchId('sports', coverage.sports_event_id))
  }
  const pref = preferredWatch(eventTicker, coverage)
  ids.add(watchId(pref.category, pref.key))
  return [...ids]
}

export function aliasNoteTargets(eventTicker: string, coverage?: ExploreCoverage | null): NoteTarget[] {
  const targets: NoteTarget[] = [{ type: 'event', category: 'kalshi', key: eventTicker }]
  if (eventTicker.startsWith('KXHIGHNY-') || coverage?.model_supported === 'weather') {
    targets.push({ type: 'event', category: 'weather', key: eventTicker })
  }
  if (coverage?.sports_event_id) {
    targets.push({ type: 'event', category: 'sports', key: coverage.sports_event_id })
  }
  return targets
}

export function isStarred(entryIds: ReadonlySet<string> | readonly { id: string }[], eventTicker: string, coverage?: ExploreCoverage | null): boolean {
  const set =
    entryIds instanceof Set
      ? entryIds
      : new Set((entryIds as readonly { id: string }[]).map((e) => e.id))
  return aliasWatchIds(eventTicker, coverage).some((id) => set.has(id))
}

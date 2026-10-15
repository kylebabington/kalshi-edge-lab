import type { MouseEvent } from 'react'
import { preferredWatch, isStarred as aliasStarred } from '../explore/identity'
import type { ExploreCoverage } from '../types/explore'
import { watchId, type WatchCategory } from './store'
import { useWatchlist } from './useWatchlist'

type Props = {
  category: WatchCategory
  eventKey: string
  label: string
  size?: 'sm' | 'md'
  /** When set, starring prefers weather/sports keys and treats aliases as already starred. */
  coverage?: ExploreCoverage | null
  eventTicker?: string
}

export function StarButton({ category, eventKey, label, size = 'sm', coverage, eventTicker }: Props) {
  const { entries, persistent, store } = useWatchlist()
  const ticker = eventTicker ?? (category === 'kalshi' ? eventKey : undefined)
  const starred = ticker
    ? aliasStarred(entries, ticker, coverage)
    : entries.some((e) => e.id === watchId(category, eventKey))

  const action = starred ? 'Remove from watchlist' : 'Add to watchlist'

  function onToggle(e: MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    if (starred) {
      // Remove every alias that matches this event so the star clears fully.
      if (ticker) {
        const pref = preferredWatch(ticker, coverage)
        const ids = new Set(
          entries
            .filter((entry) => {
              if (entry.id === watchId(pref.category, pref.key)) return true
              if (entry.category === 'kalshi' && entry.key === ticker) return true
              if (coverage?.sports_event_id && entry.category === 'sports' && entry.key === coverage.sports_event_id) return true
              if ((ticker.startsWith('KXHIGHNY-') || coverage?.model_supported === 'weather') && entry.category === 'weather' && entry.key === ticker)
                return true
              return false
            })
            .map((entry) => entry.id),
        )
        for (const id of ids) store.remove(id)
      } else {
        store.toggle(category, eventKey, label)
      }
      return
    }
    if (ticker) {
      const pref = preferredWatch(ticker, coverage)
      store.add(pref.category, pref.key, label)
      return
    }
    store.toggle(category, eventKey, label)
  }

  return (
    <button
      type="button"
      className={`star-button ${size === 'md' ? 'star-button-md' : ''}`}
      aria-pressed={starred}
      aria-label={`${action}: ${label}`}
      title={persistent ? action : `${action} (changes won't persist)`}
      onClick={onToggle}
    >
      <span aria-hidden="true">{starred ? '★' : '☆'}</span>
      {size === 'md' ? <span>{starred ? 'Watching' : 'Watch'}</span> : null}
    </button>
  )
}

export function PersistenceNotice() {
  const { persistent, problem } = useWatchlist()
  if (!problem) return null
  return (
    <p role="status" className={`text-xs ${persistent ? 'text-[var(--text-muted)]' : 'text-[var(--warning)]'}`}>
      {persistent ? problem : "Changes won't persist: browser storage is blocked or full. Your watchlist is kept in memory for this tab."}
    </p>
  )
}

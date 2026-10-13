import { watchId, type WatchCategory } from './store'
import { useWatchlist } from './useWatchlist'

type Props = { category: WatchCategory; eventKey: string; label: string; size?: 'sm' | 'md' }

export function StarButton({ category, eventKey, label, size = 'sm' }: Props) {
  const { entries, persistent, store } = useWatchlist()
  const starred = entries.some((e) => e.id === watchId(category, eventKey))
  const action = starred ? 'Remove from watchlist' : 'Add to watchlist'
  return (
    <button
      type="button"
      className={`star-button ${size === 'md' ? 'star-button-md' : ''}`}
      aria-pressed={starred}
      aria-label={`${action}: ${label}`}
      title={persistent ? action : `${action} (changes won't persist)`}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        store.toggle(category, eventKey, label)
      }}
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

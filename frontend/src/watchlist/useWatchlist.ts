import { useSyncExternalStore } from 'react'
import { watchlistStore, type WatchlistSnapshot, type WatchlistStore } from './store'

export function useWatchlist(store: WatchlistStore = watchlistStore): WatchlistSnapshot & { store: WatchlistStore } {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)
  return { ...snapshot, store }
}

import { useSyncExternalStore } from 'react'
import { savedSearchesStore, type SavedSearchesSnapshot, type SavedSearchesStore } from './store'

export function useSavedSearches(store: SavedSearchesStore = savedSearchesStore): SavedSearchesSnapshot & { store: SavedSearchesStore } {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)
  return { ...snapshot, store }
}

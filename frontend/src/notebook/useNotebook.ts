import { useSyncExternalStore } from 'react'
import { notebookStore, type NotebookSnapshot, type NotebookStore } from './store'

export function useNotebook(store: NotebookStore = notebookStore): NotebookSnapshot & { store: NotebookStore } {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)
  return { ...snapshot, store }
}

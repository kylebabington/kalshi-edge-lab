import { useCallback, useSyncExternalStore } from 'react'
import { activityReadStore, type ActivityReadSnapshot } from './readStore'

export function useActivityRead(): ActivityReadSnapshot & {
  isRead: (id: string) => boolean
  markRead: (id: string) => void
  markAllRead: (ids: readonly string[]) => void
  markUnread: (id: string) => void
} {
  const snap = useSyncExternalStore(activityReadStore.subscribe, activityReadStore.getSnapshot)
  const isRead = useCallback((id: string) => activityReadStore.isRead(id), [])
  const markRead = useCallback((id: string) => activityReadStore.markRead(id), [])
  const markAllRead = useCallback((ids: readonly string[]) => activityReadStore.markAllRead(ids), [])
  const markUnread = useCallback((id: string) => activityReadStore.markUnread(id), [])
  return { ...snap, isRead, markRead, markAllRead, markUnread }
}

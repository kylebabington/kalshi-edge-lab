/**
 * Browser-local read state for the Activity Center.
 *
 * Mark-all-read stores the exact set of activity ids present in the feed
 * snapshot at click time. New arrivals stay unread even when their
 * observed_at ties or is older. No timestamp watermark.
 */

export type ActivityReadSnapshot = {
  readIds: ReadonlySet<string>
  persistent: boolean
  problem: string | null
}

export const STORAGE_KEY = 'kalshi-edge-lab.activity-read.v1'
const BACKUP_KEY = `${STORAGE_KEY}.unreadable-backup`
const MAX_IDS = 20_000

type Parsed = { ids: string[]; problem: string | null; unreadable: boolean }

export function parseStored(text: string | null): Parsed {
  if (text == null) return { ids: [], problem: null, unreadable: false }
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return { ids: [], problem: 'Saved activity read state was unreadable and has been set aside.', unreadable: true }
  }
  const root = data as { version?: unknown; read_ids?: unknown }
  if (root?.version !== 1 || !Array.isArray(root.read_ids)) {
    return { ids: [], problem: 'Saved activity read state had an unknown format and has been set aside.', unreadable: true }
  }
  const seen = new Set<string>()
  const ids: string[] = []
  let dropped = 0
  for (const raw of root.read_ids) {
    if (typeof raw !== 'string' || !raw || raw.length > 500 || seen.has(raw)) {
      dropped += 1
      continue
    }
    seen.add(raw)
    ids.push(raw)
    if (ids.length >= MAX_IDS) break
  }
  return {
    ids,
    problem: dropped ? `${dropped} saved read ${dropped === 1 ? 'id was' : 'ids were'} invalid and ignored.` : null,
    unreadable: dropped > 0,
  }
}

type StorageGetter = () => Storage | null

function browserStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

const NOT_PERSISTING = "Browser storage is unavailable. Read state won't persist after this tab closes."

function sameSets(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  if (a.size !== b.size) return false
  for (const id of a) if (!b.has(id)) return false
  return true
}

export class ActivityReadStore {
  private snapshot: ActivityReadSnapshot = { readIds: new Set(), persistent: true, problem: null }
  private listeners = new Set<() => void>()
  private loaded = false
  private readonly getStorage: StorageGetter

  constructor(getStorage: StorageGetter = browserStorage) {
    this.getStorage = getStorage
  }

  subscribe = (listener: () => void): (() => void) => {
    this.ensureLoaded()
    this.listeners.add(listener)
    if (this.listeners.size === 1 && typeof window !== 'undefined') window.addEventListener('storage', this.onStorage)
    return () => {
      this.listeners.delete(listener)
      if (this.listeners.size === 0 && typeof window !== 'undefined') window.removeEventListener('storage', this.onStorage)
    }
  }

  getSnapshot = (): ActivityReadSnapshot => {
    this.ensureLoaded()
    return this.snapshot
  }

  isRead(id: string): boolean {
    return this.getSnapshot().readIds.has(id)
  }

  markRead(id: string): void {
    if (this.isRead(id)) return
    const next = new Set(this.snapshot.readIds)
    next.add(id)
    this.commit(next)
  }

  /** Mark exactly the ids in the feed snapshot at action time. New ids stay unread. */
  markAllRead(ids: readonly string[]): void {
    const next = new Set(this.snapshot.readIds)
    for (const id of ids) {
      if (typeof id === 'string' && id) next.add(id)
    }
    while (next.size > MAX_IDS) {
      const first = next.values().next().value
      if (first === undefined) break
      next.delete(first)
    }
    this.commit(next)
  }

  markUnread(id: string): void {
    if (!this.isRead(id)) return
    const next = new Set(this.snapshot.readIds)
    next.delete(id)
    this.commit(next)
  }

  reload(): void {
    this.loaded = true
    const storage = this.getStorage()
    let text: string | null
    try {
      if (!storage) throw new Error('no storage')
      text = storage.getItem(STORAGE_KEY)
    } catch {
      this.set({ readIds: this.snapshot.readIds, persistent: false, problem: NOT_PERSISTING })
      return
    }
    const parsed = parseStored(text)
    if (parsed.unreadable && text != null) {
      try {
        storage.setItem(BACKUP_KEY, text)
      } catch {
        /* best effort */
      }
    }
    this.set({ readIds: new Set(parsed.ids), persistent: true, problem: parsed.problem })
  }

  private ensureLoaded(): void {
    if (!this.loaded) this.reload()
  }

  private onStorage = (event: StorageEvent): void => {
    if (!this.snapshot.persistent) return
    if (event.key === null || event.key === STORAGE_KEY) this.reload()
  }

  private commit(readIds: Set<string>): void {
    const storage = this.getStorage()
    try {
      if (!storage) throw new Error('no storage')
      storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, read_ids: [...readIds] }))
      this.set({ readIds, persistent: true, problem: null })
    } catch {
      this.set({ readIds, persistent: false, problem: NOT_PERSISTING })
    }
  }

  private set(next: ActivityReadSnapshot): void {
    const cur = this.snapshot
    if (cur.persistent === next.persistent && cur.problem === next.problem && sameSets(cur.readIds, next.readIds)) {
      return
    }
    this.snapshot = { ...next, readIds: next.readIds }
    for (const l of [...this.listeners]) l()
  }
}

export const activityReadStore = new ActivityReadStore()

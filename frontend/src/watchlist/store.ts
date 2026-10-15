export type WatchCategory = 'weather' | 'sports' | 'kalshi'

export type WatchEntry = {
  /** `${category}:${key}` — the category prefix keeps weather and sports keys from colliding. */
  id: string
  category: WatchCategory
  /** Weather event ticker or sports event id, exactly as the read-only API identifies the event. */
  key: string
  label: string
  added_at: string
}

export type WatchlistSnapshot = {
  entries: readonly WatchEntry[]
  /** False when browser storage is blocked or full; entries then live in memory for this tab only. */
  persistent: boolean
  problem: string | null
}

export const STORAGE_KEY = 'kalshi-edge-lab.watchlist.v1'
const BACKUP_KEY = `${STORAGE_KEY}.unreadable-backup`
const CATEGORIES: readonly WatchCategory[] = ['weather', 'sports', 'kalshi']
const MAX_KEY_LENGTH = 300

export function watchId(category: WatchCategory, key: string): string {
  return `${category}:${key}`
}

export function parseWatchId(id: string): { category: WatchCategory; key: string } | null {
  const i = id.indexOf(':')
  if (i < 0) return null
  const category = id.slice(0, i) as WatchCategory
  const key = id.slice(i + 1)
  if (!CATEGORIES.includes(category) || !key) return null
  return { category, key }
}

function validEntry(raw: unknown): WatchEntry | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const category = r.category as WatchCategory
  const key = r.key
  if (!CATEGORIES.includes(category) || typeof key !== 'string' || !key || key.length > MAX_KEY_LENGTH) return null
  if (r.id !== watchId(category, key)) return null
  const added = typeof r.added_at === 'string' && Number.isFinite(Date.parse(r.added_at)) ? r.added_at : null
  if (!added) return null
  const label = typeof r.label === 'string' && r.label.trim() ? r.label : key
  return { id: watchId(category, key), category, key, label, added_at: added }
}

type Parsed = { entries: WatchEntry[]; problem: string | null; unreadable: boolean }

export function parseStored(text: string | null): Parsed {
  if (text == null) return { entries: [], problem: null, unreadable: false }
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return { entries: [], problem: 'Saved watchlist data was unreadable and has been set aside.', unreadable: true }
  }
  const list = (data as { version?: unknown; entries?: unknown })?.entries
  if ((data as { version?: unknown })?.version !== 1 || !Array.isArray(list)) {
    return { entries: [], problem: 'Saved watchlist data had an unknown format and has been set aside.', unreadable: true }
  }
  const seen = new Set<string>()
  const entries: WatchEntry[] = []
  let dropped = 0
  for (const item of list) {
    const e = validEntry(item)
    if (!e || seen.has(e.id)) {
      dropped += 1
      continue
    }
    seen.add(e.id)
    entries.push(e)
  }
  return {
    entries,
    problem: dropped ? `${dropped} saved watchlist ${dropped === 1 ? 'entry was' : 'entries were'} invalid and ignored.` : null,
    unreadable: dropped > 0,
  }
}

function sameEntries(a: readonly WatchEntry[], b: readonly WatchEntry[]): boolean {
  return a.length === b.length && a.every((e, i) => JSON.stringify(e) === JSON.stringify(b[i]))
}

type StorageGetter = () => Storage | null

function browserStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

const NOT_PERSISTING = "Browser storage is unavailable. Changes won't persist after this tab closes."

export class WatchlistStore {
  private snapshot: WatchlistSnapshot = { entries: [], persistent: true, problem: null }
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

  /** Stable between changes: returns the same object until entries or persistence state change. */
  getSnapshot = (): WatchlistSnapshot => {
    this.ensureLoaded()
    return this.snapshot
  }

  has(id: string): boolean {
    return this.getSnapshot().entries.some((e) => e.id === id)
  }

  add(category: WatchCategory, key: string, label: string): void {
    const id = watchId(category, key)
    if (this.has(id)) return
    const entry: WatchEntry = { id, category, key, label: label || key, added_at: new Date().toISOString() }
    this.commit([...this.snapshot.entries, entry])
  }

  remove(id: string): void {
    if (!this.has(id)) return
    this.commit(this.snapshot.entries.filter((e) => e.id !== id))
  }

  toggle(category: WatchCategory, key: string, label: string): void {
    const id = watchId(category, key)
    if (this.has(id)) this.remove(id)
    else this.add(category, key, label)
  }

  clear(): void {
    if (!this.snapshot.entries.length) return
    this.commit([])
  }

  /** Re-read storage (used on start-up, cross-tab events and in tests). */
  reload(): void {
    this.loaded = true
    const storage = this.getStorage()
    let text: string | null
    try {
      if (!storage) throw new Error('no storage')
      text = storage.getItem(STORAGE_KEY)
    } catch {
      this.set({ entries: this.snapshot.entries, persistent: false, problem: NOT_PERSISTING })
      return
    }
    const parsed = parseStored(text)
    if (parsed.unreadable && text != null) {
      try {
        storage.setItem(BACKUP_KEY, text)
      } catch {
        /* backup is best effort; the original text stays in place until the next successful write */
      }
    }
    this.set({ entries: parsed.entries, persistent: true, problem: parsed.problem })
  }

  private ensureLoaded(): void {
    if (!this.loaded) this.reload()
  }

  private onStorage = (event: StorageEvent): void => {
    // A tab running from memory would lose its unsaved entries if another tab's write replaced them.
    if (!this.snapshot.persistent) return
    if (event.key === null || event.key === STORAGE_KEY) this.reload()
  }

  private commit(entries: WatchEntry[]): void {
    const storage = this.getStorage()
    try {
      if (!storage) throw new Error('no storage')
      storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, entries }))
      this.set({ entries, persistent: true, problem: null })
    } catch {
      this.set({ entries, persistent: false, problem: NOT_PERSISTING })
    }
  }

  private set(next: WatchlistSnapshot): void {
    const cur = this.snapshot
    if (cur.persistent === next.persistent && cur.problem === next.problem && sameEntries(cur.entries, next.entries)) return
    this.snapshot = { ...next, entries: Object.freeze([...next.entries]) }
    for (const l of [...this.listeners]) l()
  }
}

export const watchlistStore = new WatchlistStore()

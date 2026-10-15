import { filtersEqual, normalizeFilters, type ExploreSavedFilters, MAX_NAME_LENGTH } from './filters'

export type SavedSearch = {
  id: string
  name: string
  filters: ExploreSavedFilters
  /** Null when not pinned. Lower values appear first on the dashboard. */
  pin_order: number | null
  created_at: string
  updated_at: string
}

export type SavedSearchesSnapshot = {
  entries: readonly SavedSearch[]
  persistent: boolean
  problem: string | null
}

export const STORAGE_KEY = 'kalshi-edge-lab.saved-searches.v1'
const BACKUP_KEY = `${STORAGE_KEY}.unreadable-backup`
const MAX_ID_LENGTH = 100

export function sameSearch(a: SavedSearch, b: SavedSearch): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

function validDate(v: unknown): string | null {
  return typeof v === 'string' && Number.isFinite(Date.parse(v)) ? v : null
}

function newId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  } catch {
    /* fall through */
  }
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/** Returns the entry or a short rejection reason. */
export function validateSearch(raw: unknown): SavedSearch | string {
  if (!raw || typeof raw !== 'object') return 'not an object'
  const r = raw as Record<string, unknown>
  if (typeof r.id !== 'string' || !r.id || r.id.length > MAX_ID_LENGTH) return 'missing or invalid id'
  if (typeof r.name !== 'string' || !r.name.trim() || r.name.trim().length > MAX_NAME_LENGTH) return `invalid name in ${r.id}`
  const created = validDate(r.created_at)
  const updated = validDate(r.updated_at)
  if (!created || !updated) return `invalid timestamps in ${r.id}`
  const pin =
    r.pin_order == null
      ? null
      : typeof r.pin_order === 'number' && Number.isFinite(r.pin_order)
        ? Math.trunc(r.pin_order)
        : null
  if (r.pin_order != null && pin == null) return `invalid pin_order in ${r.id}`
  return {
    id: r.id,
    name: r.name.trim(),
    filters: normalizeFilters(r.filters as ExploreSavedFilters),
    pin_order: pin,
    created_at: created,
    updated_at: updated,
  }
}

type Parsed = { entries: SavedSearch[]; problem: string | null; unreadable: boolean }

export function parseStored(text: string | null): Parsed {
  if (text == null) return { entries: [], problem: null, unreadable: false }
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return { entries: [], problem: 'Saved search data was unreadable and has been set aside.', unreadable: true }
  }
  const list = (data as { version?: unknown; entries?: unknown })?.entries
  if ((data as { version?: unknown })?.version !== 1 || !Array.isArray(list)) {
    return { entries: [], problem: 'Saved search data had an unknown format and has been set aside.', unreadable: true }
  }
  const seen = new Set<string>()
  const entries: SavedSearch[] = []
  let dropped = 0
  for (const item of list) {
    const e = validateSearch(item)
    if (typeof e === 'string' || seen.has(e.id)) {
      dropped += 1
      continue
    }
    seen.add(e.id)
    entries.push(e)
  }
  return {
    entries,
    problem: dropped ? `${dropped} saved ${dropped === 1 ? 'search was' : 'searches were'} invalid and ignored.` : null,
    unreadable: dropped > 0,
  }
}

function sameEntries(a: readonly SavedSearch[], b: readonly SavedSearch[]): boolean {
  return a.length === b.length && a.every((e, i) => sameSearch(e, b[i]))
}

type StorageGetter = () => Storage | null

function browserStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

const NOT_PERSISTING = "Browser storage is unavailable. Saved-search changes won't persist after this tab closes."

export class SavedSearchesStore {
  private snapshot: SavedSearchesSnapshot = { entries: [], persistent: true, problem: null }
  private listeners = new Set<() => void>()
  private loaded = false
  private readonly getStorage: StorageGetter
  private readonly now: () => Date

  constructor(getStorage: StorageGetter = browserStorage, now: () => Date = () => new Date()) {
    this.getStorage = getStorage
    this.now = now
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

  getSnapshot = (): SavedSearchesSnapshot => {
    this.ensureLoaded()
    return this.snapshot
  }

  pinned(): SavedSearch[] {
    return this.getSnapshot()
      .entries.filter((e) => e.pin_order != null)
      .sort((a, b) => (a.pin_order ?? 0) - (b.pin_order ?? 0) || a.name.localeCompare(b.name))
  }

  add(name: string, filters: ExploreSavedFilters): SavedSearch | null {
    const at = this.now().toISOString()
    const candidate = validateSearch({
      id: newId(),
      name,
      filters: normalizeFilters(filters),
      pin_order: null,
      created_at: at,
      updated_at: at,
    })
    if (typeof candidate === 'string') return null
    this.commit([...this.getSnapshot().entries, candidate])
    return candidate
  }

  rename(id: string, name: string): boolean {
    const trimmed = name.trim()
    if (!trimmed || trimmed.length > MAX_NAME_LENGTH) return false
    const entries = this.getSnapshot().entries
    const cur = entries.find((e) => e.id === id)
    if (!cur) return false
    if (cur.name === trimmed) return true
    this.commit(entries.map((e) => (e.id === id ? { ...e, name: trimmed, updated_at: this.now().toISOString() } : e)))
    return true
  }

  updateFilters(id: string, filters: ExploreSavedFilters): boolean {
    const next = normalizeFilters(filters)
    const entries = this.getSnapshot().entries
    const cur = entries.find((e) => e.id === id)
    if (!cur) return false
    if (filtersEqual(cur.filters, next)) return true
    this.commit(entries.map((e) => (e.id === id ? { ...e, filters: next, updated_at: this.now().toISOString() } : e)))
    return true
  }

  remove(id: string): void {
    const entries = this.getSnapshot().entries
    if (!entries.some((e) => e.id === id)) return
    this.commit(entries.filter((e) => e.id !== id))
  }

  setPinned(id: string, pinned: boolean): void {
    const entries = this.getSnapshot().entries
    const cur = entries.find((e) => e.id === id)
    if (!cur) return
    if (pinned && cur.pin_order != null) return
    if (!pinned && cur.pin_order == null) return
    const at = this.now().toISOString()
    if (!pinned) {
      this.commit(entries.map((e) => (e.id === id ? { ...e, pin_order: null, updated_at: at } : e)))
      return
    }
    const maxPin = entries.reduce((m, e) => (e.pin_order != null && e.pin_order > m ? e.pin_order : m), -1)
    this.commit(entries.map((e) => (e.id === id ? { ...e, pin_order: maxPin + 1, updated_at: at } : e)))
  }

  mergeNew(incoming: readonly SavedSearch[]): number {
    const entries = this.getSnapshot().entries
    const have = new Set(entries.map((e) => e.id))
    const added = incoming.filter((e) => !have.has(e.id))
    if (added.length) this.commit([...entries, ...added])
    return added.length
  }

  replaceAll(incoming: readonly SavedSearch[]): void {
    this.commit([...incoming])
  }

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
        /* best effort */
      }
    }
    this.set({ entries: parsed.entries, persistent: true, problem: parsed.problem })
  }

  private ensureLoaded(): void {
    if (!this.loaded) this.reload()
  }

  private onStorage = (event: StorageEvent): void => {
    if (!this.snapshot.persistent) return
    if (event.key === null || event.key === STORAGE_KEY) this.reload()
  }

  private commit(entries: SavedSearch[]): void {
    const storage = this.getStorage()
    try {
      if (!storage) throw new Error('no storage')
      storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, entries }))
      this.set({ entries, persistent: true, problem: null })
    } catch {
      this.set({ entries, persistent: false, problem: NOT_PERSISTING })
    }
  }

  private set(next: SavedSearchesSnapshot): void {
    const cur = this.snapshot
    if (cur.persistent === next.persistent && cur.problem === next.problem && sameEntries(cur.entries, next.entries)) return
    this.snapshot = { ...next, entries: Object.freeze([...next.entries]) }
    for (const l of [...this.listeners]) l()
  }
}

export const savedSearchesStore = new SavedSearchesStore()

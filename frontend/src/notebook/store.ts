/**
 * Personal research notebook kept in this browser only. It is separate from the watchlist,
 * never sent to the API, and not research evidence.
 */
export type NoteKind = 'note' | 'question'
/** `ai_commentary` marks text the user pasted from an AI tool; the app never generates it. */
export type NoteOrigin = 'personal' | 'ai_commentary'
export type EventCategory = 'weather' | 'sports'

export type NoteTarget = { type: 'experiment'; id: string } | { type: 'event'; category: EventCategory; key: string }

export type NotebookEntry = {
  id: string
  kind: NoteKind
  origin: NoteOrigin
  text: string
  tags: string[]
  target: NoteTarget
  created_at: string
  updated_at: string
}

export type NotebookSnapshot = {
  entries: readonly NotebookEntry[]
  /** False when browser storage is blocked or full; entries then live in memory for this tab only. */
  persistent: boolean
  problem: string | null
}

export type NoteInput = { kind: NoteKind; origin: NoteOrigin; text: string; tags: string[]; target: NoteTarget }

export const STORAGE_KEY = 'kalshi-edge-lab.notebook.v1'
const BACKUP_KEY = `${STORAGE_KEY}.unreadable-backup`
export const KINDS: readonly NoteKind[] = ['note', 'question']
export const ORIGINS: readonly NoteOrigin[] = ['personal', 'ai_commentary']
const EVENT_CATEGORIES: readonly EventCategory[] = ['weather', 'sports']
export const MAX_TEXT_LENGTH = 10000
export const MAX_TAGS = 20
const MAX_TAG_LENGTH = 40
const MAX_ID_LENGTH = 100
const MAX_KEY_LENGTH = 300
const EXPERIMENT_ID = /^(weather|sports)\.[A-Za-z0-9_.-]{1,150}$/

export function targetKey(t: NoteTarget): string {
  return t.type === 'experiment' ? `experiment:${t.id}` : `event:${t.category}:${t.key}`
}

export function normalizeTags(raw: readonly string[]): string[] {
  const out: string[] = []
  for (const t of raw) {
    const tag = t.trim().replace(/^#/, '').replace(/\s+/g, '-').toLowerCase()
    if (tag && tag.length <= MAX_TAG_LENGTH && !out.includes(tag)) out.push(tag)
  }
  return out.slice(0, MAX_TAGS)
}

export function parseTagText(text: string): string[] {
  return normalizeTags(text.split(/[,\n]/))
}

function validTarget(raw: unknown): NoteTarget | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  if (r.type === 'experiment') {
    return typeof r.id === 'string' && EXPERIMENT_ID.test(r.id) ? { type: 'experiment', id: r.id } : null
  }
  if (r.type === 'event') {
    const category = r.category as EventCategory
    const key = r.key
    if (!EVENT_CATEGORIES.includes(category) || typeof key !== 'string' || !key || key.length > MAX_KEY_LENGTH) return null
    return { type: 'event', category, key }
  }
  return null
}

function validDate(v: unknown): string | null {
  return typeof v === 'string' && Number.isFinite(Date.parse(v)) ? v : null
}

/** Returns the entry or a short reason it was rejected. */
export function validateEntry(raw: unknown): NotebookEntry | string {
  if (!raw || typeof raw !== 'object') return 'not an object'
  const r = raw as Record<string, unknown>
  if (typeof r.id !== 'string' || !r.id || r.id.length > MAX_ID_LENGTH) return 'missing or invalid id'
  if (!KINDS.includes(r.kind as NoteKind)) return `invalid kind in ${r.id}`
  if (!ORIGINS.includes(r.origin as NoteOrigin)) return `invalid origin in ${r.id}`
  if (typeof r.text !== 'string' || !r.text.trim() || r.text.length > MAX_TEXT_LENGTH) return `invalid text in ${r.id}`
  if (!Array.isArray(r.tags) || !r.tags.every((t) => typeof t === 'string') || r.tags.length > MAX_TAGS)
    return `invalid tags in ${r.id}`
  const target = validTarget(r.target)
  if (!target) return `invalid target in ${r.id}`
  const created = validDate(r.created_at)
  const updated = validDate(r.updated_at)
  if (!created || !updated) return `invalid timestamps in ${r.id}`
  return {
    id: r.id,
    kind: r.kind as NoteKind,
    origin: r.origin as NoteOrigin,
    text: r.text,
    tags: normalizeTags(r.tags as string[]),
    target,
    created_at: created,
    updated_at: updated,
  }
}

type Parsed = { entries: NotebookEntry[]; problem: string | null; unreadable: boolean }

export function parseStored(text: string | null): Parsed {
  if (text == null) return { entries: [], problem: null, unreadable: false }
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return { entries: [], problem: 'Saved notebook data was unreadable and has been set aside.', unreadable: true }
  }
  const list = (data as { entries?: unknown })?.entries
  if ((data as { version?: unknown })?.version !== 1 || !Array.isArray(list)) {
    return { entries: [], problem: 'Saved notebook data had an unknown format and has been set aside.', unreadable: true }
  }
  const seen = new Set<string>()
  const entries: NotebookEntry[] = []
  let dropped = 0
  for (const item of list) {
    const e = validateEntry(item)
    if (typeof e === 'string' || seen.has(e.id)) {
      dropped += 1
      continue
    }
    seen.add(e.id)
    entries.push(e)
  }
  return {
    entries,
    problem: dropped ? `${dropped} saved notebook ${dropped === 1 ? 'entry was' : 'entries were'} invalid and ignored.` : null,
    unreadable: dropped > 0,
  }
}

export function sameEntry(a: NotebookEntry, b: NotebookEntry): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

function sameEntries(a: readonly NotebookEntry[], b: readonly NotebookEntry[]): boolean {
  return a.length === b.length && a.every((e, i) => sameEntry(e, b[i]))
}

function newId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  } catch {
    /* fall through */
  }
  return `n-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

type StorageGetter = () => Storage | null

function browserStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

const NOT_PERSISTING = "Browser storage is unavailable. Notebook changes won't persist after this tab closes."

export class NotebookStore {
  private snapshot: NotebookSnapshot = { entries: [], persistent: true, problem: null }
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

  /** Stable between changes: returns the same object until entries or persistence state change. */
  getSnapshot = (): NotebookSnapshot => {
    this.ensureLoaded()
    return this.snapshot
  }

  forTarget(target: NoteTarget): NotebookEntry[] {
    const key = targetKey(target)
    return this.getSnapshot().entries.filter((e) => targetKey(e.target) === key)
  }

  /** Returns the new entry, or null when the input is invalid. */
  add(input: NoteInput): NotebookEntry | null {
    const at = this.now().toISOString()
    const candidate = validateEntry({ ...input, text: input.text.trim(), id: newId(), created_at: at, updated_at: at })
    if (typeof candidate === 'string') return null
    this.commit([...this.getSnapshot().entries, candidate])
    return candidate
  }

  update(id: string, patch: Partial<Pick<NotebookEntry, 'kind' | 'origin' | 'text' | 'tags'>>): boolean {
    const entries = this.getSnapshot().entries
    const cur = entries.find((e) => e.id === id)
    if (!cur) return false
    const next = validateEntry({
      ...cur,
      ...patch,
      text: (patch.text ?? cur.text).trim(),
      updated_at: this.now().toISOString(),
    })
    if (typeof next === 'string') return false
    this.commit(entries.map((e) => (e.id === id ? next : e)))
    return true
  }

  remove(id: string): void {
    const entries = this.getSnapshot().entries
    if (!entries.some((e) => e.id === id)) return
    this.commit(entries.filter((e) => e.id !== id))
  }

  /** Adds entries whose ids are new. Existing entries are never overwritten. */
  mergeNew(incoming: readonly NotebookEntry[]): number {
    const entries = this.getSnapshot().entries
    const have = new Set(entries.map((e) => e.id))
    const added = incoming.filter((e) => !have.has(e.id))
    if (added.length) this.commit([...entries, ...added])
    return added.length
  }

  /** Replaces every entry. Callers must have the user's explicit confirmation. */
  replaceAll(incoming: readonly NotebookEntry[]): void {
    this.commit([...incoming])
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

  private commit(entries: NotebookEntry[]): void {
    const storage = this.getStorage()
    try {
      if (!storage) throw new Error('no storage')
      storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, entries }))
      this.set({ entries, persistent: true, problem: null })
    } catch {
      this.set({ entries, persistent: false, problem: NOT_PERSISTING })
    }
  }

  private set(next: NotebookSnapshot): void {
    const cur = this.snapshot
    if (cur.persistent === next.persistent && cur.problem === next.problem && sameEntries(cur.entries, next.entries)) return
    this.snapshot = { ...next, entries: Object.freeze([...next.entries]) }
    for (const l of [...this.listeners]) l()
  }
}

export const notebookStore = new NotebookStore()

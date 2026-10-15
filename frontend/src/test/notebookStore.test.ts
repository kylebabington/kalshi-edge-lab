import { describe, expect, it } from 'vitest'
import { NotebookStore, STORAGE_KEY, normalizeTags, parseStored, targetKey, type NotebookEntry } from '../notebook/store'
import { exportNotebook, parseImport, previewImport } from '../notebook/transfer'

class MemoryStorage implements Storage {
  data = new Map<string, string>()
  failGet = false
  failSet = false
  get length() {
    return this.data.size
  }
  clear() {
    this.data.clear()
  }
  getItem(k: string) {
    if (this.failGet) throw new DOMException('blocked', 'SecurityError')
    return this.data.get(k) ?? null
  }
  key(i: number) {
    return [...this.data.keys()][i] ?? null
  }
  removeItem(k: string) {
    this.data.delete(k)
  }
  setItem(k: string, v: string) {
    if (this.failSet) throw new DOMException('full', 'QuotaExceededError')
    this.data.set(k, v)
  }
}

const AT = '2099-01-01T00:00:00.000Z'

function entry(id: string, over: Partial<NotebookEntry> = {}): NotebookEntry {
  return {
    id,
    kind: 'note',
    origin: 'personal',
    text: `text ${id}`,
    tags: [],
    target: { type: 'experiment', id: 'weather.phase7_prospective_v1' },
    created_at: AT,
    updated_at: AT,
    ...over,
  }
}

function stored(entries: unknown[]) {
  return JSON.stringify({ version: 1, entries })
}

const fixedNow = () => new Date(AT)

describe('notebook store', () => {
  it('adds, edits and removes entries and persists them under a versioned key', () => {
    const storage = new MemoryStorage()
    const store = new NotebookStore(() => storage, fixedNow)
    const added = store.add({
      kind: 'question',
      origin: 'personal',
      text: '  Why did coverage drop?  ',
      tags: normalizeTags(['#Coverage', 'coverage', ' follow up ']),
      target: { type: 'event', category: 'sports', key: 'EVT-1' },
    })!
    expect(added.text).toBe('Why did coverage drop?')
    expect(added.tags).toEqual(['coverage', 'follow-up'])
    expect(JSON.parse(storage.getItem(STORAGE_KEY)!).version).toBe(1)

    expect(store.update(added.id, { text: 'Edited' })).toBe(true)
    expect(store.getSnapshot().entries[0].text).toBe('Edited')
    expect(store.update(added.id, { text: '   ' })).toBe(false)

    const reloaded = new NotebookStore(() => storage, fixedNow)
    expect(reloaded.forTarget({ type: 'event', category: 'sports', key: 'EVT-1' })).toHaveLength(1)

    store.remove(added.id)
    expect(store.getSnapshot().entries).toHaveLength(0)
  })

  it('rejects empty text and invalid targets', () => {
    const store = new NotebookStore(() => new MemoryStorage(), fixedNow)
    expect(store.add({ kind: 'note', origin: 'personal', text: ' ', tags: [], target: { type: 'experiment', id: 'weather.x' } })).toBeNull()
    expect(store.add({ kind: 'note', origin: 'personal', text: 'x', tags: [], target: { type: 'experiment', id: 'bad id' } })).toBeNull()
    expect(store.getSnapshot().entries).toHaveLength(0)
  })

  it('keeps notes separate from the watchlist key', () => {
    const storage = new MemoryStorage()
    storage.setItem('kalshi-edge-lab.watchlist.v1', stored([]))
    const store = new NotebookStore(() => storage, fixedNow)
    store.add({ kind: 'note', origin: 'personal', text: 'x', tags: [], target: { type: 'experiment', id: 'weather.x' } })
    expect(storage.getItem('kalshi-edge-lab.watchlist.v1')).toBe(stored([]))
    expect(STORAGE_KEY).not.toBe('kalshi-edge-lab.watchlist.v1')
  })

  it('falls back to memory with a warning when storage is blocked', () => {
    const storage = new MemoryStorage()
    storage.failGet = true
    const store = new NotebookStore(() => storage, fixedNow)
    expect(store.getSnapshot().persistent).toBe(false)
    expect(store.getSnapshot().problem).toMatch(/won't persist/)
    store.add({ kind: 'note', origin: 'personal', text: 'x', tags: [], target: { type: 'experiment', id: 'weather.x' } })
    expect(store.getSnapshot().entries).toHaveLength(1)
  })

  it('keeps entries in memory when a write fails', () => {
    const storage = new MemoryStorage()
    storage.failSet = true
    const store = new NotebookStore(() => storage, fixedNow)
    store.add({ kind: 'note', origin: 'personal', text: 'x', tags: [], target: { type: 'experiment', id: 'weather.x' } })
    expect(store.getSnapshot().entries).toHaveLength(1)
    expect(store.getSnapshot().persistent).toBe(false)
  })

  it('works without any storage object', () => {
    const store = new NotebookStore(() => null, fixedNow)
    expect(store.getSnapshot().persistent).toBe(false)
  })

  it('backs up unreadable data instead of discarding it', () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEY, '{not json')
    const store = new NotebookStore(() => storage, fixedNow)
    expect(store.getSnapshot().problem).toMatch(/unreadable/)
    expect(storage.getItem(`${STORAGE_KEY}.unreadable-backup`)).toBe('{not json')
  })

  it('drops invalid and duplicate stored entries with a warning', () => {
    const parsed = parseStored(stored([entry('a'), entry('a'), { id: 'b', kind: 'rant' }]))
    expect(parsed.entries.map((e) => e.id)).toEqual(['a'])
    expect(parsed.problem).toMatch(/2 saved notebook entries were invalid/)
  })

  it('reloads on a cross-tab storage event', () => {
    const storage = new MemoryStorage()
    const store = new NotebookStore(() => storage, fixedNow)
    const unsubscribe = store.subscribe(() => {})
    storage.setItem(STORAGE_KEY, stored([entry('x')]))
    window.dispatchEvent(new StorageEvent('storage', { key: STORAGE_KEY }))
    expect(store.getSnapshot().entries.map((e) => e.id)).toEqual(['x'])
    unsubscribe()
  })

  it('mergeNew never overwrites existing entries', () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEY, stored([entry('a')]))
    const store = new NotebookStore(() => storage, fixedNow)
    const n = store.mergeNew([entry('a', { text: 'changed' }), entry('b')])
    expect(n).toBe(1)
    expect(store.getSnapshot().entries.find((e) => e.id === 'a')!.text).toBe('text a')
  })

  it('targetKey separates experiments, weather events and sports events', () => {
    expect(targetKey({ type: 'experiment', id: 'sports.phase1_v1' })).toBe('experiment:sports.phase1_v1')
    expect(targetKey({ type: 'event', category: 'weather', key: 'K' })).not.toBe(targetKey({ type: 'event', category: 'sports', key: 'K' }))
  })
})

describe('notebook import and export', () => {
  it('round-trips an export', () => {
    const text = exportNotebook([entry('a'), entry('b')])
    const parsed = parseImport(text)
    expect(parsed.ok && parsed.entries.map((e) => e.id)).toEqual(['a', 'b'])
  })

  it('rejects non-JSON, wrong format and wrong version', () => {
    expect(parseImport('nope')).toEqual({ ok: false, error: 'File is not valid JSON.' })
    expect(parseImport(JSON.stringify({ format: 'other', version: 1, entries: [] })).ok).toBe(false)
    expect(parseImport(JSON.stringify({ version: 2, entries: [] })).ok).toBe(false)
    expect(parseImport(JSON.stringify({ version: 1 })).ok).toBe(false)
  })

  it('lists invalid and duplicate entries instead of importing them', () => {
    const parsed = parseImport(JSON.stringify({ version: 1, entries: [entry('a'), entry('a'), { id: 'z' }] }))
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    expect(parsed.entries).toHaveLength(1)
    expect(parsed.invalid).toHaveLength(2)
    expect(parsed.invalid.join(' ')).toMatch(/duplicate id a/)
  })

  it('previews new, identical and conflicting entries', () => {
    const existing = [entry('a'), entry('b')]
    const preview = previewImport(existing, { entries: [entry('a'), entry('b', { text: 'other' }), entry('c')], invalid: ['entry 4: x'] })
    expect(preview.added.map((e) => e.id)).toEqual(['c'])
    expect(preview.identical.map((e) => e.id)).toEqual(['a'])
    expect(preview.conflicts.map((c) => c.existing.id)).toEqual(['b'])
    expect(preview.conflicts[0].existing.text).toBe('text b')
    expect(preview.invalid).toHaveLength(1)
  })
})

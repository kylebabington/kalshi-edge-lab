import { describe, expect, it } from 'vitest'
import { canonicalizeSort, filtersFromSearchParams, filtersToSearchParams, normalizeFilters } from '../savedSearches/filters'
import { SavedSearchesStore, STORAGE_KEY, parseStored } from '../savedSearches/store'
import { exportSavedSearches, parseImport, previewImport } from '../savedSearches/transfer'

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

describe('saved search filters', () => {
  it('maps legacy title_asc to canonical title when reading', () => {
    expect(canonicalizeSort('title_asc')).toBe('title')
    expect(canonicalizeSort('title')).toBe('title')
    const params = new URLSearchParams('sort=title_asc&page=3&q=nfl')
    const f = filtersFromSearchParams(params)
    expect(f.sort).toBe('title')
    expect(f.q).toBe('nfl')
    const out = filtersToSearchParams(f)
    expect(out.get('sort')).toBe('title')
    expect(out.get('page')).toBeNull()
  })

  it('drops invalid enum values', () => {
    expect(normalizeFilters({ lifecycle: 'nope', coverage: 'model', sort: 'bogus' })).toEqual({
      category: '',
      q: '',
      lifecycle: '',
      coverage: 'model',
      quote: '',
      sort: 'retrieved_desc',
    })
  })
})

describe('SavedSearchesStore', () => {
  it('persists add, rename, update, pin and delete', () => {
    const storage = new MemoryStorage()
    const store = new SavedSearchesStore(() => storage, () => new Date('2099-01-01T00:00:00Z'))
    const added = store.add('Open sports', { category: 'Sports', q: '', lifecycle: 'open', coverage: 'model', quote: '', sort: 'title_asc' })
    expect(added?.filters.sort).toBe('title')
    expect(JSON.parse(storage.getItem(STORAGE_KEY)!).version).toBe(1)
    store.rename(added!.id, 'Renamed')
    store.updateFilters(added!.id, { category: '', q: 'x', lifecycle: '', coverage: '', quote: '', sort: 'volume_desc' })
    store.setPinned(added!.id, true)
    expect(store.pinned()).toHaveLength(1)
    store.setPinned(added!.id, false)
    store.remove(added!.id)
    expect(store.getSnapshot().entries).toHaveLength(0)
  })

  it('falls back to memory when storage is blocked', () => {
    const storage = new MemoryStorage()
    storage.failSet = true
    const store = new SavedSearchesStore(() => storage)
    store.add('Temp', { category: '', q: '', lifecycle: '', coverage: '', quote: '', sort: 'retrieved_desc' })
    expect(store.getSnapshot().persistent).toBe(false)
    expect(store.getSnapshot().entries).toHaveLength(1)
  })

  it('ignores invalid stored entries', () => {
    const parsed = parseStored(JSON.stringify({ version: 1, entries: [{ id: 'bad' }, { id: 'ok', name: 'Ok', filters: {}, pin_order: null, created_at: '2099-01-01T00:00:00Z', updated_at: '2099-01-01T00:00:00Z' }] }))
    expect(parsed.entries).toHaveLength(1)
    expect(parsed.problem).toMatch(/invalid/)
  })
})

describe('saved searches transfer', () => {
  it('rejects wrong format and previews merge without overwriting conflicts', () => {
    expect(parseImport('{"format":"other","version":1,"entries":[]}')).toEqual({ ok: false, error: 'File is not a saved-searches export.' })
    const storage = new MemoryStorage()
    const store = new SavedSearchesStore(() => storage, () => new Date('2099-01-01T00:00:00Z'))
    const a = store.add('A', { category: '', q: 'a', lifecycle: '', coverage: '', quote: '', sort: 'retrieved_desc' })!
    const exported = JSON.parse(exportSavedSearches(store.getSnapshot().entries))
    exported.entries[0].name = 'A changed'
    exported.entries.push({
      id: 'brand-new',
      name: 'B',
      filters: { category: '', q: '', lifecycle: '', coverage: '', quote: '', sort: 'title' },
      pin_order: null,
      created_at: '2099-01-01T00:00:00Z',
      updated_at: '2099-01-01T00:00:00Z',
    })
    const parsed = parseImport(JSON.stringify(exported))
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) return
    const preview = previewImport(store.getSnapshot().entries, parsed)
    expect(preview.conflicts).toHaveLength(1)
    expect(preview.added).toHaveLength(1)
    store.mergeNew(preview.added)
    expect(store.getSnapshot().entries.find((e) => e.id === a.id)?.name).toBe('A')
    expect(store.getSnapshot().entries.some((e) => e.id === 'brand-new')).toBe(true)
  })
})

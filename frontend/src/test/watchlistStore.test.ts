import { describe, expect, it, vi } from 'vitest'
import { STORAGE_KEY, WatchlistStore, parseStored, parseWatchId, watchId } from '../watchlist/store'

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

function stored(entries: unknown[]) {
  return JSON.stringify({ version: 1, entries })
}

describe('watchlist identifiers', () => {
  it('qualifies keys by category so identical weather and sports keys never collide', () => {
    const storage = new MemoryStorage()
    const store = new WatchlistStore(() => storage)
    store.add('weather', 'X-1', 'Weather X')
    store.add('sports', 'X-1', 'Sports X')
    expect(store.getSnapshot().entries.map((e) => e.id)).toEqual(['weather:X-1', 'sports:X-1'])
    store.remove(watchId('weather', 'X-1'))
    expect(store.has('sports:X-1')).toBe(true)
    expect(store.has('weather:X-1')).toBe(false)
  })

  it('parses ids at the first colon so sports ids containing colons and pipes stay intact', () => {
    expect(parseWatchId('sports:ncaaf|espn:401')).toEqual({ category: 'sports', key: 'ncaaf|espn:401' })
    expect(parseWatchId('kalshi:KXOTHER-MOCK')).toEqual({ category: 'kalshi', key: 'KXOTHER-MOCK' })
    expect(parseWatchId('other:abc')).toBeNull()
    expect(parseWatchId('weather:')).toBeNull()
  })

  it('treats a repeated add as a no-op', () => {
    const storage = new MemoryStorage()
    const store = new WatchlistStore(() => storage)
    store.add('weather', 'A', 'A')
    const snap = store.getSnapshot()
    store.add('weather', 'A', 'A again')
    expect(store.getSnapshot()).toBe(snap)
    expect(store.getSnapshot().entries).toHaveLength(1)
  })
})

describe('watchlist persistence', () => {
  it('survives a reload from storage (new store instance)', () => {
    const storage = new MemoryStorage()
    new WatchlistStore(() => storage).add('sports', 'ncaaf|espn:1', 'Game')
    const again = new WatchlistStore(() => storage)
    expect(again.getSnapshot().entries.map((e) => e.id)).toEqual(['sports:ncaaf|espn:1'])
    expect(JSON.parse(storage.data.get(STORAGE_KEY)!).version).toBe(1)
  })

  it('keeps snapshots referentially stable between changes', () => {
    const storage = new MemoryStorage()
    const store = new WatchlistStore(() => storage)
    const a = store.getSnapshot()
    expect(store.getSnapshot()).toBe(a)
    store.reload()
    expect(store.getSnapshot()).toBe(a)
    store.add('weather', 'K', 'K')
    const b = store.getSnapshot()
    expect(b).not.toBe(a)
    expect(store.getSnapshot()).toBe(b)
    expect(Object.isFrozen(b.entries)).toBe(true)
  })

  it('drops invalid and duplicate stored entries and reports it', () => {
    const ok = { id: 'weather:A', category: 'weather', key: 'A', label: 'A', added_at: '2026-10-01T00:00:00Z' }
    const parsed = parseStored(
      stored([
        ok,
        ok,
        { id: 'sports:B', category: 'weather', key: 'B', label: 'B', added_at: '2026-10-01T00:00:00Z' },
        { id: 'trading:C', category: 'trading', key: 'C', added_at: '2026-10-01T00:00:00Z' },
        { id: 'weather:D', category: 'weather', key: 'D', added_at: 'not a date' },
        'junk',
      ]),
    )
    expect(parsed.entries.map((e) => e.id)).toEqual(['weather:A'])
    expect(parsed.problem).toMatch(/5 saved watchlist entries were invalid/)
  })

  it('sets corrupt JSON aside in a backup key instead of crashing', () => {
    const storage = new MemoryStorage()
    storage.data.set(STORAGE_KEY, '{not json')
    const store = new WatchlistStore(() => storage)
    expect(store.getSnapshot().entries).toEqual([])
    expect(store.getSnapshot().problem).toMatch(/unreadable/)
    expect(storage.data.get(`${STORAGE_KEY}.unreadable-backup`)).toBe('{not json')
  })

  it('falls back to memory when reading storage is blocked', () => {
    const storage = new MemoryStorage()
    storage.failGet = true
    const store = new WatchlistStore(() => storage)
    expect(store.getSnapshot().persistent).toBe(false)
    expect(store.getSnapshot().problem).toMatch(/Changes won't persist/)
    storage.failSet = true
    store.add('weather', 'A', 'A')
    expect(store.has('weather:A')).toBe(true)
    expect(store.getSnapshot().persistent).toBe(false)
  })

  it('keeps changes in memory and flags them when writing fails (storage full)', () => {
    const storage = new MemoryStorage()
    const store = new WatchlistStore(() => storage)
    store.add('weather', 'A', 'A')
    storage.failSet = true
    store.add('sports', 'B', 'B')
    const snap = store.getSnapshot()
    expect(snap.entries.map((e) => e.id)).toEqual(['weather:A', 'sports:B'])
    expect(snap.persistent).toBe(false)
    expect(snap.problem).toMatch(/Changes won't persist/)
    storage.failSet = false
    store.remove('sports:B')
    expect(store.getSnapshot().persistent).toBe(true)
    expect(JSON.parse(storage.data.get(STORAGE_KEY)!).entries).toHaveLength(1)
  })

  it('treats a missing storage object (e.g. disabled) as in-memory only', () => {
    const store = new WatchlistStore(() => null)
    store.add('weather', 'A', 'A')
    expect(store.getSnapshot().persistent).toBe(false)
    expect(store.has('weather:A')).toBe(true)
  })
})

describe('cross-tab updates', () => {
  it('reloads when another tab writes the watchlist key and notifies subscribers', () => {
    const storage = new MemoryStorage()
    const store = new WatchlistStore(() => storage)
    const listener = vi.fn()
    const unsubscribe = store.subscribe(listener)
    storage.data.set(
      STORAGE_KEY,
      stored([{ id: 'sports:Z', category: 'sports', key: 'Z', label: 'Z', added_at: '2026-10-01T00:00:00Z' }]),
    )
    window.dispatchEvent(new StorageEvent('storage', { key: STORAGE_KEY }))
    expect(listener).toHaveBeenCalledTimes(1)
    expect(store.has('sports:Z')).toBe(true)
    window.dispatchEvent(new StorageEvent('storage', { key: 'unrelated' }))
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
    storage.data.set(STORAGE_KEY, stored([]))
    window.dispatchEvent(new StorageEvent('storage', { key: STORAGE_KEY }))
    expect(store.has('sports:Z')).toBe(true)
  })

  it('does not let another tab overwrite unsaved in-memory entries', () => {
    const storage = new MemoryStorage()
    const store = new WatchlistStore(() => storage)
    const unsubscribe = store.subscribe(() => {})
    storage.failSet = true
    store.add('weather', 'Only-here', 'Only here')
    storage.data.set(STORAGE_KEY, stored([]))
    window.dispatchEvent(new StorageEvent('storage', { key: STORAGE_KEY }))
    expect(store.has('weather:Only-here')).toBe(true)
    unsubscribe()
  })
})

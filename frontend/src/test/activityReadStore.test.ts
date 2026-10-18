import { describe, expect, it } from 'vitest'
import { ActivityReadStore, STORAGE_KEY, parseStored } from '../activity/readStore'

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

describe('activity read store', () => {
  it('persists and reloads read ids across store instances', () => {
    const storage = new MemoryStorage()
    const store = new ActivityReadStore(() => storage)
    store.markRead('a')
    store.markRead('b')
    const again = new ActivityReadStore(() => storage)
    expect(again.isRead('a')).toBe(true)
    expect(again.isRead('b')).toBe(true)
    expect(JSON.parse(storage.data.get(STORAGE_KEY)!).version).toBe(1)
  })

  it('mark all read uses exact id snapshot; new arrivals stay unread', () => {
    const storage = new MemoryStorage()
    const store = new ActivityReadStore(() => storage)
    store.markAllRead(['old-1', 'old-2'])
    expect(store.isRead('old-1')).toBe(true)
    expect(store.isRead('old-2')).toBe(true)
    // Late-discovered / new arrival with equal or older observed_at still unread until its id is marked.
    expect(store.isRead('late-discovered')).toBe(false)
    expect(store.isRead('new-arrival')).toBe(false)
  })

  it('survives restart with equal timestamps without relying on a watermark', () => {
    const storage = new MemoryStorage()
    new ActivityReadStore(() => storage).markAllRead(['tie-a', 'tie-b'])
    const again = new ActivityReadStore(() => storage)
    expect(again.isRead('tie-a')).toBe(true)
    expect(again.isRead('tie-c')).toBe(false)
  })

  it('keeps in-memory state when storage is blocked', () => {
    const storage = new MemoryStorage()
    storage.failSet = true
    const store = new ActivityReadStore(() => storage)
    store.markRead('x')
    expect(store.isRead('x')).toBe(true)
    expect(store.getSnapshot().persistent).toBe(false)
    expect(store.getSnapshot().problem).toMatch(/unavailable/i)
  })

  it('drops invalid stored ids', () => {
    const parsed = parseStored(JSON.stringify({ version: 1, read_ids: ['ok', '', 12, 'ok'] }))
    expect(parsed.ids).toEqual(['ok'])
    expect(parsed.problem).toMatch(/invalid/)
  })

  it('cross-tab storage events reload when persistent', () => {
    const storage = new MemoryStorage()
    const store = new ActivityReadStore(() => storage)
    const seen: number[] = []
    store.subscribe(() => seen.push(1))
    store.markRead('one')
    storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, read_ids: ['one', 'two'] }))
    store.reload()
    expect(store.isRead('two')).toBe(true)
  })
})

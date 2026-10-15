/**
 * Expand watchlist entries into Kalshi event tickers for Refresh markets.
 * Sports entries resolve via the local sports API (GET only). Bounded concurrency
 * and per-request timeouts keep one slow lookup from blocking the rest.
 */
import { isNotFound, NotFoundError } from '../api/client'
import { fetchSportsEvent } from '../api/sports'
import type { WatchEntry } from '../watchlist/store'

export const MAX_WATCHED_TICKERS = 500
export const SPORTS_RESOLVE_CONCURRENCY = 4
export const SPORTS_RESOLVE_TIMEOUT_MS = 8000

export type UnresolvedWatch = {
  entryId: string
  label: string
  category: WatchEntry['category']
  key: string
  reason: string
}

export type RefreshTargetsResult = {
  tickers: string[]
  unresolved: UnresolvedWatch[]
  /** Entries that would have been included past MAX_WATCHED_TICKERS. */
  overCap: UnresolvedWatch[]
  truncated: boolean
}

export type ResolveDeps = {
  fetchSports: (eventId: string) => Promise<{ event: { kalshi_event_tickers: string[] } }>
  timeoutMs?: number
  concurrency?: number
  maxTickers?: number
}

class TimeoutError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'TimeoutError'
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const id = setTimeout(() => reject(new TimeoutError(`Timed out resolving ${label} after ${ms}ms`)), ms)
    promise.then(
      (v) => {
        clearTimeout(id)
        resolve(v)
      },
      (err) => {
        clearTimeout(id)
        reject(err)
      },
    )
  })
}

async function mapPool<T, R>(items: T[], concurrency: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length)
  let next = 0
  async function worker() {
    while (next < items.length) {
      const i = next
      next += 1
      out[i] = await fn(items[i])
    }
  }
  const n = Math.max(1, Math.min(concurrency, items.length || 1))
  await Promise.all(Array.from({ length: n }, () => worker()))
  return out
}

function reasonFromError(err: unknown, entry: WatchEntry): string {
  if (err instanceof TimeoutError) return err.message
  if (isNotFound(err) || err instanceof NotFoundError) {
    return `No saved sports capture for ${entry.key} on this machine.`
  }
  const msg = err instanceof Error ? err.message : String(err)
  return msg.slice(0, 200) || 'Sports lookup failed.'
}

/**
 * Resolve watchlist → Kalshi tickers. Always returns; callers may refresh with
 * whatever tickers succeeded even when some entries are unresolved or over-cap.
 */
export async function resolveWatchRefreshTargets(
  entries: readonly WatchEntry[],
  deps: ResolveDeps = { fetchSports: fetchSportsEvent },
): Promise<RefreshTargetsResult> {
  const timeoutMs = deps.timeoutMs ?? SPORTS_RESOLVE_TIMEOUT_MS
  const concurrency = deps.concurrency ?? SPORTS_RESOLVE_CONCURRENCY
  const maxTickers = deps.maxTickers ?? MAX_WATCHED_TICKERS
  const fetchSports = deps.fetchSports

  const tickers: string[] = []
  const seen = new Set<string>()
  const unresolved: UnresolvedWatch[] = []
  const overCap: UnresolvedWatch[] = []

  function tryAdd(ticker: string, source: WatchEntry): boolean {
    const t = ticker.trim()
    if (!t || seen.has(t)) return true
    if (tickers.length >= maxTickers) {
      overCap.push({
        entryId: source.id,
        label: source.label,
        category: source.category,
        key: source.key,
        reason: `Over the ${maxTickers}-ticker refresh cap; not included.`,
      })
      return false
    }
    seen.add(t)
    tickers.push(t)
    return true
  }

  const direct = entries.filter((e) => e.category === 'weather' || e.category === 'kalshi')
  const sports = entries.filter((e) => e.category === 'sports')

  for (const e of direct) {
    if (!tryAdd(e.key, e) && tickers.length >= maxTickers) {
      // Remaining directs still count as over-cap for visibility.
      for (const rest of direct.slice(direct.indexOf(e) + 1)) {
        if (!seen.has(rest.key.trim())) {
          overCap.push({
            entryId: rest.id,
            label: rest.label,
            category: rest.category,
            key: rest.key,
            reason: `Over the ${maxTickers}-ticker refresh cap; not included.`,
          })
        }
      }
      break
    }
  }

  if (sports.length && tickers.length < maxTickers) {
    const results = await mapPool(sports, concurrency, async (entry) => {
      try {
        const detail = await withTimeout(fetchSports(entry.key), timeoutMs, entry.label || entry.key)
        const list = detail.event.kalshi_event_tickers ?? []
        if (!list.length) {
          return {
            entry,
            tickers: [] as string[],
            reason: `Sports event ${entry.key} has no associated Kalshi event tickers.`,
          }
        }
        return { entry, tickers: list, reason: null as string | null }
      } catch (err) {
        return { entry, tickers: [] as string[], reason: reasonFromError(err, entry) }
      }
    })

    for (const r of results) {
      if (r.reason) {
        unresolved.push({
          entryId: r.entry.id,
          label: r.entry.label,
          category: r.entry.category,
          key: r.entry.key,
          reason: r.reason,
        })
        continue
      }
      let anyAdded = false
      let hitCap = false
      for (const t of r.tickers) {
        const before = tickers.length
        if (!tryAdd(t, r.entry)) {
          hitCap = true
          break
        }
        if (tickers.length > before) anyAdded = true
      }
      if (!anyAdded && !hitCap && r.tickers.every((t) => seen.has(t.trim()))) {
        // All aliases already covered by other watch entries — fine, not unresolved.
      }
    }
  } else if (sports.length && tickers.length >= maxTickers) {
    for (const entry of sports) {
      overCap.push({
        entryId: entry.id,
        label: entry.label,
        category: entry.category,
        key: entry.key,
        reason: `Over the ${maxTickers}-ticker refresh cap; sports expansion skipped.`,
      })
    }
  }

  // Dedupe overCap by entryId (tryAdd may push per ticker).
  const overSeen = new Set<string>()
  const overCapUnique = overCap.filter((o) => {
    if (overSeen.has(o.entryId)) return false
    overSeen.add(o.entryId)
    return true
  })

  return {
    tickers,
    unresolved,
    overCap: overCapUnique,
    truncated: overCapUnique.length > 0,
  }
}

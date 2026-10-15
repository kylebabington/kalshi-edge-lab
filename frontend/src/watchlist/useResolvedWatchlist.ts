import { useEffect, useRef, useState } from 'react'
import { resolveEntry, type Resolved } from './resolve'
import type { WatchEntry } from './store'

export type ResolvedRow = {
  entry: WatchEntry
  data: Resolved | null
  error: string | null
  loading: boolean
  reload: () => void
}

type Settled = { tick: number; data: Resolved | null; error: string | null }

/**
 * Looks up every watchlist entry so the page can sort by status.
 * Each entry has its own retry generation. A failed request stays an error
 * (with retry) and is not reported as "unavailable".
 *
 * Settled rows are read through a ref so one entry finishing does not cancel
 * the others. Only an entry-list change or that entry's retry starts a new request.
 */
export function useResolvedWatchlist(entries: readonly WatchEntry[]): ResolvedRow[] {
  const [ticks, setTicks] = useState<Record<string, number>>({})
  const [settled, setSettled] = useState<Record<string, Settled>>({})
  const settledRef = useRef<Record<string, Settled>>({})

  useEffect(() => {
    const flags = new Map<string, { cancelled: boolean }>()
    for (const entry of entries) {
      const tick = ticks[entry.id] ?? 0
      const existing = settledRef.current[entry.id]
      if (existing && existing.tick === tick) continue
      const flag = { cancelled: false }
      flags.set(entry.id, flag)
      resolveEntry(entry)
        .then((data) => {
          if (flag.cancelled) return
          const next = { ...settledRef.current, [entry.id]: { tick, data, error: null } }
          settledRef.current = next
          setSettled(next)
        })
        .catch((err: unknown) => {
          if (flag.cancelled) return
          const next = {
            ...settledRef.current,
            [entry.id]: { tick, data: null, error: err instanceof Error ? err.message : String(err) },
          }
          settledRef.current = next
          setSettled(next)
        })
    }
    return () => {
      for (const flag of flags.values()) flag.cancelled = true
    }
  }, [entries, ticks])

  return entries.map((entry) => {
    const tick = ticks[entry.id] ?? 0
    const row = settled[entry.id]
    const ready = !!row && row.tick === tick
    return {
      entry,
      data: ready ? row.data : null,
      error: ready ? row.error : null,
      loading: !ready,
      reload: () => setTicks((prev) => ({ ...prev, [entry.id]: (prev[entry.id] ?? 0) + 1 })),
    }
  })
}

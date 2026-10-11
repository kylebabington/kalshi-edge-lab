import { useEffect, useRef, useState } from 'react'

export type AsyncState<T> = {
  data: T | null
  loading: boolean
  error: string | null
  loadedAt: Date | null
}

type Settled<T> = { key: string; data: T | null; error: string | null; loadedAt: Date | null }

/** `key` must encode every input of `load`; a new key triggers a new request. */
export function useAsync<T>(load: () => Promise<T>, key: string): AsyncState<T> {
  const loadRef = useRef(load)
  const [settled, setSettled] = useState<Settled<T> | null>(null)

  useEffect(() => {
    loadRef.current = load
  })

  useEffect(() => {
    let cancelled = false
    loadRef
      .current()
      .then((data) => {
        if (!cancelled) setSettled({ key, data, error: null, loadedAt: new Date() })
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setSettled({ key, data: null, error: err instanceof Error ? err.message : String(err), loadedAt: null })
      })
    return () => {
      cancelled = true
    }
  }, [key])

  if (!settled || settled.key !== key) {
    return { data: settled?.data ?? null, loading: true, error: null, loadedAt: settled?.loadedAt ?? null }
  }
  return { data: settled.data, loading: false, error: settled.error, loadedAt: settled.loadedAt }
}

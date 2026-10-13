import { useCallback, useEffect, useRef, useState } from 'react'

export type AsyncState<T> = {
  data: T | null
  loading: boolean
  error: string | null
  /** The thrown value, kept so callers can tell a 404 from a network failure. */
  errorValue: unknown
  loadedAt: Date | null
  reload: () => void
}

type Settled<T> = { key: string; data: T | null; error: string | null; errorValue: unknown; loadedAt: Date | null }

/** `key` must encode every input of `load`; a new key (or `reload()`) triggers a new request. */
export function useAsync<T>(load: () => Promise<T>, key: string): AsyncState<T> {
  const loadRef = useRef(load)
  const [settled, setSettled] = useState<Settled<T> | null>(null)
  const [nonce, setNonce] = useState(0)
  const fullKey = `${key}#${nonce}`
  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    loadRef.current = load
  })

  useEffect(() => {
    let cancelled = false
    loadRef
      .current()
      .then((data) => {
        if (!cancelled) setSettled({ key: fullKey, data, error: null, errorValue: null, loadedAt: new Date() })
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setSettled({
            key: fullKey,
            data: null,
            error: err instanceof Error ? err.message : String(err),
            errorValue: err,
            loadedAt: null,
          })
      })
    return () => {
      cancelled = true
    }
  }, [fullKey])

  if (!settled || settled.key !== fullKey) {
    return { data: settled?.data ?? null, loading: true, error: null, errorValue: null, loadedAt: settled?.loadedAt ?? null, reload }
  }
  return { data: settled.data, loading: false, error: settled.error, errorValue: settled.errorValue, loadedAt: settled.loadedAt, reload }
}

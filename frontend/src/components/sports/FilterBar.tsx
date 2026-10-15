import { useEffect, useId, useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { label } from './format'

export type SelectFilter = {
  id: string
  label: string
  value: string
  options: { value: string; label?: string }[]
  allLabel?: string
}

type Props = {
  selects: SelectFilter[]
  /** Hidden until the user opens Advanced filters. Values stay in the URL either way. */
  advancedSelects?: SelectFilter[]
  onChange: (id: string, value: string) => void
  search?: { id?: string; label: string; value: string; placeholder?: string }
  onSearch?: (value: string) => void
  /** Clears search and select filters. The page decides which URL keys survive (for example the current tab). */
  onReset?: () => void
  /** Counts that are not selects, such as a weighting radio. */
  extraActive?: number
  children?: ReactNode
}

function toggleDetails(event: MouseEvent<HTMLElement>) {
  event.preventDefault()
  const details = event.currentTarget.parentElement
  if (details instanceof HTMLDetailsElement) details.open = !details.open
}

function activeSelects(selects: SelectFilter[]): number {
  return selects.filter((s) => s.value).length
}

function SelectControl({ s, onChange }: { s: SelectFilter; onChange: (id: string, value: string) => void }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)] w-full sm:w-auto sm:min-w-[9rem]">
      {s.label}
      <select className="filter-control" value={s.value} onChange={(e) => onChange(s.id, e.target.value)}>
        <option value="">{s.allLabel ?? 'All'}</option>
        {s.options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label ?? label(o.value)}
          </option>
        ))}
      </select>
    </label>
  )
}

export function FilterBar({
  selects,
  advancedSelects = [],
  onChange,
  search,
  onSearch,
  onReset,
  extraActive = 0,
  children,
}: Props) {
  const autoId = useId()
  const searchId = search?.id ?? autoId
  const [text, setText] = useState(search?.value ?? '')
  const [syncedValue, setSyncedValue] = useState(search?.value ?? '')
  if ((search?.value ?? '') !== syncedValue) {
    setSyncedValue(search?.value ?? '')
    setText(search?.value ?? '')
  }

  useEffect(() => {
    if (!onSearch || text === (search?.value ?? '')) return
    const handle = setTimeout(() => onSearch(text), 300)
    return () => clearTimeout(handle)
  }, [text, onSearch, search?.value])

  const advancedCount = activeSelects(advancedSelects)
  const advancedActive = advancedCount > 0
  // Open on first paint when a deep link already set an advanced filter, and again if one
  // becomes active later. The element stays uncontrolled so collapsing does not clear the URL.
  const detailsRef = useRef<HTMLDetailsElement>(null)
  const wasAdvancedActive = useRef(advancedActive)
  const initialOpen = useRef(advancedActive)
  useLayoutEffect(() => {
    if (initialOpen.current && detailsRef.current) detailsRef.current.open = true
    initialOpen.current = false
  }, [])
  useEffect(() => {
    if (advancedActive && !wasAdvancedActive.current && detailsRef.current) detailsRef.current.open = true
    wasAdvancedActive.current = advancedActive
  }, [advancedActive])

  const searchActive = search && onSearch && text.trim() ? 1 : 0
  const count = activeSelects(selects) + advancedCount + searchActive + extraActive

  return (
    <div className="panel space-y-3 max-w-full">
      <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-end gap-3">
        {search && onSearch ? (
          <form
            role="search"
            className="flex flex-col gap-1 text-xs text-[var(--text-muted)] w-full sm:grow sm:min-w-[12rem] sm:w-auto"
            onSubmit={(e) => {
              e.preventDefault()
              onSearch(text)
            }}
          >
            <label htmlFor={searchId}>{search.label}</label>
            <input
              id={searchId}
              type="search"
              className="filter-control"
              value={text}
              placeholder={search.placeholder}
              onChange={(e) => setText(e.target.value)}
            />
          </form>
        ) : null}
        {selects.map((s) => (
          <SelectControl key={s.id} s={s} onChange={onChange} />
        ))}
        {children}
      </div>
      {advancedSelects.length ? (
        <details ref={detailsRef}>
          <summary className="cursor-pointer text-sm min-h-[2.25rem] inline-flex items-center" onClick={toggleDetails}>
            Advanced filters
            {advancedCount > 0 ? (
              <span className="ml-2 text-[var(--warning)]">· {advancedCount} active</span>
            ) : null}
          </summary>
          <div className="mt-3 flex flex-col sm:flex-row sm:flex-wrap sm:items-end gap-3">
            {advancedSelects.map((s) => (
              <SelectControl key={s.id} s={s} onChange={onChange} />
            ))}
          </div>
        </details>
      ) : null}
      {count > 0 && onReset ? (
        <p className="text-sm" aria-live="polite">
          <span className="text-[var(--text)]">
            {count} {count === 1 ? 'filter' : 'filters'} active
          </span>
          <span className="text-[var(--text-muted)]"> · </span>
          <button type="button" className="pager-button text-sm" onClick={onReset}>
            Clear all
          </button>
        </p>
      ) : null}
    </div>
  )
}

export function Pager({
  page,
  pages,
  total,
  onPage,
  noun,
}: {
  page: number
  pages: number
  total: number
  onPage: (page: number) => void
  noun: string
}) {
  return (
    <nav aria-label={`${noun} pages`} className="flex flex-wrap items-center gap-3 text-sm max-w-full">
      <button type="button" className="pager-button" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Previous
      </button>
      <span className="text-[var(--text-muted)]" aria-live="polite">
        Page {page} of {pages} · {total} {noun}
      </span>
      <button type="button" className="pager-button" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next
      </button>
    </nav>
  )
}

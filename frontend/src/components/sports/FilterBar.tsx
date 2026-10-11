import { useEffect, useState, type ReactNode } from 'react'
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
  onChange: (id: string, value: string) => void
  search?: { label: string; value: string; placeholder?: string }
  onSearch?: (value: string) => void
  children?: ReactNode
}

export function FilterBar({ selects, onChange, search, onSearch, children }: Props) {
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

  return (
    <div className="panel flex flex-wrap items-end gap-3">
      {selects.map((s) => (
        <label key={s.id} className="flex flex-col gap-1 text-xs text-[var(--text-muted)] min-w-[9rem]">
          {s.label}
          <select
            className="filter-control"
            value={s.value}
            onChange={(e) => onChange(s.id, e.target.value)}
          >
            <option value="">{s.allLabel ?? 'All'}</option>
            {s.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label ?? label(o.value)}
              </option>
            ))}
          </select>
        </label>
      ))}
      {search && onSearch ? (
        <form
          role="search"
          className="flex flex-col gap-1 text-xs text-[var(--text-muted)] grow min-w-[12rem]"
          onSubmit={(e) => {
            e.preventDefault()
            onSearch(text)
          }}
        >
          <label htmlFor="sports-search">{search.label}</label>
          <input
            id="sports-search"
            type="search"
            className="filter-control"
            value={text}
            placeholder={search.placeholder}
            onChange={(e) => setText(e.target.value)}
          />
        </form>
      ) : null}
      {children}
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
    <nav aria-label={`${noun} pages`} className="flex flex-wrap items-center gap-3 text-sm">
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

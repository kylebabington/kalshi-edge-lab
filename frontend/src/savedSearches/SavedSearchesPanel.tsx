import { useMemo, useRef, useState, type ChangeEvent } from 'react'
import { Link } from 'react-router-dom'
import { exploreHref, type ExploreSavedFilters } from './filters'
import { NameForm } from './NameForm'
import {
  exportSavedSearches,
  MAX_IMPORT_BYTES,
  parseImport,
  previewImport,
  type ImportPreview,
} from './transfer'
import { useSavedSearches } from './useSavedSearches'
import type { SavedSearch, SavedSearchesStore } from './store'

function download(filename: string, text: string) {
  const blob = new Blob([text], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

type Props = {
  currentFilters: ExploreSavedFilters
  onOpen: (filters: ExploreSavedFilters) => void
  store?: SavedSearchesStore
}

/**
 * Manage Explorer saved searches. Opening/importing only restores filters —
 * never starts a market refresh.
 */
export function SavedSearchesPanel({ currentFilters, onOpen, store: storeProp }: Props) {
  const { entries, persistent, problem, store } = useSavedSearches(storeProp)
  const [mode, setMode] = useState<'idle' | 'save' | { rename: string } | { confirmDelete: string }>('idle')
  const [importError, setImportError] = useState<string | null>(null)
  const [confirmReplace, setConfirmReplace] = useState(false)
  const [importDone, setImportDone] = useState<string | null>(null)
  const [incoming, setIncoming] = useState<{ entries: SavedSearch[]; invalid: string[] } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const preview: ImportPreview | null = useMemo(
    () => (incoming ? previewImport(entries, incoming) : null),
    [incoming, entries],
  )

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    setImportDone(null)
    setImportError(null)
    setConfirmReplace(false)
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (file.size > MAX_IMPORT_BYTES) {
      setImportError('File is too large for a saved-searches export.')
      setIncoming(null)
      return
    }
    const text = await file.text()
    const parsed = parseImport(text)
    if (!parsed.ok) {
      setImportError(parsed.error)
      setIncoming(null)
      return
    }
    setIncoming({ entries: parsed.entries, invalid: parsed.invalid })
  }

  return (
    <section className="panel space-y-3" aria-label="Saved searches">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">Saved searches</h3>
        <p className="text-xs text-[var(--text-muted)]">Stored in this browser only. Not research evidence.</p>
      </div>
      {problem ? (
        <p role="status" className={`text-xs ${persistent ? 'text-[var(--text-muted)]' : 'text-[var(--warning)]'}`}>
          {problem}
        </p>
      ) : null}

      {mode === 'save' ? (
        <NameForm
          label="Name for current filters"
          submitLabel="Save search"
          onCancel={() => setMode('idle')}
          onSubmit={(name) => {
            const added = store.add(name, currentFilters)
            if (!added) return false
            setMode('idle')
            return true
          }}
        />
      ) : (
        <button type="button" className="pager-button text-sm" onClick={() => setMode('save')}>
          Save current filters…
        </button>
      )}

      {entries.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">No saved searches yet.</p>
      ) : (
        <ul className="space-y-3">
          {entries.map((s) => (
            <li key={s.id} className="rounded-lg border border-[var(--border)] p-3 space-y-2" data-testid="saved-search">
              {typeof mode === 'object' && 'rename' in mode && mode.rename === s.id ? (
                <NameForm
                  label="Rename search"
                  initial={s.name}
                  submitLabel="Save name"
                  onCancel={() => setMode('idle')}
                  onSubmit={(name) => {
                    if (!store.rename(s.id, name)) return false
                    setMode('idle')
                    return true
                  }}
                />
              ) : (
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="font-medium break-words">{s.name}</div>
                    <Link to={exploreHref(s.filters)} className="text-xs text-[var(--text-muted)] mono break-all hover:underline">
                      {exploreHref(s.filters)}
                    </Link>
                    {s.pin_order != null ? <span className="badge badge-info ml-2">Pinned</span> : null}
                  </div>
                </div>
              )}
              {typeof mode === 'object' && 'confirmDelete' in mode && mode.confirmDelete === s.id ? (
                <div role="alertdialog" aria-label={`Delete ${s.name}`} className="flex flex-wrap gap-2 text-sm">
                  <span>Delete “{s.name}”? This cannot be undone.</span>
                  <button
                    type="button"
                    className="pager-button text-[var(--negative)]"
                    onClick={() => {
                      store.remove(s.id)
                      setMode('idle')
                    }}
                  >
                    Yes, delete
                  </button>
                  <button type="button" className="pager-button" onClick={() => setMode('idle')}>
                    Cancel
                  </button>
                </div>
              ) : typeof mode === 'object' && 'rename' in mode && mode.rename === s.id ? null : (
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="pager-button text-sm"
                    onClick={() => onOpen(s.filters)}
                    aria-label={`Open saved search ${s.name}`}
                  >
                    Open
                  </button>
                  <button type="button" className="pager-button text-sm" onClick={() => store.updateFilters(s.id, currentFilters)}>
                    Update from current
                  </button>
                  <button type="button" className="pager-button text-sm" onClick={() => setMode({ rename: s.id })}>
                    Rename…
                  </button>
                  <button
                    type="button"
                    className="pager-button text-sm"
                    onClick={() => store.setPinned(s.id, s.pin_order == null)}
                  >
                    {s.pin_order != null ? 'Unpin' : 'Pin to dashboard'}
                  </button>
                  <button type="button" className="pager-button text-sm" onClick={() => setMode({ confirmDelete: s.id })}>
                    Delete…
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="border-t border-[var(--border)] pt-3 space-y-2">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="pager-button text-sm"
            disabled={!entries.length}
            onClick={() =>
              download(`kalshi-edge-lab-saved-searches-${new Date().toISOString().slice(0, 10)}.json`, exportSavedSearches(entries))
            }
          >
            Export JSON
          </button>
          <button type="button" className="pager-button text-sm" onClick={() => fileRef.current?.click()}>
            Import JSON…
          </button>
          <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={(e) => void onFile(e)} />
        </div>
        {importError ? (
          <p className="text-sm text-[var(--negative)]" role="alert">
            {importError}
          </p>
        ) : null}
        {importDone ? (
          <p className="text-sm text-[var(--text-muted)]" role="status">
            {importDone}
          </p>
        ) : null}
        {preview && incoming ? (
          <div className="space-y-2 rounded-lg border border-[var(--border)] p-3 text-sm" data-testid="saved-searches-import-preview">
            <p>
              New: <span className="mono">{preview.added.length}</span> · Identical:{' '}
              <span className="mono">{preview.identical.length}</span> · Conflicts:{' '}
              <span className="mono">{preview.conflicts.length}</span> · Invalid:{' '}
              <span className="mono">{preview.invalid.length}</span>
            </p>
            <p className="text-xs text-[var(--text-muted)]">
              Merge only adds new ids. Conflicts keep your existing search. Import never starts a market refresh.
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="pager-button text-sm"
                disabled={!preview.added.length}
                onClick={() => {
                  const n = store.mergeNew(preview.added)
                  setImportDone(`Merged ${n} new ${n === 1 ? 'search' : 'searches'}.`)
                  setIncoming(null)
                }}
              >
                Merge new
              </button>
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={confirmReplace} onChange={(e) => setConfirmReplace(e.target.checked)} />
                Replace all with import
              </label>
              <button
                type="button"
                className="pager-button text-sm text-[var(--negative)]"
                disabled={!confirmReplace}
                onClick={() => {
                  store.replaceAll(incoming.entries)
                  setImportDone(
                    `Replaced with ${incoming.entries.length} ${incoming.entries.length === 1 ? 'search' : 'searches'}.`,
                  )
                  setIncoming(null)
                  setConfirmReplace(false)
                }}
              >
                Replace all
              </button>
              <button type="button" className="pager-button text-sm" onClick={() => setIncoming(null)}>
                Cancel
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  )
}

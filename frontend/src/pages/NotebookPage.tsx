import { useCallback, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FilterBar } from '../components/sports/FilterBar'
import { NoteList, NotebookDisclaimer, StorageProblem } from '../notebook/NotebookPanel'
import { targetLabel } from '../notebook/targets'
import { useNotebook } from '../notebook/useNotebook'
import { exportNotebook, parseImport, previewImport, MAX_IMPORT_BYTES, type ImportPreview } from '../notebook/transfer'
import type { NotebookEntry, NotebookStore } from '../notebook/store'

const FILTER_KEYS = ['q', 'kind', 'origin', 'tag', 'target'] as const

function matches(e: NotebookEntry, f: Record<(typeof FILTER_KEYS)[number], string>): boolean {
  if (f.kind && e.kind !== f.kind) return false
  if (f.origin && e.origin !== f.origin) return false
  if (f.tag && !e.tags.includes(f.tag)) return false
  if (f.target && e.target.type !== f.target) return false
  const q = f.q.trim().toLowerCase()
  if (q && !`${e.text} ${e.tags.join(' ')} ${targetLabel(e.target)}`.toLowerCase().includes(q)) return false
  return true
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

function ImportPanel({ store, existing }: { store: NotebookStore; existing: readonly NotebookEntry[] }) {
  const [fileName, setFileName] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [incoming, setIncoming] = useState<NotebookEntry[] | null>(null)
  const [invalid, setInvalid] = useState<string[]>([])
  const [confirmReplace, setConfirmReplace] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const preview: ImportPreview | null = useMemo(
    () => (incoming ? previewImport(existing, { entries: incoming, invalid }) : null),
    [existing, incoming, invalid],
  )

  const reset = () => {
    setIncoming(null)
    setInvalid([])
    setFileName(null)
    setConfirmReplace(false)
    if (inputRef.current) inputRef.current.value = ''
  }

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    setDone(null)
    setError(null)
    setIncoming(null)
    setConfirmReplace(false)
    if (!file) return
    setFileName(file.name)
    if (file.size > MAX_IMPORT_BYTES) {
      setError('File is too large for a notebook export.')
      return
    }
    let text: string
    try {
      text = await file.text()
    } catch {
      setError('The file could not be read.')
      return
    }
    const parsed = parseImport(text)
    if (!parsed.ok) {
      setError(parsed.error)
      return
    }
    setIncoming(parsed.entries)
    setInvalid(parsed.invalid)
  }

  const merge = () => {
    if (!preview) return
    const n = store.mergeNew(preview.added)
    setDone(`Added ${n} new ${n === 1 ? 'entry' : 'entries'}. Existing notes were kept unchanged.`)
    reset()
  }

  const replace = () => {
    if (!incoming || !confirmReplace) return
    store.replaceAll(incoming)
    setDone(`Replaced the notebook with ${incoming.length} imported ${incoming.length === 1 ? 'entry' : 'entries'}.`)
    reset()
  }

  return (
    <section className="panel space-y-3" aria-labelledby="notebook-import-heading">
      <h3 id="notebook-import-heading" className="font-semibold">
        Import
      </h3>
      <p className="text-xs text-[var(--text-muted)]">
        Choose a notebook export (JSON). You will see a preview first. Merging only adds new entries; it never changes or
        removes notes you already have.
      </p>
      <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)] w-fit">
        Notebook file
        <input ref={inputRef} type="file" accept="application/json,.json" onChange={onFile} className="text-sm" />
      </label>
      {error ? (
        <p className="text-sm text-[var(--negative)]" role="alert">
          {fileName ? `${fileName}: ` : ''}
          {error}
        </p>
      ) : null}
      {done ? (
        <p className="text-sm" role="status">
          {done}
        </p>
      ) : null}
      {preview ? (
        <div className="space-y-3 rounded-lg border border-[var(--border)] p-3" data-testid="import-preview">
          <div className="font-medium text-sm">Preview of {fileName ?? 'file'}</div>
          <ul className="text-sm grid gap-1 sm:grid-cols-2">
            <li>
              New entries: <span className="mono">{preview.added.length}</span>
            </li>
            <li>
              Identical to existing: <span className="mono">{preview.identical.length}</span>
            </li>
            <li>
              Conflicts (same id, different content): <span className="mono">{preview.conflicts.length}</span>
            </li>
            <li>
              Invalid and skipped: <span className="mono">{preview.invalid.length}</span>
            </li>
          </ul>
          {preview.conflicts.length ? (
            <details className="text-sm">
              <summary className="cursor-pointer">Conflicts: merge keeps your existing version</summary>
              <ul className="mt-2 space-y-2">
                {preview.conflicts.map(({ existing: cur, incoming: inc }) => (
                  <li key={cur.id} className="grid gap-1 sm:grid-cols-2">
                    <div>
                      <div className="text-xs text-[var(--text-muted)]">Existing (kept)</div>
                      <p className="whitespace-pre-wrap break-words">{cur.text}</p>
                    </div>
                    <div>
                      <div className="text-xs text-[var(--text-muted)]">In file (ignored on merge)</div>
                      <p className="whitespace-pre-wrap break-words">{inc.text}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {preview.invalid.length ? (
            <details className="text-sm">
              <summary className="cursor-pointer">Invalid entries</summary>
              <ul className="mt-2 list-disc pl-5 mono text-xs">
                {preview.invalid.slice(0, 50).map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </details>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="pager-button text-sm" onClick={merge} disabled={!preview.added.length}>
              Merge {preview.added.length} new {preview.added.length === 1 ? 'entry' : 'entries'}
            </button>
            <button type="button" className="pager-button text-sm" onClick={reset}>
              Cancel
            </button>
          </div>
          <div className="space-y-2 border-t border-[var(--border)] pt-2">
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={confirmReplace} onChange={(e) => setConfirmReplace(e.target.checked)} />
              <span>
                Replace my whole notebook with this file. My {existing.length} current{' '}
                {existing.length === 1 ? 'entry' : 'entries'} will be removed.
              </span>
            </label>
            <button
              type="button"
              className="pager-button text-sm text-[var(--negative)]"
              onClick={replace}
              disabled={!confirmReplace}
            >
              Replace notebook
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}

export function NotebookPage({ store: storeProp }: { store?: NotebookStore } = {}) {
  const { entries, problem, store } = useNotebook(storeProp)
  const [params, setParams] = useSearchParams()
  const filters = {
    q: params.get('q') ?? '',
    kind: params.get('kind') ?? '',
    origin: params.get('origin') ?? '',
    tag: params.get('tag') ?? '',
    target: params.get('target') ?? '',
  }
  const setParam = useCallback(
    (key: string, value: string) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (value) next.set(key, value)
          else next.delete(key)
          return next
        },
        { replace: true },
      ),
    [setParams],
  )
  const onSearch = useCallback((v: string) => setParam('q', v.trim()), [setParam])
  const onReset = useCallback(
    () =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          for (const k of FILTER_KEYS) next.delete(k)
          return next
        },
        { replace: true },
      ),
    [setParams],
  )

  const tags = useMemo(() => [...new Set(entries.flatMap((e) => e.tags))].sort(), [entries])
  const shown = [...entries].filter((e) => matches(e, filters)).sort((a, b) => b.updated_at.localeCompare(a.updated_at))

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold">Research notebook</h2>
        <NotebookDisclaimer />
      </header>
      <StorageProblem problem={problem} />

      <section className="panel flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm">
          <span className="mono">{entries.length}</span> {entries.length === 1 ? 'entry' : 'entries'} in this browser
        </div>
        <button
          type="button"
          className="pager-button text-sm"
          disabled={!entries.length}
          onClick={() => download(`kalshi-edge-lab-notebook-${new Date().toISOString().slice(0, 10)}.json`, exportNotebook(entries))}
        >
          Export JSON
        </button>
      </section>

      <FilterBar
        search={{ label: 'Search notes', value: filters.q, placeholder: 'Text, tag or target' }}
        onSearch={onSearch}
        selects={[
          { id: 'kind', label: 'Type', value: filters.kind, options: [{ value: 'note', label: 'Notes' }, { value: 'question', label: 'Questions' }] },
          {
            id: 'origin',
            label: 'Origin',
            value: filters.origin,
            options: [
              { value: 'personal', label: 'Personal' },
              { value: 'ai_commentary', label: 'AI commentary' },
            ],
          },
          { id: 'tag', label: 'Tag', value: filters.tag, options: tags.map((t) => ({ value: t, label: `#${t}` })) },
          {
            id: 'target',
            label: 'Attached to',
            value: filters.target,
            options: [
              { value: 'experiment', label: 'Experiments' },
              { value: 'event', label: 'Events' },
            ],
          },
        ]}
        onChange={setParam}
        onReset={onReset}
      />

      <section className="panel space-y-3" aria-labelledby="notebook-entries-heading">
        <h3 id="notebook-entries-heading" className="font-semibold">
          Entries
        </h3>
        <NoteList
          entries={shown}
          store={store}
          showTarget
          empty={
            entries.length
              ? 'No entries match these filters.'
              : 'No notes yet. Add notes from an experiment page or an event page.'
          }
        />
      </section>

      <ImportPanel store={store} existing={entries} />
    </div>
  )
}

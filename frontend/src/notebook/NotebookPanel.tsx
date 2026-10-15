import { useId, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { fmtLocal } from '../lib/time'
import {
  MAX_TEXT_LENGTH,
  parseTagText,
  targetKey,
  type NoteKind,
  type NoteOrigin,
  type NoteTarget,
  type NotebookEntry,
  type NotebookStore,
} from './store'
import { targetHref, targetLabel } from './targets'
import { useNotebook } from './useNotebook'

export function NotebookDisclaimer() {
  return (
    <p className="text-xs text-[var(--text-muted)]">
      Personal notes are stored in this browser only. They are not included in collector backups, are never sent to the
      API, and are not research evidence. Use Export on the notebook page to keep a copy. Text marked as AI commentary
      was pasted by you; this app does not call any AI model.
    </p>
  )
}

export function StorageProblem({ problem }: { problem: string | null }) {
  if (!problem) return null
  return (
    <p className="warning-banner text-sm" role="status">
      {problem}
    </p>
  )
}

export function OriginBadge({ origin }: { origin: NoteOrigin }) {
  return origin === 'ai_commentary' ? (
    <span className="badge badge-warning" title="Pasted AI-generated text. Not research evidence.">
      AI commentary · not evidence
    </span>
  ) : (
    <span className="badge badge-info" title="Your own note. Not research evidence.">
      Personal note
    </span>
  )
}

export function KindBadge({ kind }: { kind: NoteKind }) {
  return <span className="badge badge-info">{kind === 'question' ? 'Question' : 'Note'}</span>
}

function NoteItem({ entry, store, showTarget }: { entry: NotebookEntry; store: NotebookStore; showTarget: boolean }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(entry.text)
  const [tags, setTags] = useState(entry.tags.join(', '))
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const editId = useId()

  const save = () => {
    if (!text.trim()) {
      setError('Text cannot be empty.')
      return
    }
    if (store.update(entry.id, { text, tags: parseTagText(tags) })) {
      setEditing(false)
      setError(null)
    } else setError('This note could not be saved.')
  }

  return (
    <li className="rounded-lg border border-[var(--border)] p-3 space-y-2" data-testid="notebook-entry">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <KindBadge kind={entry.kind} />
        <OriginBadge origin={entry.origin} />
        {showTarget ? (
          <Link to={targetHref(entry.target)} className="underline underline-offset-2 break-all">
            {targetLabel(entry.target)}
          </Link>
        ) : null}
        <span className="text-[var(--text-muted)]">
          {fmtLocal(entry.created_at)}
          {entry.updated_at !== entry.created_at ? ` · edited ${fmtLocal(entry.updated_at)}` : ''}
        </span>
      </div>
      {editing ? (
        <div className="space-y-2">
          <label htmlFor={`${editId}-text`} className="sr-only">
            Edit text
          </label>
          <textarea
            id={`${editId}-text`}
            className="filter-control w-full min-h-[5rem]"
            value={text}
            maxLength={MAX_TEXT_LENGTH}
            onChange={(e) => setText(e.target.value)}
          />
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Tags (comma separated)
            <input className="filter-control" value={tags} onChange={(e) => setTags(e.target.value)} />
          </label>
          {error ? <p className="text-sm text-[var(--negative)]">{error}</p> : null}
          <div className="flex gap-2">
            <button type="button" className="pager-button text-sm" onClick={save}>
              Save
            </button>
            <button
              type="button"
              className="pager-button text-sm"
              onClick={() => {
                setEditing(false)
                setText(entry.text)
                setTags(entry.tags.join(', '))
                setError(null)
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <p className="text-sm whitespace-pre-wrap break-words">{entry.text}</p>
          {entry.tags.length ? (
            <div className="flex flex-wrap gap-1">
              {entry.tags.map((t) => (
                <span key={t} className="mono text-xs rounded border border-[var(--border)] px-1.5">
                  #{t}
                </span>
              ))}
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="pager-button text-xs" onClick={() => setEditing(true)}>
              Edit
            </button>
            {confirmDelete ? (
              <>
                <button type="button" className="pager-button text-xs text-[var(--negative)]" onClick={() => store.remove(entry.id)}>
                  Confirm delete
                </button>
                <button type="button" className="pager-button text-xs" onClick={() => setConfirmDelete(false)}>
                  Keep
                </button>
              </>
            ) : (
              <button type="button" className="pager-button text-xs" onClick={() => setConfirmDelete(true)}>
                Delete
              </button>
            )}
          </div>
        </>
      )}
    </li>
  )
}

export function NoteList({
  entries,
  store,
  showTarget = false,
  empty,
}: {
  entries: readonly NotebookEntry[]
  store: NotebookStore
  showTarget?: boolean
  empty: string
}) {
  if (!entries.length) return <p className="text-sm text-[var(--text-muted)]">{empty}</p>
  return (
    <ul className="space-y-2">
      {entries.map((e) => (
        <NoteItem key={e.id} entry={e} store={store} showTarget={showTarget} />
      ))}
    </ul>
  )
}

/** Notes, questions and tags attached to one experiment or event. */
export function NotebookPanel({ target, title = 'My notes', store: storeProp }: { target: NoteTarget; title?: string; store?: NotebookStore }) {
  const { entries, problem, store } = useNotebook(storeProp)
  const key = targetKey(target)
  const mine = entries.filter((e) => targetKey(e.target) === key)
  const [kind, setKind] = useState<NoteKind>('note')
  const [origin, setOrigin] = useState<NoteOrigin>('personal')
  const [text, setText] = useState('')
  const [tags, setTags] = useState('')
  const [error, setError] = useState<string | null>(null)
  const formId = useId()

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!text.trim()) {
      setError('Write something first.')
      return
    }
    const added = store.add({ kind, origin, text, tags: parseTagText(tags), target })
    if (!added) {
      setError('This note could not be added.')
      return
    }
    setText('')
    setTags('')
    setError(null)
  }

  return (
    <section className="panel space-y-3" aria-labelledby={`${formId}-heading`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={`${formId}-heading`} className="font-semibold">
          {title}
        </h3>
        <Link to="/research/notebook" className="text-xs underline underline-offset-2">
          All notes
        </Link>
      </div>
      <NotebookDisclaimer />
      <StorageProblem problem={problem} />
      <NoteList entries={mine} store={store} empty="No notes here yet." />
      <form onSubmit={submit} className="space-y-2 border-t border-[var(--border)] pt-3">
        <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Type
            <select className="filter-control" value={kind} onChange={(e) => setKind(e.target.value as NoteKind)}>
              <option value="note">Note</option>
              <option value="question">Question</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Origin
            <select className="filter-control" value={origin} onChange={(e) => setOrigin(e.target.value as NoteOrigin)}>
              <option value="personal">My own note</option>
              <option value="ai_commentary">Pasted AI commentary</option>
            </select>
          </label>
        </div>
        <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
          Text
          <textarea
            className="filter-control w-full min-h-[5rem] text-sm text-[var(--text)]"
            value={text}
            maxLength={MAX_TEXT_LENGTH}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
          Tags (comma separated)
          <input className="filter-control" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="e.g. calibration, follow-up" />
        </label>
        {error ? <p className="text-sm text-[var(--negative)]">{error}</p> : null}
        <button type="submit" className="pager-button text-sm">
          Add {kind === 'question' ? 'question' : 'note'}
        </button>
      </form>
    </section>
  )
}

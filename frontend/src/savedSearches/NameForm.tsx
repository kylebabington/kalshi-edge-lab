import { useId, useState, type FormEvent } from 'react'
import { MAX_NAME_LENGTH } from './filters'

type Props = {
  label: string
  initial?: string
  submitLabel: string
  onSubmit: (name: string) => boolean | void
  onCancel: () => void
}

/** Inline name editor — no window.prompt. */
export function NameForm({ label, initial = '', submitLabel, onSubmit, onCancel }: Props) {
  const id = useId()
  const [name, setName] = useState(initial)
  const [error, setError] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) {
      setError('Enter a name.')
      return
    }
    if (trimmed.length > MAX_NAME_LENGTH) {
      setError(`Name must be at most ${MAX_NAME_LENGTH} characters.`)
      return
    }
    const ok = onSubmit(trimmed)
    if (ok === false) setError('That name could not be saved.')
    else setError(null)
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-end">
      <label htmlFor={id} className="flex flex-col gap-1 text-xs text-[var(--text-muted)] flex-1 min-w-[10rem]">
        {label}
        <input
          id={id}
          className="filter-control"
          value={name}
          maxLength={MAX_NAME_LENGTH}
          autoFocus
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <div className="flex flex-wrap gap-2">
        <button type="submit" className="pager-button text-sm">
          {submitLabel}
        </button>
        <button type="button" className="pager-button text-sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {error ? (
        <p className="text-sm text-[var(--negative)] w-full" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  )
}

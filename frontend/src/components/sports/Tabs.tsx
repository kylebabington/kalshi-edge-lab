import { useRef, type KeyboardEvent } from 'react'

type Tab = { id: string; label: string }

type Props = {
  tabs: Tab[]
  active: string
  onChange: (id: string) => void
  label: string
  idPrefix: string
}

export function Tabs({ tabs, active, onChange, label, idPrefix }: Props) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
    else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = tabs.length - 1
    else return
    event.preventDefault()
    onChange(tabs[next].id)
    refs.current[next]?.focus()
  }

  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-2 border-b border-[var(--border)] pb-2 max-w-full">
      {tabs.map((tab, index) => {
        const selected = tab.id === active
        return (
          <button
            key={tab.id}
            ref={(el) => {
              refs.current[index] = el
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(e) => onKeyDown(e, index)}
            className={`rounded-lg px-3 py-2 text-sm min-h-[2.25rem] transition ${
              selected
                ? 'bg-[var(--surface-raised)] text-[var(--text)] border border-[var(--border)]'
                : 'text-[var(--text-muted)] hover:text-[var(--text)] border border-transparent'
            }`}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

import type { ReactNode } from 'react'

/** Collapsed collector detail. The summary keeps the health badges visible so a warning is not hidden while closed. */
export function CollectorDisclosure({ badges, children }: { badges?: ReactNode; children: ReactNode }) {
  return (
    <details className="group">
      <summary className="cursor-pointer min-h-[2.25rem] flex flex-wrap items-center gap-2 font-medium">
        <span aria-hidden="true" className="text-[var(--text-muted)] transition-transform group-open:rotate-90">
          ▸
        </span>
        Collector status
        {badges}
      </summary>
      <div className="mt-3 space-y-3">{children}</div>
    </details>
  )
}

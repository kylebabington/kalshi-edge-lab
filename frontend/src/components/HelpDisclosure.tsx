import type { ReactNode } from 'react'

/** Short pages stay quiet; longer research context stays one click away. */
export function HelpDisclosure({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="text-sm text-[var(--text-muted)]">
      <summary className="cursor-pointer text-[var(--accent)] w-fit min-h-[2.25rem] inline-flex items-center">
        {title}
      </summary>
      <div className="mt-2 max-w-3xl space-y-1">{children}</div>
    </details>
  )
}

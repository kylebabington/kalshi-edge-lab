import { fmtTime } from './format'

type Props = {
  generatedAt?: string | null
  lastCaptureAt?: string | null
  loadedAt: Date | null
  source?: string
}

export function DataStamp({ generatedAt, lastCaptureAt, loadedAt, source }: Props) {
  return (
    <p className="text-xs text-[var(--text-muted)] mono" aria-label="Data timestamps">
      {lastCaptureAt !== undefined ? <>Latest saved capture: {fmtTime(lastCaptureAt)} · </> : null}
      Read by API: {fmtTime(generatedAt)} · Loaded in UI: {loadedAt ? fmtTime(loadedAt.toISOString()) : '—'}
      {source ? <> · Source: {source}</> : null}
    </p>
  )
}

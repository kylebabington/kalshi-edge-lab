import { fmtLocal } from '../../lib/time'
import type { StatusRecord } from '../../types/weather'
import type { ExperimentCard, ExperimentStatus, Period, Sample } from '../../types/research'

/** Protocol split boundaries are UTC midnights; shifting them into local time would show the previous day. */
function fmtDateish(value: string | null | undefined): string {
  if (!value) return '…'
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  if (/^\d{4}-\d{2}-\d{2}T00:00:00(\.0+)?(Z|\+00:00)$/.test(value)) return `${value.slice(0, 10)} UTC`
  return fmtLocal(value)
}

export function periodText(period: Period | null | undefined, reason?: string | null): string {
  if (!period || (!period.start && !period.end)) return reason ? `unknown (${reason})` : 'unknown'
  return `${fmtDateish(period.start)} – ${period.end ? fmtDateish(period.end) : 'ongoing'}`
}

export function sampleValue(s: Sample): string {
  if (s.value == null) return 'unavailable'
  return typeof s.value === 'number' ? s.value.toLocaleString() : String(s.value)
}

export function statusRecords(status: ExperimentStatus): StatusRecord[] {
  return [
    ...(status.implementation ? [status.implementation] : []),
    ...(status.evidence ?? []),
    ...(status.operations ? [status.operations] : []),
    ...(status.integrity ? [status.integrity] : []),
    ...(status.policy ?? []),
  ]
}

export type CatalogFilters = { q: string; designation: string; mode: string; status: string }

export function filterExperiments(cards: ExperimentCard[], f: CatalogFilters): ExperimentCard[] {
  const q = f.q.trim().toLowerCase()
  return cards.filter((c) => {
    if (f.designation && c.designation !== f.designation) return false
    if (f.mode && !c.modes.includes(f.mode as ExperimentCard['modes'][number])) return false
    if (f.status === 'available' && !c.results_available) return false
    if (f.status === 'unavailable' && c.results_available) return false
    if (q) {
      const hay = [c.id, c.title, c.protocol_id ?? '', c.version ?? '', c.category].join(' ').toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
}

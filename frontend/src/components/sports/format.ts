export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short',
  })
}

export function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.toISOString().slice(0, 16).replace('T', ' ')} UTC`
}

export function fmtProb(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return '—'
  return `${(p * 100).toFixed(1)}%`
}

export function fmtNum(v: number | null | undefined, digits = 4): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}

export function fmtSigned(v: number | null | undefined, digits = 4): string {
  if (v == null || Number.isNaN(v)) return '—'
  const s = v.toFixed(digits)
  return v > 0 ? `+${s}` : s
}

export function fmtAge(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  const m = Math.round(seconds / 60)
  if (m < 60) return `${m} min`
  return `${Math.floor(m / 60)} h ${m % 60} min`
}

export function fmtMinutes(minutes: number | null | undefined): string {
  if (minutes == null) return '—'
  return fmtAge(minutes * 60)
}

export function label(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replaceAll('_', ' ')
}

export const VIEW_LABELS: Record<string, string> = {
  upcoming: 'Upcoming (captured)',
  awaiting_settlement: 'Awaiting settlement',
  settled: 'Settled',
  missed: 'Missed window',
}

export const CANDIDATE_LABELS: Record<string, string> = {
  frozen: 'Frozen',
  calibrated: 'Calibrated',
  market: 'Market reference',
  blend: 'Blend',
}

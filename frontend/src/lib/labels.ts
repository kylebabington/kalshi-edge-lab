import type { SportsEventItem } from '../types/sports'

const FAMILY_LABELS: Record<string, string> = {
  game_winner: 'Game winner',
  spread: 'Point spread',
  total: 'Total points',
  team_total: 'Team total',
  segment: 'Period / set',
  score_props: 'Score props',
}

const COHORT_LABELS: Record<string, string> = {
  strict: 'Strict',
  exploratory: 'Exploratory',
  kalshi_settlement_only: 'Kalshi settlement only',
}

export function titleCase(value: string): string {
  return value
    .replaceAll('_', ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export function familyLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return FAMILY_LABELS[value] ?? titleCase(value)
}

export function cohortLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return COHORT_LABELS[value] ?? titleCase(value)
}

export function sportLabel(value: string | null | undefined): string {
  return value ? titleCase(value) : 'Unknown sport'
}

/** Competition names come from the API; Kalshi-only names carry a "(KXSERIES)" suffix that is dropped here. */
export function competitionLabel(code: string, name?: string | null): string {
  const n = (name ?? '').replace(/\s*\(KX[A-Z0-9]+\)\s*$/, '').replace(/\s+/g, ' ').trim()
  if (n) return n
  return titleCase(code.replace(/^k_kx/, ''))
}

export function sportsEventTitle(e: Pick<SportsEventItem, 'title' | 'participants' | 'cluster'>): string {
  if (e.title) return e.title
  if (e.participants.length) return e.participants.join(' vs ')
  return e.cluster
}

export const WEATHER_VIEW_LABELS: Record<string, string> = {
  upcoming: 'Upcoming / in progress',
  awaiting_settlement: 'Awaiting settlement',
  settled: 'Settlement confirmed',
}

export const SPORTS_VIEW_LABELS: Record<string, string> = {
  upcoming: 'Upcoming (captured)',
  awaiting_settlement: 'Awaiting settlement',
  settled: 'Settled',
  missed: 'Missed window',
}

import { CANDIDATES, type CandidateName, type ContractRow } from '../types/sports'

/** Candidates compared against the market reference (the reference itself is excluded). */
export const COMPARED: CandidateName[] = CANDIDATES.filter((c) => c !== 'market')

function prob(row: ContractRow, c: CandidateName): number | null {
  const v = row.candidates?.[c]
  return v && v.available && typeof v.p === 'number' && Number.isFinite(v.p) ? v.p : null
}

/**
 * Probability difference (candidate minus market reference) on one contract, or null unless both were saved for
 * this contract. A probability difference is not an executable edge: the reference is a candle mid, not a quote
 * that could be traded at.
 */
export function diffVsMarket(row: ContractRow, c: CandidateName): number | null {
  const a = prob(row, c)
  const m = prob(row, 'market')
  return a == null || m == null ? null : a - m
}

export type UnavailableTally = { reason: string; contracts: number }[]

export type FamilyComparison = {
  predicted: number
  matched: Record<CandidateName, number>
  meanAbsDiff: Record<CandidateName, number | null>
  unavailable: Record<CandidateName, UnavailableTally>
}

function cleanReason(reason: string | undefined): string {
  return (reason ?? 'no reason recorded').replace(/^unavailable(:\s*|\s+)/i, '') || 'no reason recorded'
}

/** Per-candidate counts over one family: contracts where the candidate and the market reference were both saved,
 * their mean absolute probability difference, and why each candidate was unavailable elsewhere. */
export function compareFamily(contracts: ContractRow[]): FamilyComparison {
  const predicted = contracts.filter((r) => r.candidates != null)
  const matched = {} as Record<CandidateName, number>
  const meanAbsDiff = {} as Record<CandidateName, number | null>
  const unavailable = {} as Record<CandidateName, UnavailableTally>
  for (const c of CANDIDATES) {
    const diffs = c === 'market' ? [] : predicted.map((r) => diffVsMarket(r, c)).filter((d): d is number => d != null)
    matched[c] = c === 'market' ? predicted.filter((r) => prob(r, 'market') != null).length : diffs.length
    meanAbsDiff[c] = diffs.length ? diffs.reduce((s, d) => s + Math.abs(d), 0) / diffs.length : null
    const reasons = new Map<string, number>()
    for (const r of predicted) {
      const v = r.candidates![c]
      if (!v.available) reasons.set(cleanReason(v.reason), (reasons.get(cleanReason(v.reason)) ?? 0) + 1)
    }
    unavailable[c] = [...reasons.entries()]
      .map(([reason, n]) => ({ reason, contracts: n }))
      .sort((a, b) => b.contracts - a.contracts || a.reason.localeCompare(b.reason))
  }
  return { predicted: predicted.length, matched, meanAbsDiff, unavailable }
}

export function paginate<T>(items: T[], page: number, size: number): { items: T[]; page: number; pages: number } {
  const pages = Math.max(1, Math.ceil(items.length / size))
  const p = Math.min(Math.max(1, Number.isFinite(page) ? Math.floor(page) : 1), pages)
  return { items: items.slice((p - 1) * size, p * size), page: p, pages }
}

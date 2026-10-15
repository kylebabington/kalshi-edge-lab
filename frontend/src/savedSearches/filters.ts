/**
 * Explorer filter snapshots for saved searches.
 * Page is never stored. Sort `title_asc` is accepted as a legacy alias for `title`.
 */
export type ExploreSavedFilters = {
  category: string
  q: string
  lifecycle: string
  coverage: string
  quote: string
  sort: string
}

export const SORT_VALUES = ['retrieved_desc', 'close_asc', 'close_desc', 'volume_desc', 'title'] as const
export type SortValue = (typeof SORT_VALUES)[number]

export const LIFECYCLE_VALUES = ['open', 'closed', 'final', 'unknown'] as const
export const COVERAGE_VALUES = ['model', 'none'] as const
export const QUOTE_VALUES = ['two_sided', 'one_sided', 'none'] as const

export const MAX_NAME_LENGTH = 120
export const MAX_Q_LENGTH = 200
export const MAX_CATEGORY_LENGTH = 120

/** Map legacy or UI aliases to the API sort value. */
export function canonicalizeSort(raw: string | null | undefined): SortValue {
  const v = (raw || '').trim()
  if (v === 'title_asc' || v === 'title') return 'title'
  if ((SORT_VALUES as readonly string[]).includes(v)) return v as SortValue
  return 'retrieved_desc'
}

function oneOf<T extends string>(value: string, allowed: readonly T[]): T | '' {
  return allowed.includes(value as T) ? (value as T) : ''
}

/** Validate and normalize filter fields. Drops unknown/invalid enum values. */
export function normalizeFilters(raw: Partial<ExploreSavedFilters> | Record<string, unknown> | null | undefined): ExploreSavedFilters {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const category = typeof r.category === 'string' ? r.category.trim().slice(0, MAX_CATEGORY_LENGTH) : ''
  const q = typeof r.q === 'string' ? r.q.trim().slice(0, MAX_Q_LENGTH) : ''
  const lifecycle = typeof r.lifecycle === 'string' ? oneOf(r.lifecycle.trim(), LIFECYCLE_VALUES) : ''
  const coverage = typeof r.coverage === 'string' ? oneOf(r.coverage.trim(), COVERAGE_VALUES) : ''
  const quote = typeof r.quote === 'string' ? oneOf(r.quote.trim(), QUOTE_VALUES) : ''
  const sort = canonicalizeSort(typeof r.sort === 'string' ? r.sort : undefined)
  return { category, q, lifecycle, coverage, quote, sort }
}

export function filtersEqual(a: ExploreSavedFilters, b: ExploreSavedFilters): boolean {
  return (
    a.category === b.category &&
    a.q === b.q &&
    a.lifecycle === b.lifecycle &&
    a.coverage === b.coverage &&
    a.quote === b.quote &&
    a.sort === b.sort
  )
}

/** Stable string for request keys / change detection (excludes page). */
export function filtersKey(f: ExploreSavedFilters): string {
  return JSON.stringify([f.category, f.q, f.lifecycle, f.coverage, f.quote, f.sort])
}

export function filtersFromSearchParams(params: URLSearchParams): ExploreSavedFilters {
  return normalizeFilters({
    category: params.get('category') ?? '',
    q: params.get('q') ?? '',
    lifecycle: params.get('lifecycle') ?? '',
    coverage: params.get('coverage') ?? '',
    quote: params.get('quote') ?? '',
    sort: params.get('sort') || 'retrieved_desc',
  })
}

/** Build URL params for opening a search. Never includes page (caller starts at 1). */
export function filtersToSearchParams(f: ExploreSavedFilters): URLSearchParams {
  const next = new URLSearchParams()
  const n = normalizeFilters(f)
  if (n.category) next.set('category', n.category)
  if (n.q) next.set('q', n.q)
  if (n.lifecycle) next.set('lifecycle', n.lifecycle)
  if (n.coverage) next.set('coverage', n.coverage)
  if (n.quote) next.set('quote', n.quote)
  if (n.sort && n.sort !== 'retrieved_desc') next.set('sort', n.sort)
  return next
}

export function exploreHref(f: ExploreSavedFilters): string {
  const q = filtersToSearchParams(f).toString()
  return q ? `/explore?${q}` : '/explore'
}

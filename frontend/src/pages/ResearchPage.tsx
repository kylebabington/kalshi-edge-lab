import { useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ErrorState, LoadingState } from '../components/StatusBadge'
import { FilterBar } from '../components/sports/FilterBar'
import { useAsync } from '../components/sports/useAsync'
import { ExperimentCard } from '../components/research/ExperimentCard'
import { WeatherModelSummarySection } from '../components/research/WeatherModelSummarySection'
import { fetchResearchExperiments } from '../api/research'
import { filterExperiments, type CatalogFilters } from '../components/research/format'
import { COLLECTION_MODES, MODE_LABELS, type ResearchCategory } from '../types/research'

const FILTER_KEYS = ['q', 'category', 'designation', 'mode', 'status'] as const

const CATEGORY_TITLES: Record<ResearchCategory, string> = { weather: 'Weather experiments', sports: 'Sports experiments' }

function CategorySection({ category, filters }: { category: ResearchCategory; filters: CatalogFilters }) {
  const load = useCallback(() => fetchResearchExperiments(category), [category])
  const state = useAsync(load, `research-experiments:${category}`)
  const headingId = `research-${category}-heading`

  let body
  if (state.loading) body = <LoadingState label={`Loading ${category} experiments…`} />
  else if (state.error)
    body = (
      <ErrorState
        title={`${category === 'weather' ? 'Weather' : 'Sports'} experiments unavailable`}
        message={state.error}
        onRetry={state.reload}
      />
    )
  else {
    const all = state.data?.experiments ?? []
    const shown = filterExperiments(all, filters)
    body = shown.length ? (
      <div className="grid gap-4 lg:grid-cols-2">
        {shown.map((c) => (
          <ExperimentCard key={c.id} card={c} />
        ))}
      </div>
    ) : (
      <div className="panel text-sm text-[var(--text-muted)]">
        {all.length ? `No ${category} experiments match these filters (${all.length} in total).` : `No ${category} experiments reported.`}
      </div>
    )
  }

  return (
    <section className="space-y-3" aria-labelledby={headingId}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={headingId} className="text-lg font-semibold">
          {CATEGORY_TITLES[category]}
        </h3>
        {state.data && !state.loading ? (
          <span className="text-xs text-[var(--text-muted)]">{state.data.experiments.length} registered</span>
        ) : null}
      </div>
      {state.data?.summary_note && !state.loading ? (
        <p className="text-xs text-[var(--text-muted)]">{state.data.summary_note}</p>
      ) : null}
      {body}
    </section>
  )
}

export function ResearchPage() {
  const [params, setParams] = useSearchParams()
  const filters: CatalogFilters = {
    q: params.get('q') ?? '',
    designation: params.get('designation') ?? '',
    mode: params.get('mode') ?? '',
    status: params.get('status') ?? '',
  }
  const category = params.get('category') ?? ''

  const setParam = useCallback(
    (key: string, value: string) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (value) next.set(key, value)
          else next.delete(key)
          return next
        },
        { replace: true },
      )
    },
    [setParams],
  )
  const onSearch = useCallback((value: string) => setParam('q', value.trim()), [setParam])
  const onReset = useCallback(() => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const k of FILTER_KEYS) next.delete(k)
        return next
      },
      { replace: true },
    )
  }, [setParams])

  const showWeather = category !== 'sports'
  const showSports = category !== 'weather'

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1 max-w-3xl">
          <h2 className="text-xl font-semibold">Research</h2>
          <p className="text-sm text-[var(--text-muted)]">
            Weather and Sports experiments and frozen protocols. Every status shows what supports it; when the support is
            missing it shows unknown or unavailable rather than a success. Research only: no bets, no recommendations.
          </p>
        </div>
        <Link to="/research/notebook" className="pager-button text-sm">
          Personal notebook
        </Link>
      </header>

      <FilterBar
        search={{ label: 'Search experiments', value: filters.q, placeholder: 'Title, ID or protocol' }}
        onSearch={onSearch}
        selects={[
          {
            id: 'category',
            label: 'Category',
            value: category,
            options: [
              { value: 'weather', label: 'Weather' },
              { value: 'sports', label: 'Sports' },
            ],
          },
          {
            id: 'designation',
            label: 'Designation',
            value: filters.designation,
            options: [
              { value: 'historical', label: 'Historical' },
              { value: 'prospective', label: 'Prospective' },
            ],
          },
          {
            id: 'mode',
            label: 'Collection mode',
            value: filters.mode,
            options: COLLECTION_MODES.map((m) => ({ value: m, label: MODE_LABELS[m] })),
          },
          {
            id: 'status',
            label: 'Results',
            value: filters.status,
            options: [
              { value: 'available', label: 'Results available' },
              { value: 'unavailable', label: 'No results available' },
            ],
          },
        ]}
        onChange={setParam}
        onReset={onReset}
      />

      {showWeather ? <CategorySection category="weather" filters={filters} /> : null}
      {showSports ? <CategorySection category="sports" filters={filters} /> : null}

      <WeatherModelSummarySection />
    </div>
  )
}

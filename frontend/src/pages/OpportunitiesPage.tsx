import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { fetchOpportunities, opportunityPath } from '../api/opportunities'
import { AIStatusBadge, AttentionScore, EligibilityBadge, FreshnessBadge } from '../components/OpportunityBadges'
import { EmptyState, ErrorState, LoadingState } from '../components/StatusBadge'
import { fmtProb } from '../components/sports/format'
import { useAsync } from '../components/sports/useAsync'
import { fmtConfidence, fmtEdge, fmtUsd } from '../lib/opportunities'
import type { AIResearchStatus, Freshness, Opportunity, OpportunityCategory, OpportunityFilters } from '../types/opportunities'
import { watchId } from '../watchlist/store'
import { useWatchlist } from '../watchlist/useWatchlist'

const CATEGORIES: { id: '' | OpportunityCategory; label: string }[] = [
  { id: '', label: 'All' },
  { id: 'weather', label: 'Weather' },
  { id: 'sports', label: 'Sports' },
]

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | undefined {
  return allowed.find((a) => a === value)
}

export function OpportunitiesPage() {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const { entries } = useWatchlist()

  const category = oneOf(params.get('category'), ['weather', 'sports'] as const)
  const filters: OpportunityFilters = {
    category,
    min_confidence: oneOf(params.get('confidence'), ['LOW', 'MODERATE', 'HIGH'] as const),
    min_edge: params.get('min_edge') ? Number(params.get('min_edge')) : undefined,
    ai_status: oneOf(params.get('ai'), ['researched', 'not_researched', 'stale'] as const) as AIResearchStatus | undefined,
    freshness: oneOf(params.get('fresh'), ['fresh', 'aging', 'stale'] as const) as Freshness | undefined,
    include_blocked: params.get('blocked') === '1',
  }
  const watchOnly = params.get('watchlist') === '1'
  const advancedActive = Boolean(
    filters.min_confidence || filters.min_edge != null || filters.ai_status || filters.freshness || filters.include_blocked || watchOnly,
  )

  const state = useAsync(() => fetchOpportunities(filters), JSON.stringify(filters))

  function setParam(key: string, value: string) {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(key, value)
        else next.delete(key)
        return next
      },
      { replace: true },
    )
  }

  const watched = new Set(entries.map((e) => e.id))
  const items = (state.data?.items ?? []).filter((o) => !watchOnly || watched.has(watchId(o.category, o.event_key)))

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold">Opportunities</h2>
        <p className="text-sm text-[var(--text-muted)]">
          What deserves your attention right now, ranked by attention score. The score is not a probability, not an
          expected value and not a recommendation; edge and EV are shown separately. Prices are saved quotes as of capture.
        </p>
      </header>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div role="group" aria-label="Filter by category" className="flex flex-wrap gap-2">
          {CATEGORIES.map((c) => (
            <button
              key={c.id || 'all'}
              type="button"
              className="pager-button text-sm"
              aria-pressed={(category ?? '') === c.id}
              onClick={() => setParam('category', c.id)}
            >
              {c.label}
            </button>
          ))}
        </div>
        {state.data ? (
          <div className="text-xs text-[var(--text-muted)]">
            AI research: {state.data.ai.enabled ? `${state.data.ai.provider} · ${state.data.ai.model}` : 'disabled'} · manual only
          </div>
        ) : null}
      </div>

      <details className="panel text-sm" open={advancedActive}>
        <summary className="cursor-pointer text-[var(--text-muted)]">More filters{advancedActive ? ' (active)' : ''}</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Minimum confidence
            <select className="filter-control" value={filters.min_confidence ?? ''} onChange={(e) => setParam('confidence', e.target.value)}>
              <option value="">Any</option>
              <option value="LOW">LOW or better</option>
              <option value="MODERATE">MODERATE or better</option>
              <option value="HIGH">HIGH</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Minimum edge
            <select className="filter-control" value={params.get('min_edge') ?? ''} onChange={(e) => setParam('min_edge', e.target.value)}>
              <option value="">Any</option>
              <option value="0.05">5 pt</option>
              <option value="0.1">10 pt</option>
              <option value="0.2">20 pt</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            AI research
            <select className="filter-control" value={filters.ai_status ?? ''} onChange={(e) => setParam('ai', e.target.value)}>
              <option value="">Any</option>
              <option value="researched">Researched</option>
              <option value="not_researched">Not researched</option>
              <option value="stale">Research stale</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
            Data freshness
            <select className="filter-control" value={filters.freshness ?? ''} onChange={(e) => setParam('fresh', e.target.value)}>
              <option value="">Any</option>
              <option value="fresh">Fresh</option>
              <option value="aging">Aging</option>
              <option value="stale">Stale (blocked)</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={watchOnly} onChange={(e) => setParam('watchlist', e.target.checked ? '1' : '')} />
            Watchlist events only
          </label>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={Boolean(filters.include_blocked)} onChange={(e) => setParam('blocked', e.target.checked ? '1' : '')} />
            Show blocked (diagnostics)
          </label>
        </div>
      </details>

      {state.loading && !state.data ? (
        <LoadingState label="Ranking opportunities…" />
      ) : state.error ? (
        <ErrorState message={state.error} onRetry={state.reload} />
      ) : items.length === 0 ? (
        <EmptyState
          title="Nothing to look at right now"
          detail="No current market matches these filters. Blocked markets are hidden unless you choose to show them."
        />
      ) : (
        <div className="panel overflow-x-auto p-0">
          <table className="data-table w-full text-sm" aria-label="Ranked opportunities">
            <thead>
              <tr>
                <th>Market</th>
                <th>Attention</th>
                <th>Model</th>
                <th>Market</th>
                <th>Edge</th>
                <th>EV / contract</th>
                <th>Confidence</th>
                <th>Freshness</th>
                <th>AI</th>
              </tr>
            </thead>
            <tbody>
              {items.map((o) => (
                <OpportunityRow key={o.id} o={o} from={location.pathname + location.search} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {state.data ? (
        <p className="text-xs text-[var(--text-muted)]">
          {items.length} shown · {state.data.counts.by_quality.BLOCKED ?? 0} blocked of {state.data.counts.total} current
          markets. Research only; nothing here places or recommends trades.
        </p>
      ) : null}
    </div>
  )
}

function OpportunityRow({ o, from }: { o: Opportunity; from: string }) {
  return (
    <tr>
      <td className="min-w-[16rem]">
        <div className="text-xs uppercase tracking-[0.12em] text-[var(--text-muted)]">{o.category}</div>
        <Link to={opportunityPath(o.ticker)} state={{ from }} className="font-medium hover:underline" aria-label={`Open ${o.title}`}>
          {o.title}
        </Link>
        <div className="mt-1 flex flex-wrap gap-1">
          <EligibilityBadge status={o.eligibility.status} />
          {o.warnings.length ? (
            <span className="text-xs text-[var(--warning)]" title={o.warnings.join('\n')}>
              {o.warnings.length} warning{o.warnings.length === 1 ? '' : 's'}
            </span>
          ) : null}
        </div>
      </td>
      <td>
        <AttentionScore score={o.attention_score} />
      </td>
      <td className="mono">{fmtProb(o.model_probability)}</td>
      <td className="mono">{fmtProb(o.market_probability)}</td>
      <td className="mono">
        {fmtEdge(o.probability_edge)}
        {o.side ? <span className="text-xs text-[var(--text-muted)]"> {o.side}</span> : null}
      </td>
      <td className="mono text-xs">
        <div>before fees {fmtUsd(o.expected_value_before_fees)}</div>
        <div>after fees {fmtUsd(o.expected_value_after_fees)}</div>
      </td>
      <td className="text-xs">{fmtConfidence(o)}</td>
      <td>
        <FreshnessBadge status={o.data_freshness_status} />
      </td>
      <td>
        <AIStatusBadge status={o.ai_research_status} />
      </td>
    </tr>
  )
}

import { useCallback, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  eventPath,
  fetchSportsCollector,
  fetchSportsCoverage,
  fetchSportsEvents,
  fetchSportsHistorical,
  fetchSportsResults,
} from '../api/sports'
import { EmptyState, ErrorState, LoadingState, StatusBadge } from '../components/StatusBadge'
import { CollectorStatusPanel } from '../components/sports/CollectorStatusPanel'
import { DataStamp } from '../components/sports/DataStamp'
import { FilterBar, Pager } from '../components/sports/FilterBar'
import { SportsEventCard } from '../components/sports/SportsEventCard'
import { Tabs } from '../components/sports/Tabs'
import { CANDIDATE_LABELS, VIEW_LABELS, fmtNum, fmtSigned, label } from '../components/sports/format'
import { useAsync } from '../components/sports/useAsync'
import { CANDIDATES, type MetricBlock, type SportsResults } from '../types/sports'

const TABS = [
  { id: 'events', label: 'Events' },
  { id: 'results', label: 'Results' },
  { id: 'coverage', label: 'Coverage' },
  { id: 'historical', label: 'Historical Research' },
]

type Params = ReturnType<typeof useSearchParams>[0]
type SetParam = (updates: Record<string, string>) => void

function TabPanel({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div role="tabpanel" id={`sports-panel-${id}`} aria-labelledby={`sports-tab-${id}`} className="space-y-4">
      {children}
    </div>
  )
}

export function SportsPage() {
  const [params, setParams] = useSearchParams()
  const tab = TABS.some((t) => t.id === params.get('tab')) ? (params.get('tab') as string) : 'events'
  const collector = useAsync(fetchSportsCollector, 'collector')

  const setParam: SetParam = useCallback(
    (updates) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          for (const [k, v] of Object.entries(updates)) {
            if (v) next.set(k, v)
            else next.delete(k)
          }
          if (!('page' in updates)) next.delete('page')
          return next
        },
        { replace: true },
      )
    },
    [setParams],
  )

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold">Sports</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Saved captures of the frozen <span className="mono">sports_phase2_capture_v2</span> candidates, taken
          inside each event's capture window and scored only after Kalshi settles the market. These are saved
          snapshots, not a live feed. Research only — no bets, no recommendations.
        </p>
      </header>
      <CollectorStatusPanel loading={collector.loading} error={collector.error} status={collector.data} />
      <Tabs
        tabs={TABS}
        active={tab}
        label="Sports views"
        idPrefix="sports"
        onChange={(id) => setParams(new URLSearchParams({ tab: id }), { replace: true })}
      />
      <TabPanel id={tab}>
        {tab === 'events' ? <EventsTab params={params} setParam={setParam} /> : null}
        {tab === 'results' ? <ResultsTab params={params} setParam={setParam} /> : null}
        {tab === 'coverage' ? <CoverageTab params={params} setParam={setParam} /> : null}
        {tab === 'historical' ? <HistoricalTab params={params} setParam={setParam} /> : null}
      </TabPanel>
    </div>
  )
}

function opts(values: string[] | undefined) {
  return (values ?? []).map((value) => ({ value }))
}

// ---------------------------------------------------------------- Events

export function EventsTab({ params, setParam }: { params: Params; setParam: SetParam }) {
  const filters = {
    sport: params.get('sport') ?? '',
    competition: params.get('competition') ?? '',
    family: params.get('family') ?? '',
    cohort: params.get('cohort') ?? '',
    status: params.get('status') ?? '',
    collection: params.get('collection') ?? '',
    q: params.get('q') ?? '',
    page: Number(params.get('page') ?? '1') || 1,
  }
  const key = JSON.stringify(filters)
  const state = useAsync(() => fetchSportsEvents({ ...filters, page_size: 24 }), key)
  const data = state.data
  const onSearch = useCallback((q: string) => setParam({ q }), [setParam])

  return (
    <>
      <FilterBar
        selects={[
          { id: 'sport', label: 'Sport', value: filters.sport, options: opts(data?.facets.sport) },
          { id: 'competition', label: 'Competition', value: filters.competition, options: opts(data?.facets.competition) },
          { id: 'family', label: 'Contract family', value: filters.family, options: opts(data?.facets.families) },
          { id: 'cohort', label: 'Cohort', value: filters.cohort, options: opts(data?.facets.cohorts) },
          {
            id: 'status',
            label: 'Capture / settlement',
            value: filters.status,
            allLabel: 'All saved records',
            options: [
              { value: 'captured', label: 'Captured (any settlement state)' },
              ...(['upcoming', 'awaiting_settlement', 'settled', 'missed'] as const).map((v) => ({
                value: v,
                label: `${VIEW_LABELS[v]}${data ? ` (${data.views[v] ?? 0})` : ''}`,
              })),
            ],
          },
          {
            id: 'collection',
            label: 'Collection mode',
            value: filters.collection,
            allLabel: 'All modes',
            options: [
              { value: 'automatic', label: 'Automatic (scheduled)' },
              { value: 'manual_pilot', label: 'Manual pilot' },
              { value: 'collector_manual', label: 'Collector started by hand' },
            ],
          },
        ]}
        onChange={(id, value) => setParam({ [id]: value })}
        search={{ label: 'Search participants or events', value: filters.q, placeholder: 'Team, player, ticker…' }}
        onSearch={onSearch}
      />
      {data ? <DataStamp generatedAt={data.generated_at} loadedAt={state.loadedAt} source={data.source} /> : null}
      <p className="text-xs text-[var(--text-muted)]">
        Upcoming captured events are listed first, then events awaiting settlement, settled events and missed
        windows. A missed window is never back-filled.
      </p>
      {state.loading ? <LoadingState label="Loading saved captures…" /> : null}
      {state.error ? <ErrorState message={state.error} /> : null}
      {data && !state.loading ? (
        data.artifacts.capture_records === 'missing' ? (
          <EmptyState
            title="No saved sports captures on this machine"
            detail="The capture record directory is missing or empty. Captures appear here after the collector records an event inside its window."
          />
        ) : data.items.length === 0 ? (
          <EmptyState title="No saved captures match these filters" detail="Clear a filter or search term to see more events." />
        ) : (
          <>
            <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {data.items.map((event) => (
                <li key={event.event_id}>
                  <Link to={eventPath(event.event_id)} className="block h-full rounded-xl" aria-label={`Open ${event.title ?? event.event_id}`}>
                    <SportsEventCard event={event} />
                  </Link>
                </li>
              ))}
            </ul>
            <Pager page={data.page} pages={data.pages} total={data.total} noun="events" onPage={(p) => setParam({ page: String(p) })} />
          </>
        )
      ) : null}
    </>
  )
}

// ---------------------------------------------------------------- Results

function MetricTables({ block, caption }: { block: MetricBlock; caption: string }) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <div className="overflow-x-auto">
        <table className="data-table">
          <caption>{caption}: standalone metrics (each candidate's own sample)</caption>
          <thead>
            <tr>
              <th scope="col">Candidate</th>
              <th scope="col">Contracts</th>
              <th scope="col">Events</th>
              <th scope="col">Brier</th>
              <th scope="col">Log loss</th>
            </tr>
          </thead>
          <tbody>
            {block.candidates.map((c) => (
              <tr key={c.candidate}>
                <th scope="row">{CANDIDATE_LABELS[c.candidate]}</th>
                <td className="mono">{c.contracts}</td>
                <td className="mono">{c.events}</td>
                <td className="mono">{c.contracts ? fmtNum(c.brier) : 'no sample'}</td>
                <td className="mono">{c.contracts ? fmtNum(c.log_loss) : 'no sample'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="overflow-x-auto">
        <table className="data-table">
          <caption>{caption}: paired comparisons on common contracts</caption>
          <thead>
            <tr>
              <th scope="col">Candidate − reference</th>
              <th scope="col">Matched contracts</th>
              <th scope="col">Unique events</th>
              <th scope="col">Δ Brier</th>
              <th scope="col">Δ Log loss</th>
            </tr>
          </thead>
          <tbody>
            {block.comparisons.map((p) => (
              <tr key={`${p.candidate}-${p.reference}`}>
                <th scope="row">
                  {CANDIDATE_LABELS[p.candidate]} − {CANDIDATE_LABELS[p.reference]}
                </th>
                <td className="mono">{p.matched_contracts}</td>
                <td className="mono">{p.unique_events}</td>
                <td className="mono">{p.matched_contracts ? fmtSigned(p.d_brier) : 'no common contracts'}</td>
                <td className="mono">{p.matched_contracts ? fmtSigned(p.d_log_loss) : 'no common contracts'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const GROUPS = [
  { id: 'sport', label: 'Sport' },
  { id: 'competition', label: 'Competition' },
  { id: 'family', label: 'Contract family' },
  { id: 'cohort', label: 'Cohort' },
]

export function ResultsView({
  data,
  groupBy,
  onGroupBy,
}: {
  data: SportsResults
  groupBy: string
  onGroupBy: (g: string) => void
}) {
  const rows = data.breakdown[groupBy] ?? []
  return (
    <div className="space-y-4">
      <div className="warning-banner text-sm space-y-1">
        <div className="font-semibold text-[var(--warning)]">{data.label}</div>
        <p>{data.pilot_note}</p>
        <p>{data.sign_note}</p>
        <p className="text-[var(--text-muted)]">
          {data.paired_note} {data.metrics_note} No ranking of bets, no executable edge and no profitability claims.
        </p>
      </div>
      <p className="text-sm text-[var(--text-muted)]">
        {data.counts.labelled_contracts} labelled contracts in {data.counts.labelled_events} events ·{' '}
        {data.counts.final_scores} final scores · weighting: {data.weighting === 'event' ? 'event-equal' : 'contract-weighted'} ({data.weighting_note})
      </p>
      {data.counts.labelled_contracts === 0 ? (
        <EmptyState title="No settled, labelled contracts for these filters" detail="Results appear after captured contracts settle YES or NO." />
      ) : (
        <MetricTables block={data.overall} caption="All selected" />
      )}
      <section className="space-y-3">
        <fieldset className="flex flex-wrap items-center gap-3 text-sm">
          <legend className="sr-only">Group results by</legend>
          <span className="text-[var(--text-muted)]">Separate by</span>
          {GROUPS.map((g) => (
            <label key={g.id} className="flex items-center gap-1">
              <input type="radio" name="group-by" value={g.id} checked={groupBy === g.id} onChange={() => onGroupBy(g.id)} />
              {g.label}
            </label>
          ))}
        </fieldset>
        {rows.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">No groups with labelled contracts.</p>
        ) : (
          rows.map((r) => (
            <details key={r.group} className="panel">
              <summary className="cursor-pointer font-medium">
                {label(groupBy)}: {r.group}{' '}
                <span className="text-xs text-[var(--text-muted)] mono">
                  ({r.candidates.find((c) => c.candidate === 'frozen')?.contracts ?? 0} frozen contracts)
                </span>
              </summary>
              <div className="mt-3">
                <MetricTables block={r} caption={r.group} />
              </div>
            </details>
          ))
        )}
      </section>
    </div>
  )
}

export function ResultsTab({ params, setParam }: { params: Params; setParam: SetParam }) {
  const filters = {
    sport: params.get('sport') ?? '',
    competition: params.get('competition') ?? '',
    family: params.get('family') ?? '',
    cohort: params.get('cohort') ?? '',
    weighting: (params.get('weighting') === 'contract' ? 'contract' : 'event') as 'event' | 'contract',
  }
  const groupBy = params.get('group') ?? 'sport'
  const state = useAsync(() => fetchSportsResults(filters), JSON.stringify(filters))
  const data = state.data
  return (
    <>
      <FilterBar
        selects={[
          { id: 'sport', label: 'Sport', value: filters.sport, options: opts(data?.facets.sport) },
          { id: 'competition', label: 'Competition', value: filters.competition, options: opts(data?.facets.competition) },
          { id: 'family', label: 'Contract family', value: filters.family, options: opts(data?.facets.family) },
          { id: 'cohort', label: 'Cohort', value: filters.cohort, options: opts(data?.facets.cohort) },
        ]}
        onChange={(id, value) => setParam({ [id]: value })}
      >
        <fieldset className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
          <legend>Weighting</legend>
          <div className="flex gap-3 text-sm text-[var(--text)]">
            <label className="flex items-center gap-1">
              <input type="radio" name="weighting" value="event" checked={filters.weighting === 'event'} onChange={() => setParam({ weighting: '' })} />
              Event-equal
            </label>
            <label className="flex items-center gap-1">
              <input type="radio" name="weighting" value="contract" checked={filters.weighting === 'contract'} onChange={() => setParam({ weighting: 'contract' })} />
              Contract-weighted
            </label>
          </div>
        </fieldset>
      </FilterBar>
      {data ? <DataStamp generatedAt={data.generated_at} loadedAt={state.loadedAt} source={data.source} /> : null}
      {state.loading ? <LoadingState label="Loading pilot results…" /> : null}
      {state.error ? <ErrorState message={state.error} /> : null}
      {data && !state.loading ? (
        data.artifacts.settlement_scores === 'missing' ? (
          <EmptyState title="No settlement scores on this machine" detail="Scores appear after the settlement check finalizes captured contracts." />
        ) : (
          <ResultsView data={data} groupBy={groupBy} onGroupBy={(g) => setParam({ group: g })} />
        )
      ) : null}
    </>
  )
}

// ---------------------------------------------------------------- Coverage

export function CoverageTab({ params, setParam }: { params: Params; setParam: SetParam }) {
  const filters = {
    sport: params.get('sport') ?? '',
    competition: params.get('competition') ?? '',
    family: params.get('family') ?? '',
    status: params.get('status') ?? '',
    q: params.get('q') ?? '',
    page: Number(params.get('page') ?? '1') || 1,
  }
  const state = useAsync(() => fetchSportsCoverage({ ...filters, page_size: 50 }), JSON.stringify(filters))
  const data = state.data
  const onSearch = useCallback((q: string) => setParam({ q }), [setParam])
  return (
    <>
      <FilterBar
        selects={[
          { id: 'sport', label: 'Sport', value: filters.sport, options: opts(data?.facets?.sport) },
          { id: 'competition', label: 'Competition', value: filters.competition, options: opts(data?.facets?.competition) },
          { id: 'family', label: 'Contract family', value: filters.family, options: opts(data?.facets?.family) },
          { id: 'status', label: 'Series status', value: filters.status, options: opts(data?.facets?.status) },
        ]}
        onChange={(id, value) => setParam({ [id]: value })}
        search={{ label: 'Search series', value: filters.q, placeholder: 'Ticker or title…' }}
        onSearch={onSearch}
      />
      {data ? <DataStamp generatedAt={data.generated_at} loadedAt={state.loadedAt} source={data.source.series} /> : null}
      {state.loading ? <LoadingState label="Loading coverage…" /> : null}
      {state.error ? <ErrorState message={state.error} /> : null}
      {data && !state.loading ? (
        !data.available ? (
          <EmptyState title="Coverage matrix unavailable" detail={data.missing ?? 'missing'} />
        ) : (
          <div className="space-y-4">
            <section className="panel space-y-2">
              <h3 className="font-semibold">Inventory series</h3>
              <p className="text-sm text-[var(--text-muted)]">
                {data.matrix_series_total} series in the availability matrix; {data.inventory_series_total ?? 'unknown'} in the inventory
                {data.inventory_matches_matrix ? ' (same series set).' : ' — the sets differ.'}
              </p>
              <ul className="flex flex-wrap gap-2 text-sm">
                {Object.entries(data.status_counts ?? {}).map(([k, v]) => (
                  <li key={k} className="status-chip">
                    {label(k)}: {v}
                  </li>
                ))}
              </ul>
              {Object.entries(data.reasons ?? {}).map(([status, rs]) => (
                <details key={status}>
                  <summary className="cursor-pointer text-sm">
                    Reasons — {label(status)} ({rs.length} distinct, current filters)
                  </summary>
                  <ul className="mt-2 text-xs mono space-y-1">
                    {rs.map((r) => (
                      <li key={r.reason}>
                        {r.series} × {r.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              ))}
            </section>
            {data.series ? (
              <section className="space-y-2">
                <div className="overflow-x-auto">
                  <table className="data-table">
                    <caption>Series availability (exact reasons)</caption>
                    <thead>
                      <tr>
                        <th scope="col">Series</th>
                        <th scope="col">Sport / competition</th>
                        <th scope="col">Family</th>
                        <th scope="col">Status</th>
                        <th scope="col">Reason</th>
                        <th scope="col">Calibration</th>
                        <th scope="col">Blend</th>
                        <th scope="col">Market</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.series.items.map((r) => (
                        <tr key={r.series_ticker}>
                          <th scope="row">
                            <div className="mono">{r.series_ticker}</div>
                            <div className="text-xs text-[var(--text-muted)]">{r.series_title}</div>
                          </th>
                          <td>
                            {r.sport} · {r.competition ?? r.competition_top}
                          </td>
                          <td>{label(r.family)}</td>
                          <td>
                            <StatusBadge status={r.status} />
                          </td>
                          <td className="text-xs">{r.reason ?? '—'}</td>
                          <td className="text-xs">{r.calibration_status ?? '—'}</td>
                          <td className="text-xs">{r.blend_status ?? '—'}</td>
                          <td className="text-xs">{r.market_candidate ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pager page={data.series.page} pages={data.series.pages} total={data.series.total} noun="series" onPage={(p) => setParam({ page: String(p) })} />
              </section>
            ) : null}
            {data.competition_family ? (
              <details className="panel">
                <summary className="cursor-pointer font-semibold">
                  Candidate availability by competition and family ({data.competition_family.shown} of {data.competition_family.total} groups)
                </summary>
                <div className="mt-3 overflow-x-auto">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th scope="col">Competition</th>
                        <th scope="col">Family</th>
                        {CANDIDATES.map((c) => (
                          <th scope="col" key={c}>
                            {CANDIDATE_LABELS[c]}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.competition_family.items.map((r) => (
                        <tr key={`${r.competition}-${r.family}`}>
                          <th scope="row">
                            <div>{r.competition}</div>
                            <div className="text-xs text-[var(--text-muted)]">
                              {r.sport} · {r.source} · {r.live_adapter}
                            </div>
                          </th>
                          <td>{label(r.family)}</td>
                          {CANDIDATES.map((c) => (
                            <td key={c} className="text-xs">
                              <StatusBadge status={r.candidates[c].status} />
                              {r.candidates[c].status !== 'available' ? <div className="mt-1">{r.candidates[c].detail}</div> : null}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            ) : null}
            {data.observed_in_saved_captures ? (
              <section className="panel space-y-2">
                <h3 className="font-semibold">Observed in saved captures</h3>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  {CANDIDATES.map((c) => {
                    const o = data.observed_in_saved_captures![c]
                    return (
                      <div key={c} className="text-sm">
                        <div className="font-medium">{CANDIDATE_LABELS[c]}</div>
                        <div className="mono">{o.available} available</div>
                        <ul className="text-xs text-[var(--text-muted)] space-y-1 mt-1">
                          {o.unavailable.map((u) => (
                            <li key={u.reason}>
                              {u.contracts} unavailable: {u.reason}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )
                  })}
                </div>
              </section>
            ) : null}
          </div>
        )
      ) : null}
    </>
  )
}

// ---------------------------------------------------------------- Historical research

export function HistoricalTab({ params, setParam }: { params: Params; setParam: SetParam }) {
  const filters = {
    protocol: params.get('protocol') ?? '',
    level: params.get('level') ?? '',
    family: params.get('family') ?? '',
    cohort: params.get('cohort') ?? '',
    comparison: params.get('comparison') ?? '',
    page: Number(params.get('page') ?? '1') || 1,
  }
  const state = useAsync(() => fetchSportsHistorical({ ...filters, page_size: 50 }), JSON.stringify(filters))
  const data = state.data
  const facets = (k: string) => [...new Set((data?.protocols ?? []).flatMap((p) => p.facets?.[k] ?? []))]
  return (
    <>
      <FilterBar
        selects={[
          {
            id: 'protocol',
            label: 'Protocol',
            value: filters.protocol,
            options: ['sports_phase1_v1', 'sports_phase1_v2', 'sports_phase2_v1'].map((value) => ({ value, label: value })),
          },
          { id: 'level', label: 'Level', value: filters.level, options: opts(facets('level')) },
          { id: 'family', label: 'Family', value: filters.family, options: opts(facets('family')) },
          { id: 'cohort', label: 'Cohort', value: filters.cohort, options: opts(facets('cohort')) },
          { id: 'comparison', label: 'Comparison', value: filters.comparison, options: opts(facets('comparison')) },
        ]}
        onChange={(id, value) => setParam({ [id]: value })}
      />
      {state.loading ? <LoadingState label="Loading historical research…" /> : null}
      {state.error ? <ErrorState message={state.error} /> : null}
      {data && !state.loading ? (
        <div className="space-y-4">
          <DataStamp generatedAt={data.generated_at} loadedAt={state.loadedAt} />
          <div className="warning-banner text-sm space-y-1">
            <div className="font-semibold text-[var(--warning)]">{data.label}</div>
            <p>{data.separation_note}</p>
            <p className="text-[var(--text-muted)]">{data.interpretation}</p>
          </div>
          {data.protocols.map((p) => (
            <section key={p.protocol} className="panel space-y-2" aria-labelledby={`hist-${p.protocol}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 id={`hist-${p.protocol}`} className="font-semibold">
                  {p.phase} · <span className="mono">{p.protocol}</span>
                </h3>
                <StatusBadge status={p.status} />
              </div>
              <p className="text-sm text-[var(--text-muted)]">
                {p.comparison}. Report: <span className="mono">docs/research/{p.report}</span>
                {p.addenda.length ? (
                  <>
                    {' '}
                    · Addenda: <span className="mono">{p.addenda.join(', ')}</span>
                  </>
                ) : null}
                {p.frozen_at ? ` · frozen ${p.frozen_at}` : ''}
              </p>
              <p className="text-xs mono">
                Source: {p.source.label} · tracked CSV: {p.source.tracked_csv}
              </p>
              {!p.available || !p.rows ? (
                <EmptyState title="Results file missing" detail={p.missing ?? 'unavailable'} />
              ) : p.rows.length === 0 ? (
                <p className="text-sm text-[var(--text-muted)]">No rows match these filters.</p>
              ) : (
                <>
                  <div className="overflow-x-auto max-h-[28rem]">
                    <table className="data-table">
                      <thead>
                        <tr>
                          {p.columns!.map((c) => (
                            <th scope="col" key={c}>
                              {label(c)}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {p.rows.map((r, i) => (
                          <tr key={i}>
                            {p.columns!.map((c) => (
                              <td key={c} className="mono text-xs">
                                {r[c]}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Pager page={p.page ?? 1} pages={p.pages ?? 1} total={p.total ?? 0} noun="rows" onPage={(pg) => setParam({ page: String(pg) })} />
                </>
              )}
            </section>
          ))}
        </div>
      ) : null}
    </>
  )
}

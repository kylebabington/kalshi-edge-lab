import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import { eventPath, fetchSportsResultsTimeline } from '../../api/sports'
import { paginate } from '../../lib/candidates'
import { fmtLocal } from '../../lib/time'
import type { SportsResultsTimeline, TimelinePoint } from '../../types/sports'
import { EmptyState, ErrorState, LoadingState } from '../StatusBadge'
import { Pager } from './FilterBar'
import { CANDIDATE_LABELS, fmtSigned } from './format'
import { useAsync } from './useAsync'

const PAGE_SIZE = 25
const DEFAULT_PAIR = 'frozen:market'

type Filters = { sport: string; competition: string; family: string; cohort: string }

function pairText(d: { candidate: string; reference: string }): string {
  const name = (k: string) => CANDIDATE_LABELS[k as keyof typeof CANDIDATE_LABELS] ?? k
  return `${name(d.candidate)} − ${name(d.reference)}`
}

function TimelineTooltip({ active, payload }: { active?: boolean; payload?: { payload: TimelinePoint & { t: number } }[] }) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="panel text-xs space-y-0.5">
      <div className="mono">{fmtLocal(p.completed_at)}</div>
      <div>
        After {p.events} completed event{p.events === 1 ? '' : 's'}: <span className="mono">{fmtSigned(p.cumulative_d_brier_event, 4)}</span>
      </div>
      <div>
        This event: <span className="mono">{fmtSigned(p.event_d_brier, 4)}</span> ({p.contracts} contracts)
      </div>
    </div>
  )
}

function TimelineView({ data, page, onPage }: { data: SportsResultsTimeline; page: number; onPage: (p: number) => void }) {
  const points = useMemo(() => data.points.map((p) => ({ ...p, t: Date.parse(p.completed_at) })), [data.points])
  const rows = useMemo(() => [...data.points].reverse(), [data.points])
  const view = paginate(rows, page, PAGE_SIZE)
  const nic = data.not_in_chart
  const basis = data.results_table_basis
  const c = data.cohort
  return (
    <div className="space-y-3">
      <p className="text-sm">
        <span className="font-semibold">{pairText(data.pair)}</span> · {c.events} completed events, {c.contracts} matched
        contracts · completed {fmtLocal(c.first_completed_at)} – {fmtLocal(c.last_completed_at)} · collection:{' '}
        {Object.entries(c.by_collection)
          .map(([m, n]) => `${data.collection_labels[m] ?? m} ${n}`)
          .join(', ') || 'none'}
      </p>
      {points.length ? (
        <div
          className="h-56 sm:h-64"
          role="img"
          aria-label={`Cumulative event-equal paired Brier difference after each of ${points.length} completed events; the table below lists every point.`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 8, right: 12, bottom: 8, left: 4 }}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
              <XAxis
                type="number"
                dataKey="t"
                scale="time"
                domain={['dataMin', 'dataMax']}
                tickFormatter={(v) => fmtLocal(new Date(Number(v)).toISOString())}
                tick={{ fontSize: 11 }}
                minTickGap={40}
              />
              <YAxis type="number" dataKey="cumulative_d_brier_event" tickFormatter={(v) => Number(v).toFixed(3)} width={56} />
              <ReferenceLine y={0} stroke="var(--text-muted)" strokeDasharray="4 4" />
              <Tooltip content={<TimelineTooltip />} />
              <Scatter data={points} fill="#3b82f6" isAnimationActive={false} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <EmptyState
          title="No completed events for this pair and these filters"
          detail="A point appears once every selected contract of an event has a confirmed YES/NO outcome."
        />
      )}
      <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[minmax(14rem,auto)_1fr]">
        <dt className="text-[var(--text-muted)]">Final point (dated, completed events)</dt>
        <dd className="mono">
          {data.final.cumulative_d_brier_event == null ? '—' : fmtSigned(data.final.cumulative_d_brier_event, 4)} · same
          cohort paired metric{' '}
          {data.final.paired_d_brier_event_on_cohort == null ? '—' : fmtSigned(data.final.paired_d_brier_event_on_cohort, 4)}
          {data.final.equal ? '' : ' (MISMATCH)'}
        </dd>
        <dt className="text-[var(--text-muted)]">Results table basis (different cohort)</dt>
        <dd>
          <span className="mono">{basis.d_brier_event == null ? '—' : fmtSigned(basis.d_brier_event, 4)}</span> over{' '}
          {basis.contracts} contracts in {basis.events} events. {basis.note}
        </dd>
        <dt className="text-[var(--text-muted)]">Not in the chart</dt>
        <dd>
          partially settled {nic.partially_settled.events} events ({nic.partially_settled.selected_contracts} contracts) ·
          not yet settled {nic.pending.events} · settled without YES/NO {nic.nonbinary.events} · inconsistent{' '}
          {nic.inconsistent.events} · completed without a settlement time {nic.undated_completed.events}
          {nic.undated_completed.events
            ? ` (their own difference ${nic.undated_completed.d_brier_event == null ? '—' : fmtSigned(nic.undated_completed.d_brier_event, 4)})`
            : ''}
        </dd>
      </dl>
      {rows.length ? (
        <>
          <div className="overflow-x-auto">
            <table className="data-table">
              <caption>Completed events, most recent first</caption>
              <thead>
                <tr>
                  <th scope="col">Completed (last selected contract settled)</th>
                  <th scope="col">Event</th>
                  <th scope="col">Collection</th>
                  <th scope="col">Contracts</th>
                  <th scope="col">Event difference</th>
                  <th scope="col">Cumulative (events so far)</th>
                </tr>
              </thead>
              <tbody>
                {view.items.map((p) => (
                  <tr key={p.event_id}>
                    <th scope="row" className="mono text-xs">{fmtLocal(p.completed_at)}</th>
                    <td className="text-xs">
                      <Link className="mono break-all" to={eventPath(p.event_id)}>
                        {p.event_id}
                      </Link>
                    </td>
                    <td className="text-xs">{data.collection_labels[p.collection_mode] ?? p.collection_mode}</td>
                    <td className="mono text-xs">{p.contracts}</td>
                    <td className="mono text-xs">{fmtSigned(p.event_d_brier, 4)}</td>
                    <td className="mono text-xs">
                      {fmtSigned(p.cumulative_d_brier_event, 4)} ({p.events})
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={view.page} pages={view.pages} total={rows.length} onPage={onPage} noun="completed events" />
        </>
      ) : null}
    </div>
  )
}

export function TimelinePanel({
  filters,
  params,
  setParam,
}: {
  filters: Filters
  params: URLSearchParams
  setParam: (updates: Record<string, string>) => void
}) {
  const pair = params.get('tpair') || DEFAULT_PAIR
  const collection = params.get('tcollection') ?? ''
  const page = Number(params.get('tpage') ?? '1') || 1
  const query = { ...filters, pair, collection }
  const state = useAsync(() => fetchSportsResultsTimeline(query), JSON.stringify(query))
  const data = state.data
  const pairs = data?.pairs ?? [
    { candidate: 'frozen', reference: 'market' },
    { candidate: 'calibrated', reference: 'frozen' },
    { candidate: 'blend', reference: 'frozen' },
    { candidate: 'blend', reference: 'market' },
  ]
  return (
    <section className="panel space-y-3" aria-labelledby="results-timeline">
      <div className="space-y-1">
        <h3 id="results-timeline" className="font-semibold">
          Results over time <span className="text-xs font-normal text-[var(--text-muted)]">(prospective pilot only)</span>
        </h3>
        <p className="text-xs text-[var(--text-muted)]">
          Cumulative event-equal paired Brier difference over completed events, using the filters above. Historical
          research results are not mixed in; they are on the Historical Research tab.
        </p>
      </div>
      <div className="flex flex-wrap gap-3 text-sm">
        <label>
          Pair{' '}
          <select
            className="ml-1 rounded border border-[var(--border)] bg-transparent px-2 py-1"
            value={pair}
            onChange={(e) => setParam({ tpair: e.target.value === DEFAULT_PAIR ? '' : e.target.value, tpage: '' })}
          >
            {pairs.map((p) => (
              <option key={`${p.candidate}:${p.reference}`} value={`${p.candidate}:${p.reference}`}>
                {pairText(p)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Collection{' '}
          <select
            className="ml-1 rounded border border-[var(--border)] bg-transparent px-2 py-1"
            value={collection}
            onChange={(e) => setParam({ tcollection: e.target.value, tpage: '' })}
          >
            <option value="">All</option>
            {(data?.collection_modes ?? ['automatic', 'collector_manual', 'manual_pilot']).map((m) => (
              <option key={m} value={m}>
                {data?.collection_labels[m] ?? m}
              </option>
            ))}
          </select>
        </label>
      </div>
      {state.loading ? <LoadingState label="Loading results over time…" /> : null}
      {state.error ? <ErrorState message={state.error} onRetry={state.reload} /> : null}
      {data && !state.loading ? (
        <>
          <p className="text-xs text-[var(--text-muted)]">
            {data.note} {data.sign_note}
          </p>
          <TimelineView data={data} page={page} onPage={(p) => setParam({ tpage: p > 1 ? String(p) : '' })} />
        </>
      ) : null}
    </section>
  )
}

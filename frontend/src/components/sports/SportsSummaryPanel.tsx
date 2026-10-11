import { Link } from 'react-router-dom'
import { fetchSportsSummary } from '../../api/sports'
import { CANDIDATES, type CollectionMode } from '../../types/sports'
import { ErrorState, LoadingState } from '../StatusBadge'
import { DataStamp } from './DataStamp'
import { CANDIDATE_LABELS, fmtTime, label } from './format'
import { useAsync } from './useAsync'

const COLLECTION_MODES: CollectionMode[] = ['automatic', 'collector_manual', 'manual_pilot']

export function SportsSummaryPanel() {
  const state = useAsync(fetchSportsSummary, 'sports-summary')
  if (state.loading) return <LoadingState label="Loading sports summary…" />
  if (state.error) return <ErrorState message={state.error} />
  const s = state.data
  if (!s) return null
  const c = s.counts
  const coll = s.collection
  const rows: [string, number][] = [
    ['Captured events', c.captured_events],
    ['Scored events', c.scored_events],
    ['Pending settlement', c.pending_events],
    ['Upcoming (captured)', c.upcoming_events],
    ['Missed windows', c.missed_events],
  ]
  return (
    <section className="panel space-y-3" aria-labelledby="sports-summary-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="sports-summary-heading" className="font-semibold">
          Sports — saved captures
        </h3>
        <Link to="/sports" className="text-sm text-[var(--accent)] hover:underline">
          Open sports →
        </Link>
      </div>
      {s.artifacts.capture_records === 'missing' ? (
        <p className="text-sm text-[var(--text-muted)]">No saved sports captures on this machine yet.</p>
      ) : null}
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3">
            <dt className="text-[var(--text-muted)]">{k}</dt>
            <dd className="mono">{v}</dd>
          </div>
        ))}
      </dl>
      <div>
        <div className="text-xs text-[var(--text-muted)] mb-1">Candidate availability (contracts in saved captures)</div>
        <ul className="flex flex-wrap gap-3 text-sm">
          {CANDIDATES.map((k) => (
            <li key={k} className="mono">
              {CANDIDATE_LABELS[k]}: {s.candidate_counts[k]?.available ?? 0}
              <span className="text-[var(--text-muted)]"> / {c.captured_contracts}</span>
            </li>
          ))}
        </ul>
      </div>
      {coll ? (
        <div className="text-xs text-[var(--text-muted)]">
          <div className="mb-1">
            Collection mode (automatic collection began {fmtTime(coll.amendment.first_scheduled_action_at)}, every{' '}
            {coll.amendment.cadence_minutes} min; see <span className="mono break-all">{coll.amendment.doc}</span>)
          </div>
          <ul className="flex flex-wrap gap-3 mono">
            {COLLECTION_MODES.filter((m) => m !== 'collector_manual' || coll.by_mode[m].captured + coll.by_mode[m].missed > 0).map(
              (m) => (
                <li key={m}>
                  {coll.labels[m]}: {coll.by_mode[m].captured} captured / {coll.by_mode[m].missed} missed
                </li>
              ),
            )}
          </ul>
        </div>
      ) : null}
      <p className="text-xs text-[var(--text-muted)]">
        Last capture: {fmtTime(s.last_capture_at)} · Collector:{' '}
        {s.collector.recorded
          ? `${label(s.collector.last_cycle_result)} at ${fmtTime(s.collector.last_cycle_finished_at)}`
          : 'no cycle recorded'}
      </p>
      <DataStamp generatedAt={s.generated_at} loadedAt={state.loadedAt} source={s.source} />
    </section>
  )
}

import type { ReactNode } from 'react'
import { HEALTH_LABELS, weatherHealth, type CollectorHealth } from '../lib/health'
import { fmtDuration, fmtLocal, minutesBetween } from '../lib/time'
import type { SchedulerState } from '../types/sports'
import type { WeatherCollectorStatus } from '../types/weather'
import { ErrorState, LoadingState, StatusBadge } from './StatusBadge'

function schedulerText(s: SchedulerState): string {
  if (!s.observable) return 'unknown'
  if (s.configured === false) return 'not registered'
  if (s.enabled === true) return 'enabled'
  if (s.enabled === false) return 'disabled'
  return 'unknown'
}

function Row({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-[var(--border)] py-1">
      <dt className="text-[var(--text-muted)] shrink-0 max-w-[60%]">{term}</dt>
      <dd className="mono text-right text-xs break-words min-w-0">{children}</dd>
    </div>
  )
}

function ago(iso: string | null | undefined, nowIso: string): string {
  if (!iso) return 'none recorded'
  const m = minutesBetween(iso, nowIso)
  return `${fmtLocal(iso)} (${m == null ? 'age unknown' : `${fmtDuration(m)} ago`})`
}

function HealthBlock({
  heading,
  health,
  rows,
  freshness,
  note,
}: {
  heading: string
  health: CollectorHealth
  rows: [string, ReactNode][]
  freshness: [string, ReactNode][]
  note: string
}) {
  return (
    <section className="panel space-y-3" aria-label={heading}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{heading}</h3>
        <StatusBadge status={HEALTH_LABELS[health.state]} />
      </div>
      <p className="text-xs text-[var(--text-muted)]">{health.reason}. Health is based on successful cycle times only.</p>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <h4 className="text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1">Collector</h4>
          <dl className="text-sm">
            {rows.map(([k, v]) => (
              <Row key={k} term={k}>
                {v}
              </Row>
            ))}
          </dl>
        </div>
        <div>
          <h4 className="text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1">Saved forecast freshness</h4>
          <dl className="text-sm">
            {freshness.map(([k, v]) => (
              <Row key={k} term={k}>
                {v}
              </Row>
            ))}
          </dl>
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            Forecast age depends on when events are due; an old forecast does not mean the collector is unhealthy.
          </p>
        </div>
      </div>
      <p className="text-xs text-[var(--text-muted)]">{note}</p>
    </section>
  )
}

type State<T> = { loading: boolean; error: string | null; data: T | null; reload: () => void }

export function WeatherHealthPanel({ state }: { state: State<WeatherCollectorStatus> }) {
  if (state.loading && !state.data) return <LoadingState label="Loading weather collector healthâ€¦" />
  if (state.error) return <ErrorState title="Weather collector status unavailable" message={state.error} onRetry={state.reload} />
  const s = state.data
  if (!s) return null
  const now = s.generated_at
  return (
    <HealthBlock
      heading="Weather collector"
      health={weatherHealth(s)}
      rows={[
        ['Scheduler', schedulerText(s.scheduler)],
        ['Last successful cycle', ago(s.cycles.last_successful_cycle_at, now)],
        [
          'Last cycle',
          s.cycles.last_cycle_finished_at
            ? `${fmtLocal(s.cycles.last_cycle_finished_at)} (exit ${s.cycles.last_cycle_exit_code})`
            : s.log_observed
              ? 'none recorded'
              : 'task log not found on this machine',
        ],
        ['Cycle running now', s.lock.held ? `yes, since ${fmtLocal(s.lock.acquired_at)}` : 'no'],
        ['Failures (24 h) / consecutive', `${s.cycles.failures_24h} / ${s.cycles.consecutive_failures}`],
      ]}
      freshness={[
        ['Latest saved forecast', ago(s.forecast_freshness.last_snapshot_prediction_as_of, now)],
        ['Latest score written', ago(s.forecast_freshness.last_score_written_at, now)],
        ['Missed checkpoints (24 h)', String(s.forecast_freshness.missed_checkpoints_24h)],
      ]}
      note={`Task ${s.task_name} runs about every ${s.cadence_minutes} minutes. ${s.limits}`}
    />
  )
}

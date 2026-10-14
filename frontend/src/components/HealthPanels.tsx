import type { ReactNode } from 'react'
import {
  HEALTH_LABELS,
  sportsBackupHealth,
  sportsHealth,
  sportsLastSuccess,
  weatherHealth,
  type CollectorHealth,
} from '../lib/health'
import { fmtDuration, fmtLocal, minutesBetween } from '../lib/time'
import type { SchedulerState, SportsCollectorStatus } from '../types/sports'
import type { WeatherCollectorStatus } from '../types/weather'
import { ErrorState, LoadingState, StatusBadge } from './StatusBadge'
import { label } from './sports/format'

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
      <dt className="text-[var(--text-muted)]">{term}</dt>
      <dd className="mono text-right text-xs break-words">{children}</dd>
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
  if (state.loading && !state.data) return <LoadingState label="Loading weather collector health…" />
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

export function SportsHealthPanel({ state }: { state: State<SportsCollectorStatus> }) {
  if (state.loading && !state.data) return <LoadingState label="Loading sports collector health…" />
  if (state.error) return <ErrorState title="Sports collector status unavailable" message={state.error} onRetry={state.reload} />
  const s = state.data
  if (!s) return null
  const now = s.generated_at
  const health = sportsHealth(s)
  const lastResult = s.history?.last_cycle_result ?? s.status.last_cycle?.result
  const lastFinished = s.history?.last_cycle_finished_at ?? s.status.last_cycle?.finished_at
  const backup = sportsBackupHealth(s)
  const b = s.backup
  return (
    <>
      <HealthBlock
        heading="Sports collector"
        health={health}
        rows={[
          ['Scheduler', schedulerText(s.scheduler)],
          ['Last successful cycle', ago(sportsLastSuccess(s), now)],
          ['Last cycle', lastFinished ? `${fmtLocal(lastFinished)} (${label(lastResult)})` : 'none recorded'],
          ['Cycle running now', s.lock.held ? `yes (${label(s.lock.state)})` : 'no'],
          ['Cycle failures (24 h) / consecutive', `${s.status.failures_24h ?? '—'} / ${s.status.consecutive_failures ?? '—'}`],
        ]}
        freshness={[
          ['Latest accepted capture', ago(s.freshness.last_accepted_capture_at, now)],
          ['Latest score written', ago(s.freshness.last_score_written_at, now)],
          ['Deadline misses (24 h / total)', `${s.missed_windows.last_24h} / ${s.missed_windows.total}`],
        ]}
        note={`Task ${s.task_name} runs every ${s.cadence_minutes} minutes. Opening this page never starts a collection.`}
      />
      <section className="panel space-y-2" aria-label="Sports state backup">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">Sports state backup</h3>
          <StatusBadge status={backup.label} />
        </div>
        <p className="text-xs text-[var(--text-muted)]">
          {backup.reason}. Runs as its own task, so backup problems never delay or fail a capture cycle.
        </p>
        <dl className="grid gap-x-6 text-sm md:grid-cols-2">
          <Row term="Backup task">{s.backup_task ? schedulerText(s.backup_task) : 'unknown'}</Row>
          <Row term="Last snapshot">{ago(b?.last_snapshot_at, now)}</Row>
          <Row term="Last upload">{ago(b?.last_pushed_at, now)}</Row>
          <Row term="Upload pending">{b?.unpushed ? 'yes' : 'no'}</Row>
        </dl>
      </section>
    </>
  )
}

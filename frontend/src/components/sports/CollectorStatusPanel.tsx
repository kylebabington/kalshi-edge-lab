import type { ReactNode } from 'react'
import type { SchedulerState, SportsCollectorStatus } from '../../types/sports'
import { ErrorState, LoadingState, StatusBadge } from '../StatusBadge'
import { fmtMinutes, fmtTime, label } from './format'

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
      <dd className="mono text-right text-xs break-all">{children}</dd>
    </div>
  )
}

type Props = { loading: boolean; error: string | null; status: SportsCollectorStatus | null }

export function CollectorStatusPanel({ loading, error, status }: Props) {
  if (loading) return <LoadingState label="Loading sports collector status…" />
  if (error) return <ErrorState message={error} />
  if (!status) return null
  const s = status.status
  const last = s.last_cycle
  const sched = status.scheduler
  return (
    <section className="panel space-y-3" aria-labelledby="collector-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="collector-heading" className="font-semibold">
          Sports collector
        </h3>
        <StatusBadge status={`Scheduler: ${schedulerText(sched)}`} />
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Task {status.task_name} runs every {status.cadence_minutes} minutes and checks settlements at most every{' '}
        {status.settlement_interval_minutes} minutes. Opening this page never starts a collection. {status.limits}
      </p>
      <dl className="grid gap-x-6 sm:grid-cols-2 text-sm">
        <Row term="Configured / enabled">
          {String(sched.configured)} / {String(sched.enabled)}
        </Row>
        <Row term="Next scheduled run (local)">{sched.next_run_time}</Row>
        <Row term="Last task result">{sched.last_result}</Row>
        <Row term="Lock">
          {status.lock.held ? `held (${label(status.lock.state)})` : status.lock.state}
        </Row>
        <Row term="Last cycle start">{fmtTime(last?.started_at)}</Row>
        <Row term="Last cycle end">
          {fmtTime(last?.finished_at)}
          {last?.duration_s != null ? ` (${Math.round(last.duration_s)} s)` : ''}
        </Row>
        <Row term="Last cycle result">{last?.result ? label(last.result) : 'no cycle recorded'}</Row>
        <Row term="Last successful capture">{fmtTime(s.last_successful_capture_at)}</Row>
        <Row term="Last settlement check">{fmtTime(s.last_settlement_check_at)}</Row>
        <Row term="Failures (24 h) / consecutive">
          {s.failures_24h ?? '—'} / {s.consecutive_failures ?? '—'}
        </Row>
        <Row term="Missed windows (24 h / total)">
          {status.missed_windows.last_24h} / {status.missed_windows.total}
        </Row>
        <Row term="Since last accepted capture">{fmtMinutes(status.freshness.minutes_since_last_capture)}</Row>
        <Row term="Since last score">{fmtMinutes(status.freshness.minutes_since_last_score)}</Row>
        <Row term="Weather task">{schedulerText(status.weather_task)}</Row>
      </dl>
      {!sched.observable ? (
        <p className="text-xs text-[var(--warning)]">
          Scheduler state unknown{sched.reason ? `: ${sched.reason}` : ''}.
        </p>
      ) : null}
      {s.bottlenecks_24h && s.bottlenecks_24h.length ? (
        <div className="warning-banner text-xs" role="status">
          <div className="font-semibold text-[var(--warning)]">Capture timing bottleneck</div>
          {s.bottlenecks_24h.slice(-1).map((b) => (
            <p key={b}>{b}</p>
          ))}
        </div>
      ) : null}
      {s.recent_failures && s.recent_failures.length ? (
        <details className="text-xs">
          <summary className="cursor-pointer">Recent failures ({s.recent_failures.length})</summary>
          <ul className="mt-2 space-y-1 mono">
            {s.recent_failures.map((f) => (
              <li key={f.at}>
                {fmtTime(f.at)} — {label(f.result)} {f.detail ?? ''}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  )
}

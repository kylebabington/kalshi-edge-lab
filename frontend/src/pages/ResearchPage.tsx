import type { ModelSummary } from '../types/weather'
import { ErrorState, LoadingState, StatusBadge } from '../components/StatusBadge'

type Props = {
  loading: boolean
  error: string | null
  summary: ModelSummary | null
}

export function ResearchPage({ loading, error, summary }: Props) {
  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} />

  const constants = summary?.methodology_constants ?? {}
  const status = summary?.research_status ?? {}
  const multi = summary?.multi_source_evidence ?? {}
  const journal = summary?.prospective_journal ?? {}
  const transfer = summary?.settlement_transfer ?? {}
  const prospective = summary?.prospective_validation
  const coverage = prospective?.checkpoint_coverage ?? {}
  const prospectiveN = Number(transfer.prospective_n ?? 0)
  const prospectiveTarget = Number(transfer.prospective_target_n ?? 20)
  const incumbent = prospective?.incumbent_gfs
  const shadowMetrics = prospective?.shadow_gfs_hrrr_equal
  const delta = prospective?.paired_delta_brier as
    | { mean?: number; ci95?: number[] }
    | null
    | undefined

  return (
    <div className="space-y-5">
      <h2 className="text-xl font-semibold">Research</h2>
      <div className="grid gap-4 md:grid-cols-2">
        {[
          ['Weather Phase 1', status.weather_phase_1 ?? 'COMPLETE'],
          ['Weather Phase 2', status.weather_phase_2 ?? 'COMPLETE'],
          ['Weather Phase 3 Transfer', status.weather_phase_3_transfer ?? 'EXPERIMENTAL'],
          ['Weather Phase 4 Evidence', status.weather_phase_4_evidence ?? 'COMPLETE'],
          ['Weather Phase 5 Shadow', status.weather_phase_5_shadow ?? 'SHADOW_ONLY'],
          ['CLINYC Target Calibration', status.clinyc_target_calibration ?? 'IN PROGRESS'],
          ['Weather ↔ Kalshi Execution Backtest', status.weather_kalshi_execution_backtest ?? 'BLOCKED'],
          ['Live Recommendations', status.live_recommendations ?? 'DISABLED'],
        ].map(([title, value]) => (
          <div key={title} className="panel flex items-center justify-between gap-3">
            <div className="font-medium">{title}</div>
            <StatusBadge status={String(value)} />
          </div>
        ))}
      </div>

      <section className="panel space-y-3">
        <h3 className="font-semibold">MULTI-SOURCE EVIDENCE</h3>
        <dl className="grid gap-2 sm:grid-cols-2 text-sm mono">
          {Object.entries(multi).map(([key, value]) => (
            <div key={key} className="flex justify-between gap-3 border-b border-[var(--border)] py-1">
              <dt>{key}</dt>
              <dd>
                <StatusBadge status={String(value)} />
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="panel space-y-3">
        <h3 className="font-semibold">PROSPECTIVE JOURNAL</h3>
        <dl className="grid gap-2 sm:grid-cols-3 text-sm mono">
          <div>
            snapshots <span className="font-semibold">{String(journal.snapshots ?? 0)}</span>
          </div>
          <div>
            scored <span className="font-semibold">{String(journal.scored_snapshots ?? 0)}</span>
          </div>
          <div>
            settled scored{' '}
            <span className="font-semibold">{String(journal.settled_events_scored ?? 0)}</span>
          </div>
        </dl>
      </section>

      <section className="panel space-y-2">
        <h3 className="font-semibold">CLINYC_TRANSFER_V1</h3>
        <p className="text-sm text-[var(--text-muted)]">Prospective confirmation progress (frozen hypothesis).</p>
        <div className="text-2xl mono">
          {prospectiveN} / {prospectiveTarget}
        </div>
        <StatusBadge status={String(transfer.transfer_status ?? 'experimental')} />
      </section>

      <section className="panel space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="font-semibold">PROSPECTIVE WEATHER VALIDATION</h3>
          <StatusBadge status={String(prospective?.status ?? 'SHADOW ONLY')} />
        </div>
        <p className="text-sm text-[var(--text-muted)]">
          Incumbent remains calibrated GFS. Equal-weight GFS+HRRR is shadow research only.
        </p>
        <dl className="grid gap-2 sm:grid-cols-2 text-sm mono">
          {Object.entries(coverage).map(([id, row]) => (
            <div key={id} className="flex justify-between gap-3 border-b border-[var(--border)] py-1">
              <dt>{id}</dt>
              <dd>{row.display ?? `${row.captured ?? 0} / ${row.total ?? 0}`}</dd>
            </div>
          ))}
        </dl>
        <div className="text-sm mono space-y-1">
          <div>
            scored events{' '}
            <span className="font-semibold">{String(prospective?.scored_events ?? 0)}</span>
          </div>
          <div>
            captured{' '}
            <span className="font-semibold">{String(prospective?.captured_checkpoints ?? 0)}</span>
            {' / '}
            missed{' '}
            <span className="font-semibold">{String(prospective?.missed_checkpoints ?? 0)}</span>
          </div>
          {incumbent ? (
            <div>
              incumbent GFS Brier={String(incumbent.brier ?? '—')} log_loss=
              {String(incumbent.log_loss ?? '—')}
            </div>
          ) : null}
          {shadowMetrics ? (
            <div>
              GFS+HRRR Equal Brier={String(shadowMetrics.brier ?? '—')} log_loss=
              {String(shadowMetrics.log_loss ?? '—')}
            </div>
          ) : null}
          {delta ? (
            <div>
              paired Δ Brier mean={String(delta.mean ?? '—')}
              {Array.isArray(delta.ci95)
                ? ` ci95=[${String(delta.ci95[0])}, ${String(delta.ci95[1])}]`
                : ''}
            </div>
          ) : null}
          <div>
            transfer for shadow V1:{' '}
            <StatusBadge
              status={String(prospective?.experimental_transfer_status ?? 'experimental')}
            />
          </div>
        </div>
      </section>

      <section className="panel">
        <h3 className="font-semibold">Methodology constants</h3>
        <p className="text-sm text-[var(--text-muted)] mt-1">
          Phase 5 does not promote the shadow. Combination policy = none for live WeatherPrediction.
        </p>
        <dl className="mt-3 grid gap-2 sm:grid-cols-2 text-sm mono">
          {Object.entries(constants).map(([key, value]) => (
            <div key={key} className="flex justify-between gap-3 border-b border-[var(--border)] py-1">
              <dt className="text-[var(--text-muted)]">{key}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  )
}

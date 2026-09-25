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

  return (
    <div className="space-y-5">
      <h2 className="text-xl font-semibold">Research</h2>
      <div className="grid gap-4 md:grid-cols-2">
        {[
          ['Weather Phase 1', status.weather_phase_1 ?? 'COMPLETE'],
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

      <section className="panel">
        <h3 className="font-semibold">Methodology constants</h3>
        <p className="text-sm text-[var(--text-muted)] mt-1">
          Phase 1 constants are not lowered for small CLINYC samples.
        </p>
        <dl className="mt-3 grid gap-2 sm:grid-cols-2 text-sm mono">
          {Object.entries(constants).map(([key, value]) => (
            <div key={key} className="flex justify-between gap-3 border-b border-[var(--border)] py-1">
              <dt className="text-[var(--text-muted)]">{key}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  )
}

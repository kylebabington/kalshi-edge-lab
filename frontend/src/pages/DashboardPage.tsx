import { Link } from 'react-router-dom'
import type { WeatherEvent } from '../types/weather'
import { EventCard } from '../components/EventCard'
import { EmptyState, ErrorState, LoadingState, StatusBadge } from '../components/StatusBadge'
import type { ModelSummary } from '../types/weather'

type Props = {
  loading: boolean
  error: string | null
  events: WeatherEvent[]
  summary: ModelSummary | null
}

export function DashboardPage({ loading, error, events, summary }: Props) {
  if (loading) return <LoadingState label="Loading live weather markets…" />
  if (error) return <ErrorState message={error} />

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-xl font-semibold">Current Weather Markets</h2>
        <p className="text-sm text-[var(--text-muted)] mt-1">
          What the external evidence model is looking at right now.
        </p>
        {events.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              title="No open KXHIGHNY range-bucket events"
              detail="Open markets will appear here when available."
            />
          </div>
        ) : (
          <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {events.map((event) => (
              <Link key={event.event_ticker} to={`/markets/${event.event_ticker}`}>
                <EventCard event={event} />
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="panel">
          <h3 className="font-semibold">Model Health</h3>
          {summary?.model_summary_available ? (
            <dl className="mt-3 space-y-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-[var(--text-muted)]">Calibration events / rows</dt>
                <dd className="mono">{String(summary.calibration.row_count ?? '—')}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-[var(--text-muted)]">Usable NWS residuals</dt>
                <dd className="mono">{String(summary.calibration.usable_nws_residuals ?? '—')}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-[var(--text-muted)]">Usable CLINYC residuals</dt>
                <dd className="mono">{String(summary.calibration.usable_clinyc_residuals ?? '—')}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-[var(--text-muted)]">Transfer status</dt>
                <dd>
                  <StatusBadge
                    status={
                      summary.settlement_regime?.transfer_validated
                        ? 'VALIDATED'
                        : String(
                            summary.settlement_transfer?.transfer_status ??
                              summary.settlement_regime?.transfer_status ??
                              'EXPERIMENTAL',
                          ).toUpperCase()
                    }
                  />
                </dd>
              </div>
            </dl>
          ) : (
            <p className="mt-3 text-sm text-[var(--text-muted)]">
              model_summary_available = false — run calibration/backtest CLI explicitly.
            </p>
          )}
        </div>

        <div className="panel">
          <h3 className="font-semibold">Research Status</h3>
          <ul className="mt-3 space-y-2 text-sm">
            <li className="flex justify-between gap-3">
              <span>Weather probability model</span>
              <StatusBadge status={summary?.research_status?.weather_phase_1 ?? 'ACTIVE'} />
            </li>
            <li className="flex justify-between gap-3">
              <span>CLINYC transfer validation</span>
              <StatusBadge
                status={summary?.research_status?.clinyc_target_calibration ?? 'IN PROGRESS'}
              />
            </li>
            <li className="flex justify-between gap-3">
              <span>Kalshi execution engine</span>
              <StatusBadge status="AVAILABLE" />
            </li>
            <li className="flex justify-between gap-3">
              <span>Trade recommendations</span>
              <StatusBadge status="DISABLED" />
            </li>
          </ul>
        </div>
      </section>
    </div>
  )
}

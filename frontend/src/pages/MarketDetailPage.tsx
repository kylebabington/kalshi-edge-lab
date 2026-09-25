import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { WeatherEvent } from '../types/weather'
import { SourceMismatchBanner } from '../components/EventCard'
import { EmptyState, ErrorState, LoadingState, StatusBadge } from '../components/StatusBadge'

function cents(value: number | null | undefined) {
  if (value == null) return '—'
  return `${Math.round(value * 100)}¢`
}

function pct(value: number | null | undefined) {
  if (value == null) return '—'
  return `${(value * 100).toFixed(0)}%`
}

type Props = {
  loading: boolean
  error: string | null
  event: WeatherEvent | null
}

export function MarketDetailPage({ loading, error, event }: Props) {
  if (loading) return <LoadingState label="Loading market detail…" />
  if (error) return <ErrorState message={error} />
  if (!event) return <EmptyState title="Event not found" detail="No detail payload available." />

  const pred = event.prediction
  const buckets = Object.entries(pred.bucket_probabilities || {}).map(([label, p]) => ({
    label,
    probability: p,
  }))
  const gfs = (event.evidence?.gfs ?? {}) as Record<string, unknown>
  const gefs = (event.evidence?.gefs ?? {}) as Record<string, unknown>
  const obs = (event.evidence?.observations ?? {}) as Record<string, unknown>

  return (
    <div className="space-y-5">
      <header className="space-y-3">
        <div className="text-xs uppercase tracking-[0.16em] text-[var(--text-muted)]">NYC Daily High</div>
        <h2 className="text-2xl font-semibold">{event.target_date}</h2>
        <div className="mono text-[var(--text-muted)]">Event: {event.event_ticker}</div>
        <div className="flex flex-wrap gap-2">
          <span className="badge badge-info">Weather</span>
          <span className="badge badge-warning">Research Only</span>
          <span className="badge">{event.resolution.station_id}</span>
          <StatusBadge status={pred.prediction_status} />
        </div>
      </header>

      <SourceMismatchBanner event={event} />

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="panel space-y-3">
          <h3 className="font-semibold">External Forecast</h3>
          <div className="text-3xl mono">{pred.expected_high == null ? '—' : `${pred.expected_high.toFixed(1)}°F`}</div>
          <dl className="grid grid-cols-2 gap-2 text-sm mono">
            <div>P10 {pred.p10_high ?? '—'}°F</div>
            <div>P25 {pred.p25_high ?? '—'}°F</div>
            <div>P50 {pred.p50_high ?? '—'}°F</div>
            <div>P75 {pred.p75_high ?? '—'}°F</div>
            <div>P90 {pred.p90_high ?? '—'}°F</div>
            <div>N={pred.residual_sample_size ?? 0}</div>
          </dl>
          <div className="flex flex-wrap gap-2">
            <StatusBadge status={pred.confidence_label ?? 'UNKNOWN'} />
            <StatusBadge status={pred.model_agreement ?? 'UNKNOWN'} />
            <StatusBadge status={pred.calibration_target_regime ?? 'UNKNOWN'} />
          </div>
        </section>

        <section className="panel space-y-3">
          <h3 className="font-semibold">Weather Evidence</h3>
          <div className="grid gap-3 sm:grid-cols-3 text-sm">
            <div className="rounded-lg border border-[var(--border)] p-3">
              <div className="text-[var(--text-muted)]">GFS</div>
              <div className="mono mt-1">{String(gfs.forecast_high ?? '—')}°F</div>
              <div className="text-xs text-[var(--text-muted)] mt-1">{String(gfs.run_label ?? '')}</div>
            </div>
            <div className="rounded-lg border border-[var(--border)] p-3">
              <div className="text-[var(--text-muted)]">GEFS</div>
              <div className="mono mt-1">μ {String(gefs.mean ?? '—')}</div>
              <div className="text-xs text-[var(--text-muted)] mt-1">
                n={String(gefs.member_count ?? '—')} σ={String(gefs.std ?? '—')}
              </div>
            </div>
            <div className="rounded-lg border border-[var(--border)] p-3">
              <div className="text-[var(--text-muted)]">Observations</div>
              <div className="text-xs text-[var(--text-muted)] mt-1">{String(obs.note ?? '')}</div>
            </div>
          </div>
        </section>
      </div>

      <section className="panel">
        <h3 className="font-semibold mb-3">Probability Distribution</h3>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={buckets}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="label" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} interval={0} angle={-20} textAnchor="end" height={70} />
              <YAxis tickFormatter={(v) => `${Math.round(v * 100)}%`} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
              <Tooltip
                formatter={(value) => pct(Number(value))}
                contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
              />
              <Bar dataKey="probability" fill="var(--accent)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="panel overflow-x-auto">
        <h3 className="font-semibold mb-3">Kalshi Market</h3>
        <table className="w-full text-sm text-left">
          <thead className="text-[var(--text-muted)]">
            <tr>
              <th className="py-2 pr-3">Outcome</th>
              <th className="py-2 pr-3">External P</th>
              <th className="py-2 pr-3">YES Bid</th>
              <th className="py-2 pr-3">YES Ask</th>
              <th className="py-2 pr-3">Mid</th>
              <th className="py-2">Spread</th>
            </tr>
          </thead>
          <tbody>
            {event.kalshi_markets.map((market) => {
              const label = market.label ?? market.ticker ?? '—'
              const external = pred.bucket_probabilities[label] ?? null
              return (
                <tr key={String(market.ticker ?? label)} className="border-t border-[var(--border)]">
                  <td className="py-2 pr-3">{label}</td>
                  <td className="py-2 pr-3 mono">{pct(external)}</td>
                  <td className="py-2 pr-3 mono">{cents(market.yes_bid_dollars)}</td>
                  <td className="py-2 pr-3 mono">{cents(market.yes_ask_dollars)}</td>
                  <td className="py-2 pr-3 mono">{cents(market.mid_dollars)}</td>
                  <td className="py-2 mono">{cents(market.spread_dollars)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>

      <section className="panel overflow-x-auto">
        <h3 className="font-semibold">Probability Comparison</h3>
        <p className="text-sm text-[var(--text-muted)] mt-1">
          Raw probability difference = external_probability − market_midpoint. Does not include fees,
          execution assumptions, uncertainty buffer, or settlement-source mismatch.
        </p>
        <table className="mt-3 w-full text-sm text-left">
          <thead className="text-[var(--text-muted)]">
            <tr>
              <th className="py-2 pr-3">Outcome</th>
              <th className="py-2 pr-3">External P</th>
              <th className="py-2 pr-3">Market mid</th>
              <th className="py-2">Raw difference</th>
            </tr>
          </thead>
          <tbody>
            {event.kalshi_markets.map((market) => {
              const label = market.label ?? '—'
              const external = pred.bucket_probabilities[label] ?? null
              const mid = market.mid_dollars ?? null
              const diff = external != null && mid != null ? external - mid : null
              return (
                <tr key={`cmp-${label}`} className="border-t border-[var(--border)]">
                  <td className="py-2 pr-3">{label}</td>
                  <td className="py-2 pr-3 mono">{pct(external)}</td>
                  <td className="py-2 pr-3 mono">{pct(mid)}</td>
                  <td className="py-2 mono">{diff == null ? '—' : `${(diff * 100).toFixed(1)} pp`}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="panel">
          <h3 className="font-semibold">Why</h3>
          <ul className="mt-2 space-y-1 text-sm text-[var(--text-muted)]">
            {(Array.isArray(pred.provenance?.confidence_reasons)
              ? (pred.provenance?.confidence_reasons as string[])
              : []
            ).map((reason) => (
              <li key={reason}>• {reason}</li>
            ))}
            <li>• residual sample size = {pred.residual_sample_size ?? 0}</li>
            <li>• current GFS = {pred.point_forecast_high ?? '—'}°F</li>
          </ul>
        </div>
        <div className="panel">
          <h3 className="font-semibold">Warnings</h3>
          <ul className="mt-2 space-y-1 text-sm text-[var(--warning)]">
            {[...(event.warnings ?? []), ...pred.warnings].map((w) => (
              <li key={w}>• {w}</li>
            ))}
          </ul>
        </div>
      </section>
    </div>
  )
}

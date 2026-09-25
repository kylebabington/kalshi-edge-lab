import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { ModelSummary } from '../types/weather'
import { EmptyState, ErrorState, LoadingState, StatusBadge } from '../components/StatusBadge'

type Props = {
  loading: boolean
  error: string | null
  summary: ModelSummary | null
}

function pct(value: unknown): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

export function WeatherModelPage({ loading, error, summary }: Props) {
  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} />
  if (!summary) return <EmptyState title="No summary" detail="Model summary unavailable." />

  if (!summary.model_summary_available) {
    return (
      <EmptyState
        title="model_summary_available = false"
        detail={(summary.notes ?? []).join(' ') || 'Run weather_model.py --build-calibration / --backtest explicitly.'}
      />
    )
  }

  const errorRows = Object.entries(summary.forecast_error_by_run ?? {})
  const reliability = (summary.reliability_bins ?? []).map((bin) => ({
    bin: String(bin.bin ?? ''),
    predicted: Number(bin.mean_predicted ?? 0),
    observed: Number(bin.observed_frequency ?? 0),
  }))

  const transfer = summary.settlement_transfer ?? {}
  const transferStatus = String(transfer.transfer_status ?? summary.settlement_regime?.transfer_status ?? 'unavailable')
  const showValidated = transferStatus === 'validated' && Boolean(transfer.transfer_validated)
  const direct = summary.direct_clinyc ?? {}
  const datasetExists = Boolean(
    direct.dataset_exists ?? summary.calibration?.direct_clinyc_dataset_exists,
  )
  const directEligible = Boolean(
    direct.operationally_eligible ?? summary.calibration?.direct_clinyc_operationally_eligible,
  )
  const developmentN = Number(transfer.development_n ?? 0)
  const prospectiveN = Number(transfer.prospective_n ?? 0)
  const prospectiveTarget = Number(transfer.prospective_target_n ?? 20)
  const upperBound = transfer.integer_mismatch_95_upper

  return (
    <div className="space-y-5">
      <h2 className="text-xl font-semibold">Weather Model</h2>

      <section className="panel">
        <h3 className="font-semibold">Calibration dataset</h3>
        <dl className="mt-3 grid gap-2 sm:grid-cols-2 text-sm">
          <div>Rows: <span className="mono">{String(summary.calibration.row_count ?? '—')}</span></div>
          <div>NWS residuals: <span className="mono">{String(summary.calibration.usable_nws_residuals ?? '—')}</span></div>
          <div>CLINYC residuals: <span className="mono">{String(summary.calibration.usable_clinyc_residuals ?? '—')}</span></div>
          <div>
            Date range:{' '}
            <span className="mono">
              {summary.calibration.date_range
                ? JSON.stringify(summary.calibration.date_range)
                : '—'}
            </span>
          </div>
        </dl>
      </section>

      <section className="panel space-y-3">
        <h3 className="font-semibold">SETTLEMENT TARGET TRANSFER</h3>
        <div className="text-sm mono space-y-1">
          <div>NWS / KNYC</div>
          <div className="pl-4 text-[var(--text-muted)]">↓</div>
          <div>TWC / CLINYC</div>
        </div>
        <dl className="grid gap-2 sm:grid-cols-2 text-sm">
          <div>Development pairs: <span className="mono">{developmentN || '—'}</span></div>
          <div>Exact match: <span className="mono">{pct(transfer.exact_match_rate)}</span></div>
          <div>Same bucket: <span className="mono">{pct(transfer.same_bucket_rate)}</span></div>
          <div>
            Observed mismatches:{' '}
            <span className="mono">
              {String(transfer.exact_integer_mismatches ?? transfer.integer_mismatch_rate_observed ?? '—')}
            </span>
          </div>
          <div>
            95% upper mismatch bound:{' '}
            <span className="mono">
              {typeof upperBound === 'number' ? pct(upperBound) : '—'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            Transfer status:{' '}
            <StatusBadge status={showValidated ? 'VALIDATED' : 'EXPERIMENTAL'} />
          </div>
          <div>
            Prospective confirmation:{' '}
            <span className="mono">
              {prospectiveN} / {prospectiveTarget}
            </span>
          </div>
        </dl>
        {!transfer.available && (
          <p className="text-sm text-[var(--text-muted)]">
            Transfer assessment artifact unavailable. Run{' '}
            <span className="mono">weather_model.py --phase3-transfer</span>.
          </p>
        )}
      </section>

      <section className="panel space-y-2">
        <h3 className="font-semibold">Direct CLINYC calibration</h3>
        <div className="text-sm flex items-center gap-2">
          Direct CLINYC dataset:{' '}
          <StatusBadge status={datasetExists ? 'AVAILABLE' : 'UNAVAILABLE'} />
        </div>
        <div className="text-sm flex items-center gap-2">
          Direct CLINYC calibrated prediction:{' '}
          <StatusBadge
            status={directEligible ? 'ELIGIBLE' : 'INSUFFICIENT HISTORY'}
          />
        </div>
        <p className="text-sm text-[var(--text-muted)]">
          Dataset presence does not imply operational same-run hierarchical pools
          (MIN_N_MONTH / MIN_N_SEASON / MIN_N_RUN) are satisfied.
        </p>
      </section>

      <section className="panel overflow-x-auto">
        <h3 className="font-semibold mb-3">Forecast error by run</h3>
        <table className="w-full text-sm text-left">
          <thead className="text-[var(--text-muted)]">
            <tr>
              <th className="py-2 pr-3">Run</th>
              <th className="py-2 pr-3">N</th>
              <th className="py-2 pr-3">MAE</th>
              <th className="py-2 pr-3">RMSE</th>
              <th className="py-2">Bias</th>
            </tr>
          </thead>
          <tbody>
            {errorRows.map(([run, metrics]) => (
              <tr key={run} className="border-t border-[var(--border)]">
                <td className="py-2 pr-3 mono">{run}</td>
                <td className="py-2 pr-3 mono">{metrics.N ?? '—'}</td>
                <td className="py-2 pr-3 mono">{metrics.MAE?.toFixed?.(2) ?? '—'}</td>
                <td className="py-2 pr-3 mono">{metrics.RMSE?.toFixed?.(2) ?? '—'}</td>
                <td className="py-2 mono">{metrics.bias?.toFixed?.(2) ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h3 className="font-semibold mb-3">Reliability</h3>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={reliability}>
              <CartesianGrid stroke="var(--border)" />
              <XAxis dataKey="predicted" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
              <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
              <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)' }} />
              <Line type="monotone" dataKey="observed" stroke="var(--accent)" dot />
              <Line type="monotone" dataKey="predicted" stroke="var(--text-muted)" strokeDasharray="4 4" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="panel space-y-2">
        <h3 className="font-semibold">Settlement regime</h3>
        <div className="text-sm">Historical: <span className="mono">{String(summary.settlement_regime?.historical ?? 'nws_cli_knyc')}</span></div>
        <div className="text-sm">Current: <span className="mono">{String(summary.settlement_regime?.current_typical ?? 'weather_company_clinyc')}</span></div>
        <div className="text-sm flex items-center gap-2">
          Transfer:{' '}
          <StatusBadge status={showValidated ? 'VALIDATED' : 'EXPERIMENTAL'} />
        </div>
      </section>
    </div>
  )
}

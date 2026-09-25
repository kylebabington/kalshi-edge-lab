import type { WeatherEvent } from '../types/weather'
import { StatusBadge } from './StatusBadge'

function fmtTemp(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value.toFixed(1)}°F`
}

export function SourceMismatchBanner({ event }: { event: WeatherEvent }) {
  const pred = event.prediction
  const mismatch =
    pred.calibration_target_regime &&
    pred.live_target_regime &&
    pred.calibration_target_regime !== pred.live_target_regime &&
    !pred.settlement_source_transfer_validated

  const insufficient = pred.prediction_status === 'INSUFFICIENT_TARGET_REGIME_HISTORY'

  if (!mismatch && !insufficient) return null

  return (
    <div className="warning-banner space-y-2" role="alert">
      <div className="font-semibold tracking-wide text-[var(--warning)]">
        {mismatch
          ? 'EXPERIMENTAL — SETTLEMENT SOURCE MISMATCH'
          : 'INSUFFICIENT TARGET REGIME HISTORY'}
      </div>
      <div className="grid gap-1 text-sm mono">
        <div>Historical calibration target: {pred.calibration_target_regime ?? '—'}</div>
        <div>Live settlement target: {pred.live_target_regime ?? '—'}</div>
        <div>
          Transfer validated:{' '}
          {pred.settlement_source_transfer_validated ? 'YES' : 'NO'}
        </div>
        <div>Prediction status: {pred.prediction_status}</div>
      </div>
      <p className="text-sm text-[var(--text-muted)]">
        Thresholds are not lowered to fill probabilities. Research-only display.
      </p>
    </div>
  )
}

export function EventCard({ event }: { event: WeatherEvent }) {
  const pred = event.prediction
  return (
    <article className="panel hover:border-[color-mix(in_oklab,var(--accent)_40%,var(--border))] transition">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-[0.14em] text-[var(--text-muted)]">
            {event.target_date}
          </div>
          <h3 className="mt-1 text-lg font-semibold mono">{event.event_ticker}</h3>
        </div>
        <StatusBadge status={pred.prediction_status} />
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-[var(--text-muted)]">Expected high</dt>
          <dd className="mono text-base">{fmtTemp(pred.expected_high)}</dd>
        </div>
        <div>
          <dt className="text-[var(--text-muted)]">Confidence</dt>
          <dd>
            <StatusBadge status={pred.confidence_label ?? 'UNKNOWN'} />
          </dd>
        </div>
        <div>
          <dt className="text-[var(--text-muted)]">Target</dt>
          <dd className="mono text-xs">{pred.live_target_regime ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-[var(--text-muted)]">Calibration</dt>
          <dd className="mono text-xs">{pred.calibration_target_regime ?? '—'}</dd>
        </div>
      </dl>
      {(pred.calibration_target_regime !== pred.live_target_regime ||
        !pred.settlement_source_transfer_validated) && (
        <div className="mt-3 text-xs text-[var(--warning)]">⚠ source mismatch / unvalidated transfer</div>
      )}
    </article>
  )
}

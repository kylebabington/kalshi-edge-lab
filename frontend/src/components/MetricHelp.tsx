import { useId, useState } from 'react'

const METRIC_HELP = {
  brier:
    'Brier score: the mean squared gap between the forecast probability and the outcome (1 if it happened, 0 if not). 0 is perfect; lower is better.',
  log_loss:
    'Log loss: the negative log of the probability given to what actually happened. Confident misses are penalized heavily; lower is better.',
  event_equal:
    "Event-equal weighting: each event's contracts are averaged first, then every event counts once, so events with many contracts do not dominate.",
  paired:
    'Paired difference: candidate minus reference, computed only on contracts where both have a probability. A negative value means the candidate had lower error on that shared sample.',
} as const

type MetricKey = keyof typeof METRIC_HELP

/** Short inline explanation; a toggle button rather than a hover-only tooltip so it works on touch and keyboard. */
export function MetricHelp({ metric, label }: { metric: MetricKey; label?: string }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <span className="metric-help">
      <button
        type="button"
        className="metric-help-button"
        aria-expanded={open}
        aria-controls={id}
        aria-label={`What is ${label ?? metric.replace('_', ' ')}?`}
        onClick={() => setOpen((o) => !o)}
      >
        ?
      </button>
      {open ? (
        <span id={id} role="note" className="metric-help-text">
          {METRIC_HELP[metric]}
        </span>
      ) : null}
    </span>
  )
}

export function ResultKindBadge({ kind }: { kind: 'prospective' | 'historical' }) {
  return kind === 'prospective' ? (
    <span className="badge badge-info" title="Forecasts were saved before the outcome was known, then scored after settlement">
      Prospective · saved before outcome
    </span>
  ) : (
    <span className="badge badge-warning" title="Retrospective backtest or replay on past data under a frozen protocol">
      Historical research · backtest / replay
    </span>
  )
}

export function SampleNote({ n, unit, period }: { n: number | string | null | undefined; unit: string; period: string }) {
  return (
    <p className="text-xs text-[var(--text-muted)]">
      Sample: <span className="mono">{n ?? 'unknown'}</span> {unit} · Data period: <span className="mono">{period}</span>
    </p>
  )
}

import { Link } from 'react-router-dom'
import { ResultKindBadge } from '../MetricHelp'
import { StatusRecordBadge } from '../StatusRecordBadge'
import { experimentPath } from '../../api/research'
import { periodText, sampleValue, statusRecords } from './format'
import {
  MODE_LABELS,
  type CollectionMode,
  type ExperimentCard as Card,
  type ExperimentStatus,
  type ResultLink,
  type Sample,
} from '../../types/research'

const RESULT_KIND_LABELS: Record<string, string> = {
  tracked: 'tracked summary',
  local: 'local artifact',
  api: 'live page',
}

export function DesignationBadge({ designation }: { designation: string }) {
  if (designation === 'historical' || designation === 'prospective') return <ResultKindBadge kind={designation} />
  return <span className="badge badge-warning">designation unknown</span>
}

export function ModeChips({ modes }: { modes: CollectionMode[] }) {
  if (!modes.length) return <span className="text-xs text-[var(--text-muted)]">collection mode unknown</span>
  return (
    <span className="flex flex-wrap gap-1">
      {modes.map((m) => (
        <span key={m} className="badge badge-info">
          {MODE_LABELS[m] ?? m}
        </span>
      ))}
    </span>
  )
}

export function SampleList({ samples }: { samples: Sample[] }) {
  if (!samples.length) return <p className="text-xs text-[var(--text-muted)]">No sample counts reported.</p>
  return (
    <dl className="grid gap-1 text-sm">
      {samples.map((s) => (
        <div key={s.label} className="flex flex-wrap justify-between gap-x-3 border-b border-[var(--border)] py-0.5">
          <dt className="text-[var(--text-muted)]" title={s.basis}>
            {s.label}
          </dt>
          <dd className="mono" title={s.value == null ? (s.reason ?? s.basis) : s.basis}>
            {sampleValue(s)}
            {s.value == null && s.reason ? <span className="font-sans text-xs text-[var(--text-muted)]"> · {s.reason}</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  )
}

export function ResultList({ results }: { results: ResultLink[] }) {
  if (!results.length) return <p className="text-xs text-[var(--text-muted)]">No results listed.</p>
  return (
    <ul className="space-y-1 text-sm">
      {results.map((r) => (
        <li key={`${r.kind}:${r.label}`} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className={r.available ? 'badge badge-info' : 'badge badge-warning'}>
            {r.available ? 'available' : 'unavailable'}
          </span>
          {r.available && r.href ? (
            <Link to={r.href} className="underline underline-offset-2">
              {r.label}
            </Link>
          ) : (
            <span>{r.label}</span>
          )}
          <span className="text-xs text-[var(--text-muted)]">{RESULT_KIND_LABELS[r.kind] ?? r.kind}</span>
          {r.path ? <span className="mono text-xs text-[var(--text-muted)] break-all">{r.path}</span> : null}
          {!r.available && r.reason ? <span className="text-xs text-[var(--text-muted)]">· {r.reason}</span> : null}
        </li>
      ))}
    </ul>
  )
}

export function StatusSummary({ status }: { status: ExperimentStatus }) {
  const records = statusRecords(status)
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        <StatusRecordBadge record={status.operations ?? null} showBasis={false} />
        <StatusRecordBadge record={status.integrity ?? null} showBasis={false} />
        {(status.policy ?? []).map((p) => (
          <StatusRecordBadge key={`${p.label}:${p.state}`} record={p} showBasis={false} showKind={false} />
        ))}
      </div>
      {records.length ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-[var(--text-muted)]">Status basis ({records.length})</summary>
          <ul className="mt-2 space-y-2">
            {records.map((r, i) => (
              <li key={`${r.kind}:${r.label}:${i}`}>
                <div className="text-xs font-medium">{r.label}</div>
                <StatusRecordBadge record={r} />
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  )
}

export function ExperimentCard({ card }: { card: Card }) {
  return (
    <article className="panel space-y-3 min-w-0" aria-labelledby={`exp-${card.id}`}>
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="badge badge-info">{card.category === 'weather' ? 'Weather' : 'Sports'}</span>
          <DesignationBadge designation={card.designation} />
          <span className="mono text-[var(--text-muted)]">{card.version ?? 'version unknown'}</span>
        </div>
        <h4 id={`exp-${card.id}`} className="font-semibold leading-snug">
          <Link to={experimentPath(card.id)} className="hover:underline underline-offset-2">
            {card.title}
          </Link>
        </h4>
        <div className="mono text-xs text-[var(--text-muted)] break-all">{card.id}</div>
      </header>

      {card.error ? (
        <p className="warning-banner text-sm" role="status">
          Part of this card could not be built: <span className="mono">{card.error}</span>
        </p>
      ) : null}
      {card.loading ? (
        <p className="text-sm text-[var(--text-muted)]" role="status">
          The sports index is still loading on the server. Counts stay unknown until it is ready.
        </p>
      ) : null}

      <ModeChips modes={card.modes} />
      <dl className="grid gap-1 text-sm">
        <div className="flex flex-wrap justify-between gap-x-3">
          <dt className="text-[var(--text-muted)]">Data period</dt>
          <dd className="mono text-right" title={card.period?.basis}>
            {periodText(card.period, card.period_reason)}
          </dd>
        </div>
        {card.lifecycle ? (
          <div className="flex flex-wrap justify-between gap-x-3">
            <dt className="text-[var(--text-muted)]">Lifecycle</dt>
            <dd className="text-right" title={card.lifecycle.basis}>
              {card.lifecycle.value}
            </dd>
          </div>
        ) : null}
      </dl>

      <SampleList samples={card.samples} />
      <div>
        <div className="text-xs font-medium text-[var(--text-muted)] mb-1">Results</div>
        <ResultList results={card.results} />
      </div>
      <StatusSummary status={card.status} />
    </article>
  )
}

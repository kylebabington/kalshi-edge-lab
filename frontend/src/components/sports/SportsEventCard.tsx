import { CANDIDATES, type SportsEventItem } from '../../types/sports'
import { StatusBadge } from '../StatusBadge'
import { CANDIDATE_LABELS, VIEW_LABELS, fmtMinutes, fmtTime, label } from './format'

export function SportsEventCard({ event }: { event: SportsEventItem }) {
  const captured = event.capture_status === 'WINDOW_CAPTURE'
  const heading = event.participants.length ? event.participants.join(' · ') : (event.title ?? event.cluster)
  return (
    <article className="panel h-full hover:border-[color-mix(in_oklab,var(--accent)_40%,var(--border))] transition">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-[0.14em] text-[var(--text-muted)]">
            {event.sport ?? 'Unknown sport'} · {event.competition_name ?? event.competition}
          </div>
          <h3 className="mt-1 text-base font-semibold break-words">{heading}</h3>
          {event.title && event.participants.length ? (
            <div className="text-xs text-[var(--text-muted)] mt-0.5">{event.title}</div>
          ) : null}
        </div>
        <StatusBadge status={VIEW_LABELS[event.view] ?? event.view} />
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
        <div>
          <dt className="text-[var(--text-muted)] text-xs">Event start</dt>
          <dd className="mono text-xs">{fmtTime(event.event_start)}</dd>
        </div>
        <div>
          <dt className="text-[var(--text-muted)] text-xs">Captured at</dt>
          <dd className="mono text-xs">{captured ? fmtTime(event.prediction_as_of) : 'not captured'}</dd>
        </div>
        <div>
          <dt className="text-[var(--text-muted)] text-xs">Forecast horizon</dt>
          <dd className="mono text-xs">{captured ? `${fmtMinutes(event.horizon_minutes)} before start` : '—'}</dd>
        </div>
        <div>
          <dt className="text-[var(--text-muted)] text-xs">Settlement</dt>
          <dd className="text-xs">
            {captured
              ? `${label(event.settlement.state)} (${event.settlement.final}/${event.settlement.contracts} final)`
              : 'not applicable'}
          </dd>
        </div>
      </dl>
      {captured ? (
        <div className="mt-3">
          <div className="text-xs text-[var(--text-muted)]">
            Candidates available (of {event.contracts_ok} contracts)
          </div>
          <ul className="mt-1 flex flex-wrap gap-2 text-xs mono">
            {CANDIDATES.map((c) => (
              <li key={c} className="status-chip">
                {CANDIDATE_LABELS[c]} {event.candidate_available[c]}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="mt-3 text-xs text-[var(--warning)]">{event.missed_reason ?? 'Window missed'}</p>
      )}
      {event.collection_mode ? (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Collection: {event.collection_mode === 'automatic' ? 'automatic (scheduled)' : label(event.collection_mode)}
        </p>
      ) : null}
      {event.integrity.record_checksum !== 'ok' ? (
        <p className="mt-2 text-xs text-[var(--negative)]">Integrity problem: record checksum mismatch</p>
      ) : null}
    </article>
  )
}

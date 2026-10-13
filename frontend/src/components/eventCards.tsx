import { eventPath } from '../api/sports'
import { weatherEventPath } from '../api/weather'
import {
  SPORTS_VIEW_LABELS,
  WEATHER_VIEW_LABELS,
  competitionLabel,
  familyLabel,
  sportLabel,
  sportsEventTitle,
} from '../lib/labels'
import { eventSettlementText, snapshotScoreText, weatherTitle } from '../lib/weatherText'
import { fmtLocal } from '../lib/time'
import { CANDIDATES, type SportsEventItem } from '../types/sports'
import type { SavedWeatherEvent, WeatherEvent } from '../types/weather'
import { CANDIDATE_LABELS, fmtMinutes, label } from './sports/format'
import { UnifiedEventCard, type EventCardModel } from './UnifiedEventCard'

function fmtTemp(v: number | null | undefined): string {
  return v == null || !Number.isFinite(v) ? 'unavailable' : `${v.toFixed(1)}°F`
}

// ---------------------------------------------------------------- sports

function sportsCardModel(e: SportsEventItem): EventCardModel {
  const captured = e.capture_status === 'WINDOW_CAPTURE'
  return {
    category: 'sports',
    eventKey: e.event_id,
    href: eventPath(e.event_id),
    title: sportsEventTitle(e),
    eyebrow: `${sportLabel(e.sport)} · ${competitionLabel(e.competition, e.competition_name)}`,
    status: SPORTS_VIEW_LABELS[e.view] ?? e.view,
    dataKind: 'saved',
    rows: [
      { label: 'Starts', value: <span className="mono">{fmtLocal(e.event_start)}</span> },
      {
        label: 'Forecast captured',
        value: captured ? (
          <span className="mono">
            {fmtLocal(e.prediction_as_of)}
            {e.horizon_minutes != null ? ` (${fmtMinutes(e.horizon_minutes)} before start)` : ''}
          </span>
        ) : (
          'not captured'
        ),
      },
      {
        label: 'Settlement',
        value: captured ? `${label(e.settlement.state)} (${e.settlement.final}/${e.settlement.contracts} final)` : 'not applicable',
      },
      {
        label: 'Last settled',
        value: captured ? (
          e.settlement.last_settled_at ? <span className="mono">{fmtLocal(e.settlement.last_settled_at)}</span> : 'none yet'
        ) : (
          'not applicable'
        ),
      },
    ],
  }
}

export function SportsCard({ event }: { event: SportsEventItem }) {
  const captured = event.capture_status === 'WINDOW_CAPTURE'
  return (
    <UnifiedEventCard model={sportsCardModel(event)}>
      {event.title && event.participants.length ? (
        <p className="text-xs text-[var(--text-muted)]">Outcomes: {event.participants.join(' · ')}</p>
      ) : null}
      {captured ? (
        <div>
          <div className="text-xs text-[var(--text-muted)]">
            {event.families.map(familyLabel).join(', ') || 'No contract families'} · candidates available of{' '}
            {event.contracts_ok} contracts
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
        <p className="text-xs text-[var(--warning)]">{event.missed_reason ?? 'Window missed'}</p>
      )}
      {event.collection_mode ? (
        <p className="text-xs text-[var(--text-muted)]">
          Collection: {event.collection_mode === 'automatic' ? 'automatic (scheduled)' : label(event.collection_mode)}
        </p>
      ) : null}
      {event.integrity.record_checksum !== 'ok' ? (
        <p className="text-xs text-[var(--negative)]">Integrity problem: record checksum mismatch</p>
      ) : null}
    </UnifiedEventCard>
  )
}

// ---------------------------------------------------------------- weather

function savedWeatherCardModel(e: SavedWeatherEvent): EventCardModel {
  const snap = e.displayed_snapshot
  return {
    category: 'weather',
    eventKey: e.event_ticker,
    href: weatherEventPath(e.event_ticker),
    title: weatherTitle(e.event_ticker, e.target_date),
    eyebrow: `Weather · ${e.event_ticker}`,
    status: WEATHER_VIEW_LABELS[e.view] ?? e.view,
    dataKind: 'saved',
    rows: [
      { label: 'Latest forecast saved', value: <span className="mono">{fmtLocal(snap.prediction_as_of)}</span> },
      { label: 'Expected high (latest)', value: <span className="mono">{fmtTemp(snap.expected_high)}</span> },
      { label: 'Event settlement', value: eventSettlementText(e.event_settlement) },
      { label: 'Latest snapshot score', value: snapshotScoreText(e.displayed_snapshot_score) },
    ],
  }
}

export function SavedWeatherCard({ event }: { event: SavedWeatherEvent }) {
  return (
    <UnifiedEventCard model={savedWeatherCardModel(event)}>
      <p className="text-xs text-[var(--text-muted)]">
        {event.snapshot_count} saved {event.snapshot_count === 1 ? 'snapshot' : 'snapshots'} ({event.scored_snapshot_count}{' '}
        scored) · checkpoints captured {event.receipts.captured}, missed {event.receipts.missed}
      </p>
      {event.event_settlement.state === 'confirmed' && event.displayed_snapshot_score.state !== 'scored' ? (
        <p className="text-xs text-[var(--warning)]">
          The event is settled, but the latest snapshot shown here has no valid score of its own.
        </p>
      ) : null}
    </UnifiedEventCard>
  )
}

function liveWeatherCardModel(e: WeatherEvent): EventCardModel {
  const pred = e.prediction
  return {
    category: 'weather',
    eventKey: e.event_ticker,
    href: weatherEventPath(e.event_ticker),
    title: weatherTitle(e.event_ticker, e.target_date ?? pred.target_date),
    eyebrow: `Weather · ${e.event_ticker}`,
    status: pred.prediction_status,
    dataKind: 'market',
    rows: [
      { label: 'Expected high (now)', value: <span className="mono">{fmtTemp(pred.expected_high)}</span> },
      { label: 'Confidence', value: pred.confidence_label ?? 'unavailable' },
      { label: 'Settlement target', value: <span className="mono">{pred.live_target_regime ?? 'unknown'}</span> },
      { label: 'Market quotes', value: `${e.kalshi_markets.length} buckets` },
    ],
  }
}

export function LiveWeatherCard({ event }: { event: WeatherEvent }) {
  const pred = event.prediction
  const mismatch = pred.calibration_target_regime !== pred.live_target_regime || !pred.settlement_source_transfer_validated
  return (
    <UnifiedEventCard model={liveWeatherCardModel(event)}>
      {mismatch ? <p className="text-xs text-[var(--warning)]">Source mismatch / unvalidated transfer</p> : null}
    </UnifiedEventCard>
  )
}

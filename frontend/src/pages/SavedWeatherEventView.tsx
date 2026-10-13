import { DataKindTag } from '../components/UnifiedEventCard'
import { eventSettlementText, snapshotScoreText, weatherTitle } from '../lib/weatherText'
import { MetricHelp } from '../components/MetricHelp'
import { StatusBadge } from '../components/StatusBadge'
import { TimezoneNote } from '../components/TimezoneNote'
import { DataStamp } from '../components/sports/DataStamp'
import { fmtNum, fmtProb } from '../components/sports/format'
import { WEATHER_VIEW_LABELS } from '../lib/labels'
import { fmtLocal, fmtTargetDate } from '../lib/time'
import type { SavedWeatherEventDetail } from '../types/weather'
import { StarButton } from '../watchlist/StarButton'
import { BackLink } from './BackLink'

export function SavedWeatherEventView({ data, loadedAt }: { data: SavedWeatherEventDetail; loadedAt: Date | null }) {
  const e = data.event
  const snap = e.displayed_snapshot
  const score = e.displayed_snapshot_score
  const settle = e.event_settlement
  const title = weatherTitle(e.event_ticker, e.target_date)
  const probs = Object.entries(snap.bucket_probabilities)
  return (
    <div className="space-y-5">
      <header className="space-y-2">
        <BackLink fallback="/markets" label="Weather events" />
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Weather · {e.event_ticker}</p>
            <h2 className="text-xl font-semibold">{title}</h2>
          </div>
          <StarButton category="weather" eventKey={e.event_ticker} label={title} size="md" />
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={WEATHER_VIEW_LABELS[e.view] ?? e.view} />
          <DataKindTag kind="saved" />
          <span className="badge badge-warning">Research only</span>
        </div>
        <p className="text-sm text-[var(--text-muted)]">
          This event is not among the currently open markets, so only saved forecasts are shown. They were recorded
          before the outcome and never change.
        </p>
        <TimezoneNote />
        <DataStamp generatedAt={data.generated_at} loadedAt={loadedAt} source={data.source} />
      </header>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="panel space-y-2">
          <h3 className="font-semibold">Event settlement</h3>
          <p className="text-sm">{eventSettlementText(settle)}</p>
          {settle.state === 'confirmed' ? (
            <dl className="text-xs grid grid-cols-2 gap-1">
              <dt className="text-[var(--text-muted)]">Recorded high</dt>
              <dd className="mono">{settle.actual_high_f == null ? 'unavailable' : `${settle.actual_high_f}°F`}</dd>
              <dt className="text-[var(--text-muted)]">Source</dt>
              <dd className="mono">{settle.source ?? 'unknown'}</dd>
              <dt className="text-[var(--text-muted)]">Confirmed (scored) at</dt>
              <dd className="mono">{fmtLocal(settle.confirmed_at)}</dd>
            </dl>
          ) : (
            <p className="text-xs text-[var(--text-muted)]">{settle.reason}</p>
          )}
          <p className="text-xs text-[var(--text-muted)]">
            Confirmed when any valid score of any saved snapshot records the winning bucket. This is separate from
            whether the snapshot shown below has been scored.
          </p>
        </div>
        <div className="panel space-y-2">
          <h3 className="font-semibold">Latest saved snapshot</h3>
          <dl className="text-xs grid grid-cols-2 gap-1">
            <dt className="text-[var(--text-muted)]">Saved (prediction as of)</dt>
            <dd className="mono">{fmtLocal(snap.prediction_as_of)}</dd>
            <dt className="text-[var(--text-muted)]">Target date</dt>
            <dd>{fmtTargetDate(e.target_date)}</dd>
            <dt className="text-[var(--text-muted)]">Status / confidence</dt>
            <dd>
              {snap.prediction_status ?? 'unknown'} / {snap.confidence_label ?? 'unavailable'}
            </dd>
            <dt className="text-[var(--text-muted)]">Score of this snapshot</dt>
            <dd>{snapshotScoreText(score)}</dd>
            {score.state === 'scored' ? (
              <>
                <dt className="text-[var(--text-muted)]">
                  Brier <MetricHelp metric="brier" label="Brier" />
                </dt>
                <dd className="mono">{fmtNum(score.brier)} (n = 1 snapshot, {fmtTargetDate(e.target_date)})</dd>
                <dt className="text-[var(--text-muted)]">
                  Log loss <MetricHelp metric="log_loss" label="log loss" />
                </dt>
                <dd className="mono">{fmtNum(score.log_loss)} (n = 1 snapshot, {fmtTargetDate(e.target_date)})</dd>
              </>
            ) : null}
          </dl>
          <div className="overflow-x-auto">
            <table className="data-table">
              <caption>Saved bucket probabilities (latest snapshot, stored order)</caption>
              <thead>
                <tr>
                  <th scope="col">Bucket</th>
                  <th scope="col">Probability</th>
                </tr>
              </thead>
              <tbody>
                {probs.length === 0 ? (
                  <tr>
                    <td colSpan={2} className="text-[var(--text-muted)]">
                      Unavailable: this snapshot has no valid bucket probabilities.
                    </td>
                  </tr>
                ) : (
                  probs.map(([bucket, p]) => (
                    <tr key={bucket}>
                      <th scope="row">{bucket}</th>
                      <td className="mono">{fmtProb(p)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {snap.invalid_probabilities ? (
            <p className="text-xs text-[var(--warning)]">
              {snap.invalid_probabilities} stored probabilities were not valid numbers and are omitted (unavailable, not zero).
            </p>
          ) : null}
        </div>
      </section>

      <section className="panel space-y-2">
        <h3 className="font-semibold">All saved snapshots ({data.snapshots.length})</h3>
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Saved at</th>
                <th scope="col">Checkpoint</th>
                <th scope="col">Status</th>
                <th scope="col">Expected high</th>
                <th scope="col">Snapshot score</th>
              </tr>
            </thead>
            <tbody>
              {data.snapshots.map((s) => (
                <tr key={s.snapshot_id}>
                  <th scope="row" className="mono text-xs">
                    {fmtLocal(s.prediction_as_of)}
                  </th>
                  <td className="mono text-xs">{s.checkpoint_id ?? '—'}</td>
                  <td className="text-xs">{s.prediction_status ?? 'unknown'}</td>
                  <td className="mono text-xs">{s.expected_high == null ? 'unavailable' : `${s.expected_high.toFixed(1)}°F`}</td>
                  <td className="text-xs">
                    {snapshotScoreText(s.score)}
                    {s.score.state === 'scored' ? <span className="mono"> · Brier {fmtNum(s.score.brier)}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

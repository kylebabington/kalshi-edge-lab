import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import { fmtProb } from './sports/format'
import { fmtLocal } from '../lib/time'
import { bucketOptionLabel, defaultBucket, historyPoints, type HistoryPoint } from '../lib/weatherHistory'
import type { ObservationPosition, SavedSnapshotHistoryRow, SavedWeatherEventDetail } from '../types/weather'

const POSITION_TEXT: Record<ObservationPosition, string> = {
  in_day: 'in the target climate day',
  before_day: 'before the target climate day',
  after_day: 'after the target climate day',
  unknown: 'time unknown',
}

function probabilityCell(row: SavedSnapshotHistoryRow, bucketId: string | null): string {
  if (!bucketId) return 'unavailable'
  const v = row.probabilities?.[bucketId]
  if (!v) return 'not in this snapshot'
  if (v.invalid || v.p == null) return 'unavailable (invalid saved value)'
  return fmtProb(v.p)
}

function runText(row: SavedSnapshotHistoryRow): string {
  const r = row.run
  if (!r) return 'unavailable'
  const parts = [r.forecast_run ?? r.run_id ?? 'run unknown']
  if (r.gfs_model_run_at) parts.push(`GFS ${fmtLocal(r.gfs_model_run_at)}`)
  if (r.hrrr_model_run_at) parts.push(`HRRR ${fmtLocal(r.hrrr_model_run_at)}`)
  if (r.gefs_member_count != null) parts.push(`GEFS ${r.gefs_member_count} members`)
  return parts.join(' · ')
}

function observationText(row: SavedSnapshotHistoryRow): string {
  const o = row.observation
  if (!o || !o.present) return o?.used_by_prediction === false ? 'not used (none saved)' : 'none saved'
  const used = o.used_by_prediction == null ? 'use unknown' : o.used_by_prediction ? 'used' : 'not used'
  const high = o.high_so_far_f == null ? 'high so far unavailable' : `high so far ${o.high_so_far_f.toFixed(1)}°F`
  const latest = o.latest_timestamp
    ? `latest ${fmtLocal(o.latest_timestamp)} (${POSITION_TEXT[o.latest_position ?? 'unknown']})`
    : 'latest time unknown'
  const pts = o.trajectory_points
  const counts = pts
    ? `${pts.in_day} in day · ${pts.before_day + pts.after_day} outside${pts.unknown ? ` · ${pts.unknown} time unknown` : ''}`
    : ''
  return [o.station_id ?? 'station unknown', used, high, latest, counts].filter(Boolean).join(' · ')
}

function HistoryTooltip({ active, payload }: { active?: boolean; payload?: { payload: HistoryPoint }[] }) {
  if (!active || !payload?.length) return null
  const pt = payload[0].payload
  return (
    <div className="panel text-xs space-y-0.5">
      <div className="mono">{fmtLocal(pt.row.prediction_as_of)}</div>
      <div>Checkpoint {pt.row.checkpoint_id ?? 'not recorded'}</div>
      <div className="mono">Saved model probability {fmtProb(pt.p)}</div>
    </div>
  )
}

/**
 * Saved model probabilities over capture time for one bucket. Points only: nothing was recorded between captures,
 * so nothing is drawn between them. These are saved model outputs, never market prices.
 */
export function PredictionHistory({ data }: { data: SavedWeatherEventDetail }) {
  const [params, setParams] = useSearchParams()
  const buckets = data.buckets ?? []
  const requested = params.get('bucket')
  const bucketId = requested && buckets.some((b) => b.id === requested) ? requested : defaultBucket(data)
  const bucket = buckets.find((b) => b.id === bucketId) ?? null
  const rows = data.snapshots
  const points = useMemo(() => historyPoints(rows, bucketId), [rows, bucketId])
  const missing = rows.length - points.length
  const domain = useMemo(() => {
    if (!points.length) return [0, 1]
    const lo = points[0].t
    const hi = points[points.length - 1].t
    const pad = Math.max((hi - lo) * 0.05, 30 * 60 * 1000)
    return [lo - pad, hi + pad]
  }, [points])

  const selectBucket = (id: string) => {
    const next = new URLSearchParams(params)
    next.set('bucket', id)
    setParams(next, { replace: true })
  }

  if (!buckets.length) {
    return (
      <section className="panel space-y-2" aria-labelledby="prediction-history">
        <h3 id="prediction-history" className="font-semibold">Prediction history</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Unavailable: no saved snapshot for this event has bucket probabilities.
        </p>
      </section>
    )
  }

  return (
    <section className="panel space-y-3" aria-labelledby="prediction-history">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <h3 id="prediction-history" className="font-semibold">Prediction history</h3>
        <label className="text-sm">
          Bucket{' '}
          <select
            className="mono ml-2 max-w-full rounded border border-[var(--border)] bg-transparent px-2 py-1"
            value={bucketId ?? ''}
            onChange={(e) => selectBucket(e.target.value)}
          >
            {buckets.map((b) => (
              <option key={b.id} value={b.id}>
                {bucketOptionLabel(b)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Each point is the saved model probability for the selected bucket at the time the snapshot was captured. Points
        are not connected or interpolated: nothing was recorded between captures. These are saved model outputs, not
        market prices.
      </p>
      {bucket && bucket.definition_variants > 1 ? (
        <p className="text-xs text-[var(--warning)]">
          The label “{bucket.label}” was saved with {bucket.definition_variants} different definitions; each is a
          separate series.
        </p>
      ) : null}
      {points.length ? (
        <div className="h-56 sm:h-64" role="img" aria-label={`Saved probability for ${bucket?.label ?? 'bucket'} at ${points.length} capture times; see the table below.`}>
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
              <XAxis
                type="number"
                dataKey="t"
                domain={domain}
                scale="time"
                tickFormatter={(v) => fmtLocal(new Date(Number(v)).toISOString())}
                tick={{ fontSize: 11 }}
                minTickGap={40}
              />
              <YAxis
                type="number"
                dataKey="p"
                domain={[0, 1]}
                tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`}
                width={44}
              />
              <Tooltip content={<HistoryTooltip />} />
              <Scatter data={points} fill="#3b82f6" line={false} isAnimationActive={false} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="text-sm text-[var(--text-muted)]">No saved snapshot has a valid probability for this bucket.</p>
      )}
      <p className="text-xs text-[var(--text-muted)]">
        {points.length} of {rows.length} saved snapshots plotted
        {missing ? ` · ${missing} without a valid saved probability for this bucket (shown as unavailable below)` : ''}.
        Observation days use the fixed-EST climate day (midnight to midnight EST all year), not the New York calendar day.
      </p>
      <div className="overflow-x-auto">
        <table className="data-table">
          <caption>Saved snapshots in capture order · selected bucket: {bucket ? bucketOptionLabel(bucket) : 'none'}</caption>
          <thead>
            <tr>
              <th scope="col">Captured (prediction as of)</th>
              <th scope="col">Checkpoint</th>
              <th scope="col">Saved probability</th>
              <th scope="col">Model / run</th>
              <th scope="col">Observations</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.snapshot_id}>
                <th scope="row" className="mono text-xs">{fmtLocal(row.prediction_as_of)}</th>
                <td className="mono text-xs">{row.checkpoint_id ?? 'not recorded'}</td>
                <td className="mono text-xs">{probabilityCell(row, bucketId)}</td>
                <td className="text-xs">{runText(row)}</td>
                <td className="text-xs">{observationText(row)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { PredictionSnapshotMeta, WeatherEvent } from '../types/weather'
import { fetchEventSnapshots, fetchSnapshot } from '../api/weather'
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

function fmtTemp(value: unknown) {
  if (value == null || value === '') return '—'
  const n = Number(value)
  if (Number.isFinite(n)) return `${n.toFixed(1)}°F`
  return String(value)
}

const FRESH_SECONDS = 2 * 3600
const AGING_SECONDS = 6 * 3600

function freshnessFromAgeSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return 'unavailable'
  if (seconds <= FRESH_SECONDS) return 'fresh'
  if (seconds <= AGING_SECONDS) return 'aging'
  return 'stale'
}

/** Age of evidence relative to a reference time (NOW wall-clock or snapshot as-of). */
function ageSecondsAt(
  referenceIso: string | null | undefined,
  asOfIso: string | null | undefined,
): number | null {
  if (!referenceIso || !asOfIso) return null
  const ref = Date.parse(referenceIso)
  const asOf = Date.parse(asOfIso)
  if (!Number.isFinite(ref) || !Number.isFinite(asOf)) return null
  return Math.max(0, (asOf - ref) / 1000)
}

function freshnessBadge(status: unknown) {
  const s = String(status || 'unavailable').toUpperCase()
  return <StatusBadge status={s} />
}

function formatAgeHours(seconds: number | null | undefined, historical: boolean) {
  if (seconds == null) return '—'
  const label = historical ? 'age at snapshot' : 'run age'
  return `${label} ${(seconds / 3600).toFixed(1)}h`
}

function EvidenceCard({
  title,
  status,
  freshness,
  children,
}: {
  title: string
  status?: unknown
  freshness?: unknown
  children: ReactNode
}) {
  return (
    <div className="rounded-lg border border-[var(--border)] p-3 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs uppercase tracking-[0.14em] text-[var(--text-muted)]">{title}</div>
        <div className="flex flex-wrap gap-1">
          {freshnessBadge(freshness)}
          {status != null ? <StatusBadge status={String(status).toUpperCase()} /> : null}
        </div>
      </div>
      <div className="text-sm space-y-1">{children}</div>
    </div>
  )
}

type Props = {
  loading: boolean
  error: string | null
  event: WeatherEvent | null
}

export function MarketDetailPage({ loading, error, event }: Props) {
  const [asOfMode, setAsOfMode] = useState<'NOW' | string>('NOW')
  const [snapshotMetas, setSnapshotMetas] = useState<PredictionSnapshotMeta[]>([])
  const [historical, setHistorical] = useState<WeatherEvent | null>(null)
  const [histLoading, setHistLoading] = useState(false)
  const [evolutionView, setEvolutionView] = useState<'probability' | 'temperature'>('probability')
  const [selectedBucket, setSelectedBucket] = useState<string | null>(null)
  const [snapshotPayloads, setSnapshotPayloads] = useState<Record<string, Record<string, unknown>>>({})

  useEffect(() => {
    if (!event?.event_ticker) return
    let cancelled = false
    async function loadMetas() {
      try {
        const payload = await fetchEventSnapshots(event!.event_ticker)
        if (!cancelled) setSnapshotMetas(payload.snapshots || [])
      } catch {
        if (!cancelled) setSnapshotMetas([])
      }
    }
    void loadMetas()
    return () => {
      cancelled = true
    }
  }, [event?.event_ticker])

  useEffect(() => {
    if (asOfMode === 'NOW' || !asOfMode) {
      setHistorical(null)
      return
    }
    let cancelled = false
    async function loadSnap() {
      setHistLoading(true)
      try {
        const snap = await fetchSnapshot(asOfMode)
        if (cancelled) return
        // Reconstruct a WeatherEvent-shaped view from one coherent snapshot.
        const prediction = (snap.prediction || {}) as WeatherEvent['prediction']
        const view: WeatherEvent = {
          event_ticker: String(snap.event_ticker),
          target_date: String(snap.target_date || ''),
          resolution: {
            event_ticker: String(snap.event_ticker),
            target_date: String(snap.target_date || ''),
            settlement_source_regime: String(snap.resolution_regime || 'unknown'),
            resolution_source: String(snap.resolution_regime || 'unknown'),
            resolution_certainty: String(snap.resolution_certainty || 'unknown'),
            station_id: 'SNAPSHOT',
            warnings: [],
          },
          prediction: {
            ...prediction,
            event_ticker: String(snap.event_ticker),
            target_date: String(snap.target_date || ''),
            prediction_status: String(snap.prediction_status || prediction.prediction_status),
            bucket_probabilities:
              (snap.external_probability_distribution as Record<string, number>) ||
              prediction.bucket_probabilities ||
              {},
          },
          evidence: (snap.evidence as Record<string, unknown>) || {},
          kalshi_markets:
            ((snap.kalshi_quote_sidecar as { markets?: WeatherEvent['kalshi_markets'] })?.markets) ||
            [],
          research_status: (snap.research_status as Record<string, unknown>) || {},
          warnings: (snap.warnings as string[]) || [],
        }
        setHistorical(view)
        setSnapshotPayloads((prev) => ({ ...prev, [asOfMode]: snap }))
      } catch {
        if (!cancelled) setHistorical(null)
      } finally {
        if (!cancelled) setHistLoading(false)
      }
    }
    void loadSnap()
    return () => {
      cancelled = true
    }
  }, [asOfMode])

  useEffect(() => {
    let cancelled = false
    async function loadAll() {
      const next: Record<string, Record<string, unknown>> = {}
      for (const meta of snapshotMetas) {
        if (!meta.snapshot_id) continue
        try {
          next[meta.snapshot_id] = await fetchSnapshot(meta.snapshot_id)
        } catch {
          /* skip */
        }
      }
      if (!cancelled) setSnapshotPayloads(next)
    }
    if (snapshotMetas.length) void loadAll()
    return () => {
      cancelled = true
    }
  }, [snapshotMetas])

  const display = asOfMode === 'NOW' ? event : historical
  const asOfLabel =
    asOfMode === 'NOW'
      ? 'NOW'
      : String((snapshotPayloads[asOfMode]?.prediction_as_of as string) || asOfMode)

  const buckets = useMemo(() => {
    if (!display) return []
    return Object.entries(display.prediction.bucket_probabilities || {}).map(([label, p]) => ({
      label,
      probability: p,
    }))
  }, [display])

  useEffect(() => {
    if (!selectedBucket && buckets.length) setSelectedBucket(buckets[0].label)
  }, [buckets, selectedBucket])

  const evolutionProbData = useMemo(() => {
    return snapshotMetas.map((meta) => {
      const snap = snapshotPayloads[meta.snapshot_id]
      const probs =
        (snap?.external_probability_distribution as Record<string, number>) ||
        ((snap?.prediction as { bucket_probabilities?: Record<string, number> })?.bucket_probabilities) ||
        {}
      const row: Record<string, string | number | null> = {
        time: String(meta.prediction_as_of || meta.snapshot_id),
      }
      if (selectedBucket) row[selectedBucket] = probs[selectedBucket] ?? null
      return row
    })
  }, [snapshotMetas, snapshotPayloads, selectedBucket])

  const evolutionTempData = useMemo(() => {
    return snapshotMetas.map((meta) => {
      const snap = snapshotPayloads[meta.snapshot_id]
      const evidence = (snap?.evidence || {}) as Record<string, Record<string, unknown>>
      return {
        time: String(meta.prediction_as_of || meta.snapshot_id),
        gfs: Number(evidence.gfs?.expected_high_f ?? evidence.gfs?.forecast_high_f) || null,
        hrrr: Number(evidence.hrrr?.forecast_high_f) || null,
        gefs: (() => {
          const direct = evidence.gefs?.forecast_high_f
          if (direct != null) return Number(direct) || null
          const meta = evidence.gefs?.metadata as { median?: number } | undefined
          return meta?.median != null ? Number(meta.median) || null : null
        })(),
        nws: Number(evidence.nws_forecast?.forecast_high_f) || null,
        obs: Number(evidence.observations?.high_so_far_f) || null,
      }
    })
  }, [snapshotMetas, snapshotPayloads])

  if (loading) return <LoadingState label="Loading market detail…" />
  if (error) return <ErrorState message={error} />
  if (!event) return <EmptyState title="Event not found" detail="No detail payload available." />
  if (histLoading && asOfMode !== 'NOW') return <LoadingState label="Loading snapshot…" />
  if (!display) return <EmptyState title="Snapshot unavailable" detail="Could not load selected As Of snapshot." />

  const pred = display.prediction
  const shadowBlock =
    display.shadow_predictions?.SHADOW_GFS_HRRR_EQUAL_V1 ??
    Object.values(display.shadow_predictions || {})[0]
  const evidence = (display.evidence ?? {}) as Record<string, Record<string, unknown>>
  const gfs = evidence.gfs ?? {}
  const gefs = evidence.gefs ?? {}
  const hrrr = evidence.hrrr ?? {}
  const nws = evidence.nws_forecast ?? {}
  const afd = evidence.nws_discussion ?? {}
  const obs = evidence.observations ?? {}
  const agreement = (evidence.agreement ?? {}) as Record<string, unknown>
  const gefsMeta = (gefs.metadata ?? {}) as Record<string, unknown>

  // Historical As Of: freshness relative to snapshot.prediction_as_of, never wall-clock.
  const historicalMode = asOfMode !== 'NOW'
  const freshnessAsOf = historicalMode
    ? String(
        snapshotPayloads[asOfMode]?.prediction_as_of ||
          (display.prediction as { as_of?: string }).as_of ||
          asOfLabel,
      )
    : new Date().toISOString()

  function cardFreshness(source: Record<string, unknown>) {
    const ref =
      (source.available_at as string | undefined) ||
      (source.model_run_at as string | undefined) ||
      (source.issued_at as string | undefined) ||
      (source.latest_timestamp as string | undefined)
    if (historicalMode) {
      const age = ageSecondsAt(ref, freshnessAsOf)
      return { seconds: age, status: freshnessFromAgeSeconds(age) }
    }
    const storedSeconds =
      source.freshness_seconds == null ? null : Number(source.freshness_seconds)
    const storedStatus = String(source.freshness_status || freshnessFromAgeSeconds(storedSeconds))
    return { seconds: storedSeconds, status: storedStatus }
  }

  const gfsFresh = cardFreshness(gfs)
  const hrrrFresh = cardFreshness(hrrr)
  const gefsFresh = cardFreshness(gefs)
  const nwsFresh = cardFreshness(nws)
  const obsFresh = cardFreshness(obs)
  const afdFresh = cardFreshness(afd)

  return (
    <div className="space-y-5">
      <header className="space-y-3">
        <div className="text-xs uppercase tracking-[0.16em] text-[var(--text-muted)]">NYC Daily High</div>
        <h2 className="text-2xl font-semibold">{display.target_date}</h2>
        <div className="mono text-[var(--text-muted)]">Event: {display.event_ticker}</div>
        <div className="flex flex-wrap gap-2">
          <span className="badge badge-info">Weather</span>
          <span className="badge badge-warning">Research Only</span>
          <span className="badge">{display.resolution.station_id}</span>
          <StatusBadge status={pred.prediction_status} />
        </div>
        {asOfMode !== 'NOW' ? (
          <div className="rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm space-y-1">
            <div>
              AS OF <span className="mono">{asOfLabel}</span> — all fields from one immutable snapshot
            </div>
            <div className="text-xs text-[var(--text-muted)]">
              Freshness is age at snapshot — not current wall-clock time. No live sources mixed in.
            </div>
          </div>
        ) : null}
      </header>

      <section className="panel space-y-2">
        <h3 className="font-semibold">As Of inspector</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Historical views use one coherent snapshot. Never mixes old prediction with new evidence.
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={`badge ${asOfMode === 'NOW' ? 'badge-info' : ''}`}
            onClick={() => setAsOfMode('NOW')}
          >
            NOW
          </button>
          {snapshotMetas.map((meta) => (
            <button
              key={meta.snapshot_id}
              type="button"
              className={`badge ${asOfMode === meta.snapshot_id ? 'badge-info' : ''}`}
              onClick={() => setAsOfMode(meta.snapshot_id)}
            >
              {new Date(meta.prediction_as_of).toLocaleString()}
            </button>
          ))}
          {!snapshotMetas.length ? (
            <span className="text-sm text-[var(--text-muted)]">No snapshots yet — run --snapshot-live</span>
          ) : null}
        </div>
      </section>

      <SourceMismatchBanner event={display} />

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="panel space-y-3">
          <h3 className="font-semibold">External Forecast</h3>
          <div className="text-3xl mono">
            {pred.expected_high == null ? '—' : `${pred.expected_high.toFixed(1)}°F`}
          </div>
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
          <h3 className="font-semibold">Source Agreement</h3>
          <div className="text-sm">
            <StatusBadge status={String(agreement.disagreement_label || 'UNKNOWN')} />
          </div>
          <dl className="grid grid-cols-2 gap-2 text-sm mono">
            <div>spread {agreement.max_min_disagreement_f == null ? '—' : `${Number(agreement.max_min_disagreement_f).toFixed(1)}°F`}</div>
            <div>GFS−HRRR {agreement.gfs_hrrr_difference_f == null ? '—' : Number(agreement.gfs_hrrr_difference_f).toFixed(1)}</div>
            <div>GFS−NWS {agreement.gfs_nws_difference_f == null ? '—' : Number(agreement.gfs_nws_difference_f).toFixed(1)}</div>
            <div>HRRR−NWS {agreement.hrrr_nws_difference_f == null ? '—' : Number(agreement.hrrr_nws_difference_f).toFixed(1)}</div>
          </dl>
          <p className="text-xs text-[var(--text-muted)]">Descriptive only — no blended probability.</p>
        </section>
      </div>

      <section className="panel space-y-3">
        <h3 className="font-semibold">EXTERNAL EVIDENCE</h3>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <EvidenceCard title="CALIBRATED GFS" status={gfs.quality_status} freshness={gfsFresh.status}>
            <div className="mono text-lg">{fmtTemp(gfs.expected_high_f ?? gfs.forecast_high_f)}</div>
            <div>forecast {fmtTemp(gfs.forecast_high_f)}</div>
            <div>calibration N={String(gfs.residual_sample_size ?? (gfs.metadata as { calibration_n?: number })?.calibration_n ?? '—')}</div>
            <div className="text-xs text-[var(--text-muted)]">
              run {String((gfs.metadata as { run_label?: string })?.run_label ?? gfs.model_run_at ?? '—')}
            </div>
            <div className="text-xs text-[var(--text-muted)]">
              {formatAgeHours(gfsFresh.seconds, historicalMode)}
            </div>
          </EvidenceCard>

          <EvidenceCard title="HRRR" status={hrrr.quality_status} freshness={hrrrFresh.status}>
            <div className="mono text-lg">{fmtTemp(hrrr.forecast_high_f)}</div>
            <div>previous {fmtTemp(hrrr.prior_forecast_high_f)}</div>
            <div>
              trend{' '}
              {hrrr.run_to_run_trend_f == null
                ? '—'
                : `${Number(hrrr.run_to_run_trend_f) >= 0 ? '+' : ''}${Number(hrrr.run_to_run_trend_f).toFixed(1)}°F`}
            </div>
            <div className="text-xs text-[var(--text-muted)]">
              {formatAgeHours(hrrrFresh.seconds, historicalMode)}
            </div>
          </EvidenceCard>

          <EvidenceCard title="GEFS" status={gefs.quality_status} freshness={gefsFresh.status}>
            <div className="mono text-lg">median {fmtTemp(gefs.forecast_high_f ?? gefsMeta.median)}</div>
            <div>mean {fmtTemp(gefs.expected_high_f ?? gefsMeta.mean)}</div>
            <div>spread {gefsMeta.spread == null ? '—' : `${Number(gefsMeta.spread).toFixed(1)}°F`}</div>
            <div>members {String(gefsMeta.member_count ?? '—')}</div>
          </EvidenceCard>

          <EvidenceCard title="NWS" status={nws.quality_status} freshness={nwsFresh.status}>
            <div className="mono text-lg">{fmtTemp(nws.forecast_high_f)}</div>
            <div>hourly high {fmtTemp(nws.hourly_high_f)}</div>
            <div className="text-xs text-[var(--text-muted)]">issued {String(nws.issued_at ?? '—')}</div>
          </EvidenceCard>

          <EvidenceCard title="KNYC OBSERVATIONS" status={obs.quality_status} freshness={obsFresh.status}>
            <div className="mono text-lg">{fmtTemp(obs.latest_temperature_f)}</div>
            <div>high so far {fmtTemp(obs.high_so_far_f)}</div>
            <div className="text-xs text-[var(--text-muted)]">high time {String(obs.time_of_high ?? '—')}</div>
            <div>
              Δ1h {obs.change_1h_f == null ? '—' : `${Number(obs.change_1h_f).toFixed(1)}`} · Δ3h{' '}
              {obs.change_3h_f == null ? '—' : `${Number(obs.change_3h_f).toFixed(1)}`}
            </div>
          </EvidenceCard>

          <EvidenceCard title="FORECAST DISCUSSION" status={afd.quality_status} freshness={afdFresh.status}>
            <div className="text-xs text-[var(--text-muted)]">issued {String(afd.issued_at ?? '—')}</div>
            <div>WFO {String(afd.wfo ?? '—')}</div>
            <ul className="list-disc pl-4 text-xs">
              {((afd.key_messages as string[]) || []).slice(0, 3).map((m) => (
                <li key={m}>{m}</li>
              ))}
              {!((afd.key_messages as string[]) || []).length ? <li>No key messages extracted</li> : null}
            </ul>
            <div className="text-xs text-[var(--text-muted)]">
              confidence: {((afd.confidence_wording as string[]) || []).slice(0, 1).join(' ') || '—'}
            </div>
            <div className="text-xs text-[var(--text-muted)]">
              model notes: {((afd.model_disagreement_mentions as string[]) || []).slice(0, 1).join(' ') || '—'}
            </div>
          </EvidenceCard>
        </div>
      </section>

      <section className="panel space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">Forecast Evolution</h3>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={`badge ${evolutionView === 'probability' ? 'badge-info' : ''}`}
              onClick={() => setEvolutionView('probability')}
            >
              Probability
            </button>
            <button
              type="button"
              className={`badge ${evolutionView === 'temperature' ? 'badge-info' : ''}`}
              onClick={() => setEvolutionView('temperature')}
            >
              Temperature
            </button>
          </div>
        </div>
        <p className="text-sm text-[var(--text-muted)]">
          Real snapshots only — points that were never recorded are not reconstructed.
        </p>
        {!snapshotMetas.length ? (
          <EmptyState title="No snapshot history" detail="Run weather_model.py --snapshot-live at checkpoints." />
        ) : evolutionView === 'probability' ? (
          <>
            <label className="text-sm">
              Bucket{' '}
              <select
                className="mono ml-2 rounded border border-[var(--border)] bg-transparent px-2 py-1"
                value={selectedBucket ?? ''}
                onChange={(e) => setSelectedBucket(e.target.value)}
              >
                {buckets.map((b) => (
                  <option key={b.label} value={b.label}>
                    {b.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={evolutionProbData}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                  <XAxis dataKey="time" hide />
                  <YAxis domain={[0, 1]} tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`} />
                  <Tooltip />
                  {selectedBucket ? (
                    <Line type="monotone" dataKey={selectedBucket} stroke="#3b82f6" dot strokeWidth={2} />
                  ) : null}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </>
        ) : (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={evolutionTempData}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis dataKey="time" hide />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="gfs" name="GFS expected" stroke="#3b82f6" dot />
                <Line type="monotone" dataKey="hrrr" name="HRRR" stroke="#ef4444" dot />
                <Line type="monotone" dataKey="gefs" name="GEFS median" stroke="#8b5cf6" dot />
                <Line type="monotone" dataKey="nws" name="NWS" stroke="#10b981" dot />
                <Line type="monotone" dataKey="obs" name="High so far" stroke="#f59e0b" dot />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      <section className="panel space-y-3">
        <h3 className="font-semibold">Probability Distribution</h3>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={buckets}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
              <XAxis dataKey="label" hide />
              <YAxis tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`} domain={[0, 1]} />
              <Tooltip formatter={(v) => pct(Number(v))} />
              <Bar dataKey="probability" fill="#3b82f6" />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {buckets.map((b) => (
            <div key={b.label} className="flex justify-between text-sm mono border-b border-[var(--border)] py-1">
              <span>{b.label}</span>
              <span>{pct(b.probability)}</span>
            </div>
          ))}
        </div>
      </section>

      {shadowBlock ? (
        <section className="panel space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-semibold">SHADOW MODELS</h3>
            <span className="badge badge-warning">SHADOW — NOT USED FOR DECISION</span>
          </div>
          <p className="text-sm text-[var(--text-muted)]">
            GFS + HRRR Equal (research only). Main probability above remains calibrated GFS incumbent.
          </p>
          {shadowBlock.status === 'available' && shadowBlock.probabilities ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {Object.entries(shadowBlock.probabilities).map(([label, probability]) => (
                <div
                  key={label}
                  className="flex justify-between text-sm mono border-b border-[var(--border)] py-1"
                >
                  <span>{label}</span>
                  <span>{pct(Number(probability))}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm mono text-[var(--text-muted)]">
              unavailable
              {shadowBlock.unavailable_reason ? `: ${shadowBlock.unavailable_reason}` : ''}
            </p>
          )}
        </section>
      ) : null}

      <section className="panel space-y-3">
        <h3 className="font-semibold">Probability Comparison</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Raw probability difference vs Kalshi midpoint (research display only — not a trade signal).
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[var(--text-muted)]">
                <th className="py-1">Bucket</th>
                <th>Model</th>
                <th>Mid</th>
                <th>Δ</th>
              </tr>
            </thead>
            <tbody>
              {display.kalshi_markets.map((m) => {
                const modelP = pred.bucket_probabilities?.[m.label || ''] ?? null
                const mid = m.mid_dollars ?? null
                const delta = modelP != null && mid != null ? modelP - mid : null
                return (
                  <tr key={m.ticker || m.label} className="border-t border-[var(--border)] mono">
                    <td className="py-1">{m.label}</td>
                    <td>{pct(modelP)}</td>
                    <td>{cents(mid)}</td>
                    <td>{delta == null ? '—' : `${(delta * 100).toFixed(0)}¢`}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

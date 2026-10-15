import { ErrorState, LoadingState, StatusBadge } from '../StatusBadge'
import { MetricHelp, ResultKindBadge, SampleNote } from '../MetricHelp'
import { StatusRecordBadge } from '../StatusRecordBadge'
import { useAsync } from '../sports/useAsync'
import { fetchModelSummary, fetchWeatherMetricsContext } from '../../api/weather'
import { fmtLocal, fmtTargetDate } from '../../lib/time'
import type { ResearchStatusDetail, SourceStatusDetail, StatusRecord } from '../../types/weather'

function metric(v: unknown) {
  if (v == null || v === '') return 'unavailable'
  const n = Number(v)
  return Number.isFinite(n) ? (Number.isInteger(n) ? String(n) : n.toFixed(4)) : String(v)
}

function count(v: unknown): string {
  if (v == null || v === '') return 'unavailable'
  return String(v)
}

function range(first: string | null | undefined, last: string | null | undefined) {
  return first ? `${fmtTargetDate(first)} – ${fmtTargetDate(last ?? first)}` : 'unknown'
}

const LEGACY_TITLES: Record<string, string> = {
  weather_phase_1: 'Weather Phase 1',
  weather_phase_2: 'Weather Phase 2',
  weather_phase_3_transfer: 'Weather Phase 3 Transfer',
  weather_phase_4_evidence: 'Weather Phase 4 Evidence',
  weather_phase_5_shadow: 'Weather Phase 5 Shadow',
  clinyc_target_calibration: 'CLINYC Target Calibration',
  weather_kalshi_execution_backtest: 'Weather ↔ Kalshi Execution Backtest',
  live_recommendations: 'Live Recommendations',
  model_combination_policy: 'Model combination policy',
  nbm: 'NBM',
}

function isRecord(d: ResearchStatusDetail): d is StatusRecord & { value?: string } {
  return typeof (d as StatusRecord).kind === 'string' && typeof (d as StatusRecord).state === 'string'
}

function PhaseDetail({ detail }: { detail: ResearchStatusDetail | undefined }) {
  if (!detail) {
    return (
      <p className="text-xs text-[var(--text-muted)]">
        unknown basis: the API returned no supporting record for this legacy value.
      </p>
    )
  }
  if (isRecord(detail)) return <StatusRecordBadge record={detail} />
  const records = [
    ...(detail.implementation ? [detail.implementation] : []),
    ...(detail.evidence ?? []),
    ...(detail.protocol ? [detail.protocol] : []),
    ...(detail.completion ? [detail.completion] : []),
  ]
  return (
    <div className="space-y-1.5">
      {detail.reason ? <p className="text-xs text-[var(--text-muted)]">{detail.reason}</p> : null}
      {detail.basis ? <p className="text-xs text-[var(--text-muted)]">Basis: {detail.basis}</p> : null}
      {records.map((r, i) => (
        <div key={`${r.kind}:${r.label}:${i}`} className="text-xs">
          <div className="font-medium">{r.label}</div>
          <StatusRecordBadge record={r} />
        </div>
      ))}
    </div>
  )
}

function SourceDetail({ detail }: { detail: SourceStatusDetail | undefined }) {
  if (!detail) {
    return <p className="text-xs text-[var(--text-muted)]">No supporting record; operational state unknown.</p>
  }
  return (
    <div className="space-y-1.5 text-xs">
      <StatusRecordBadge record={detail.configuration} />
      {detail.evidence.map((r, i) => (
        <StatusRecordBadge key={`${r.label}:${i}`} record={r} />
      ))}
      <StatusRecordBadge record={detail.operations} />
    </div>
  )
}

/** The weather model summary, loaded on its own so a failure here never hides the catalog. */
export function WeatherModelSummarySection() {
  const summaryState = useAsync(fetchModelSummary, 'model-summary')
  const context = useAsync(fetchWeatherMetricsContext, 'weather-metrics-context')

  let body
  if (summaryState.loading) body = <LoadingState label="Loading weather model summary…" />
  else if (summaryState.error)
    body = <ErrorState title="Weather model summary unavailable" message={summaryState.error} onRetry={summaryState.reload} />
  else if (!summaryState.data) body = <ErrorState message="Model summary unavailable." onRetry={summaryState.reload} />
  else {
    const summary = summaryState.data
    const replay = context.data?.operational_replay
    const replayPeriod = context.loading
      ? 'loading…'
      : replay?.available
        ? `${range(replay.first_target_date, replay.last_target_date)} (historical replay)`
        : 'unknown'
    const prospectiveScores = context.data?.prospective_snapshot_scores
    const prospectivePeriod = context.loading
      ? 'loading…'
      : prospectiveScores
        ? `${range(prospectiveScores.first_target_date, prospectiveScores.last_target_date)} (target dates)`
        : 'unknown'

    const constants = summary.methodology_constants ?? {}
    const status = summary.research_status
    const statusDetails = summary.research_status_details ?? {}
    const multi = summary.multi_source_evidence
    const multiDetails = summary.multi_source_evidence_details ?? {}
    const journal = summary.prospective_journal
    const transfer = summary.settlement_transfer
    const prospective = summary.prospective_validation
    const coverage = prospective?.checkpoint_coverage ?? {}
    const incumbent = prospective?.incumbent_gfs
    const shadowMetrics = prospective?.shadow_gfs_hrrr_equal
    const delta = prospective?.paired_delta_brier as { mean?: number; ci95?: number[] } | null | undefined

    body = (
      <div className="space-y-4">
        {status && Object.keys(status).length ? (
          <div className="grid gap-4 md:grid-cols-2">
            {Object.entries(status).map(([key, value]) => {
              const detail = statusDetails[key]
              return (
                <div key={key} className="panel space-y-2 min-w-0">
                  <div className="flex items-center justify-between gap-3">
                    <div className="font-medium">{LEGACY_TITLES[key] ?? key}</div>
                    <StatusBadge status={String(value)} />
                  </div>
                  <PhaseDetail detail={detail} />
                </div>
              )
            })}
          </div>
        ) : (
          <div className="panel text-sm text-[var(--text-muted)]">Research status: unknown (not reported by the API).</div>
        )}

        <section className="panel space-y-3">
          <h4 className="font-semibold">Data sources</h4>
          <p className="text-xs text-[var(--text-muted)]">
            CONFIGURED means the source is wired into the pipeline; it is not evidence of a successful fetch. PRESENT
            means a calibration file exists; its validity is not checked here. Operations come from the latest saved
            snapshot.
          </p>
          {multi && Object.keys(multi).length ? (
            <dl className="grid gap-3 sm:grid-cols-2 text-sm">
              {Object.entries(multi).map(([key, value]) => (
                <div key={key} className="space-y-1 border-b border-[var(--border)] pb-2 min-w-0">
                  <div className="flex justify-between gap-3">
                    <dt className="mono">{key}</dt>
                    <dd>
                      <StatusBadge status={String(value)} />
                    </dd>
                  </div>
                  <SourceDetail detail={multiDetails[key]} />
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-sm text-[var(--text-muted)]">Data source status: unknown (not reported by the API).</p>
          )}
        </section>

        <section className="panel space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="font-semibold">PROSPECTIVE JOURNAL</h4>
            <ResultKindBadge kind="prospective" />
          </div>
          <SampleNote
            n={prospectiveScores ? `${prospectiveScores.scored_snapshots} scored snapshots in ${prospectiveScores.events}` : null}
            unit="events"
            period={prospectivePeriod}
          />
          {journal ? (
            <dl className="grid gap-2 sm:grid-cols-3 text-sm mono">
              <div>
                snapshots <span className="font-semibold">{count(journal.snapshots)}</span>
              </div>
              <div>
                scored <span className="font-semibold">{count(journal.scored_snapshots)}</span>
              </div>
              <div title={typeof journal.settled_events_scored_basis === 'string' ? journal.settled_events_scored_basis : undefined}>
                settled events scored <span className="font-semibold">{count(journal.settled_events_scored)}</span>
              </div>
            </dl>
          ) : (
            <p className="text-sm text-[var(--text-muted)]">Prospective journal counts: unavailable.</p>
          )}
        </section>

        <section className="panel space-y-2">
          <h4 className="font-semibold">CLINYC_TRANSFER_V1</h4>
          <p className="text-sm text-[var(--text-muted)]">Prospective confirmation progress (frozen hypothesis).</p>
          {transfer ? (
            <>
              <div className="text-2xl mono">
                {count(transfer.prospective_n)} / {count(transfer.prospective_target_n)}
              </div>
              <StatusBadge status={transfer.transfer_status != null ? String(transfer.transfer_status) : 'unknown'} />
              <p className="text-xs text-[var(--text-muted)]">
                Artifact updated: {transfer.last_updated ? fmtLocal(String(transfer.last_updated)) : 'unknown'}
                {typeof transfer.last_updated_basis === 'string' ? ` (${transfer.last_updated_basis})` : ''}
                {typeof transfer.stale_basis === 'string' ? ` · freshness: ${transfer.stale_basis}` : ''}
              </p>
            </>
          ) : (
            <p className="text-sm text-[var(--text-muted)]">Settlement transfer: unavailable.</p>
          )}
        </section>

        <section className="panel space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h4 className="font-semibold">PROSPECTIVE WEATHER VALIDATION</h4>
            <StatusBadge status={prospective?.status ? String(prospective.status) : 'unknown'} />
          </div>
          <p className="text-sm text-[var(--text-muted)]">
            Incumbent remains calibrated GFS. Equal-weight GFS+HRRR is shadow research only.
          </p>
          <dl className="grid gap-2 sm:grid-cols-2 text-sm mono">
            {Object.entries(coverage).map(([id, row]) => (
              <div key={id} className="flex justify-between gap-3 border-b border-[var(--border)] py-1">
                <dt>{id}</dt>
                <dd>{row.display ?? (row.captured != null && row.total != null ? `${row.captured} / ${row.total}` : 'unknown')}</dd>
              </div>
            ))}
          </dl>
          <div className="text-sm mono space-y-1">
            <div title={prospective?.scored_events_basis ?? undefined}>
              scored events <span className="font-semibold">{count(prospective?.scored_events)}</span>
              {prospective?.scored_events == null && prospective?.scored_events_basis ? (
                <span className="font-sans text-xs text-[var(--text-muted)]"> · {prospective.scored_events_basis}</span>
              ) : null}
            </div>
            <div>
              captured <span className="font-semibold">{count(prospective?.captured_checkpoints)}</span>
              {' / '}
              missed <span className="font-semibold">{count(prospective?.missed_checkpoints)}</span>
            </div>
            {incumbent || shadowMetrics || delta ? (
              <div className="space-y-1 rounded-lg border border-[var(--border)] p-2 font-sans">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">Shadow evaluation metrics</span>
                  <ResultKindBadge kind="historical" />
                </div>
                <p className="text-xs text-[var(--text-muted)]">
                  These come from the shadow evaluation replay over past operational forecasts, not from the saved
                  prospective snapshots above.
                </p>
                {incumbent ? (
                  <div className="mono">
                    incumbent GFS Brier <MetricHelp metric="brier" label="Brier" /> = {metric(incumbent.brier)} · log loss{' '}
                    <MetricHelp metric="log_loss" label="log loss" /> = {metric(incumbent.log_loss)} · n ={' '}
                    {metric((incumbent as { n?: unknown }).n)}
                  </div>
                ) : null}
                {shadowMetrics ? (
                  <div className="mono">
                    GFS+HRRR equal Brier = {metric(shadowMetrics.brier)} · log loss = {metric(shadowMetrics.log_loss)} · n ={' '}
                    {metric((shadowMetrics as { n?: unknown }).n)}
                  </div>
                ) : null}
                {delta ? (
                  <div className="mono">
                    paired Δ Brier (shadow − GFS) <MetricHelp metric="paired" label="a paired difference" /> mean ={' '}
                    {metric(delta.mean)}
                    {Array.isArray(delta.ci95) ? ` · 95% CI [${metric(delta.ci95[0])}, ${metric(delta.ci95[1])}]` : ''}
                    {(delta as { n?: unknown }).n != null ? ` · n = ${metric((delta as { n?: unknown }).n)}` : ''}
                  </div>
                ) : null}
                <SampleNote n={replay ? replay.target_dates : null} unit="replay target dates available" period={replayPeriod} />
              </div>
            ) : null}
            <div>
              transfer for shadow V1:{' '}
              <StatusBadge status={prospective?.experimental_transfer_status ? String(prospective.experimental_transfer_status) : 'unknown'} />
            </div>
          </div>
        </section>

        <section className="panel">
          <h4 className="font-semibold">Methodology constants</h4>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Phase 5 does not promote the shadow. Combination policy = none for live WeatherPrediction.
          </p>
          <dl className="mt-3 grid gap-2 sm:grid-cols-2 text-sm mono">
            {Object.entries(constants).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-3 border-b border-[var(--border)] py-1">
                <dt className="text-[var(--text-muted)]">{key}</dt>
                <dd>{String(value)}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
    )
  }

  return (
    <section id="weather-model-summary" className="space-y-3" aria-labelledby="weather-model-summary-heading">
      <h3 id="weather-model-summary-heading" className="text-lg font-semibold">
        Weather model summary
      </h3>
      {body}
    </section>
  )
}

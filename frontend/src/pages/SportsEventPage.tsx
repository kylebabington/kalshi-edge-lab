import { useMemo } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { CartesianGrid, Legend, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import { isNotFound } from '../api/client'
import { COMPARED, compareFamily, diffVsMarket, paginate } from '../lib/candidates'
import { fetchSportsEvent } from '../api/sports'
import { EmptyState, ErrorState, LoadingState, StatusBadge } from '../components/StatusBadge'
import { TimezoneNote } from '../components/TimezoneNote'
import { DataKindTag } from '../components/UnifiedEventCard'
import { competitionLabel, familyLabel, sportLabel, sportsEventTitle } from '../lib/labels'
import { StarButton } from '../watchlist/StarButton'
import { NotebookPanel } from '../notebook/NotebookPanel'
import { BackLink } from './BackLink'
import { DataStamp } from '../components/sports/DataStamp'
import {
  CANDIDATE_LABELS,
  VIEW_LABELS,
  fmtAge,
  fmtNum,
  fmtProb,
  fmtSigned,
  fmtTime,
  fmtUtc,
  label,
} from '../components/sports/format'
import { useAsync } from '../components/sports/useAsync'
import {
  CANDIDATES,
  type CandidateName,
  type CandidateView,
  type ContractRow,
  type SportsEventDetail,
} from '../types/sports'

function CandidateCell({ c, settled }: { c: CandidateView; settled: boolean }) {
  if (!c.available) {
    return (
      <span className="text-xs text-[var(--text-muted)]">Unavailable: {(c.reason ?? 'no reason recorded').replace(/^unavailable(:\s*|\s+)/i, '')}</span>
    )
  }
  return (
    <div className="space-y-0.5">
      <div className="mono">{fmtProb(c.p)}</div>
      {c.market_reference ? (
        <div className="text-xs text-[var(--text-muted)]">
          candle {fmtUtc(c.market_reference.candle_end_utc)} · age {fmtAge(c.market_reference.candle_age_s_at_prediction)}
          {c.market_reference.bid != null || c.market_reference.ask != null ? (
            <>
              {' '}
              · candle bid/ask {fmtProb(c.market_reference.bid)} / {fmtProb(c.market_reference.ask)}
            </>
          ) : null}
        </div>
      ) : null}
      {settled && c.brier != null ? (
        <div className="text-xs mono text-[var(--text-muted)]">
          Brier {fmtNum(c.brier)} · LL {fmtNum(c.log_loss)}
        </div>
      ) : null}
    </div>
  )
}

const CONTRACT_PAGE_SIZE = 25
const CANDIDATE_COLORS: Record<CandidateName, string> = {
  frozen: '#3b82f6',
  calibrated: '#8b5cf6',
  market: '#f59e0b',
  blend: '#10b981',
}

function ComparisonSummary({ contracts }: { contracts: ContractRow[] }) {
  const cmp = useMemo(() => compareFamily(contracts), [contracts])
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="data-table">
          <caption>
            Candidates side by side over this family’s {cmp.predicted} predicted contracts. Differences are candidate minus
            market reference on contracts where both were saved: probability differences, not executable edge.
          </caption>
          <thead>
            <tr>
              <th scope="col">Candidate</th>
              <th scope="col">Available</th>
              <th scope="col">Matched with market reference</th>
              <th scope="col">Mean |probability difference|</th>
              <th scope="col">Unavailable because</th>
            </tr>
          </thead>
          <tbody>
            {CANDIDATES.map((c) => {
              const unavailable = cmp.unavailable[c].reduce((s, u) => s + u.contracts, 0)
              return (
                <tr key={c}>
                  <th scope="row">{CANDIDATE_LABELS[c]}</th>
                  <td className="mono text-xs">
                    {cmp.predicted - unavailable} / {cmp.predicted}
                  </td>
                  <td className="mono text-xs">{c === 'market' ? 'reference' : cmp.matched[c]}</td>
                  <td className="mono text-xs">{c === 'market' ? '—' : cmp.meanAbsDiff[c] == null ? 'no matched contracts' : fmtNum(cmp.meanAbsDiff[c], 3)}</td>
                  <td className="text-xs">
                    {cmp.unavailable[c].length
                      ? cmp.unavailable[c].map((u) => `${u.reason} (${u.contracts})`).join('; ')
                      : 'available on every predicted contract'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function VisibleContractsChart({ rows, first }: { rows: ContractRow[]; first: number }) {
  const series = useMemo(
    () =>
      CANDIDATES.map((c) => ({
        c,
        points: rows.flatMap((r, i) => {
          const v = r.candidates?.[c]
          return v && v.available && typeof v.p === 'number' ? [{ x: first + i + 1, p: v.p, ticker: r.ticker }] : []
        }),
      })),
    [rows, first],
  )
  if (!series.some((s) => s.points.length)) return null
  return (
    <div
      className="h-56"
      role="img"
      aria-label={`Saved candidate probabilities for the ${rows.length} contracts on this page; the table below lists every value.`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
          <XAxis type="number" dataKey="x" domain={[first, first + rows.length + 1]} allowDecimals={false} tick={{ fontSize: 11 }} label={{ value: 'contract # (table row)', position: 'insideBottom', offset: -4, fontSize: 11 }} />
          <YAxis type="number" dataKey="p" domain={[0, 1]} tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`} width={44} />
          <Tooltip formatter={(v) => fmtProb(Number(v))} labelFormatter={() => ''} />
          <Legend />
          {series.map((s) => (
            <Scatter key={s.c} name={CANDIDATE_LABELS[s.c]} data={s.points} fill={CANDIDATE_COLORS[s.c]} isAnimationActive={false} />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

function ContractTable({
  family,
  contracts,
  page,
  onPage,
}: {
  family: string
  contracts: ContractRow[]
  page: number
  onPage: (p: number) => void
}) {
  const view = paginate(contracts, page, CONTRACT_PAGE_SIZE)
  const first = (view.page - 1) * CONTRACT_PAGE_SIZE
  return (
    <section className="panel space-y-3" aria-labelledby={`family-${family}`}>
      <h3 id={`family-${family}`} className="font-semibold">
        {familyLabel(family)} <span className="text-xs text-[var(--text-muted)]">({contracts.length} binary contracts)</span>
      </h3>
      <ComparisonSummary contracts={contracts} />
      <VisibleContractsChart rows={view.items} first={first} />
      <p className="text-xs text-[var(--text-muted)]">
        Contracts {contracts.length ? first + 1 : 0}–{first + view.items.length} of {contracts.length}. Chart and table show
        this page only; points are saved probabilities at the single prediction checkpoint.
      </p>
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Contract</th>
              {CANDIDATES.map((c) => (
                <th scope="col" key={c}>
                  {CANDIDATE_LABELS[c]}
                </th>
              ))}
              {COMPARED.map((c) => (
                <th scope="col" key={`d-${c}`}>
                  {CANDIDATE_LABELS[c]} − market (probability difference)
                </th>
              ))}
              <th scope="col">Listing bid / ask (diagnostic)</th>
              <th scope="col">Settlement</th>
            </tr>
          </thead>
          <tbody>
            {view.items.map((row, i) => {
              const settled = row.settlement.outcome != null
              return (
                <tr key={row.ticker}>
                  <td className="mono text-xs">{first + i + 1}</td>
                  <th scope="row" className="min-w-[14rem]">
                    <div className="mono text-xs">{row.ticker}</div>
                    <div>{row.description ?? row.market_title ?? 'No saved market description'}</div>
                    {row.cohort ? <div className="text-xs text-[var(--text-muted)]">cohort: {row.cohort}</div> : null}
                  </th>
                  {row.candidates ? (
                    <>
                      {CANDIDATES.map((c) => (
                        <td key={c}>
                          <CandidateCell c={row.candidates![c]} settled={settled} />
                        </td>
                      ))}
                      {COMPARED.map((c) => {
                        const d = diffVsMarket(row, c)
                        return (
                          <td key={`d-${c}`} className="mono text-xs">
                            {d == null ? <span className="text-[var(--text-muted)]">not matched</span> : fmtSigned(d, 3)}
                          </td>
                        )
                      })}
                    </>
                  ) : (
                    <td colSpan={CANDIDATES.length + COMPARED.length} className="text-xs text-[var(--text-muted)]">
                      Not predicted ({label(row.status)}): {row.excluded_reason ?? 'no reason recorded'}
                    </td>
                  )}
                  <td className="mono text-xs">
                    {row.listing_diagnostics
                      ? `${row.listing_diagnostics.current_yes_bid ?? '—'} / ${row.listing_diagnostics.current_yes_ask ?? '—'}`
                      : '—'}
                  </td>
                  <td className="text-xs">
                    {settled ? (
                      <span className="font-semibold">{row.settlement.outcome === 1 ? 'YES' : 'NO'}</span>
                    ) : (
                      <StatusBadge status={label(row.settlement.state)} />
                    )}
                    {row.settlement.detail ? <div className="text-[var(--text-muted)]">{row.settlement.detail}</div> : null}
                    {row.settlement.score_checksum === 'MISMATCH' ? (
                      <div className="text-[var(--negative)]">score checksum mismatch</div>
                    ) : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {view.pages > 1 ? (
        <nav className="flex flex-wrap items-center gap-2 text-sm" aria-label={`${familyLabel(family)} contract pages`}>
          <button type="button" className="badge" disabled={view.page <= 1} onClick={() => onPage(view.page - 1)}>
            Previous
          </button>
          <span>
            Page {view.page} of {view.pages}
          </span>
          <button type="button" className="badge" disabled={view.page >= view.pages} onClick={() => onPage(view.page + 1)}>
            Next
          </button>
        </nav>
      ) : null}
    </section>
  )
}

function FamilyContracts({ families }: { families: SportsEventDetail['families'] }) {
  const [params, setParams] = useSearchParams()
  const requested = params.get('family')
  const current = families.find((f) => f.family === requested) ?? families[0]
  const page = Number(params.get('cpage') ?? '1') || 1
  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) {
      if (v == null) next.delete(k)
      else next.set(k, v)
    }
    setParams(next, { replace: true })
  }
  return (
    <div className="space-y-3">
      {families.length > 1 ? (
        <label className="text-sm">
          Contract family{' '}
          <select
            className="ml-2 rounded border border-[var(--border)] bg-transparent px-2 py-1"
            value={current.family}
            onChange={(e) => update({ family: e.target.value, cpage: null })}
          >
            {families.map((f) => (
              <option key={f.family} value={f.family}>
                {familyLabel(f.family)} ({f.contracts.length})
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <ContractTable
        key={current.family}
        family={current.family}
        contracts={current.contracts}
        page={page}
        onPage={(p) => update({ cpage: p > 1 ? String(p) : null })}
      />
    </div>
  )
}

function KeyValues({ data }: { data: Record<string, unknown> }) {
  return (
    <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[minmax(10rem,auto)_1fr]">
      {Object.entries(data).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-[var(--text-muted)]">{label(k)}</dt>
          <dd className="mono break-all">{typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? '—')}</dd>
        </div>
      ))}
    </dl>
  )
}

export function SportsEventView({ data, loadedAt }: { data: SportsEventDetail; loadedAt: Date | null }) {
  const e = data.event
  const p = data.provenance
  const title = sportsEventTitle(e)
  return (
    <div className="space-y-5">
      <header className="space-y-2">
        <BackLink fallback="/sports" label="Sports events" />
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">
              {sportLabel(e.sport)} · {competitionLabel(e.competition, e.competition_name)}
            </p>
            <h2 className="text-xl font-semibold">{title}</h2>
            {e.title && e.participants.length ? (
              <p className="text-sm text-[var(--text-muted)]">Participants: {e.participants.join(' vs ')}</p>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={VIEW_LABELS[e.view]} />
            <StarButton category="sports" eventKey={e.event_id} label={title} size="md" />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <DataKindTag kind="saved" />
        </div>
        <TimezoneNote />
        <p className="text-xs mono text-[var(--text-muted)]">Event id: {e.event_id}</p>
        {p.collection ? (
          <p className="text-xs text-[var(--text-muted)]">
            Collection: {p.collection.label}
            {p.collection.cycle_started_at ? ` (cycle ${fmtTime(p.collection.cycle_started_at)})` : ''} · see{' '}
            <span className="mono break-all">{p.collection.amendment_doc}</span>
          </p>
        ) : null}
        <DataStamp generatedAt={data.generated_at} lastCaptureAt={e.prediction_as_of} loadedAt={loadedAt} source={data.source} />
      </header>

      <section className="panel grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <div className="text-xs text-[var(--text-muted)]">Event start</div>
          <div>{fmtTime(e.event_start)}</div>
        </div>
        <div>
          <div className="text-xs text-[var(--text-muted)]">Capture window</div>
          <div>
            {fmtTime(e.window_open)} – {fmtTime(e.scheduled_cutoff)}
          </div>
        </div>
        <div>
          <div className="text-xs text-[var(--text-muted)]">Prediction as of</div>
          <div>
            {fmtTime(e.prediction_as_of)}
            {e.horizon_minutes != null ? ` (${Math.round(e.horizon_minutes)} min before start)` : ''}
          </div>
        </div>
        <div>
          <div className="text-xs text-[var(--text-muted)]">Settlement</div>
          <div>
            {label(e.settlement.state)} · {e.settlement.final}/{e.settlement.contracts} final
          </div>
        </div>
      </section>

      {data.missed ? (
        <div className="warning-banner text-sm">
          <div className="font-semibold text-[var(--warning)]">Missed capture window</div>
          <p>
            {data.missed.reason ?? 'No reason recorded'} · recorded {fmtTime(data.missed.written_at)} · prior failed attempts:{' '}
            {data.missed.prior_failed_attempts}. Missed forecasts are never reconstructed from later downloads.
          </p>
        </div>
      ) : null}

      <div className="panel text-xs text-[var(--text-muted)] space-y-1">
        <p>{data.notes.binary}</p>
        <p>{data.notes.market_reference}</p>
        <details>
          <summary className="cursor-pointer">Candidate definitions</summary>
          <dl className="mt-2 space-y-1">
            {Object.entries(data.notes.candidates).map(([k, v]) => (
              <div key={k}>
                <dt className="inline font-semibold">{CANDIDATE_LABELS[k as keyof typeof CANDIDATE_LABELS] ?? k}: </dt>
                <dd className="inline">{v}</dd>
              </div>
            ))}
          </dl>
        </details>
      </div>

      {data.families.length && e.prediction_as_of ? (
        <div className="panel text-sm space-y-1" role="note">
          <div className="font-semibold">One saved prediction checkpoint</div>
          <p className="text-[var(--text-muted)]">
            Every candidate for this event was saved once, at {fmtTime(e.prediction_as_of)}
            {e.horizon_minutes != null ? ` (${Math.round(e.horizon_minutes)} min before start)` : ''}. There is no time
            series of predictions to plot; the comparison below is at that single checkpoint. The market reference is the
            saved candle mid with its candle time, not a live or tradable price.
          </p>
        </div>
      ) : null}

      {data.families.length === 0 ? (
        <EmptyState title="No contracts recorded" detail="This record has no captured contracts." />
      ) : (
        <FamilyContracts families={data.families} />
      )}

      <details className="panel">
        <summary className="cursor-pointer font-semibold">Provenance</summary>
        <div className="mt-3 space-y-4">
          <KeyValues
            data={{
              protocol: p.protocol,
              schema: p.schema,
              run_id: p.run_id,
              capture_started_at: p.capture_started_at,
              timing_label: p.timing_label,
              comparability_note: p.comparability_note,
              model: p.model,
              settlement_runs: p.settlement_runs.join(', ') || 'none yet',
            }}
          />
          <div>
            <h4 className="text-sm font-semibold mb-1">Integrity</h4>
            <KeyValues data={p.integrity} />
          </div>
          <div>
            <h4 className="text-sm font-semibold mb-1">Timing</h4>
            <KeyValues data={p.timing} />
          </div>
          <div>
            <h4 className="text-sm font-semibold mb-1">Evidence timing</h4>
            <KeyValues data={p.evidence_timing} />
          </div>
          <div>
            <h4 className="text-sm font-semibold mb-1">Code pins (sha256)</h4>
            <KeyValues data={p.pins} />
          </div>
          <div>
            <h4 className="text-sm font-semibold mb-1">Dependencies (sha256)</h4>
            <KeyValues data={p.dependencies} />
          </div>
          <p className="text-xs text-[var(--text-muted)]">
            Descriptions: {data.description_source.source} ({JSON.stringify(data.description_source.files)})
          </p>
        </div>
      </details>
    </div>
  )
}

export function SportsEventPage() {
  const { eventId = '' } = useParams()
  const state = useAsync(() => fetchSportsEvent(eventId), eventId)
  if (state.loading) return <LoadingState label="Loading saved capture…" />
  if (state.error) {
    return (
      <div className="space-y-3">
        <BackLink fallback="/sports" label="Sports events" />
        {isNotFound(state.errorValue) ? (
          <EmptyState title="Event not found" detail={`No saved capture has id ${eventId}.`} />
        ) : (
          <ErrorState message={state.error} onRetry={state.reload} />
        )}
      </div>
    )
  }
  return state.data ? (
    <div className="space-y-5">
      <SportsEventView data={state.data} loadedAt={state.loadedAt} />
      <NotebookPanel target={{ type: 'event', category: 'sports', key: eventId }} title="My notes on this event" />
    </div>
  ) : null
}

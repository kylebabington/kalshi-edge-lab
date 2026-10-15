import { useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { fetchOpportunity, researchOpportunity } from '../api/opportunities'
import { isNotFound } from '../api/client'
import { AttentionScore, EligibilityBadge, FreshnessBadge } from '../components/OpportunityBadges'
import { EmptyState, ErrorState, LoadingState } from '../components/StatusBadge'
import { fmtAge, fmtProb } from '../components/sports/format'
import { useAsync } from '../components/sports/useAsync'
import { fmtConfidence, fmtEdge, fmtUsd } from '../lib/opportunities'
import { fmtLocal } from '../lib/time'
import type { AISummary, Opportunity, OpportunityDetail, ResearchItem, ResearchResult } from '../types/opportunities'
import { BackLink } from './BackLink'

const AI_NOTE = 'AI research does not modify the statistical model probability.'

export function OpportunityDetailPage() {
  const { ticker = '' } = useParams()
  const state = useAsync(() => fetchOpportunity(ticker), ticker)

  return (
    <div className="space-y-5">
      <BackLink fallback="/opportunities" label="All opportunities" />
      {state.loading && !state.data ? (
        <LoadingState label="Loading opportunity…" />
      ) : state.error ? (
        isNotFound(state.errorValue) ? (
          <EmptyState title="No current opportunity" detail={`No current market with ticker ${ticker}. It may have closed or started.`} />
        ) : (
          <ErrorState message={state.error} onRetry={state.reload} />
        )
      ) : state.data ? (
        <Detail detail={state.data} />
      ) : null}
    </div>
  )
}

function Detail({ detail }: { detail: OpportunityDetail }) {
  const o = detail.opportunity
  return (
    <>
      <header className="space-y-2">
        <div className="text-xs uppercase tracking-[0.14em] text-[var(--text-muted)]">
          {o.category} · <span className="mono normal-case tracking-normal">{o.ticker}</span>
        </div>
        <h2 className="text-xl font-semibold break-words">{o.title}</h2>
        <div className="flex flex-wrap items-center gap-2">
          <EligibilityBadge status={o.eligibility.status} />
          <FreshnessBadge status={o.data_freshness_status} />
          <span className="badge badge-warning">Research only</span>
        </div>
      </header>

      {o.blocking_reasons.length ? (
        <section className="warning-banner text-sm" aria-label="Blocking reasons">
          <div className="font-semibold text-[var(--negative)]">Blocked: not ranked and not eligible for AI research</div>
          <ul className="list-disc pl-5">
            {o.blocking_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {o.warnings.length ? (
        <section className="warning-banner text-sm" aria-label="Warnings">
          <div className="font-semibold text-[var(--warning)]">Warnings</div>
          <ul className="list-disc pl-5">
            {o.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Numbers o={o} />
        <WhyModel o={o} />
      </div>
      <AIResearchPanel key={o.ticker} o={o} ai={detail.ai} initial={detail.research} />
      <details className="panel text-sm">
        <summary className="cursor-pointer font-medium">Evidence supplied to AI research ({detail.evidence.length})</summary>
        <ul className="mt-3 space-y-3">
          {detail.evidence.map((e) => (
            <li key={e.id}>
              <div className="text-xs text-[var(--text-muted)]">
                <span className="mono">{e.id}</span> · {e.source} · {e.source_type}
                {e.published_at ? ` · ${fmtLocal(e.published_at)}` : ''}
              </div>
              <div className="font-medium">{e.title}</div>
              <pre className="mono text-xs whitespace-pre-wrap break-words text-[var(--text-muted)]">
                {e.content.length > 1200 ? `${e.content.slice(0, 1200)}…` : e.content}
              </pre>
            </li>
          ))}
        </ul>
      </details>
    </>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  )
}

function Numbers({ o }: { o: Opportunity }) {
  return (
    <section className="panel space-y-3" aria-label="Numbers">
      <h3 className="font-semibold">Numbers</h3>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
        <Row label="Model probability (YES)">{fmtProb(o.model_probability)}</Row>
        <Row label="Market probability (mid)">{fmtProb(o.market_probability)}</Row>
        <Row label="YES bid / ask">
          <span className="mono">
            {o.yes_bid ?? '—'} / {o.yes_ask ?? '—'}
          </span>
        </Row>
        <Row label="Probability edge">
          {fmtEdge(o.probability_edge)}
          {o.side ? ` on ${o.side}` : ''}
        </Row>
        <Row label="Expected value before fees">{fmtUsd(o.expected_value_before_fees)} per contract</Row>
        <Row label="Fees">
          {o.estimated_fees == null ? (
            <span className="text-[var(--warning)]">unavailable{o.fee_note ? ` (${o.fee_note})` : ''}</span>
          ) : (
            `${fmtUsd(o.estimated_fees)} per contract`
          )}
        </Row>
        <Row label="Expected value after fees">{fmtUsd(o.expected_value_after_fees)}</Row>
        <Row label="Confidence">{fmtConfidence(o)}</Row>
        <Row label="Model agreement">{o.model_agreement ?? 'unavailable'}</Row>
        <Row label="Sample size">{o.sample_size ?? 'not recorded'}</Row>
        <Row label="Data freshness">
          {o.data_freshness_status} ({fmtAge(o.data_age_seconds)} old)
        </Row>
        <Row label="Prediction as of">{fmtLocal(o.prediction_as_of)}</Row>
      </dl>
    </section>
  )
}

function WhyModel({ o }: { o: Opportunity }) {
  return (
    <section className="panel space-y-3" aria-label="Why the model likes it">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">Why the model likes it</h3>
        <AttentionScore score={o.attention_score} size="lg" />
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Attention score: deterministic, from the components below. It is not a probability, not expected value and not
        a recommendation.
      </p>
      <ul className="space-y-1 text-sm">
        {o.attention_components.map((c) => (
          <li key={`${c.name}-${c.reason}`} className="flex gap-2">
            <span
              className={`mono w-12 shrink-0 text-right ${c.points > 0 ? 'text-[var(--positive)]' : c.points < 0 ? 'text-[var(--negative)]' : 'text-[var(--text-muted)]'}`}
            >
              {c.points > 0 ? `+${c.points}` : c.points}
            </span>
            <span className="break-words">{c.reason}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ItemList({ title, items }: { title: string; items: ResearchItem[] }) {
  return (
    <div>
      <h4 className="text-sm font-semibold">{title}</h4>
      {items.length ? (
        <ul className="mt-1 space-y-1 text-sm">
          {items.map((i, idx) => (
            <li key={idx}>
              <span className="badge mr-1">{i.basis}</span>
              {i.statement}
              {i.evidence_ids.length ? <span className="mono text-xs text-[var(--text-muted)]"> [{i.evidence_ids.join(', ')}]</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-[var(--text-muted)]">None identified.</p>
      )}
    </div>
  )
}

function AIResearchPanel({ o, ai, initial }: { o: Opportunity; ai: AISummary; initial: ResearchResult }) {
  const [result, setResult] = useState<ResearchResult>(initial)
  const [running, setRunning] = useState(false)
  const [requestError, setRequestError] = useState<string | null>(null)
  const research = result.research
  const eligibility = result.eligibility

  async function run() {
    setRunning(true)
    setRequestError(null)
    try {
      setResult(await researchOpportunity(o.ticker, research != null))
    } catch (err) {
      setRequestError(err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  const failure = result.status === 'unavailable' || result.status === 'error' ? result.error : null
  return (
    <section className="panel space-y-4" aria-label="AI Research">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">AI Research</h3>
        <EligibilityBadge status={eligibility.status} />
      </div>
      <p className="text-sm font-medium" role="note">
        {AI_NOTE}
      </p>

      {eligibility.status === 'BLOCKED' ? (
        <div className="text-sm">
          <p className="text-[var(--text-muted)]">Not eligible for AI research:</p>
          <ul className="list-disc pl-5">
            {eligibility.blocking_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      ) : !ai.enabled ? (
        <p className="text-sm text-[var(--text-muted)]">AI research is disabled (AI_PROVIDER=none). Everything else works without it.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="pager-button text-sm" onClick={run} disabled={running}>
            {running ? 'Researching…' : research ? 'Refresh AI Research' : 'Research with AI'}
          </button>
          <span className="text-xs text-[var(--text-muted)]">
            Manual only · {ai.provider} · {ai.model ?? 'no model'}
          </span>
        </div>
      )}

      {eligibility.status !== 'BLOCKED' && eligibility.warnings.length ? (
        <div className="text-xs text-[var(--warning)]">
          Researched with caution: {eligibility.warnings.join(' · ')}
        </div>
      ) : null}
      {failure ? (
        <div role="alert" className="text-sm text-[var(--negative)]">
          AI research {result.status}: {failure.message}
        </div>
      ) : null}
      {requestError ? (
        <div role="alert" className="text-sm text-[var(--negative)]">
          Request failed: {requestError}
        </div>
      ) : null}

      {research ? (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2 text-xs text-[var(--text-muted)]">
            <span>Last researched {fmtLocal(result.created_at)}</span>
            <span>· {result.provider}</span>
            <span>· {result.model}</span>
            <span className={`badge ${result.freshness === 'stale' ? 'badge-warning' : 'badge-info'}`}>{result.freshness}</span>
            {result.data_changed ? <span className="badge badge-warning">Underlying data changed since research</span> : null}
          </div>
          <div>
            <h4 className="text-sm font-semibold">Summary</h4>
            <p className="text-sm">{research.summary}</p>
          </div>
          <ItemList title="Supporting evidence" items={research.supporting_evidence} />
          <ItemList title="Contradictory evidence" items={research.contradictory_evidence} />
          <ItemList title="Important changes" items={research.important_changes} />
          <ItemList title="Main risks" items={research.risk_factors} />
          {research.unknowns.length ? (
            <div>
              <h4 className="text-sm font-semibold">Unknown</h4>
              <ul className="list-disc pl-5 text-sm">
                {research.unknowns.map((u) => (
                  <li key={u}>{u}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="text-sm">
            Research confidence: <span className="font-semibold">{research.research_confidence}</span>
            {research.research_confidence_capped ? (
              <span className="text-xs text-[var(--text-muted)]"> (capped: settlement transfer unvalidated)</span>
            ) : null}
          </div>
        </div>
      ) : eligibility.status !== 'BLOCKED' ? (
        <p className="text-sm text-[var(--text-muted)]">No AI research yet for this market.</p>
      ) : null}
    </section>
  )
}

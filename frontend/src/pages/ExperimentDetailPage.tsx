import { useCallback } from 'react'
import { Link, useParams } from 'react-router-dom'
import { EmptyState, ErrorState, LoadingState } from '../components/StatusBadge'
import { StatusRecordBadge } from '../components/StatusRecordBadge'
import { useAsync } from '../components/sports/useAsync'
import { DesignationBadge, ModeChips, ResultList, SampleList } from '../components/research/ExperimentCard'
import { periodText, statusRecords } from '../components/research/format'
import { isNotFound } from '../api/client'
import { fetchResearchExperiment } from '../api/research'
import { fmtLocal } from '../lib/time'
import { NotebookPanel } from '../notebook/NotebookPanel'
import type { ExperimentDetail, ExperimentSource, ModeBlock } from '../types/research'

function ModeBlockPanel({ block }: { block: ModeBlock }) {
  return (
    <div
      className={`rounded-lg border p-3 space-y-2 min-w-0 ${block.applies ? 'border-[var(--border)]' : 'border-dashed border-[var(--border)] opacity-80'}`}
      data-testid={`mode-${block.mode}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-medium">{block.label}</h4>
        <span className={block.applies ? 'badge badge-info' : 'badge badge-warning'}>
          {block.applies ? 'part of this protocol' : 'not part of this protocol'}
        </span>
      </div>
      <p className="text-sm text-[var(--text-muted)]">{block.description}</p>
      {block.applies ? (
        <>
          {block.period ? (
            <div className="text-sm">
              Period: <span className="mono">{periodText(block.period)}</span>
            </div>
          ) : null}
          {block.samples.length ? <SampleList samples={block.samples} /> : null}
          {block.operations ? <StatusRecordBadge record={block.operations} /> : null}
          {block.href ? (
            <Link to={block.href} className="text-sm underline underline-offset-2">
              Open related page
            </Link>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

function SourceTable({ title, note, sources }: { title: string; note: string; sources: ExperimentSource[] }) {
  return (
    <div className="space-y-1 min-w-0">
      <h4 className="font-medium text-sm">{title}</h4>
      <p className="text-xs text-[var(--text-muted)]">{note}</p>
      {sources.length ? (
        <ul className="space-y-1 text-sm">
          {sources.map((s) => (
            <li key={`${s.label}:${s.path}`} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <span className={s.exists ? 'badge badge-info' : 'badge badge-warning'}>{s.exists ? 'present' : 'missing'}</span>
              <span>{s.label}</span>
              <span className="mono text-xs text-[var(--text-muted)] break-all">{s.path}</span>
              {s.as_of ? <span className="text-xs text-[var(--text-muted)]">· modified {fmtLocal(s.as_of)}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-[var(--text-muted)]">None listed.</p>
      )}
    </div>
  )
}

function Detail({ exp }: { exp: ExperimentDetail }) {
  const tracked = exp.sources.filter((s) => s.tracked)
  const local = exp.sources.filter((s) => !s.tracked)
  const records = statusRecords(exp.status)
  return (
    <div className="space-y-5">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="badge badge-info">{exp.category === 'weather' ? 'Weather' : 'Sports'}</span>
          <DesignationBadge designation={exp.designation} />
          <span className="mono text-[var(--text-muted)]">{exp.version ?? 'version unknown'}</span>
          {(exp.status.policy ?? []).map((p) => (
            <StatusRecordBadge key={`${p.label}:${p.state}`} record={p} showBasis={false} showKind={false} />
          ))}
        </div>
        <h2 className="text-xl font-semibold">{exp.title}</h2>
        <dl className="grid gap-1 text-sm sm:grid-cols-2">
          <div>
            <dt className="inline text-[var(--text-muted)]">Experiment ID: </dt>
            <dd className="inline mono break-all">{exp.id}</dd>
          </div>
          <div>
            <dt className="inline text-[var(--text-muted)]">Protocol: </dt>
            <dd className="inline mono break-all">{exp.protocol_id ?? 'unknown'}</dd>
          </div>
          <div>
            <dt className="inline text-[var(--text-muted)]">Frozen: </dt>
            <dd className="inline">{exp.frozen_at ? fmtLocal(exp.frozen_at) : 'unknown'}</dd>
          </div>
          <div>
            <dt className="inline text-[var(--text-muted)]">Data period: </dt>
            <dd className="inline mono">{periodText(exp.period, exp.period_reason)}</dd>
          </div>
          {exp.lifecycle ? (
            <div className="sm:col-span-2">
              <dt className="inline text-[var(--text-muted)]">Lifecycle: </dt>
              <dd className="inline">
                {exp.lifecycle.value} <span className="text-xs text-[var(--text-muted)]">({exp.lifecycle.basis})</span>
              </dd>
            </div>
          ) : null}
        </dl>
        <ModeChips modes={exp.modes} />
        {exp.summary_note ? <p className="text-xs text-[var(--text-muted)]">{exp.summary_note}</p> : null}
      </header>

      {exp.error ? (
        <p className="warning-banner text-sm" role="status">
          Part of this page could not be built: <span className="mono">{exp.error}</span>
        </p>
      ) : null}
      {exp.loading ? (
        <p className="warning-banner text-sm" role="status">
          The sports index is still loading on the server. Counts stay unknown until it is ready; reload later.
        </p>
      ) : null}

      <section className="panel space-y-3">
        <h3 className="font-semibold">Research question</h3>
        <p className="text-sm">{exp.question ?? 'Not recorded.'}</p>
        <div className="text-sm">
          <span className="text-[var(--text-muted)]">Reference: </span>
          {exp.reference ?? 'not recorded'}
        </div>
        <div>
          <h4 className="font-medium text-sm mb-1">Candidates</h4>
          {exp.candidates.length ? (
            <ul className="space-y-1 text-sm">
              {exp.candidates.map((c) => (
                <li key={c.id}>
                  <span className="font-medium">{c.label}</span>
                  {c.label !== c.id ? <span className="mono text-xs text-[var(--text-muted)]"> ({c.id})</span> : null}
                  {c.description ? <span className="text-[var(--text-muted)]"> · {c.description}</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-[var(--text-muted)]">Not recorded.</p>
          )}
        </div>
        <div>
          <h4 className="font-medium text-sm mb-1">Cohort rules</h4>
          {exp.cohort_rules.length ? (
            <dl className="space-y-1 text-sm">
              {exp.cohort_rules.map((r, i) => (
                <div key={`${r.name}:${i}`} className="grid gap-x-3 sm:grid-cols-[12rem_1fr]">
                  <dt className="mono text-xs text-[var(--text-muted)] break-all">{r.name}</dt>
                  <dd className="break-words">{r.rule}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-xs text-[var(--text-muted)]">Not recorded.</p>
          )}
        </div>
        <div>
          <h4 className="font-medium text-sm mb-1">Limitations</h4>
          {exp.limitations.length ? (
            <ul className="list-disc pl-5 space-y-1 text-sm">
              {exp.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-[var(--text-muted)]">Not recorded.</p>
          )}
        </div>
      </section>

      <section className="panel space-y-3">
        <h3 className="font-semibold">How the data was collected</h3>
        <div className="grid gap-3 lg:grid-cols-3">
          {exp.mode_blocks.length ? (
            exp.mode_blocks.map((b) => <ModeBlockPanel key={b.mode} block={b} />)
          ) : (
            <p className="text-sm text-[var(--text-muted)]">Collection details unavailable.</p>
          )}
        </div>
      </section>

      <section className="panel space-y-3">
        <h3 className="font-semibold">Results</h3>
        <SampleList samples={exp.samples} />
        <ResultList results={exp.results} />
        {exp.links.length ? (
          <div className="flex flex-wrap gap-3 text-sm">
            {exp.links.map((l) => (
              <Link key={`${l.label}:${l.href}`} to={l.href} className="underline underline-offset-2">
                {l.label}
              </Link>
            ))}
          </div>
        ) : null}
      </section>

      <section className="panel space-y-3">
        <h3 className="font-semibold">Status and its basis</h3>
        {records.length ? (
          <ul className="grid gap-3 sm:grid-cols-2">
            {records.map((r, i) => (
              <li key={`${r.kind}:${r.label}:${i}`} className="min-w-0">
                <div className="text-sm font-medium">{r.label}</div>
                <StatusRecordBadge record={r} />
              </li>
            ))}
          </ul>
        ) : (
          <StatusRecordBadge record={null} />
        )}
      </section>

      <section className="panel space-y-3">
        <h3 className="font-semibold">Sources</h3>
        <div className="grid gap-4 md:grid-cols-2">
          <SourceTable
            title="Tracked in the repository"
            note="Protocols, hypotheses and summary documents committed to git."
            sources={tracked}
          />
          <SourceTable
            title="Local research artifacts"
            note="Files generated on this machine and not tracked in git; another machine may not have them."
            sources={local}
          />
        </div>
      </section>

      <section className="panel space-y-2">
        <h3 className="font-semibold">Frozen settings</h3>
        <p className="text-xs text-[var(--text-muted)]">
          Read-only view of the frozen protocol. Large blocks are summarised for display; the file itself is authoritative
          and cannot be changed here.
        </p>
        {exp.frozen_settings ? (
          <details>
            <summary className="cursor-pointer text-sm">Show frozen settings</summary>
            <pre className="mt-2 max-h-[32rem] overflow-auto rounded-lg border border-[var(--border)] p-3 text-xs mono whitespace-pre-wrap break-words" aria-readonly="true">
              {JSON.stringify(exp.frozen_settings, null, 2)}
            </pre>
          </details>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">No frozen settings file for this experiment.</p>
        )}
      </section>

      <NotebookPanel target={{ type: 'experiment', id: exp.id }} title="My notes on this experiment" />
    </div>
  )
}

export function ExperimentDetailPage() {
  const { experimentId = '' } = useParams()
  const load = useCallback(() => fetchResearchExperiment(experimentId), [experimentId])
  const state = useAsync(load, `research-experiment:${experimentId}`)

  let body
  if (state.loading) body = <LoadingState label="Loading experiment…" />
  else if (isNotFound(state.errorValue))
    body = <EmptyState title="Experiment not found" detail={`No experiment is registered with the ID ${experimentId}.`} />
  else if (state.error)
    body = <ErrorState title="Experiment unavailable" message={state.error} onRetry={state.reload} />
  else if (state.data) body = <Detail exp={state.data} />
  else body = <ErrorState message="Experiment unavailable." onRetry={state.reload} />

  return (
    <div className="space-y-4">
      <Link to="/research" className="text-sm underline underline-offset-2">
        ← Research
      </Link>
      {body}
    </div>
  )
}

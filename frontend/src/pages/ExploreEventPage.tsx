import { Link, useParams } from 'react-router-dom'
import { isNotFound } from '../api/client'
import { fetchExploreEvent } from '../api/explore'
import { EmptyState, ErrorState, LoadingState } from '../components/StatusBadge'
import { useAsync } from '../components/sports/useAsync'
import { aliasNoteTargets, preferredWatch } from '../explore/identity'
import { coverageSummary, freshnessLabel, lifecycleLabel, modelSupportLabel, savedForecastLabel } from '../explore/labels'
import { fmtLocal } from '../lib/time'
import type { ExploreEventDetail, ExploreMarket } from '../types/explore'
import { NotebookPanel } from '../notebook/NotebookPanel'
import { StarButton } from '../watchlist/StarButton'
import { BackLink } from './BackLink'

export function ExploreEventPage() {
  const { eventTicker = '' } = useParams()
  const state = useAsync(() => fetchExploreEvent(eventTicker), eventTicker)

  return (
    <div className="space-y-5">
      <BackLink fallback="/explore" label="Market Explorer" />
      {state.loading && !state.data ? (
        <LoadingState label="Loading event…" />
      ) : state.error ? (
        isNotFound(state.errorValue) ? (
          <EmptyState
            title="Event not in explorer cache"
            detail={`No cached explorer record for ${eventTicker}. Run Refresh markets, or the event may be closed/unavailable.`}
          />
        ) : (
          <ErrorState message={state.error} onRetry={state.reload} />
        )
      ) : state.data ? (
        <Detail event={state.data} />
      ) : null}
    </div>
  )
}

function Detail({ event }: { event: ExploreEventDetail }) {
  const title = event.title || event.event_ticker
  const cov = event.coverage
  const pref = preferredWatch(event.event_ticker, cov)
  const noteTargets = aliasNoteTargets(event.event_ticker, cov)
  const primaryNote = { type: 'event' as const, category: pref.category, key: pref.key }
  const aliases = noteTargets.filter(
    (t) => t.type === 'event' && !(t.category === primaryNote.category && t.key === primaryNote.key),
  )

  return (
    <>
      <header className="space-y-2">
        <div className="text-xs uppercase tracking-[0.14em] text-[var(--text-muted)]">
          {event.category || 'Kalshi'} · <span className="mono normal-case tracking-normal">{event.event_ticker}</span>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h2 className="text-xl font-semibold break-words">{title}</h2>
          <StarButton
            category={pref.category}
            eventKey={pref.key}
            label={title}
            size="md"
            coverage={cov}
            eventTicker={event.event_ticker}
          />
        </div>
        {event.sub_title ? <p className="text-sm text-[var(--text-muted)]">{event.sub_title}</p> : null}
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="badge badge-info">Lifecycle: {lifecycleLabel(event.lifecycle)}</span>
          <span className="badge badge-info">Freshness: {freshnessLabel(event.retrieval_freshness)}</span>
          {event.markets_complete === false ? <span className="badge badge-warning">Markets list incomplete</span> : null}
        </div>
      </header>

      <section className="panel space-y-2 text-sm" aria-label="Coverage and retrieval">
        <h3 className="font-semibold">Coverage</h3>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          <dt className="text-[var(--text-muted)]">Model</dt>
          <dd>
            {modelSupportLabel(cov)}
            {cov.model_supported_basis ? <span className="text-xs text-[var(--text-muted)]"> · {cov.model_supported_basis}</span> : null}
          </dd>
          <dt className="text-[var(--text-muted)]">Saved forecast</dt>
          <dd>
            {savedForecastLabel(cov)}
            {cov.saved_forecast_basis ? <span className="text-xs text-[var(--text-muted)]"> · {cov.saved_forecast_basis}</span> : null}
            {cov.saved_forecast_href ? (
              <>
                {' '}
                <Link to={cov.saved_forecast_href} className="text-[var(--accent)] hover:underline">
                  Open →
                </Link>
              </>
            ) : null}
          </dd>
          <dt className="text-[var(--text-muted)]">Summary</dt>
          <dd>{coverageSummary(cov)}</dd>
          <dt className="text-[var(--text-muted)]">Retrieved</dt>
          <dd className="mono">{event.retrieved_at ? fmtLocal(event.retrieved_at) : '—'}</dd>
          <dt className="text-[var(--text-muted)]">Lookup</dt>
          <dd>
            {event.last_lookup?.outcome ?? 'not_attempted'}
            {event.last_lookup?.at ? ` · ${fmtLocal(event.last_lookup.at)}` : ''}
            {event.last_lookup?.reason ? ` · ${event.last_lookup.reason}` : ''}
          </dd>
        </dl>
        {cov.research_hrefs?.length ? (
          <ul className="flex flex-wrap gap-3 text-xs">
            {cov.research_hrefs.map((h) => (
              <li key={h.href}>
                <Link to={h.href} className="text-[var(--accent)] hover:underline">
                  {h.label} →
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="panel space-y-3" aria-label="Latest retrieved quotes">
        <div>
          <h3 className="font-semibold">{event.quote_section_label || 'Latest retrieved quotes'}</h3>
          <p className="text-xs text-[var(--text-muted)]">
            These are the latest quotes stored in the explorer cache, with retrieval timestamps—not live streaming prices.
            {event.markets_complete === false
              ? ' Market enumeration is incomplete; finality is not inferred from this list.'
              : ''}
          </p>
        </div>
        {event.markets.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">No markets in the cached record.</p>
        ) : (
          <ul className="space-y-3">
            {event.markets.map((m) => (
              <MarketRow key={m.ticker || JSON.stringify(m)} market={m} />
            ))}
          </ul>
        )}
        {event.notes?.length ? (
          <ul className="text-xs text-[var(--text-muted)] list-disc pl-4">
            {event.notes.slice(0, 6).map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="panel space-y-2 text-sm" aria-label="Rules and sources">
        <h3 className="font-semibold">Rules and settlement sources</h3>
        {event.settlement_sources?.length ? (
          <ul className="list-disc pl-5">
            {event.settlement_sources.map((s, i) => (
              <li key={`${s.name}-${i}`}>
                {s.url ? (
                  <a href={s.url} className="text-[var(--accent)] hover:underline" target="_blank" rel="noreferrer">
                    {s.name || s.url}
                  </a>
                ) : (
                  s.name || 'Unnamed source'
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[var(--text-muted)]">No settlement sources in the cached record.</p>
        )}
        <div className="flex flex-wrap gap-3 text-xs">
          {event.contract_url ? (
            <a href={event.contract_url} className="text-[var(--accent)] hover:underline" target="_blank" rel="noreferrer">
              Contract page →
            </a>
          ) : null}
          {event.contract_terms_url ? (
            <a href={event.contract_terms_url} className="text-[var(--accent)] hover:underline" target="_blank" rel="noreferrer">
              Contract terms →
            </a>
          ) : null}
        </div>
        {event.markets.some((m) => m.rules_primary) ? (
          <details>
            <summary className="cursor-pointer text-[var(--text-muted)]">Primary rules text</summary>
            <ul className="mt-2 space-y-2">
              {event.markets
                .filter((m) => m.rules_primary)
                .map((m) => (
                  <li key={m.ticker} className="text-xs">
                    <span className="mono">{m.ticker}</span>
                    <pre className="mono whitespace-pre-wrap break-words text-[var(--text-muted)] mt-1">
                      {(m.rules_primary || '').slice(0, 2000)}
                    </pre>
                  </li>
                ))}
            </ul>
          </details>
        ) : null}
      </section>

      <NotebookPanel target={primaryNote} aliases={aliases} title="My notes on this event" />
    </>
  )
}

function MarketRow({ market }: { market: ExploreMarket }) {
  const fmt = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(2))
  return (
    <li className="rounded-lg border border-[var(--border)] p-3 space-y-1 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="mono text-xs">{market.ticker}</span>
        <span className="text-xs text-[var(--text-muted)]">
          {market.status || 'unknown'}
          {market.is_open ? ' · open' : ''}
          {market.not_in_latest_response ? ' · missing from latest response' : ''}
        </span>
      </div>
      {(market.yes_sub_title || market.no_sub_title) && (
        <p className="text-xs text-[var(--text-muted)]">
          {market.yes_sub_title ? `Yes: ${market.yes_sub_title}` : null}
          {market.yes_sub_title && market.no_sub_title ? ' · ' : null}
          {market.no_sub_title ? `No: ${market.no_sub_title}` : null}
        </p>
      )}
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
        <div>
          <dt className="text-[var(--text-muted)]">Yes bid/ask</dt>
          <dd className="mono">
            {fmt(market.yes_bid)} / {fmt(market.yes_ask)}
          </dd>
        </div>
        <div>
          <dt className="text-[var(--text-muted)]">No bid/ask</dt>
          <dd className="mono">
            {fmt(market.no_bid)} / {fmt(market.no_ask)}
          </dd>
        </div>
        <div>
          <dt className="text-[var(--text-muted)]">Last</dt>
          <dd className="mono">{fmt(market.last_price)}</dd>
        </div>
        <div>
          <dt className="text-[var(--text-muted)]">Retrieved</dt>
          <dd className="mono">{market.retrieved_at ? fmtLocal(market.retrieved_at) : '—'}</dd>
        </div>
      </dl>
      <p className="text-xs text-[var(--text-muted)]">
        Freshness: {freshnessLabel(market.retrieval_freshness)}
        {market.close_time ? ` · closes ${fmtLocal(market.close_time)}` : ''}
      </p>
    </li>
  )
}

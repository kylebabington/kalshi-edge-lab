import type {
  Eligibility,
  Opportunity,
  OpportunityDetail,
  OpportunityFilters,
  OpportunityList,
  ResearchResult,
} from '../types/opportunities'

const AI_NOTE = 'AI research does not modify the statistical model probability.'
const TRANSFER_WARNING =
  'Settlement-source transfer KNYC -> CLINYC is NOT validated; weather probabilities are experimental (calibrated on NWS CLI KNYC, settled on Weather Company CLINYC).'

function eligibility(status: Eligibility['status'], over: Partial<Eligibility> = {}): Eligibility {
  return {
    eligible: status !== 'BLOCKED',
    status,
    reasons: ['prediction, quote, freshness and integrity checks passed'],
    warnings: [],
    blocking_reasons: [],
    domain: 'weather',
    rules_version: 'weather_v1',
    ...over,
  }
}

function base(over: Partial<Opportunity>): Opportunity {
  return {
    id: 'weather:MOCK',
    ticker: 'MOCK',
    event_ticker: 'MOCK-EVENT',
    event_key: 'MOCK-EVENT',
    category: 'weather',
    title: 'Mock market',
    contract_label: null,
    series: null,
    model_probability: 0.5,
    market_probability: 0.4,
    yes_bid: 0.39,
    yes_ask: 0.41,
    spread: 0.02,
    side: 'YES',
    side_probability: 0.5,
    side_price: 0.41,
    probability_edge: 0.09,
    expected_value_before_fees: 0.09,
    estimated_fees: 0.0169,
    expected_value_after_fees: 0.0731,
    fee_source: 'series_metadata_no_fee_change_history',
    fee_note: 'series-level schedule from local cache; event-level fee overrides not checked',
    confidence_score: null,
    confidence_label: null,
    confidence_source: null,
    model_agreement: null,
    sample_size: null,
    calibration_quality: null,
    prediction_as_of: '2026-10-15T16:05:00+00:00',
    quote_as_of: '2026-10-15T16:05:00+00:00',
    data_age_seconds: 900,
    data_freshness_status: 'fresh',
    prediction_status: 'OK',
    quality_status: 'CAUTION',
    quality_flags: [],
    warnings: [],
    blocking_reasons: [],
    attention_score: 40,
    attention_components: [],
    supporting_factors: [],
    risk_factors: [],
    ai_research_status: 'not_researched',
    ai_research_freshness: 'unavailable',
    ai_researched_at: null,
    research_mode: 'RESEARCH_ONLY',
    eligibility: eligibility('ELIGIBLE'),
    ...over,
  }
}

export const mockOpportunities: Opportunity[] = [
  base({
    id: 'weather:KXHIGHNY-26OCT15-T64',
    ticker: 'KXHIGHNY-26OCT15-T64',
    event_ticker: 'KXHIGHNY-26OCT15',
    event_key: 'KXHIGHNY-26OCT15',
    title: 'NYC high temperature 2026-10-15: 63° or below',
    contract_label: '63° or below',
    series: 'KXHIGHNY',
    model_probability: 0.325,
    market_probability: 0.045,
    yes_bid: 0.04,
    yes_ask: 0.05,
    spread: 0.01,
    side_probability: 0.325,
    side_price: 0.05,
    probability_edge: 0.275,
    expected_value_before_fees: 0.275,
    estimated_fees: 0.0034,
    expected_value_after_fees: 0.2716,
    confidence_score: 0.3,
    confidence_label: 'LOW',
    confidence_source: 'weather_model',
    model_agreement: 'LOW',
    sample_size: 114,
    calibration_quality: 'same_run_global:adequate',
    data_age_seconds: 1200,
    attention_score: 48,
    attention_components: [
      { name: 'edge', points: 40, reason: '+27.5 pt probability edge on YES vs executable price' },
      { name: 'ev_after_fees', points: 10, reason: 'after-fee EV $+0.2716 per contract' },
      { name: 'confidence', points: 0, reason: 'LOW model confidence (no bonus)' },
      { name: 'calibration', points: 8, reason: 'calibration sample N=114' },
      { name: 'freshness', points: 10, reason: 'fresh data (20 min old)' },
      { name: 'weather_transfer_unvalidated', points: -15, reason: TRANSFER_WARNING },
      { name: 'model_agreement_low', points: -5, reason: 'calibrated GFS and GEFS bucket distributions disagree (LOW)' },
    ],
    warnings: [TRANSFER_WARNING, 'calibrated GFS and GEFS bucket distributions disagree (LOW)'],
    eligibility: eligibility('ELIGIBLE_WITH_CAUTION', {
      warnings: [TRANSFER_WARNING, 'calibrated GFS and GEFS bucket distributions disagree (LOW)'],
      reasons: [
        'prediction, quote, freshness and integrity checks passed',
        'LOW confidence (0.30) is attributable to the unvalidated settlement transfer; allowed with caution',
      ],
    }),
  }),
  base({
    id: 'sports:KXNFLGAME-MOCK-HOME',
    ticker: 'KXNFLGAME-MOCK-HOME',
    event_ticker: 'KXNFLGAME-MOCK',
    event_key: 'nfl|espn:401',
    category: 'sports',
    title: 'Mock United vs Fixture City - Mock United',
    series: 'KXNFLGAME',
    model_probability: 0.62,
    market_probability: 0.5,
    yes_bid: 0.49,
    yes_ask: 0.51,
    side_probability: 0.62,
    side_price: 0.51,
    probability_edge: 0.11,
    expected_value_before_fees: 0.11,
    estimated_fees: null,
    expected_value_after_fees: null,
    fee_source: 'unavailable',
    fee_note: 'no cached fee schedule for series KXNFLGAME',
    model_agreement: 'HIGH',
    calibration_quality: 'calibrated',
    attention_score: 36,
    attention_components: [
      { name: 'edge', points: 22, reason: '+11.0 pt probability edge on YES vs executable price' },
      { name: 'ev_after_fees', points: 0, reason: 'after-fee EV not scored (fees unavailable)' },
      { name: 'confidence', points: 0, reason: 'no validated confidence metric for this domain' },
      { name: 'calibration', points: 6, reason: 'calibrated sports model available' },
      { name: 'freshness', points: 10, reason: 'fresh data (15 min old)' },
      { name: 'fees_unavailable', points: -5, reason: 'fees unavailable (no cached fee schedule); after-fee EV not computed' },
    ],
    warnings: ['fees unavailable (no cached fee schedule); after-fee EV not computed'],
    ai_research_status: 'researched',
    ai_research_freshness: 'cached',
    ai_researched_at: '2026-10-15T16:20:00+00:00',
    eligibility: eligibility('ELIGIBLE_WITH_CAUTION', {
      domain: 'sports',
      rules_version: 'sports_v1',
      warnings: ['fees unavailable (no cached fee schedule); after-fee EV not computed'],
    }),
  }),
  base({
    id: 'sports:KXNFLGAME-MOCK-AWAY',
    ticker: 'KXNFLGAME-MOCK-AWAY',
    event_ticker: 'KXNFLGAME-MOCK',
    event_key: 'nfl|espn:401',
    category: 'sports',
    title: 'Mock United vs Fixture City - Fixture City',
    model_probability: 0.38,
    market_probability: null,
    yes_bid: null,
    yes_ask: null,
    spread: null,
    side: null,
    side_probability: null,
    side_price: null,
    probability_edge: null,
    expected_value_before_fees: null,
    estimated_fees: null,
    expected_value_after_fees: null,
    fee_source: 'unavailable',
    quality_status: 'BLOCKED',
    attention_score: 0,
    blocking_reasons: ['no saved bid/ask quote for this contract'],
    eligibility: eligibility('BLOCKED', {
      domain: 'sports',
      rules_version: 'sports_v1',
      reasons: [],
      blocking_reasons: ['no saved bid/ask quote for this contract'],
    }),
  }),
]

export function mockOpportunityList(filters: OpportunityFilters = {}): OpportunityList {
  const items = mockOpportunities.filter(
    (o) =>
      (filters.include_blocked || o.quality_status !== 'BLOCKED') &&
      (!filters.category || o.category === filters.category) &&
      (!filters.ai_status || o.ai_research_status === filters.ai_status) &&
      (filters.min_edge == null || (o.probability_edge ?? -1) >= filters.min_edge),
  )
  return {
    generated_at: '2026-10-15T16:30:00+00:00',
    mode: 'RESEARCH_ONLY',
    note: 'attention_score ranks which markets deserve a closer look. It is not a probability, not an expected value and not a recommendation.',
    ai: { enabled: true, provider: 'ollama', model: 'qwen3:8b', min_edge: 0.05, auto_research: false, note: AI_NOTE },
    total: items.length,
    counts: { total: mockOpportunities.length, by_quality: { CAUTION: 2, BLOCKED: 1 }, by_category: { weather: 1, sports: 2 } },
    page: 1,
    page_size: 500,
    pages: 1,
    items,
  }
}

export const mockResearchDone: ResearchResult = {
  ticker: 'KXNFLGAME-MOCK-HOME',
  status: 'ok',
  freshness: 'cached',
  data_changed: false,
  eligibility: mockOpportunities[1].eligibility,
  research: {
    summary: 'The frozen and calibrated models both rate the home side above the market reference.',
    supporting_evidence: [{ statement: 'Calibrated model gives 0.62', basis: 'FACT', evidence_ids: ['E1'] }],
    contradictory_evidence: [{ statement: 'Market reference sits at 0.50', basis: 'FACT', evidence_ids: ['E2'] }],
    risk_factors: [{ statement: 'Injury and lineup information is not collected', basis: 'UNKNOWN', evidence_ids: [] }],
    important_changes: [],
    unknowns: ['Starting lineups'],
    research_confidence: 'LOW',
    model_probability_should_not_be_changed: true,
  },
  provider: 'ollama',
  model: 'qwen3:8b',
  created_at: '2026-10-15T16:20:00+00:00',
  error: null,
  note: AI_NOTE,
}

function emptyResearch(o: Opportunity): ResearchResult {
  return {
    ticker: o.ticker,
    status: 'not_researched',
    freshness: 'unavailable',
    data_changed: false,
    eligibility: o.eligibility,
    research: null,
    provider: 'ollama',
    model: 'qwen3:8b',
    created_at: null,
    error: null,
    note: AI_NOTE,
  }
}

export function mockOpportunityDetail(ticker: string): OpportunityDetail | null {
  const o = mockOpportunities.find((x) => x.ticker === ticker)
  if (!o) return null
  return {
    generated_at: '2026-10-15T16:30:00+00:00',
    opportunity: o,
    evidence: [
      {
        id: 'E1',
        source: 'kalshi-edge-lab model',
        source_type: 'model_output',
        title: 'Statistical model output',
        content: `Model probability (YES): ${o.model_probability ?? 'unavailable'}`,
        published_at: o.prediction_as_of,
        retrieved_at: null,
        url: null,
        reliability: 'internal_model',
      },
    ],
    research: o.ticker === mockResearchDone.ticker ? mockResearchDone : emptyResearch(o),
    ai: mockOpportunityList().ai,
    note: mockOpportunityList().note,
  }
}

export function mockResearch(ticker: string): ResearchResult {
  const o = mockOpportunities.find((x) => x.ticker === ticker) ?? mockOpportunities[0]
  if (!o.eligibility.eligible) return { ...emptyResearch(o), status: 'ineligible' }
  return { ...mockResearchDone, ticker: o.ticker, eligibility: o.eligibility, freshness: 'fresh' }
}

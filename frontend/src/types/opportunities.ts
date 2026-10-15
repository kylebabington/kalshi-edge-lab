export type OpportunityCategory = 'weather' | 'sports'
export type QualityStatus = 'OK' | 'CAUTION' | 'BLOCKED'
export type EligibilityStatus = 'ELIGIBLE' | 'ELIGIBLE_WITH_CAUTION' | 'BLOCKED'
export type Freshness = 'fresh' | 'aging' | 'stale' | 'unavailable'
export type AIResearchStatus = 'not_researched' | 'researched' | 'stale'
export type ResearchFreshness = 'fresh' | 'cached' | 'stale' | 'unavailable'

export type Eligibility = {
  eligible: boolean
  status: EligibilityStatus
  reasons: string[]
  warnings: string[]
  blocking_reasons: string[]
  domain: string
  rules_version: string
}

export type AttentionComponent = { name: string; points: number; reason: string }

export type QualityFlag = { code: string; severity: 'block' | 'caution' | 'info'; message: string; penalty: number }

export type Opportunity = {
  id: string
  ticker: string
  event_ticker: string
  event_key: string
  category: OpportunityCategory
  title: string
  contract_label: string | null
  series: string | null
  model_probability: number | null
  market_probability: number | null
  yes_bid: number | null
  yes_ask: number | null
  spread: number | null
  side: 'YES' | 'NO' | null
  side_probability: number | null
  side_price: number | null
  probability_edge: number | null
  expected_value_before_fees: number | null
  estimated_fees: number | null
  expected_value_after_fees: number | null
  fee_source: string
  fee_note: string | null
  confidence_score: number | null
  confidence_label: string | null
  confidence_source: string | null
  model_agreement: string | null
  sample_size: number | null
  calibration_quality: string | null
  prediction_as_of: string | null
  quote_as_of: string | null
  data_age_seconds: number | null
  data_freshness_status: Freshness
  prediction_status: string
  quality_status: QualityStatus
  quality_flags: QualityFlag[]
  warnings: string[]
  blocking_reasons: string[]
  attention_score: number
  attention_components: AttentionComponent[]
  supporting_factors: string[]
  risk_factors: string[]
  ai_research_status: AIResearchStatus
  ai_research_freshness?: ResearchFreshness
  ai_researched_at?: string | null
  research_mode: string
  eligibility: Eligibility
}

export type AISummary = {
  enabled: boolean
  provider: string
  model: string | null
  min_edge: number
  auto_research: boolean
  note: string
}

export type OpportunityList = {
  generated_at: string
  mode: string
  note: string
  ai: AISummary
  total: number
  counts: { total: number; by_quality: Record<string, number>; by_category: Record<string, number> }
  page: number
  page_size: number
  pages: number
  items: Opportunity[]
}

export type OpportunityFilters = {
  category?: OpportunityCategory
  min_confidence?: 'LOW' | 'MODERATE' | 'HIGH'
  min_edge?: number
  freshness?: Freshness
  ai_status?: AIResearchStatus
  include_blocked?: boolean
}

export type ResearchItem = {
  statement: string
  basis: 'FACT' | 'INFERENCE' | 'UNKNOWN'
  evidence_ids: string[]
  validator_note?: string
}

export type AIResearch = {
  summary: string
  supporting_evidence: ResearchItem[]
  contradictory_evidence: ResearchItem[]
  risk_factors: ResearchItem[]
  important_changes: ResearchItem[]
  unknowns: string[]
  research_confidence: 'LOW' | 'MODERATE' | 'HIGH'
  research_confidence_capped?: boolean
  model_probability_should_not_be_changed: true
}

export type ResearchResult = {
  ticker: string
  status: 'ok' | 'ineligible' | 'unavailable' | 'error' | 'not_researched'
  freshness: ResearchFreshness
  data_changed: boolean
  eligibility: Eligibility
  research: AIResearch | null
  provider: string | null
  model: string | null
  created_at: string | null
  error: { code: string; message: string } | null
  note: string
}

export type ResearchEvidence = {
  id: string
  source: string
  source_type: string
  title: string
  content: string
  published_at: string | null
  retrieved_at: string | null
  url: string | null
  reliability: string
}

export type OpportunityDetail = {
  generated_at: string
  opportunity: Opportunity
  evidence: ResearchEvidence[]
  research: ResearchResult
  ai: AISummary
  note: string
}

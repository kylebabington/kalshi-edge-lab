import type { StatusRecord } from './weather'

export type ResearchCategory = 'weather' | 'sports'
export type ResearchDesignation = 'historical' | 'prospective'
export type CollectionMode = 'historical_replay' | 'manual_pilot' | 'automatic_collection'

export const COLLECTION_MODES: CollectionMode[] = ['historical_replay', 'manual_pilot', 'automatic_collection']
export const MODE_LABELS: Record<CollectionMode, string> = {
  historical_replay: 'Historical replay',
  manual_pilot: 'Manual pilot',
  automatic_collection: 'Automatic collection',
}

export type Sample = { label: string; value: number | string | null; basis: string; reason?: string | null }

export type ResultLink = {
  label: string
  kind: 'tracked' | 'local' | 'api' | string
  available: boolean
  href?: string | null
  path?: string | null
  as_of?: string | null
  reason?: string | null
}

export type Period = { label: string; start: string | null; end: string | null; basis: string }

export type ExperimentStatus = {
  implementation?: StatusRecord
  evidence?: StatusRecord[]
  operations?: StatusRecord
  integrity?: StatusRecord
  policy?: StatusRecord[]
}

export type ExperimentCard = {
  id: string
  category: ResearchCategory
  protocol_id: string | null
  title: string
  version: string | null
  designation: ResearchDesignation | string
  modes: CollectionMode[]
  lifecycle?: { value: string; basis: string } | null
  frozen_at?: string | null
  period?: Period | null
  period_reason?: string | null
  samples: Sample[]
  results: ResultLink[]
  results_available: boolean
  status: ExperimentStatus
  summary_note?: string | null
  error?: string | null
  loading?: boolean
}

export type ExperimentList = {
  generated_at: string
  category: ResearchCategory
  mode: string
  summary_note?: string
  experiments: ExperimentCard[]
}

export type ModeBlock = {
  mode: CollectionMode
  label: string
  applies: boolean
  description: string
  samples: Sample[]
  period?: Period | null
  operations?: StatusRecord | null
  href?: string | null
}

export type ExperimentSource = { label: string; path: string; tracked: boolean; exists: boolean; as_of?: string | null }

export type ExperimentDetail = ExperimentCard & {
  generated_at: string
  mode: string
  question?: string | null
  reference?: string | null
  candidates: { id: string; label: string; description: string }[]
  cohort_rules: { name: string; rule: string }[]
  limitations: string[]
  mode_blocks: ModeBlock[]
  sources: ExperimentSource[]
  frozen_settings?: Record<string, unknown> | null
  links: { label: string; href: string }[]
}

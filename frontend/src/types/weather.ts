import type { SchedulerState } from './sports'

export type WeatherPrediction = {
  event_ticker: string
  target_date: string
  prediction_status: string
  expected_high: number | null
  median_high: number | null
  p10_high: number | null
  p25_high: number | null
  p50_high: number | null
  p75_high: number | null
  p90_high: number | null
  bucket_probabilities: Record<string, number>
  confidence_score: number | null
  confidence_label: string | null
  calibration_target_regime: string | null
  live_target_regime: string | null
  settlement_source_transfer_validated: boolean | null
  residual_sample_size: number | null
  model_agreement?: string | null
  calibration_method?: string | null
  transfer_status?: string | null
  warnings: string[]
  provenance?: Record<string, unknown>
  point_forecast_high?: number | null
  gefs_member_count?: number | null
  gefs_mean?: number | null
  gefs_median?: number | null
  gefs_std?: number | null
  gefs_min?: number | null
  gefs_max?: number | null
}

export type WeatherResolution = {
  event_ticker: string
  target_date: string
  settlement_source_regime: string
  resolution_source: string
  resolution_certainty: string
  station_id: string
  warnings: string[]
}

export type KalshiMarket = {
  ticker?: string | null
  label?: string | null
  yes_bid_dollars?: number | null
  yes_ask_dollars?: number | null
  mid_dollars?: number | null
  spread_dollars?: number | null
}

export type WeatherEvent = {
  event_ticker: string
  target_date?: string | null
  resolution: WeatherResolution
  prediction: WeatherPrediction
  evidence?: Record<string, unknown>
  kalshi_markets: KalshiMarket[]
  research_status?: Record<string, unknown>
  shadow_predictions?: Record<string, ShadowPredictionBlock>
  warnings?: string[]
}

export type ShadowPredictionBlock = {
  status?: string
  probabilities?: Record<string, number> | null
  unavailable_reason?: string | null
  candidate_id?: string
  transfer_status?: string
  live_target_regime?: string
}

export type ProspectiveValidation = {
  status?: string
  available?: boolean
  checkpoint_coverage?: Record<
    string,
    { captured?: number; missed?: number; total?: number; display?: string }
  >
  scored_events?: number | null
  scored_events_basis?: string
  captured_checkpoints?: number
  missed_checkpoints?: number
  incumbent_gfs?: Record<string, number | null> | null
  shadow_gfs_hrrr_equal?: Record<string, number | null> | null
  paired_delta_brier?: Record<string, unknown> | null
  experimental_transfer_status?: string
  experimental_transfer_status_basis?: string
  /** Methodology declaration (how replay is designed), not a run result. */
  historical_replay?: Record<string, unknown>
  historical_replay_kind?: string
}

export type StatusKind = 'implementation' | 'evidence' | 'operations' | 'integrity' | 'policy' | 'configuration'

/** What kind of claim a status is and what supports it. */
export type StatusRecord = {
  kind: StatusKind | string
  state: string
  label: string
  basis: string
  source?: string | null
  as_of?: string | null
  reason?: string | null
  checks?: Record<string, unknown>
}

export type PhaseStatusDetail = {
  label: string
  value: string
  reason?: string | null
  basis?: string
  implementation?: StatusRecord
  evidence?: StatusRecord[]
  completion?: StatusRecord
  protocol?: StatusRecord
}

export type ResearchStatusDetail = PhaseStatusDetail | (StatusRecord & { value?: string })

export type SourceStatusDetail = {
  label: string
  value: string
  configuration: StatusRecord
  evidence: StatusRecord[]
  operations: StatusRecord
}

export type ModelSummary = {
  model_summary_available: boolean
  generated_at: string
  mode: string
  methodology_constants: Record<string, number | string>
  calibration: Record<string, unknown>
  forecast_error_by_run?: Record<string, Record<string, number>>
  oos?: Record<string, unknown> | null
  reliability_bins?: Array<Record<string, unknown>> | null
  settlement_regime?: Record<string, unknown>
  settlement_transfer?: Record<string, unknown>
  direct_clinyc?: Record<string, unknown>
  /** Legacy strings. Phases without a tracked completion record are UNKNOWN, never COMPLETE. */
  research_status?: Record<string, string>
  research_status_details?: Record<string, ResearchStatusDetail>
  /** Legacy strings: CONFIGURED or PRESENT (file present, validity not checked); never ACTIVE. */
  multi_source_evidence?: Record<string, string>
  multi_source_evidence_details?: Record<string, SourceStatusDetail>
  prospective_journal?: Record<string, unknown>
  prospective_validation?: ProspectiveValidation
  notes?: string[]
}

export type LiveWeatherResponse = {
  generated_at: string
  mode: string
  calibration_csv_loaded?: boolean
  events: WeatherEvent[]
}

export type WeatherView = 'upcoming' | 'awaiting_settlement' | 'settled'

export type SavedSnapshotSummary = {
  snapshot_id: string
  event_ticker: string
  target_date: string | null
  prediction_as_of: string | null
  created_at: string | null
  checkpoint_id: string | null
  prediction_status: string | null
  confidence_label: string | null
  expected_high: number | null
  resolution_regime?: string | null
  transfer_status?: string | null
  bucket_probabilities: Record<string, number>
  invalid_probabilities: number
}

/** Score of one snapshot. Only `scored` carries metrics; every other state is explicitly unavailable. */
export type SnapshotScoreState = {
  state: 'scored' | 'not_scored' | 'unavailable' | 'invalid'
  reason?: string
  brier?: number | null
  log_loss?: number | null
  scored_at?: string | null
  probability_on_winner?: number | null
  winning_bucket?: string | null
}

/** Event-level settlement, confirmed by any valid score of any snapshot of the event. */
export type EventSettlementState = {
  state: 'confirmed' | 'not_confirmed' | 'conflict'
  reason?: string
  winning_bucket?: string | null
  actual_high_f?: number | null
  source?: string | null
  regime?: string | null
  confirmed_at?: string | null
  confirmed_by_snapshot_id?: string | null
  confirming_scores?: number
}

export type SavedWeatherEvent = {
  category: 'weather'
  event_ticker: string
  target_date: string | null
  view: WeatherView
  snapshot_count: number
  scored_snapshot_count: number
  first_prediction_as_of: string | null
  displayed_snapshot: SavedSnapshotSummary
  displayed_snapshot_score: SnapshotScoreState
  event_settlement: EventSettlementState
  receipts: { captured: number; missed: number }
}

export type SavedWeatherEventList = {
  generated_at: string
  label: string
  source: string
  artifacts: { snapshots: string; scores: string }
  filters: Record<string, string>
  sort: string
  total: number
  page: number
  page_size: number
  pages: number
  views: Record<WeatherView, number>
  items: SavedWeatherEvent[]
}

/** Saved definition of one bucket. Same label with a different definition is a separate series. */
export type WeatherBucketDefinition = {
  id: string
  label: string
  lower_f: number | null
  upper_f: number | null
  ticker: string | null
  bounds_parsed: boolean
  snapshots: number
  valid_points: number
  definition_variants: number
}

export type ObservationPosition = 'in_day' | 'before_day' | 'after_day' | 'unknown'

export type SnapshotObservation = {
  present: boolean
  used_by_prediction: boolean | null
  station_id?: string | null
  high_so_far_f?: number | null
  latest_temperature_f?: number | null
  latest_timestamp?: string | null
  quality_status?: string | null
  freshness_status?: string | null
  climate_day_bounds_utc?: [string, string] | null
  latest_position?: ObservationPosition
  trajectory_points?: Record<ObservationPosition, number>
}

export type SnapshotRun = {
  run_id: string | null
  forecast_run: string | null
  gfs_model_run_at: string | null
  hrrr_model_run_at: string | null
  gefs_member_count: number | null
  calibration_artifact_id: string | null
  schema_version: string | null
}

export type SavedSnapshotHistoryRow = Pick<
  SavedSnapshotSummary,
  'snapshot_id' | 'prediction_as_of' | 'created_at' | 'checkpoint_id' | 'prediction_status' | 'confidence_label' | 'expected_high'
> & {
  score: SnapshotScoreState
  probabilities?: Record<string, { p: number | null; invalid: boolean }>
  run?: SnapshotRun
  observation?: SnapshotObservation
}

export type SavedWeatherEventDetail = {
  generated_at: string
  label: string
  source: string
  event: SavedWeatherEvent
  snapshots: SavedSnapshotHistoryRow[]
  buckets?: WeatherBucketDefinition[]
  history_note?: string
}

export type SavedWeatherFilters = {
  status?: string
  q?: string
  sort?: string
  page?: number
  page_size?: number
}

export type WeatherCollectorStatus = {
  generated_at: string
  task_name: string
  cadence_minutes: number
  scheduler: SchedulerState
  lock: { held: boolean; state: string; acquired_at?: string | null }
  log_observed: boolean
  cycles: {
    last_cycle_finished_at: string | null
    last_cycle_exit_code: number | null
    last_successful_cycle_at: string | null
    minutes_since_last_successful_cycle: number | null
    cycles_24h: number
    failures_24h: number
    consecutive_failures: number
  }
  forecast_freshness: {
    last_snapshot_prediction_as_of: string | null
    minutes_since_last_snapshot: number | null
    last_captured_checkpoint_at: string | null
    last_score_written_at: string | null
    minutes_since_last_score: number | null
    missed_checkpoints_24h: number
  }
  limits: string
}

export type WeatherMetricsContext = {
  generated_at: string
  prospective_snapshot_scores: {
    kind: 'prospective'
    scored_snapshots: number
    events: number
    first_target_date: string | null
    last_target_date: string | null
  }
  operational_replay: {
    kind: 'historical'
    available: boolean
    missing?: string
    first_target_date?: string | null
    last_target_date?: string | null
    target_dates?: number
  }
}

export type PredictionSnapshotMeta = {
  snapshot_id: string
  prediction_as_of: string
  created_at?: string
  event_ticker?: string
  target_date?: string
  prediction_status?: string
  path?: string
}

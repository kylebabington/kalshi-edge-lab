export type CandidateName = 'frozen' | 'calibrated' | 'market' | 'blend'

export const CANDIDATES: CandidateName[] = ['frozen', 'calibrated', 'market', 'blend']

export type SportsArtifacts = {
  capture_records: string
  settlement_scores: string
  settlement_runs: string
  capture_runs: string
  raw_evidence: string
}

export type CollectorBrief = {
  recorded: boolean
  note?: string
  last_cycle_finished_at?: string | null
  last_cycle_result?: string | null
  last_successful_capture_at?: string | null
  last_settlement_check_at?: string | null
  consecutive_failures?: number | null
}

export type SportsSummary = {
  generated_at: string
  label: string
  source: string
  mode: string
  protocol: string
  scoring_protocol: string
  artifacts: SportsArtifacts
  counts: {
    captured_events: number
    missed_events: number
    upcoming_events: number
    scored_events: number
    fully_settled_events: number
    pending_events: number
    captured_contracts: number
    labelled_contracts: number
  }
  candidate_counts: Record<CandidateName, { available: number; unavailable: number }>
  by_sport: Record<string, Record<string, number>>
  last_capture_at: string | null
  last_capture_run: { run_id?: string; started_at?: string; finished_at?: string; counts?: Record<string, number> } | null
  last_settlement_run: { run_id?: string; finished_at?: string; counts?: Record<string, number> } | null
  integrity: Record<string, number>
  collector: CollectorBrief
  collection?: {
    by_mode: Record<CollectionMode, { captured: number; missed: number }>
    labels: Record<CollectionMode, string>
    amendment: {
      id: string
      date: string
      doc: string
      cadence_minutes: number
      collector_commit: string
      first_scheduled_action_at: string
      first_trigger_run_at: string
    }
  }
}

export type CollectionMode = 'automatic' | 'collector_manual' | 'manual_pilot'

export type EventView = 'upcoming' | 'awaiting_settlement' | 'settled' | 'missed'

export type SettlementSummary = {
  state: string
  contracts: number
  final: number
  labelled: number
  pending: number
  states: Record<string, number>
  last_settled_at?: string | null
}

export type SportsEventItem = {
  event_id: string
  competition: string
  competition_name?: string | null
  sport: string | null
  source: string | null
  cluster: string
  title: string | null
  title_source?: string | null
  participants: string[]
  kalshi_event_tickers: string[]
  event_start: string | null
  window_open: string | null
  scheduled_cutoff: string | null
  capture_status: 'WINDOW_CAPTURE' | 'MISSED' | string
  prediction_as_of: string | null
  finalized_at: string | null
  horizon_minutes: number | null
  missed_reason: string | null
  view: EventView
  settlement: SettlementSummary
  families: string[]
  cohorts: string[]
  contracts_total: number
  contracts_ok: number
  candidate_available: Record<CandidateName, number>
  comparability: string | null
  integrity: Record<string, string>
  collection_mode?: CollectionMode
}

export type SportsEventList = {
  generated_at: string
  label: string
  source: string
  artifacts: SportsArtifacts
  filters: Record<string, string>
  sort?: string
  total: number
  page: number
  page_size: number
  pages: number
  views: Record<EventView, number>
  facets: { sport: string[]; competition: string[]; families: string[]; cohorts: string[] }
  facet_labels?: { competition?: Record<string, string> }
  items: SportsEventItem[]
}

export type EventFilters = {
  sport?: string
  competition?: string
  family?: string
  cohort?: string
  status?: string
  collection?: string
  q?: string
  sort?: string
  page?: number
  page_size?: number
}

export type CandidateView = {
  available: boolean
  reason?: string
  p?: number
  model?: string | null
  map?: Record<string, unknown> | null
  market_reference?: {
    mid: number
    bid: number | null
    ask: number | null
    candle_end_utc: string | null
    candle_age_s_at_prediction: number | null
  }
  brier?: number
  log_loss?: number
}

export type ContractRow = {
  ticker: string
  series: string | null
  kind: string | null
  segment: string | null
  strike: number | string | null
  side: string | null
  cohort: string | null
  status: string
  excluded_reason: string | null
  market_pick: boolean | null
  description: string | null
  market_title: string | null
  rules: string | null
  candidates: Record<CandidateName, CandidateView> | null
  listing_diagnostics: { current_yes_bid: string | null; current_yes_ask: string | null; note: string } | null
  settlement: {
    state: string
    detail?: string | null
    outcome: number | null
    settled_at?: string | null
    score_checksum?: string
  }
}

export type SportsEventDetail = {
  generated_at: string
  label: string
  source: string
  event: SportsEventItem
  families: { family: string; contracts: ContractRow[] }[]
  description_source: { source: string; files: Record<string, number> }
  notes: {
    binary: string
    market_reference: string
    market_rule: Record<string, unknown>
    candidates: Record<string, string>
  }
  missed: { reason: string | null; written_at: string | null; prior_failed_attempts: number } | null
  provenance: {
    protocol: string | null
    schema: string | null
    run_id: string | null
    capture_started_at?: string | null
    comparability_note?: string | null
    timing_label?: string | null
    collection?: {
      mode: CollectionMode
      label: string
      cycle_started_at: string | null
      amendment: string
      amendment_doc: string
      note: string
    }
    integrity: Record<string, unknown>
    pins: Record<string, string>
    dependencies: Record<string, string>
    timing: Record<string, unknown>
    evidence_timing: Record<string, unknown>
    model: Record<string, unknown>
    settlement_runs: string[]
  }
}

export type CandidateMetric = {
  candidate: CandidateName
  contracts: number
  events: number
  brier?: number | null
  log_loss?: number | null
}

export type PairedComparison = {
  candidate: CandidateName
  reference: CandidateName
  matched_contracts: number
  unique_events: number
  d_brier?: number | null
  d_log_loss?: number | null
}

export type MetricBlock = { candidates: CandidateMetric[]; comparisons: PairedComparison[] }

export type ResultFilters = {
  sport?: string
  competition?: string
  family?: string
  cohort?: string
  weighting?: 'event' | 'contract'
}

export type SportsResults = {
  generated_at: string
  label: string
  pilot_note: string
  sign_note: string
  source: string
  weighting: 'event' | 'contract'
  weighting_note: string
  paired_note: string
  metrics_note: string
  filters: Record<string, string>
  counts: {
    final_scores: number
    labelled_contracts: number
    labelled_events: number
    settlement_states: Record<string, number>
    excluded_score_checksum_problems: number
  }
  period?: {
    first_prediction_as_of: string | null
    last_prediction_as_of: string | null
    first_settled_at: string | null
    last_settled_at: string | null
  }
  facets: Record<string, string[]>
  overall: MetricBlock
  breakdown: Record<string, ({ group: string } & MetricBlock)[]>
  artifacts: SportsArtifacts
}

export type TimelineFilters = {
  pair?: string
  sport?: string
  competition?: string
  family?: string
  cohort?: string
  collection?: string
}

export type TimelinePoint = {
  event_id: string
  completed_at: string
  contracts: number
  collection_mode: CollectionMode
  event_d_brier: number
  events: number
  cumulative_d_brier_event: number
}

type GroupCount = { events: number; selected_contracts: number }

export type SportsResultsTimeline = {
  generated_at: string
  label: string
  kind: 'prospective'
  pilot_note: string
  sign_note: string
  note: string
  pair: { candidate: CandidateName; reference: CandidateName }
  pairs: { candidate: CandidateName; reference: CandidateName }[]
  filters: Record<string, string>
  collection: string
  collection_modes: CollectionMode[]
  collection_labels: Record<string, string>
  points: TimelinePoint[]
  cohort: {
    events: number
    contracts: number
    first_completed_at: string | null
    last_completed_at: string | null
    first_prediction_as_of: string | null
    last_prediction_as_of: string | null
    by_collection: Record<string, number>
  }
  final: { cumulative_d_brier_event: number | null; paired_d_brier_event_on_cohort: number | null; equal: boolean }
  not_in_chart: {
    partially_settled: GroupCount
    pending: GroupCount
    nonbinary: GroupCount
    inconsistent: GroupCount
    undated_completed: GroupCount & { d_brier_event: number | null }
  }
  results_table_basis: { note: string; contracts: number; events: number; d_brier_event: number | null; d_brier: number | null }
  artifacts: SportsArtifacts
}

export type CoverageFilters = {
  sport?: string
  competition?: string
  family?: string
  status?: string
  q?: string
  page?: number
  page_size?: number
}

export type CandidateAvailability = { status: string; detail: string }

export type SportsCoverage = {
  generated_at: string
  available: boolean
  missing?: string
  source: Record<string, string>
  filters: Record<string, string>
  inventory_series_total?: number | null
  matrix_series_total?: number
  inventory_matches_matrix?: boolean
  status_counts?: Record<string, number>
  facets?: Record<string, string[]>
  reasons?: Record<string, { reason: string; series: number }[]>
  series?: {
    total: number
    page: number
    page_size: number
    pages: number
    items: {
      series_ticker: string
      series_title: string | null
      sport: string
      competition: string | null
      competition_top: string
      family: string
      source: string | null
      status: string
      reason: string | null
      comparability: string | null
      calibration_status: string | null
      blend_status: string | null
      market_candidate: string | null
    }[]
  } | null
  competition_family?: {
    total: number
    shown: number
    available_counts: Record<CandidateName, number>
    items: {
      competition: string
      sport: string
      source: string
      family: string
      live_adapter: string
      comparability: string
      series_supported: number
      series_unsupported: number
      candidates: Record<CandidateName, CandidateAvailability>
    }[]
  }
  observed_in_saved_captures?: Record<
    CandidateName,
    { available: number; unavailable: { reason: string; contracts: number }[] }
  >
}

export type HistoricalFilters = {
  protocol?: string
  level?: string
  key?: string
  family?: string
  cohort?: string
  comparison?: string
  page?: number
  page_size?: number
}

export type HistoricalProtocol = {
  protocol: string
  phase: string
  comparison: string
  status: string
  frozen_at?: string | null
  period?: { label: string; start: string; end: string } | null
  report: string
  addenda: string[]
  source: { label: string; tracked_csv: string; local_artifacts: string }
  available: boolean
  missing?: string
  columns?: string[]
  total?: number
  page?: number
  pages?: number
  page_size?: number
  rows: Record<string, string>[] | null
  facets?: Record<string, string[]>
}

export type SportsHistorical = {
  generated_at: string
  label: string
  separation_note: string
  interpretation: string
  filters: Record<string, string>
  protocols: HistoricalProtocol[]
}

export type SchedulerState = {
  observable: boolean
  configured: boolean | 'unknown'
  enabled: boolean | 'unknown'
  status: string
  next_run_time: string
  last_run_time: string
  last_result: string
  last_result_decoded?: { code: number | null; hex: string | null; name: string; meaning: string }
  reason?: string
  logon_mode?: string
}

export type CollectorCycle = {
  started_at?: string
  finished_at?: string
  duration_s?: number
  result?: string
  exit_code?: number
  detail?: string | null
  plan?: Record<string, unknown>
  capture?: Record<string, unknown>
  settlement?: Record<string, unknown>
  bottleneck?: string | null
}

export type SportsCollectorStatus = {
  generated_at: string
  task_name: string
  scheduler: SchedulerState
  weather_task: SchedulerState
  backup_task_name?: string
  backup_task?: SchedulerState
  cycle_failures?: { last_24h?: number | null; consecutive?: number | null; cycles_24h?: number | null }
  lock: { held: boolean; state: string; file_age_s?: number; owner?: Record<string, unknown> }
  cadence_minutes: number
  settlement_interval_minutes: number
  status_recorded: boolean
  status: {
    note?: string
    last_cycle?: CollectorCycle | null
    last_successful_cycle_at?: string | null
    last_capture_run_at?: string | null
    last_successful_capture_at?: string | null
    last_settlement_check_at?: string | null
    settlement_deferred_since?: string | null
    settlement_forced_by?: string | null
    consecutive_failures?: number
    cycles_24h?: number
    skipped_lock_busy_24h?: number
    failures_24h?: number
    recent_failures?: { at: string; result: string; detail?: string | null }[]
    duration_s_24h?: { median: number; max: number } | null
    bottlenecks_24h?: string[]
  }
  missed_windows: {
    total: number
    last_24h: number
    recent: { event_id: string; scheduled_cutoff: string | null; written_at: string | null; reason: string | null }[]
  }
  freshness: {
    last_accepted_capture_at: string | null
    minutes_since_last_capture: number | null
    last_score_written_at: string | null
    minutes_since_last_score: number | null
    last_cycle_finished_at: string | null
    minutes_since_last_cycle: number | null
  }
  history?: {
    recorded: boolean
    last_cycle_finished_at?: string | null
    last_cycle_result?: string | null
    last_successful_cycle_at?: string | null
  }
  backup?: {
    recorded: boolean
    note?: string
    backup?: string
    at?: string
    finished_at?: string
    duration_s?: number
    files?: number
    bytes?: number
    missing_count?: number
    incomplete?: boolean
    local_head?: string
    ref?: string
    snapshot?: string | null
    push?: string | null
    unpushed?: boolean
    last_snapshot_at?: string | null
    last_snapshot_lock_s?: number | null
    sports_lock_held_s?: number | null
    last_pushed_at?: string | null
    staging?: string | null
    paused?: { paused_at?: string; reason?: string } | null
  }
  limits: string
}

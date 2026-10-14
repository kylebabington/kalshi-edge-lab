/**
 * MOCK FIXTURES — used only when VITE_USE_MOCK=true. Every payload is labeled as a mock fixture and none
 * of it is a saved capture, score or research result. Normal mode never falls back to these.
 */
import type {
  EventFilters,
  SportsCollectorStatus,
  SportsCoverage,
  SportsEventDetail,
  SportsEventItem,
  SportsEventList,
  SportsHistorical,
  SportsResults,
  SportsResultsTimeline,
  SportsSummary,
} from '../types/sports'

export const MOCK_SOURCE = 'MOCK FIXTURE — not a saved capture'

export const mockSportsResultsTimeline: SportsResultsTimeline = {
  generated_at: '2026-10-11T14:00:00Z',
  label: 'Prospective pilot: paired Brier difference over completed events (MOCK FIXTURE)',
  kind: 'prospective',
  pilot_note: 'MOCK FIXTURE',
  sign_note: 'Differences are candidate minus reference on the same contracts.',
  note: 'MOCK FIXTURE — each point adds one completed event.',
  pair: { candidate: 'frozen', reference: 'market' },
  pairs: [
    { candidate: 'frozen', reference: 'market' },
    { candidate: 'calibrated', reference: 'frozen' },
    { candidate: 'blend', reference: 'frozen' },
    { candidate: 'blend', reference: 'market' },
  ],
  filters: {},
  collection: 'all',
  collection_modes: ['automatic', 'collector_manual', 'manual_pilot'],
  collection_labels: { automatic: 'Automatic', collector_manual: 'Collector (manual)', manual_pilot: 'Manual pilot' },
  points: [
    { event_id: 'ncaaf|espn:mock-1', completed_at: '2026-10-05T03:10:00Z', contracts: 2, collection_mode: 'manual_pilot', event_d_brier: 0.02, events: 1, cumulative_d_brier_event: 0.02 },
    { event_id: 'nfl|espn:mock-2', completed_at: '2026-10-06T21:00:00Z', contracts: 3, collection_mode: 'automatic', event_d_brier: -0.04, events: 2, cumulative_d_brier_event: -0.01 },
  ],
  cohort: {
    events: 2,
    contracts: 5,
    first_completed_at: '2026-10-05T03:10:00Z',
    last_completed_at: '2026-10-06T21:00:00Z',
    first_prediction_as_of: '2026-10-04T23:00:00Z',
    last_prediction_as_of: '2026-10-06T16:00:00Z',
    by_collection: { manual_pilot: 1, automatic: 1 },
  },
  final: { cumulative_d_brier_event: -0.01, paired_d_brier_event_on_cohort: -0.01, equal: true },
  not_in_chart: {
    partially_settled: { events: 1, selected_contracts: 4 },
    pending: { events: 3, selected_contracts: 3 },
    nonbinary: { events: 0, selected_contracts: 0 },
    inconsistent: { events: 0, selected_contracts: 0 },
    undated_completed: { events: 1, selected_contracts: 2, d_brier_event: 0.005 },
  },
  results_table_basis: {
    note: 'All labelled contracts under these filters (MOCK FIXTURE).',
    contracts: 9,
    events: 4,
    d_brier_event: -0.004,
    d_brier: -0.006,
  },
  artifacts: { capture_records: 'mock', settlement_scores: 'mock', settlement_runs: 'mock', capture_runs: 'mock', raw_evidence: 'mock' },
}

const artifacts = {
  capture_records: 'mock',
  settlement_scores: 'mock',
  settlement_runs: 'mock',
  capture_runs: 'mock',
  raw_evidence: 'mock',
}

const settledEvent: SportsEventItem = {
  event_id: 'ncaaf|espn:mock-1',
  competition: 'ncaaf',
  competition_name: 'NCAA Football (mock)',
  sport: 'Football',
  source: 'espn_team',
  cluster: 'espn:mock-1',
  title: 'Mock State at Example Tech',
  title_source: MOCK_SOURCE,
  participants: ['Example Tech', 'Mock State'],
  kalshi_event_tickers: ['KXNCAAFGAME-MOCK1', 'KXNCAAFSPREAD-MOCK1'],
  event_start: '2026-10-10T17:00:00Z',
  window_open: '2026-10-10T15:40:00Z',
  scheduled_cutoff: '2026-10-10T16:00:00Z',
  capture_status: 'WINDOW_CAPTURE',
  prediction_as_of: '2026-10-10T15:45:31Z',
  finalized_at: '2026-10-10T15:45:32Z',
  horizon_minutes: 74.5,
  missed_reason: null,
  view: 'settled',
  settlement: { state: 'settled', contracts: 3, final: 3, labelled: 3, pending: 0, states: { SETTLED_YES: 2, SETTLED_NO: 1 } },
  families: ['game_winner', 'spread'],
  cohorts: ['strict'],
  contracts_total: 3,
  contracts_ok: 3,
  candidate_available: { frozen: 3, calibrated: 3, market: 2, blend: 1 },
  comparability: 'exact',
  integrity: { record_checksum: 'ok' },
}

const upcomingEvent: SportsEventItem = {
  ...settledEvent,
  event_id: 'epl|espn:mock-2',
  competition: 'epl',
  competition_name: 'Premier League (mock)',
  sport: 'Soccer',
  cluster: 'espn:mock-2',
  title: 'Mock United vs Fixture City',
  participants: ['Fixture City', 'Mock United'],
  kalshi_event_tickers: ['KXEPLGAME-MOCK2'],
  event_start: '2099-01-01T15:00:00Z',
  view: 'upcoming',
  settlement: { state: 'pending', contracts: 1, final: 0, labelled: 0, pending: 1, states: { NOT_YET_CHECKED: 1 } },
  families: ['game_winner'],
  candidate_available: { frozen: 1, calibrated: 0, market: 1, blend: 0 },
  contracts_total: 1,
  contracts_ok: 1,
}

const missedEvent: SportsEventItem = {
  ...settledEvent,
  event_id: 'nfl|espn:mock-3',
  competition: 'nfl',
  competition_name: 'NFL (mock)',
  cluster: 'espn:mock-3',
  title: 'Mock Missed Game',
  participants: [],
  capture_status: 'MISSED',
  prediction_as_of: null,
  finalized_at: null,
  horizon_minutes: null,
  missed_reason: 'checkpoint window expired without an accepted capture',
  view: 'missed',
  settlement: { state: 'not_applicable', contracts: 0, final: 0, labelled: 0, pending: 0, states: {} },
  families: [],
  cohorts: [],
  contracts_total: 0,
  contracts_ok: 0,
  candidate_available: { frozen: 0, calibrated: 0, market: 0, blend: 0 },
}

export const mockSportsEventItems: SportsEventItem[] = [upcomingEvent, settledEvent, missedEvent]

export function mockSportsEvents(filters: EventFilters = {}): SportsEventList {
  const q = (filters.q ?? '').toLowerCase()
  const items = mockSportsEventItems.filter(
    (e) =>
      (!filters.sport || e.sport === filters.sport) &&
      (!filters.competition || e.competition === filters.competition) &&
      (!filters.family || e.families.includes(filters.family)) &&
      (!filters.cohort || e.cohorts.includes(filters.cohort)) &&
      (!filters.status || filters.status === 'all' || e.view === filters.status ||
        (filters.status === 'captured' && e.capture_status === 'WINDOW_CAPTURE')) &&
      (!q || `${e.title} ${e.participants.join(' ')} ${e.event_id}`.toLowerCase().includes(q)),
  )
  return {
    generated_at: '2026-10-11T14:00:00Z',
    label: 'Saved captures (MOCK FIXTURE)',
    source: MOCK_SOURCE,
    artifacts,
    filters: {},
    total: items.length,
    page: 1,
    page_size: 24,
    pages: 1,
    views: { upcoming: 1, awaiting_settlement: 0, settled: 1, missed: 1 },
    facets: {
      sport: ['Football', 'Soccer'],
      competition: ['epl', 'ncaaf', 'nfl'],
      families: ['game_winner', 'spread'],
      cohorts: ['strict'],
    },
    facet_labels: {
      competition: { epl: 'Premier League (mock)', ncaaf: 'NCAA Football (mock)', nfl: 'NFL (mock)' },
    },
    sort: filters.sort || 'default',
    items,
  }
}

export const mockSportsSummary: SportsSummary = {
  generated_at: '2026-10-11T14:00:00Z',
  label: 'Saved captures (MOCK FIXTURE)',
  source: MOCK_SOURCE,
  mode: 'RESEARCH_ONLY',
  protocol: 'sports_phase2_capture_v2',
  scoring_protocol: 'sports_capture_v2_scoring_v1',
  artifacts,
  counts: {
    captured_events: 2,
    missed_events: 1,
    upcoming_events: 1,
    scored_events: 1,
    fully_settled_events: 1,
    pending_events: 1,
    captured_contracts: 4,
    labelled_contracts: 3,
  },
  candidate_counts: {
    frozen: { available: 4, unavailable: 0 },
    calibrated: { available: 3, unavailable: 1 },
    market: { available: 3, unavailable: 1 },
    blend: { available: 1, unavailable: 3 },
  },
  by_sport: { Football: { settled: 1, missed: 1 }, Soccer: { upcoming: 1 } },
  last_capture_at: '2026-10-10T15:45:31Z',
  last_capture_run: { run_id: 'mock-run', started_at: '2026-10-10T15:42:38Z', finished_at: '2026-10-10T15:45:55Z' },
  last_settlement_run: { run_id: 'mock-settle', finished_at: '2026-10-11T12:30:00Z', counts: { already_finalized: 3 } },
  integrity: { record_checksum_mismatches: 0, unreadable_records: 0, score_checksum_mismatches: 0 },
  collector: { recorded: false, note: 'MOCK FIXTURE — no collector state' },
}

export const mockSportsEventDetail: SportsEventDetail = {
  generated_at: '2026-10-11T14:00:00Z',
  label: 'Saved capture (MOCK FIXTURE)',
  source: MOCK_SOURCE,
  event: settledEvent,
  families: [
    {
      family: 'game_winner',
      contracts: [
        {
          ticker: 'KXNCAAFGAME-MOCK1-EXT',
          series: 'KXNCAAFGAME',
          kind: 'winner',
          segment: null,
          strike: null,
          side: 'home',
          cohort: 'strict',
          status: 'ok',
          excluded_reason: null,
          market_pick: true,
          description: 'Example Tech',
          market_title: 'Example Tech wins',
          rules: 'MOCK FIXTURE rules text.',
          candidates: {
            frozen: { available: true, p: 0.62, model: 'elo', brier: 0.1444, log_loss: 0.478 },
            calibrated: { available: true, p: 0.6, map: { form: 'identity' }, brier: 0.16, log_loss: 0.5108 },
            market: {
              available: true,
              p: 0.575,
              market_reference: {
                mid: 0.575,
                bid: 0.57,
                ask: 0.58,
                candle_end_utc: '2026-10-10T15:00:00Z',
                candle_age_s_at_prediction: 2731,
              },
              brier: 0.1806,
              log_loss: 0.5534,
            },
            blend: { available: true, p: 0.59, map: { w: 0.3 }, brier: 0.1681, log_loss: 0.5276 },
          },
          listing_diagnostics: {
            current_yes_bid: '0.5700',
            current_yes_ask: '0.5900',
            note: 'listing snapshot at capture; never a candidate or blend input',
          },
          settlement: { state: 'SETTLED_YES', detail: 'result yes, value 1', outcome: 1, settled_at: '2026-10-10T21:00:00Z' },
        },
      ],
    },
    {
      family: 'spread',
      contracts: [
        {
          ticker: 'KXNCAAFSPREAD-MOCK1-EXT7',
          series: 'KXNCAAFSPREAD',
          kind: 'spread',
          segment: null,
          strike: 7.5,
          side: 'home',
          cohort: 'strict',
          status: 'ok',
          excluded_reason: null,
          market_pick: false,
          description: 'Example Tech wins by over 7.5 points',
          market_title: 'Example Tech wins by over 7.5 points',
          rules: 'MOCK FIXTURE rules text.',
          candidates: {
            frozen: { available: true, p: 0.41, model: 'score', brier: 0.1681, log_loss: 0.5276 },
            calibrated: { available: true, p: 0.45, map: { form: 'intercept' }, brier: 0.2025, log_loss: 0.5978 },
            market: { available: false, reason: 'unavailable: no quote (candle older than 3 h)' },
            blend: { available: false, reason: 'market candidate unavailable' },
          },
          listing_diagnostics: null,
          settlement: { state: 'SETTLED_NO', detail: 'result no, value 0', outcome: 0 },
        },
        {
          ticker: 'KXNCAAFSPREAD-MOCK1-EXT3',
          series: 'KXNCAAFSPREAD',
          kind: 'spread',
          segment: null,
          strike: 3.5,
          side: 'home',
          cohort: 'strict',
          status: 'ok',
          excluded_reason: null,
          market_pick: false,
          description: 'Example Tech wins by over 3.5 points',
          market_title: 'Example Tech wins by over 3.5 points',
          rules: 'MOCK FIXTURE rules text.',
          candidates: {
            frozen: { available: true, p: 0.52, model: 'score', brier: 0.2304, log_loss: 0.6539 },
            calibrated: { available: true, p: 0.55, map: { form: 'intercept' }, brier: 0.2025, log_loss: 0.5978 },
            market: { available: true, p: 0.5, market_reference: { mid: 0.5, bid: 0.49, ask: 0.51, candle_end_utc: '2026-10-10T15:00:00Z', candle_age_s_at_prediction: 2731 }, brier: 0.25, log_loss: 0.6931 },
            blend: { available: false, reason: 'unavailable: matched training support below threshold' },
          },
          listing_diagnostics: null,
          settlement: { state: 'SETTLED_YES', detail: 'result yes, value 1', outcome: 1 },
        },
      ],
    },
  ],
  description_source: { source: MOCK_SOURCE, files: {} },
  notes: {
    binary:
      'Each contract is a separate binary question. Spread and total strikes overlap, so probabilities across strikes are not mutually exclusive buckets and do not sum to one.',
    market_reference:
      'The market reference is the bid/ask midpoint of the latest hourly Kalshi candle that ended at or before prediction_as_of. It is not an executable quote.',
    market_rule: { max_age_hours: 3, period_interval_min: 60 },
    candidates: { frozen: 'MOCK definition', calibrated: 'MOCK definition', market: 'MOCK definition', blend: 'MOCK definition' },
  },
  missed: null,
  provenance: {
    protocol: 'sports_phase2_capture_v2',
    schema: 'sports_phase2_capture_v2_record',
    run_id: 'mock-run',
    integrity: { record_checksum: 'ok', record_sha256: 'mock-sha', evidence: { status: 'mock', files: {} } },
    pins: { live_code_hash: 'mock' },
    dependencies: {},
    timing: { window_rule_satisfied: true },
    evidence_timing: { requests: 0 },
    model: { phase1_protocol: 'sports_phase1_v2' },
    settlement_runs: [],
  },
}

export const mockSportsResults: SportsResults = {
  generated_at: '2026-10-11T14:00:00Z',
  label: 'Prospective pilot (MOCK FIXTURE)',
  pilot_note: 'MOCK FIXTURE — descriptive pilot layout only.',
  sign_note:
    'Differences are candidate minus reference on the same contracts. A negative value means the candidate had lower error.',
  source: MOCK_SOURCE,
  weighting: 'event',
  weighting_note: 'each event counts equally',
  paired_note: 'Paired comparisons use only common contracts.',
  metrics_note: 'Brier and log loss (clip 1e-4).',
  filters: {},
  counts: {
    final_scores: 3,
    labelled_contracts: 3,
    labelled_events: 1,
    settlement_states: { SETTLED_YES: 2, SETTLED_NO: 1 },
    excluded_score_checksum_problems: 0,
  },
  facets: { sport: ['Football'], competition: ['ncaaf'], family: ['game_winner', 'spread'], cohort: ['strict'] },
  overall: {
    candidates: [
      { candidate: 'frozen', contracts: 3, events: 1, brier: 0.181, log_loss: 0.553 },
      { candidate: 'calibrated', contracts: 3, events: 1, brier: 0.188, log_loss: 0.569 },
      { candidate: 'market', contracts: 2, events: 1, brier: 0.215, log_loss: 0.623 },
      { candidate: 'blend', contracts: 1, events: 1, brier: 0.168, log_loss: 0.528 },
    ],
    comparisons: [
      { candidate: 'frozen', reference: 'market', matched_contracts: 2, unique_events: 1, d_brier: -0.028, d_log_loss: -0.057 },
      { candidate: 'calibrated', reference: 'frozen', matched_contracts: 3, unique_events: 1, d_brier: 0.007, d_log_loss: 0.016 },
      { candidate: 'blend', reference: 'frozen', matched_contracts: 1, unique_events: 1, d_brier: 0.024, d_log_loss: 0.05 },
      { candidate: 'blend', reference: 'market', matched_contracts: 1, unique_events: 1, d_brier: -0.012, d_log_loss: -0.026 },
    ],
  },
  breakdown: { sport: [], competition: [], family: [], cohort: [] },
  period: {
    first_prediction_as_of: '2026-10-10T15:46:00Z',
    last_prediction_as_of: '2026-10-10T15:46:00Z',
    first_settled_at: '2026-10-11T03:10:00Z',
    last_settled_at: '2026-10-11T03:10:00Z',
  },
  artifacts,
}

export const mockSportsCoverage: SportsCoverage = {
  generated_at: '2026-10-11T14:00:00Z',
  available: true,
  source: { series: MOCK_SOURCE },
  filters: {},
  inventory_series_total: 3,
  matrix_series_total: 3,
  inventory_matches_matrix: true,
  status_counts: { supported: 1, unsupported: 1, unattempted: 1 },
  facets: { sport: ['Football'], competition: ['ncaaf'], family: ['game_winner'], status: ['supported', 'unattempted', 'unsupported'] },
  reasons: { unsupported: [{ reason: 'no payoff parser (mock)', series: 1 }] },
  series: {
    total: 3,
    page: 1,
    page_size: 50,
    pages: 1,
    items: [
      { series_ticker: 'KXMOCKGAME', series_title: 'Mock Game', sport: 'Football', competition: 'ncaaf', competition_top: 'NCAAF', family: 'game_winner', source: 'espn_team', status: 'supported', reason: 'frozen elo model', comparability: 'exact', calibration_status: 'identity', blend_status: 'w 0.3', market_candidate: 'candle rule' },
      { series_ticker: 'KXMOCKSCORE', series_title: 'Mock Score', sport: 'Football', competition: 'ncaaf', competition_top: 'NCAAF', family: 'score_props', source: 'espn_team', status: 'unsupported', reason: 'no payoff parser (mock)', comparability: null, calibration_status: null, blend_status: null, market_candidate: null },
      { series_ticker: 'KXMOCKAWARD', series_title: 'Mock Award', sport: 'Football', competition: null, competition_top: 'NCAAF', family: 'awards', source: null, status: 'unattempted', reason: 'awards: voting outcomes without as-of inputs', comparability: null, calibration_status: null, blend_status: null, market_candidate: null },
    ],
  },
  competition_family: {
    total: 1,
    shown: 1,
    available_counts: { frozen: 1, calibrated: 1, market: 1, blend: 1 },
    items: [
      {
        competition: 'ncaaf',
        sport: 'Football',
        source: 'espn_team',
        family: 'game_winner',
        live_adapter: 'supported',
        comparability: 'exact',
        series_supported: 1,
        series_unsupported: 0,
        candidates: {
          frozen: { status: 'available', detail: 'frozen elo model' },
          calibrated: { status: 'available', detail: 'identity' },
          market: { status: 'available', detail: 'candle rule' },
          blend: { status: 'available', detail: 'w 0.3' },
        },
      },
    ],
  },
  observed_in_saved_captures: {
    frozen: { available: 4, unavailable: [] },
    calibrated: { available: 3, unavailable: [{ reason: 'unavailable: training support below threshold', contracts: 1 }] },
    market: { available: 3, unavailable: [{ reason: 'segment markets: no quotes by scope decision', contracts: 1 }] },
    blend: { available: 1, unavailable: [{ reason: 'unavailable: matched training support below threshold', contracts: 3 }] },
  },
}

export const mockSportsHistorical: SportsHistorical = {
  generated_at: '2026-10-11T14:00:00Z',
  label: 'Historical research (MOCK FIXTURE)',
  separation_note: 'MOCK FIXTURE — historical results are shown separately from the prospective pilot.',
  interpretation: 'MOCK FIXTURE interpretation.',
  filters: {},
  protocols: [
    {
      protocol: 'sports_phase1_v2',
      phase: 'Phase 1',
      comparison: 'frozen model vs naive baseline (retrospective correction)',
      status: 'current',
      report: 'sports_phase1_results_v2.md',
      addenda: [],
      source: { label: MOCK_SOURCE, tracked_csv: 'mock', local_artifacts: 'mock' },
      available: true,
      columns: ['level', 'key', 'cohort', 'family', 'test_contracts', 'd_brier_vs_naive', 'class_event_equal'],
      total: 1,
      page: 1,
      pages: 1,
      page_size: 100,
      rows: [
        { level: 'competition', key: 'mock', cohort: 'exploratory', family: 'game_winner', test_contracts: '10', d_brier_vs_naive: '-0.0050', class_event_equal: 'unclear' },
      ],
      facets: { level: ['competition'], family: ['game_winner'], cohort: ['exploratory'] },
    },
  ],
}

export const mockSportsCollector: SportsCollectorStatus = {
  generated_at: '2026-10-11T14:00:00Z',
  task_name: 'KalshiEdgeLab-SportsProspective',
  scheduler: {
    observable: false,
    configured: 'unknown',
    enabled: 'unknown',
    status: 'unknown',
    next_run_time: 'unknown',
    last_run_time: 'unknown',
    last_result: 'unknown',
    reason: 'MOCK FIXTURE',
  },
  weather_task: {
    observable: false,
    configured: 'unknown',
    enabled: 'unknown',
    status: 'unknown',
    next_run_time: 'unknown',
    last_run_time: 'unknown',
    last_result: 'unknown',
    reason: 'MOCK FIXTURE',
  },
  lock: { held: false, state: 'free' },
  cadence_minutes: 5,
  settlement_interval_minutes: 60,
  status_recorded: false,
  status: { note: 'MOCK FIXTURE — no collector cycle recorded' },
  missed_windows: { total: 1, last_24h: 0, recent: [] },
  freshness: {
    last_accepted_capture_at: '2026-10-10T15:45:32Z',
    minutes_since_last_capture: null,
    last_score_written_at: null,
    minutes_since_last_score: null,
    last_cycle_finished_at: null,
    minutes_since_last_cycle: null,
  },
  limits: 'MOCK FIXTURE — captures need the machine awake, logged in and online.',
}

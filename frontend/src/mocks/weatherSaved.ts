/**
 * MOCK FIXTURES — used only when VITE_USE_MOCK=true. Labeled as mock fixtures; none of this is a saved
 * snapshot, score or collector record.
 */
import type {
  SavedSnapshotHistoryRow,
  SavedWeatherEvent,
  SavedWeatherEventDetail,
  SavedWeatherEventList,
  SavedWeatherFilters,
  SnapshotRun,
  WeatherBucketDefinition,
  WeatherCollectorStatus,
  WeatherMetricsContext,
} from '../types/weather'

export const MOCK_WEATHER_SOURCE = 'MOCK FIXTURE — not a saved snapshot'

function snapshot(ticker: string, date: string, asOf: string) {
  return {
    snapshot_id: `${ticker}__${asOf.replace(/[-:]/g, '').slice(0, 15)}Z`,
    event_ticker: ticker,
    target_date: date,
    prediction_as_of: asOf,
    created_at: asOf,
    checkpoint_id: 'd0_1200',
    prediction_status: 'OK',
    confidence_label: 'MEDIUM',
    expected_high: 61.2,
    bucket_probabilities: { '60° to 61°': 0.42, '62° to 63°': 0.31 },
    invalid_probabilities: 0,
  }
}

const upcoming: SavedWeatherEvent = {
  category: 'weather',
  event_ticker: 'KXHIGHNY-99JAN01',
  target_date: '2099-01-01',
  view: 'upcoming',
  snapshot_count: 1,
  scored_snapshot_count: 0,
  first_prediction_as_of: '2098-12-31T23:05:00Z',
  displayed_snapshot: snapshot('KXHIGHNY-99JAN01', '2099-01-01', '2098-12-31T23:05:00Z'),
  displayed_snapshot_score: { state: 'not_scored', reason: 'no score file for this snapshot yet' },
  event_settlement: { state: 'not_confirmed', reason: 'no valid score confirms the winning bucket yet' },
  receipts: { captured: 1, missed: 0 },
}

const settled: SavedWeatherEvent = {
  ...upcoming,
  event_ticker: 'KXHIGHNY-26SEP26',
  target_date: '2026-09-26',
  view: 'settled',
  snapshot_count: 2,
  scored_snapshot_count: 1,
  first_prediction_as_of: '2026-09-25T22:05:00Z',
  displayed_snapshot: snapshot('KXHIGHNY-26SEP26', '2026-09-26', '2026-09-26T16:05:00Z'),
  displayed_snapshot_score: { state: 'not_scored', reason: 'no score file for this snapshot yet' },
  event_settlement: {
    state: 'confirmed',
    winning_bucket: '60° to 61°',
    actual_high_f: 61,
    source: 'kalshi_expiration_value',
    confirmed_at: '2026-09-27T12:44:26Z',
    confirmed_by_snapshot_id: 'KXHIGHNY-26SEP26__20260925T220500Z',
    confirming_scores: 1,
  },
}

export const mockSavedWeatherItems = [upcoming, settled]

export function mockSavedWeatherEvents(filters: SavedWeatherFilters = {}): SavedWeatherEventList {
  const q = (filters.q ?? '').toLowerCase()
  const items = mockSavedWeatherItems.filter(
    (e) => (!filters.status || e.view === filters.status) && (!q || e.event_ticker.toLowerCase().includes(q)),
  )
  return {
    generated_at: '2026-10-11T14:00:00Z',
    label: 'Saved weather forecasts (MOCK FIXTURE)',
    source: MOCK_WEATHER_SOURCE,
    artifacts: { snapshots: 'mock', scores: 'mock' },
    filters: {},
    sort: filters.sort ?? 'default',
    total: items.length,
    page: 1,
    page_size: 24,
    pages: 1,
    views: { upcoming: 1, awaiting_settlement: 0, settled: 1 },
    items,
  }
}

const B60 = '60° to 61°|60|61|KXHIGHNY-26SEP26-B60.5'
const B62 = '62° to 63°|62|63|KXHIGHNY-26SEP26-B62.5'

export const mockWeatherBuckets: WeatherBucketDefinition[] = [
  { id: B60, label: '60° to 61°', lower_f: 60, upper_f: 61, ticker: 'KXHIGHNY-26SEP26-B60.5', bounds_parsed: true, snapshots: 2, valid_points: 1, definition_variants: 1 },
  { id: B62, label: '62° to 63°', lower_f: 62, upper_f: 63, ticker: 'KXHIGHNY-26SEP26-B62.5', bounds_parsed: true, snapshots: 1, valid_points: 1, definition_variants: 1 },
]

const RUN: SnapshotRun = {
  run_id: 'day_06z',
  forecast_run: 'event-day 06Z',
  gfs_model_run_at: '2026-09-26T06:00:00Z',
  hrrr_model_run_at: '2026-09-26T09:00:00Z',
  gefs_member_count: 31,
  calibration_artifact_id: 'mock',
  schema_version: '5.0.0',
}

function historyRows(event: SavedWeatherEvent): SavedSnapshotHistoryRow[] {
  const last = { ...event.displayed_snapshot, score: event.displayed_snapshot_score }
  if (event.event_ticker !== 'KXHIGHNY-26SEP26') return [last]
  const first = snapshot(event.event_ticker, '2026-09-26', '2026-09-25T22:05:00Z')
  return [
    {
      ...first,
      checkpoint_id: 'dminus1_1800',
      score: { state: 'scored', brier: 0.21, log_loss: 0.6, winning_bucket: '60° to 61°', scored_at: '2026-09-27T12:44:26Z' },
      probabilities: { [B60]: { p: null, invalid: true } },
      run: { ...RUN, run_id: 'prev_18z', forecast_run: 'previous-day 18Z' },
      observation: { present: true, used_by_prediction: false, station_id: 'KNYC', high_so_far_f: null, latest_timestamp: null, latest_position: 'unknown', trajectory_points: { in_day: 0, before_day: 0, after_day: 0, unknown: 2 } },
    },
    {
      ...last,
      probabilities: { [B60]: { p: 0.42, invalid: false }, [B62]: { p: 0.31, invalid: false } },
      run: RUN,
      observation: { present: true, used_by_prediction: true, station_id: 'KNYC', high_so_far_f: 58.0, latest_timestamp: '2026-09-26T15:51:00Z', latest_position: 'in_day', trajectory_points: { in_day: 11, before_day: 1, after_day: 0, unknown: 0 } },
    },
  ]
}

export function mockSavedWeatherEvent(ticker: string): SavedWeatherEventDetail | null {
  const event = mockSavedWeatherItems.find((e) => e.event_ticker === ticker)
  if (!event) return null
  return {
    generated_at: '2026-10-11T14:00:00Z',
    label: 'Saved weather forecasts (MOCK FIXTURE)',
    source: MOCK_WEATHER_SOURCE,
    event,
    snapshots: historyRows(event),
    buckets: event.event_ticker === 'KXHIGHNY-26SEP26' ? mockWeatherBuckets : [],
    history_note: 'MOCK FIXTURE',
  }
}

export const mockWeatherCollector: WeatherCollectorStatus = {
  generated_at: '2026-10-11T14:00:00Z',
  task_name: 'KalshiEdgeLab-WeatherProspective',
  cadence_minutes: 60,
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
  lock: { held: false, state: 'free' },
  log_observed: false,
  cycles: {
    last_cycle_finished_at: null,
    last_cycle_exit_code: null,
    last_successful_cycle_at: null,
    minutes_since_last_successful_cycle: null,
    cycles_24h: 0,
    failures_24h: 0,
    consecutive_failures: 0,
  },
  forecast_freshness: {
    last_snapshot_prediction_as_of: '2026-09-26T16:05:00Z',
    minutes_since_last_snapshot: null,
    last_captured_checkpoint_at: null,
    last_score_written_at: null,
    minutes_since_last_score: null,
    missed_checkpoints_24h: 0,
  },
  limits: 'MOCK FIXTURE — captures need the machine awake, logged in and online.',
}

export const mockWeatherMetricsContext: WeatherMetricsContext = {
  generated_at: '2026-10-11T14:00:00Z',
  prospective_snapshot_scores: {
    kind: 'prospective',
    scored_snapshots: 1,
    events: 1,
    first_target_date: '2026-09-26',
    last_target_date: '2026-09-26',
  },
  operational_replay: { kind: 'historical', available: true, first_target_date: '2026-04-02', last_target_date: '2026-07-25', target_dates: 114 },
}

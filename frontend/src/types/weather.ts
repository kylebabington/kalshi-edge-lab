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
  warnings?: string[]
}

export type LiveWeatherResponse = {
  generated_at: string
  mode: string
  calibration_csv_loaded?: boolean
  events: WeatherEvent[]
}

export type ModelSummary = {
  model_summary_available: boolean
  generated_at: string
  mode: string
  methodology_constants: Record<string, number>
  calibration: Record<string, unknown>
  forecast_error_by_run?: Record<string, Record<string, number>>
  oos?: Record<string, unknown> | null
  reliability_bins?: Array<Record<string, unknown>> | null
  settlement_regime?: Record<string, unknown>
  settlement_transfer?: Record<string, unknown>
  direct_clinyc?: Record<string, unknown>
  research_status?: Record<string, string>
  notes?: string[]
}

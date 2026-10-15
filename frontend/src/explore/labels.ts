import type { ExploreCoverage, RetrievalFreshness } from '../types/explore'

/** Model support is independent of whether a saved forecast exists. */
export function modelSupportLabel(c: ExploreCoverage | undefined | null): string {
  if (!c) return 'Unknown'
  if (c.model_supported === 'weather') return 'Weather model supported'
  if (c.model_supported === 'sports') return 'Sports model supported'
  if (c.model_supported === 'none') return 'No model registered'
  return String(c.model_supported)
}

/**
 * Never say "No model available" for a supported series that simply lacks a capture.
 * Preserve lookup_pending while the sports index is still loading.
 */
export function savedForecastLabel(c: ExploreCoverage | undefined | null): string {
  if (!c) return 'Unknown'
  if (c.saved_forecast === 'available') return 'Saved forecast available'
  if (c.saved_forecast === 'lookup_pending') return 'Saved forecast lookup pending'
  if (c.model_supported === 'sports' || c.model_supported === 'weather') return 'No saved forecast'
  return 'Saved forecast unavailable'
}

export function coverageSummary(c: ExploreCoverage | undefined | null): string {
  if (!c) return 'Coverage unknown'
  if (c.model_supported === 'none') return 'No model registered'
  if (c.saved_forecast === 'available') return `${modelSupportLabel(c)} · saved forecast available`
  if (c.saved_forecast === 'lookup_pending') return `${modelSupportLabel(c)} · lookup pending`
  return `${modelSupportLabel(c)} · no saved forecast`
}

export function freshnessLabel(f: RetrievalFreshness | undefined | null): string {
  if (!f) return 'unknown'
  return f.state || 'unknown'
}

export function lifecycleLabel(lifecycle: string | { state?: string } | null | undefined): string {
  if (!lifecycle) return 'unknown'
  if (typeof lifecycle === 'object') return lifecycle.state || 'unknown'
  return lifecycle
}

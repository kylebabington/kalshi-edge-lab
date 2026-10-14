import type { SavedSnapshotHistoryRow, SavedWeatherEventDetail, WeatherBucketDefinition } from '../types/weather'

export function bucketOptionLabel(b: WeatherBucketDefinition): string {
  if (b.definition_variants <= 1) return b.label
  const bounds = b.bounds_parsed ? `${b.lower_f ?? '…'}–${b.upper_f ?? '…'}°F` : 'bounds not parsed'
  return `${b.label} (${bounds}, ${b.ticker ?? 'no saved ticker'})`
}

/** Bucket shown by default: the most likely bucket in the latest snapshot that has a valid probability. */
export function defaultBucket(data: SavedWeatherEventDetail): string | null {
  const buckets = data.buckets ?? []
  for (const row of [...data.snapshots].reverse()) {
    let best: [string, number] | null = null
    for (const b of buckets) {
      const v = row.probabilities?.[b.id]
      if (v && v.p != null && (best == null || v.p > best[1])) best = [b.id, v.p]
    }
    if (best) return best[0]
  }
  return buckets[0]?.id ?? null
}

export type HistoryPoint = { t: number; p: number; row: SavedSnapshotHistoryRow }

/** Snapshots with a valid saved probability for the bucket, in capture order. Nothing is filled in between. */
export function historyPoints(rows: SavedSnapshotHistoryRow[], bucketId: string | null): HistoryPoint[] {
  if (!bucketId) return []
  const out: HistoryPoint[] = []
  for (const row of rows) {
    const v = row.probabilities?.[bucketId]
    const t = row.prediction_as_of ? Date.parse(row.prediction_as_of) : NaN
    if (v && v.p != null && !v.invalid && Number.isFinite(t)) out.push({ t, p: v.p, row })
  }
  return out
}

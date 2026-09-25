import { Link } from 'react-router-dom'
import type { WeatherEvent } from '../types/weather'
import { EmptyState, ErrorState, LoadingState, StatusBadge } from '../components/StatusBadge'

type Props = {
  loading: boolean
  error: string | null
  events: WeatherEvent[]
}

export function MarketsPage({ loading, error, events }: Props) {
  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} />
  if (!events.length) {
    return <EmptyState title="No markets" detail="No open KXHIGHNY events currently." />
  }

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">Markets</h2>
      <div className="panel overflow-x-auto">
        <table className="w-full text-sm text-left">
          <thead className="text-[var(--text-muted)]">
            <tr>
              <th className="py-2 pr-3">Date</th>
              <th className="py-2 pr-3">Event</th>
              <th className="py-2 pr-3">Status</th>
              <th className="py-2 pr-3">Confidence</th>
              <th className="py-2">Expected</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.event_ticker} className="border-t border-[var(--border)]">
                <td className="py-2 pr-3 mono">{event.target_date}</td>
                <td className="py-2 pr-3">
                  <Link className="text-[var(--accent)] underline-offset-2 hover:underline" to={`/markets/${event.event_ticker}`}>
                    {event.event_ticker}
                  </Link>
                </td>
                <td className="py-2 pr-3">
                  <StatusBadge status={event.prediction.prediction_status} />
                </td>
                <td className="py-2 pr-3">
                  <StatusBadge status={event.prediction.confidence_label ?? 'UNKNOWN'} />
                </td>
                <td className="py-2 mono">
                  {event.prediction.expected_high == null
                    ? '—'
                    : `${event.prediction.expected_high.toFixed(1)}°F`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

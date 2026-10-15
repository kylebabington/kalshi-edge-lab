import type { NoteTarget } from './store'

export function targetHref(t: NoteTarget): string {
  if (t.type === 'experiment') return `/research/${encodeURIComponent(t.id)}`
  if (t.category === 'weather') return `/markets/${encodeURIComponent(t.key)}`
  if (t.category === 'sports') return `/sports/events/${encodeURIComponent(t.key)}`
  return `/explore/events/${encodeURIComponent(t.key)}`
}

export function targetLabel(t: NoteTarget): string {
  if (t.type === 'experiment') return `Experiment ${t.id}`
  const kind = t.category === 'weather' ? 'Weather' : t.category === 'sports' ? 'Sports' : 'Kalshi'
  return `${kind} event ${t.key}`
}

import type { NoteTarget } from './store'

export function targetHref(t: NoteTarget): string {
  if (t.type === 'experiment') return `/research/${encodeURIComponent(t.id)}`
  return t.category === 'weather' ? `/markets/${encodeURIComponent(t.key)}` : `/sports/events/${encodeURIComponent(t.key)}`
}

export function targetLabel(t: NoteTarget): string {
  return t.type === 'experiment' ? `Experiment ${t.id}` : `${t.category === 'weather' ? 'Weather' : 'Sports'} event ${t.key}`
}

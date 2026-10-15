import { sameEntry, validateEntry, type NotebookEntry } from './store'

export const EXPORT_FORMAT = 'kalshi-edge-lab.notebook'
export const MAX_IMPORT_BYTES = 5_000_000

export function exportNotebook(entries: readonly NotebookEntry[], now: Date = new Date()): string {
  return JSON.stringify({ format: EXPORT_FORMAT, version: 1, exported_at: now.toISOString(), entries }, null, 2)
}

export type ParsedImport =
  | { ok: false; error: string }
  | { ok: true; entries: NotebookEntry[]; invalid: string[] }

/** Validates an export file. Invalid or duplicate entries are listed, never silently kept. */
export function parseImport(text: string): ParsedImport {
  if (text.length > MAX_IMPORT_BYTES) return { ok: false, error: 'File is too large for a notebook export.' }
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return { ok: false, error: 'File is not valid JSON.' }
  }
  if (!data || typeof data !== 'object') return { ok: false, error: 'File is not a notebook export.' }
  const d = data as { format?: unknown; version?: unknown; entries?: unknown }
  if (d.format !== undefined && d.format !== EXPORT_FORMAT) return { ok: false, error: 'File is not a notebook export.' }
  if (d.version !== 1) return { ok: false, error: 'Unsupported notebook export version.' }
  if (!Array.isArray(d.entries)) return { ok: false, error: 'File has no entries list.' }
  const entries: NotebookEntry[] = []
  const invalid: string[] = []
  const seen = new Set<string>()
  d.entries.forEach((raw, i) => {
    const e = validateEntry(raw)
    if (typeof e === 'string') invalid.push(`entry ${i + 1}: ${e}`)
    else if (seen.has(e.id)) invalid.push(`entry ${i + 1}: duplicate id ${e.id}`)
    else {
      seen.add(e.id)
      entries.push(e)
    }
  })
  return { ok: true, entries, invalid }
}

export type ImportPreview = {
  added: NotebookEntry[]
  identical: NotebookEntry[]
  conflicts: { existing: NotebookEntry; incoming: NotebookEntry }[]
  invalid: string[]
}

export function previewImport(existing: readonly NotebookEntry[], parsed: { entries: NotebookEntry[]; invalid: string[] }): ImportPreview {
  const byId = new Map(existing.map((e) => [e.id, e]))
  const preview: ImportPreview = { added: [], identical: [], conflicts: [], invalid: parsed.invalid }
  for (const e of parsed.entries) {
    const cur = byId.get(e.id)
    if (!cur) preview.added.push(e)
    else if (sameEntry(cur, e)) preview.identical.push(e)
    else preview.conflicts.push({ existing: cur, incoming: e })
  }
  return preview
}

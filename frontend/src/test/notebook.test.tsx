import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { NotebookPanel } from '../notebook/NotebookPanel'
import { NotebookPage } from '../pages/NotebookPage'
import { NotebookStore, STORAGE_KEY, type NotebookEntry } from '../notebook/store'
import { exportNotebook } from '../notebook/transfer'

class MemoryStorage implements Storage {
  data = new Map<string, string>()
  failGet = false
  get length() {
    return this.data.size
  }
  clear() {
    this.data.clear()
  }
  getItem(k: string) {
    if (this.failGet) throw new DOMException('blocked', 'SecurityError')
    return this.data.get(k) ?? null
  }
  key(i: number) {
    return [...this.data.keys()][i] ?? null
  }
  removeItem(k: string) {
    this.data.delete(k)
  }
  setItem(k: string, v: string) {
    this.data.set(k, v)
  }
}

const AT = '2099-01-01T00:00:00.000Z'
const target = { type: 'experiment', id: 'sports.phase1_v1' } as const

function entry(id: string, over: Partial<NotebookEntry> = {}): NotebookEntry {
  return { id, kind: 'note', origin: 'personal', text: `text ${id}`, tags: [], target, created_at: AT, updated_at: AT, ...over }
}

function storeWith(entries: NotebookEntry[] = [], storage = new MemoryStorage()) {
  storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, entries }))
  return { store: new NotebookStore(() => storage), storage }
}

function jsonFile(text: string, name = 'notes.json') {
  return new File([text], name, { type: 'application/json' })
}

describe('NotebookPanel', () => {
  it('adds a tagged question and keeps AI commentary visibly separate', async () => {
    const user = userEvent.setup()
    const { store, storage } = storeWith()
    render(
      <MemoryRouter>
        <NotebookPanel target={target} store={store} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/not included in collector backups/)).toBeInTheDocument()
    expect(screen.getByText(/not research evidence/)).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Type'), 'question')
    await user.type(screen.getByLabelText('Text'), 'Is the NBA cohort too small?')
    await user.type(screen.getByLabelText('Tags (comma separated)'), 'nba, cohort')
    await user.click(screen.getByRole('button', { name: 'Add question' }))
    const item = screen.getByTestId('notebook-entry')
    expect(within(item).getByText('Question')).toBeInTheDocument()
    expect(within(item).getByText('Personal note')).toBeInTheDocument()
    expect(within(item).getByText('#nba')).toBeInTheDocument()
    expect(JSON.parse(storage.getItem(STORAGE_KEY)!).entries).toHaveLength(1)

    await user.selectOptions(screen.getByLabelText('Type'), 'note')
    await user.selectOptions(screen.getByLabelText('Origin'), 'ai_commentary')
    await user.type(screen.getByLabelText('Text'), 'Pasted model summary')
    await user.click(screen.getByRole('button', { name: 'Add note' }))
    expect(screen.getByText('AI commentary · not evidence')).toBeInTheDocument()
  })

  it('only lists notes for its own target', () => {
    const { store } = storeWith([entry('a'), entry('b', { target: { type: 'event', category: 'sports', key: 'E1' }, text: 'other target' })])
    render(
      <MemoryRouter>
        <NotebookPanel target={target} store={store} />
      </MemoryRouter>,
    )
    expect(screen.getByText('text a')).toBeInTheDocument()
    expect(screen.queryByText('other target')).toBeNull()
  })

  it('shows notes for all aliases without rewriting their targets', async () => {
    const user = userEvent.setup()
    const kalshiTarget = { type: 'event' as const, category: 'kalshi' as const, key: 'KXNFL-1' }
    const sportsTarget = { type: 'event' as const, category: 'sports' as const, key: 'nfl|cluster' }
    const { store } = storeWith([
      entry('early', { target: kalshiTarget, text: 'noted before mapping' }),
      entry('later', { target: sportsTarget, text: 'noted after mapping' }),
    ])
    render(
      <MemoryRouter>
        <NotebookPanel target={sportsTarget} aliases={[kalshiTarget]} store={store} />
      </MemoryRouter>,
    )
    expect(screen.getByText('noted before mapping')).toBeInTheDocument()
    expect(screen.getByText('noted after mapping')).toBeInTheDocument()
    expect(store.getSnapshot().entries.find((e) => e.id === 'early')!.target).toEqual(kalshiTarget)

    await user.type(screen.getByLabelText('Text'), 'new preferred note')
    await user.click(screen.getByRole('button', { name: 'Add note' }))
    const added = store.getSnapshot().entries.find((e) => e.text === 'new preferred note')
    expect(added?.target).toEqual(sportsTarget)
  })

  it('edits and deletes with confirmation', async () => {
    const user = userEvent.setup()
    const { store } = storeWith([entry('a')])
    render(
      <MemoryRouter>
        <NotebookPanel target={target} store={store} />
      </MemoryRouter>,
    )
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const box = screen.getByLabelText('Edit text')
    await user.clear(box)
    await user.type(box, 'revised')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByText('revised')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(store.getSnapshot().entries).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
    expect(store.getSnapshot().entries).toHaveLength(0)
  })

  it('warns when browser storage is blocked', () => {
    const storage = new MemoryStorage()
    storage.failGet = true
    const store = new NotebookStore(() => storage)
    render(
      <MemoryRouter>
        <NotebookPanel target={target} store={store} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/won't persist/)).toBeInTheDocument()
  })
})

describe('NotebookPage import', () => {
  it('previews a merge and keeps existing notes on conflict', async () => {
    const user = userEvent.setup()
    const { store } = storeWith([entry('a'), entry('b')])
    render(
      <MemoryRouter initialEntries={['/research/notebook']}>
        <NotebookPage store={store} />
      </MemoryRouter>,
    )
    const file = jsonFile(exportNotebook([entry('a'), entry('b', { text: 'from file' }), entry('c')]))
    await user.upload(screen.getByLabelText('Notebook file'), file)
    const preview = await screen.findByTestId('import-preview')
    expect(within(preview).getByText(/New entries:/)).toHaveTextContent('1')
    expect(within(preview).getByText(/Identical to existing:/)).toHaveTextContent('1')
    expect(within(preview).getByText(/Conflicts/, { selector: 'li' })).toHaveTextContent('1')

    await user.click(within(preview).getByRole('button', { name: /Merge 1 new entry/ }))
    const ids = store.getSnapshot().entries.map((e) => e.id)
    expect(ids).toEqual(['a', 'b', 'c'])
    expect(store.getSnapshot().entries.find((e) => e.id === 'b')!.text).toBe('text b')
    expect(screen.getByText(/Existing notes were kept unchanged/)).toBeInTheDocument()
  })

  it('replaces only after explicit confirmation', async () => {
    const user = userEvent.setup()
    const { store } = storeWith([entry('a')])
    render(
      <MemoryRouter>
        <NotebookPage store={store} />
      </MemoryRouter>,
    )
    await user.upload(screen.getByLabelText('Notebook file'), jsonFile(exportNotebook([entry('z')])))
    const preview = await screen.findByTestId('import-preview')
    const replace = within(preview).getByRole('button', { name: 'Replace notebook' })
    expect(replace).toBeDisabled()
    await user.click(replace)
    expect(store.getSnapshot().entries.map((e) => e.id)).toEqual(['a'])
    await user.click(within(preview).getByRole('checkbox'))
    await user.click(replace)
    expect(store.getSnapshot().entries.map((e) => e.id)).toEqual(['z'])
  })

  it('reports an invalid file without touching existing notes', async () => {
    const user = userEvent.setup()
    const { store } = storeWith([entry('a')])
    render(
      <MemoryRouter>
        <NotebookPage store={store} />
      </MemoryRouter>,
    )
    await user.upload(screen.getByLabelText('Notebook file'), jsonFile('{broken'))
    expect(await screen.findByRole('alert')).toHaveTextContent('not valid JSON')
    expect(screen.queryByTestId('import-preview')).toBeNull()
    expect(store.getSnapshot().entries.map((e) => e.id)).toEqual(['a'])
  })

  it('shows invalid entries in the preview', async () => {
    const user = userEvent.setup()
    const { store } = storeWith([])
    render(
      <MemoryRouter>
        <NotebookPage store={store} />
      </MemoryRouter>,
    )
    await user.upload(
      screen.getByLabelText('Notebook file'),
      jsonFile(JSON.stringify({ version: 1, entries: [entry('ok'), { id: 'bad', kind: 'note' }] })),
    )
    const preview = await screen.findByTestId('import-preview')
    expect(within(preview).getByText(/Invalid and skipped:/)).toHaveTextContent('1')
  })

  it('filters entries by URL parameters', () => {
    const { store } = storeWith([entry('a', { kind: 'question', text: 'a question' }), entry('b', { text: 'a note' })])
    render(
      <MemoryRouter initialEntries={['/research/notebook?kind=question']}>
        <NotebookPage store={store} />
      </MemoryRouter>,
    )
    expect(screen.getByText('a question')).toBeInTheDocument()
    expect(screen.queryByText('a note')).toBeNull()
  })
})

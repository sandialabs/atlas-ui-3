/**
 * Team Library panel lifecycle (issue #1169, fifth review):
 *
 * - an open editor keeps its unsaved title, description and text when its
 *   entry drops out of (or comes back into) the list -- a search, a scope
 *   change, a background reload or a further page;
 * - only the newest listing request updates the list: an older response that
 *   arrives late (or fails late) is ignored, pages never mix filters, and the
 *   loading indicator follows the request that is still current.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import PromptLibraryPanel from '../components/PromptLibraryPanel'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))

const entry = (id, title, extra = {}) => ({
  id, title, description: '', owner_email: 'me@x.com', is_owner: true, permission: 'owner',
  access_via: ['owner'], latest_version: { id: `${id}-v1`, version_number: 1 }, can_edit: true,
  can_manage: true, shared: false, sharing: { groups: [], users: [] }, has_unpublished_changes: false, ...extra,
})
const A = entry('p-a', 'Alpha reviewer')
const B = entry('p-b', 'Beta helper')
const C = entry('p-c', 'Gamma notes')

/** A promise the test resolves (or rejects) when it chooses. */
function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

let library

function makeLibrary(listPrompts) {
  return {
    entries: [A, B, C], loaded: true, error: null, complete: true,
    listPrompts,
    getPrompt: vi.fn(async id => ({ ...[A, B, C].find(p => p.id === id), draft_content: `${id} saved text`, draft_revision: 3 })),
    updatePrompt: vi.fn(async (id, fields, rev) => ({ ...[A, B, C].find(p => p.id === id), ...fields, draft_content: fields.content, draft_revision: rev + 1 })),
    publishPrompt: vi.fn(), listVersions: vi.fn(async () => ({ versions: [], total: 0 })), getVersion: vi.fn(),
    getSharing: vi.fn(), setSharing: vi.fn(), listShareableGroups: vi.fn(), forkPrompt: vi.fn(),
    archivePrompt: vi.fn(async () => true), setTestDraft: vi.fn(), createPrompt: vi.fn(),
  }
}

function useLibrary(listPrompts) {
  library = makeLibrary(listPrompts)
  useChat.mockImplementation(() => ({ promptLibrary: { ...library }, activePromptKey: null, makePromptActive: vi.fn() }))
}

const fields = () => ({
  title: screen.getByLabelText('Title'),
  description: screen.getByLabelText('Description'),
  content: screen.getByPlaceholderText(/System prompt text/),
})

async function openEditorAndType(title = 'Alpha reviewer') {
  const row = (await screen.findByText(title)).closest('li')
  fireEvent.click(within(row).getByRole('button', { name: `Edit ${title}` }))
  await screen.findByRole('region', { name: `Edit ${title}` })
  const f = fields()
  fireEvent.change(f.title, { target: { value: 'UNSAVED title' } })
  fireEvent.change(f.description, { target: { value: 'UNSAVED description' } })
  fireEvent.change(f.content, { target: { value: 'UNSAVED body' } })
}

function expectUnsavedIntact() {
  const f = fields()
  expect(f.title).toHaveValue('UNSAVED title')
  expect(f.description).toHaveValue('UNSAVED description')
  expect(f.content).toHaveValue('UNSAVED body')
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('an open editor survives list changes', () => {
  it('keeps unsaved fields when a search filters its entry out, and back in', async () => {
    useLibrary(vi.fn(async (scope, query) => {
      const all = [A, B, C]
      const hits = query ? all.filter(p => p.title.toLowerCase().includes(query.toLowerCase())) : all
      return { prompts: hits, total: hits.length }
    }))
    render(<PromptLibraryPanel />)
    await openEditorAndType()
    fireEvent.change(screen.getByPlaceholderText('Search title or description'), { target: { value: 'beta' } })
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('all', 'beta', 0))
    await sleep(50)
    expect(screen.getByText('Beta helper')).toBeInTheDocument()
    expectUnsavedIntact()
    expect(screen.getByText(/not in the current results/i)).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Search title or description'), { target: { value: '' } })
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('all', '', 0))
    await sleep(50)
    expectUnsavedIntact()
    expect(screen.queryByText(/not in the current results/i)).toBeNull()
  })

  it('keeps unsaved fields across a scope change', async () => {
    useLibrary(vi.fn(async scope => {
      const prompts = scope === 'shared' ? [B] : [A, B, C]
      return { prompts, total: prompts.length }
    }))
    render(<PromptLibraryPanel />)
    await openEditorAndType()
    fireEvent.click(screen.getByRole('button', { name: 'Shared with me' }))
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('shared', '', 0))
    await sleep(50)
    expectUnsavedIntact()
  })

  it('keeps unsaved fields across a background reload and a further page', async () => {
    let call = 0
    useLibrary(vi.fn(async (scope, query, offset) => {
      call += 1
      if (offset > 0) return { prompts: [C], total: 3 }
      // A background reload where the entry being edited moved off page one.
      return call === 1 ? { prompts: [A, B], total: 3 } : { prompts: [B], total: 3 }
    }))
    render(<PromptLibraryPanel />)
    await openEditorAndType()
    // Archiving another entry reloads the list in the background.
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const betaRow = screen.getByText('Beta helper').closest('li')
    fireEvent.click(within(betaRow).getByRole('button', { name: 'Archive Beta helper' }))
    await waitFor(() => expect(library.archivePrompt).toHaveBeenCalled())
    await waitFor(() => expect(library.listPrompts).toHaveBeenCalledTimes(2))
    await sleep(50)
    expectUnsavedIntact()
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('all', '', 1))
    await sleep(50)
    expectUnsavedIntact()
    expect(screen.getAllByText('Alpha reviewer')).toHaveLength(1)  // never listed twice
  })

  it('saves what was typed even after the entry left the results', async () => {
    useLibrary(vi.fn(async (scope, query) => (query ? { prompts: [], total: 0 } : { prompts: [A, B, C], total: 3 })))
    render(<PromptLibraryPanel />)
    await openEditorAndType()
    fireEvent.change(screen.getByPlaceholderText('Search title or description'), { target: { value: 'zzz' } })
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('all', 'zzz', 0))
    await sleep(50)
    expect(screen.queryByText('No matching prompts.')).toBeInTheDocument()
    expectUnsavedIntact()
    fireEvent.click(screen.getByRole('button', { name: /Save draft/ }))
    await waitFor(() => expect(library.updatePrompt).toHaveBeenCalledWith(
      'p-a', expect.objectContaining({ title: 'UNSAVED title', description: 'UNSAVED description', content: 'UNSAVED body' }), 3,
    ))
  })

  it('closes only when the user closes it', async () => {
    useLibrary(vi.fn(async (scope, query) => (query ? { prompts: [], total: 0 } : { prompts: [A], total: 1 })))
    render(<PromptLibraryPanel />)
    await openEditorAndType()
    fireEvent.change(screen.getByPlaceholderText('Search title or description'), { target: { value: 'q' } })
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('all', 'q', 0))
    await sleep(50)
    const region = screen.getByRole('region', { name: 'Edit Alpha reviewer' })
    fireEvent.keyDown(region, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Edit Alpha reviewer' })).toBeNull())
    expect(screen.queryByText('Alpha reviewer')).toBeNull()  // and it leaves the filtered list
  })
})

describe('only the newest listing request updates the list', () => {
  it('ignores an older search response that arrives after a newer one', async () => {
    const pending = {}
    useLibrary(vi.fn((scope, query) => {
      pending[query] = deferred()
      return pending[query].promise
    }))
    render(<PromptLibraryPanel />)
    await waitFor(() => expect(pending['']).toBeDefined())
    await act(async () => { pending[''].resolve({ prompts: [A, B, C], total: 3 }) })
    const search = screen.getByPlaceholderText('Search title or description')
    fireEvent.change(search, { target: { value: 'al' } })
    await waitFor(() => expect(pending.al).toBeDefined())
    fireEvent.change(search, { target: { value: 'gam' } })
    await waitFor(() => expect(pending.gam).toBeDefined())
    await act(async () => { pending.gam.resolve({ prompts: [C], total: 1 }) })
    await act(async () => { pending.al.resolve({ prompts: [A], total: 1 }) })  // late, stale
    expect(screen.getByText('Gamma notes')).toBeInTheDocument()
    expect(screen.queryByText('Alpha reviewer')).toBeNull()
    expect(library.listPrompts).toHaveBeenCalledTimes(3)  // '', 'al', 'gam' -- nothing extra
  })

  it('keeps the loading indicator while the current request is pending, and drops a stale failure', async () => {
    const pending = {}
    useLibrary(vi.fn((scope) => {
      pending[scope] = deferred()
      return pending[scope].promise
    }))
    render(<PromptLibraryPanel />)
    await waitFor(() => expect(pending.all).toBeDefined())
    fireEvent.click(screen.getByRole('button', { name: 'Mine' }))
    await waitFor(() => expect(pending.mine).toBeDefined())
    await act(async () => { pending.all.reject(new Error('stale failure')) })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText('Loading library...')).toBeInTheDocument()  // "mine" still loading
    await act(async () => { pending.mine.resolve({ prompts: [A], total: 1 }) })
    expect(screen.queryByText('Loading library...')).toBeNull()
    expect(screen.getByText('Alpha reviewer')).toBeInTheDocument()
  })

  it('shows a failure of the current request, and recovers on the next one', async () => {
    let fail = true
    useLibrary(vi.fn(async () => {
      if (fail) throw new Error('The prompt library is temporarily unavailable.')
      return { prompts: [A], total: 1 }
    }))
    render(<PromptLibraryPanel />)
    expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable')
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Mine' }))
    expect(await screen.findByText('Alpha reviewer')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('never appends a page that belongs to an earlier filter', async () => {
    const pending = []
    useLibrary(vi.fn((scope, query, offset) => {
      const d = deferred()
      pending.push({ scope, offset, d })
      return d.promise
    }))
    render(<PromptLibraryPanel />)
    await waitFor(() => expect(pending).toHaveLength(1))
    await act(async () => { pending[0].d.resolve({ prompts: [A], total: 3 }) })
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await waitFor(() => expect(pending).toHaveLength(2))
    fireEvent.click(screen.getByRole('button', { name: 'Team' }))
    await waitFor(() => expect(pending).toHaveLength(3))
    await act(async () => { pending[2].d.resolve({ prompts: [B], total: 1 }) })
    await act(async () => { pending[1].d.resolve({ prompts: [C], total: 3 }) })  // page 2 of "all", late
    expect(screen.getByText('Beta helper')).toBeInTheDocument()
    expect(screen.queryByText('Gamma notes')).toBeNull()
    expect(screen.queryByText('Alpha reviewer')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
  })
})

describe('"Load more" between a filter change and its answer (independent review)', () => {
  it('is not offered, and requests nothing, until the new filter has loaded', async () => {
    const pending = []
    useLibrary(vi.fn((scope, query, offset) => {
      const d = deferred()
      pending.push({ scope, query, offset, d })
      return d.promise
    }))
    render(<PromptLibraryPanel />)
    await waitFor(() => expect(pending).toHaveLength(1))
    await act(async () => { pending[0].d.resolve({ prompts: [A], total: 3 }) })
    fireEvent.change(screen.getByPlaceholderText('Search title or description'), { target: { value: 'be' } })
    // Inside the debounce window the list still belongs to the old filter.
    const more = screen.getByRole('button', { name: 'Load more' })
    expect(more).toBeDisabled()
    fireEvent.click(more)
    expect(pending).toHaveLength(1)
    await waitFor(() => expect(pending).toHaveLength(2))
    expect(pending[1]).toMatchObject({ query: 'be', offset: 0 })
    await act(async () => { pending[1].d.resolve({ prompts: [B], total: 2 }) })
    expect(screen.getByRole('button', { name: 'Load more' })).not.toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await waitFor(() => expect(pending).toHaveLength(3))
    expect(pending[2]).toMatchObject({ query: 'be', offset: 1 })
  })
})

describe('a just-created prompt that the reloaded list does not include yet', () => {
  it('stays open in the editor even though its detail has no listing fields', async () => {
    const listPrompts = vi.fn(async () => ({ prompts: [A], total: 1 }))
    useLibrary(listPrompts)
    // The create response is a detail view: no access_via, no sharing summary.
    library.createPrompt.mockResolvedValue({ id: 'p-new', title: 'Fresh', description: '', is_owner: true,
      permission: 'owner', draft_content: 'body', draft_revision: 1, latest_version: null })
    render(<PromptLibraryPanel />)
    await screen.findByText('Alpha reviewer')
    fireEvent.click(screen.getByRole('button', { name: /New library prompt/ }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Fresh' } })
    fireEvent.change(screen.getByPlaceholderText(/System prompt text/), { target: { value: 'body' } })
    fireEvent.click(screen.getByRole('button', { name: /Create private prompt/ }))
    await screen.findByRole('region', { name: 'Edit Fresh' })
    await waitFor(() => expect(listPrompts).toHaveBeenLastCalledWith('mine', '', 0))
    await sleep(50)
    expect(screen.getByRole('region', { name: 'Edit Fresh' })).toBeInTheDocument()
    expect(screen.getByText(/kept open while you work on it/)).toBeInTheDocument()
  })
})

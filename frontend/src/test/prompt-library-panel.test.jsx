/**
 * Team Library panel and picker (issue #1169): buttons follow the permission
 * the server reports, filters/search reach the API, create/publish/share/fork
 * flows call the library, and the chat picker lists published entries and
 * flags a selection that is no longer available.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import PromptLibraryPanel from '../components/PromptLibraryPanel'
import PromptSelector from '../components/PromptSelector'
import { useChat } from '../contexts/ChatContext'
import { libraryPromptKey } from '../hooks/chat/useSelections'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: () => ({ isComplianceAccessible: () => true }),
}))

const owned = {
  id: 'p-own', title: 'My reviewer', description: 'mine', owner_email: 'me@x.com', is_owner: true,
  permission: 'owner', access_via: ['owner'], latest_version: { id: 'v1', version_number: 1 },
  can_edit: true, can_manage: true, shared: true, sharing: { groups: ['eng'], users: [] },
  has_unpublished_changes: false,
}
const teamUse = {
  id: 'p-team', title: 'Team helper', description: '', owner_email: 'lead@x.com', is_owner: false,
  permission: 'use', access_via: ['eng'], latest_version: { id: 'v7', version_number: 7 },
  can_edit: false, can_manage: false, shared: true,
}

function makeLibrary(items = [owned, teamUse]) {
  return {
    entries: items,
    loaded: true,
    error: null,
    listPrompts: vi.fn(async () => items),
    getPrompt: vi.fn(async id => ({ ...items.find(p => p.id === id), draft_content: 'draft body' })),
    createPrompt: vi.fn(async fields => ({ ...owned, id: 'p-new', title: fields.title, latest_version: null, draft_content: fields.content })),
    updatePrompt: vi.fn(async (id, fields) => ({ ...owned, ...fields, draft_content: fields.content })),
    publishPrompt: vi.fn(async () => ({ id: 'v2', version_number: 2 })),
    listVersions: vi.fn(async () => [
      { id: 'v7', version_number: 7, content: 'seventh', change_description: 'tone', author_email: 'lead@x.com' },
    ]),
    getSharing: vi.fn(async () => ({ groups: ['eng'], users: [] })),
    setSharing: vi.fn(async () => ({ groups: ['eng', 'ops'], users: [] })),
    listShareableGroups: vi.fn(async () => ['eng', 'ops']),
    forkPrompt: vi.fn(async () => ({ ...owned, id: 'p-fork', title: 'Team helper (copy)', draft_content: 'seventh' })),
    archivePrompt: vi.fn(async () => true),
    setTestDraft: vi.fn(),
  }
}

let library
let makePromptActive

beforeEach(() => {
  vi.clearAllMocks()
  library = makeLibrary()
  makePromptActive = vi.fn()
  useChat.mockReturnValue({ promptLibrary: library, activePromptKey: null, makePromptActive })
})

const row = async title => (await screen.findByText(title)).closest('li')

describe('PromptLibraryPanel', () => {
  it('shows owner controls only where the server granted them', async () => {
    render(<PromptLibraryPanel />)
    const mine = await row('My reviewer')
    for (const label of ['Edit My reviewer', 'Share My reviewer', 'Archive My reviewer', 'Version history for My reviewer']) {
      expect(within(mine).getByLabelText(label)).toBeInTheDocument()
    }
    const team = await row('Team helper')
    expect(within(team).queryByLabelText('Edit Team helper')).toBeNull()
    expect(within(team).queryByLabelText('Share Team helper')).toBeNull()
    expect(within(team).queryByLabelText('Archive Team helper')).toBeNull()
    expect(within(team).getByLabelText('Fork Team helper')).toBeInTheDocument()
    expect(within(team).getByText(/Owner: lead@x.com/)).toBeInTheDocument()
    expect(within(team).getByText('v7')).toBeInTheDocument()
    expect(within(team).getByText(/your access: use/)).toBeInTheDocument()
  })

  it('passes filter and search to the API', async () => {
    render(<PromptLibraryPanel />)
    await row('My reviewer')
    fireEvent.click(screen.getByRole('button', { name: 'Shared with me' }))
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('shared', ''))
    fireEvent.change(screen.getByPlaceholderText('Search title or description'), { target: { value: 'tone' } })
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('shared', 'tone'))
    expect(screen.getByRole('button', { name: 'Shared with me' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('selects latest or a specific version for chat', async () => {
    render(<PromptLibraryPanel />)
    const team = await row('Team helper')
    fireEvent.click(within(team).getByRole('button', { name: 'Use' }))
    expect(makePromptActive).toHaveBeenCalledWith(libraryPromptKey('p-team'))
    fireEvent.click(within(team).getByLabelText('Version history for Team helper'))
    fireEvent.click(await screen.findByRole('button', { name: 'Use this version' }))
    expect(makePromptActive).toHaveBeenLastCalledWith(libraryPromptKey('p-team', 'v7'))
  })

  it('creates a private prompt, then publishes with a change description', async () => {
    render(<PromptLibraryPanel />)
    await row('My reviewer')
    fireEvent.click(screen.getByRole('button', { name: /New library prompt/ }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Fresh' } })
    fireEvent.change(screen.getByLabelText(/Draft system prompt/), { target: { value: 'be brief' } })
    fireEvent.click(screen.getByRole('button', { name: /Create private prompt/ }))
    await waitFor(() => expect(library.createPrompt).toHaveBeenCalledWith({ title: 'Fresh', description: '', content: 'be brief' }))
    fireEvent.change(await screen.findByLabelText(/Change description/), { target: { value: 'first cut' } })
    fireEvent.click(screen.getByRole('button', { name: /Publish version/ }))
    await waitFor(() => expect(library.publishPrompt).toHaveBeenCalledWith('p-new', 'first cut'))
  })

  it('previews and tests a draft without publishing', async () => {
    render(<PromptLibraryPanel />)
    fireEvent.click(within(await row('My reviewer')).getByLabelText('Edit My reviewer'))
    fireEvent.click(await screen.findByRole('button', { name: 'Preview' }))
    expect(screen.getByText('draft body')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Test in chat/ }))
    expect(library.setTestDraft).toHaveBeenCalledWith({ promptId: 'p-own', title: 'My reviewer', content: 'draft body' })
    expect(makePromptActive).toHaveBeenCalledWith('libdraft:p-own')
    expect(library.publishPrompt).not.toHaveBeenCalled()
  })

  it('shares with groups the user belongs to', async () => {
    render(<PromptLibraryPanel />)
    fireEvent.click(within(await row('My reviewer')).getByLabelText('Share My reviewer'))
    fireEvent.click(await screen.findByLabelText('ops'))
    fireEvent.change(screen.getByLabelText('Add a person by email'), { target: { value: 'Ed@X.com' } })
    fireEvent.click(screen.getByRole('button', { name: /Add/ }))
    fireEvent.change(screen.getByLabelText('Permission for ed@x.com'), { target: { value: 'edit' } })
    fireEvent.click(screen.getByRole('button', { name: /Save sharing/ }))
    await waitFor(() => expect(library.setSharing).toHaveBeenCalledWith('p-own', {
      groups: ['eng', 'ops'],
      users: [{ email: 'ed@x.com', permission: 'edit' }],
    }))
  })

  it('forks into a private copy and opens it for editing', async () => {
    render(<PromptLibraryPanel />)
    fireEvent.click(within(await row('Team helper')).getByLabelText('Fork Team helper'))
    await waitFor(() => expect(library.forkPrompt).toHaveBeenCalledWith('p-team', null))
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('mine', ''))
  })

  it('surfaces server errors', async () => {
    library.forkPrompt.mockRejectedValueOnce(new Error('Prompt not found'))
    render(<PromptLibraryPanel />)
    fireEvent.click(within(await row('Team helper')).getByLabelText('Fork Team helper'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Prompt not found')
  })
})

describe('PromptSelector team library section', () => {
  const base = {
    prompts: [], selectedPrompts: new Set(), makePromptActive: vi.fn(), clearActivePrompt: vi.fn(),
    removePrompts: vi.fn(), features: { custom_prompts: true }, userPrompts: [],
  }

  it('lists published entries and selects the latest version', () => {
    const make = vi.fn()
    useChat.mockReturnValue({ ...base, makePromptActive: make, activePromptKey: null, libraryPrompts: [owned, teamUse], promptLibrary: library })
    render(<PromptSelector />)
    fireEvent.click(screen.getByTitle(/Select custom prompts/))
    fireEvent.click(screen.getByText('Team helper'))
    expect(make).toHaveBeenCalledWith(libraryPromptKey('p-team'))
  })

  it('flags a selection that is no longer available', () => {
    useChat.mockReturnValue({ ...base, activePromptKey: libraryPromptKey('gone'), libraryPrompts: [owned], promptLibrary: library })
    render(<PromptSelector />)
    expect(screen.getByText('Unavailable team prompt')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle(/Select custom prompts/))
    expect(screen.getByRole('alert')).toHaveTextContent(/no longer available/)
  })

  it('names the pinned version', () => {
    useChat.mockReturnValue({ ...base, activePromptKey: libraryPromptKey('p-team', 'v7'), libraryPrompts: [teamUse], promptLibrary: library })
    render(<PromptSelector />)
    expect(screen.getByText('Team helper (v7)')).toBeInTheDocument()
  })
})

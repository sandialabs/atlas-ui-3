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
    complete: true,
    listPrompts: vi.fn(async () => ({ prompts: items, total: items.length })),
    getPrompt: vi.fn(async id => ({ ...items.find(p => p.id === id), draft_content: 'draft body', draft_revision: 4 })),
    createPrompt: vi.fn(async fields => ({ ...owned, id: 'p-new', title: fields.title, latest_version: null, draft_content: fields.content, draft_revision: 1 })),
    updatePrompt: vi.fn(async (id, fields, rev) => ({ ...owned, ...fields, draft_content: fields.content, draft_revision: rev + 1 })),
    publishPrompt: vi.fn(async () => ({ id: 'v2', version_number: 2 })),
    listVersions: vi.fn(async () => [
      { id: 'v7', version_number: 7, content: 'seventh', change_description: 'tone', author_email: 'lead@x.com' },
    ]),
    getSharing: vi.fn(async () => ({ revision: 3, groups: ['eng'], users: [] })),
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
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('shared', '', 0))
    fireEvent.change(screen.getByPlaceholderText('Search title or description'), { target: { value: 'tone' } })
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('shared', 'tone', 0))
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
    await waitFor(() => expect(library.publishPrompt).toHaveBeenCalledWith('p-new', 'first cut', 1))
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
      expected_revision: 3,
    }))
  })

  it('forks into a private copy and opens it for editing', async () => {
    render(<PromptLibraryPanel />)
    fireEvent.click(within(await row('Team helper')).getByLabelText('Fork Team helper'))
    await waitFor(() => expect(library.forkPrompt).toHaveBeenCalledWith('p-team', null))
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('mine', '', 0))
  })

  it('surfaces server errors', async () => {
    library.forkPrompt.mockRejectedValueOnce(new Error('Prompt not found'))
    render(<PromptLibraryPanel />)
    fireEvent.click(within(await row('Team helper')).getByLabelText('Fork Team helper'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Prompt not found')
  })
})

describe('optimistic concurrency and pagination in the panel', () => {
  it('saves with the loaded draft revision and publishes the revision it saved', async () => {
    render(<PromptLibraryPanel />)
    fireEvent.click(within(await row('My reviewer')).getByLabelText('Edit My reviewer'))
    fireEvent.change(await screen.findByLabelText(/Draft system prompt/), { target: { value: 'edited body' } })
    fireEvent.click(screen.getByRole('button', { name: /Publish version/ }))
    await waitFor(() => expect(library.publishPrompt).toHaveBeenCalled())
    expect(library.updatePrompt).toHaveBeenCalledWith('p-own', { title: 'My reviewer', description: 'mine', content: 'edited body' }, 4)
    expect(library.publishPrompt).toHaveBeenCalledWith('p-own', '', 5)
  })

  it('publishes an unchanged draft at the revision it was loaded with', async () => {
    render(<PromptLibraryPanel />)
    fireEvent.click(within(await row('My reviewer')).getByLabelText('Edit My reviewer'))
    fireEvent.click(await screen.findByRole('button', { name: /Publish version/ }))
    await waitFor(() => expect(library.publishPrompt).toHaveBeenCalledWith('p-own', '', 4))
    expect(library.updatePrompt).not.toHaveBeenCalled()
  })

  it('shows the conflict when someone else changed the draft', async () => {
    library.updatePrompt.mockRejectedValueOnce(Object.assign(
      new Error('The draft was changed by someone else. Reload it, review the changes, and try again.'), { status: 409 }))
    render(<PromptLibraryPanel />)
    fireEvent.click(within(await row('My reviewer')).getByLabelText('Edit My reviewer'))
    fireEvent.change(await screen.findByLabelText(/Draft system prompt/), { target: { value: 'mine' } })
    fireEvent.click(screen.getByRole('button', { name: /Save draft/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/changed by someone else/)
    expect(library.publishPrompt).not.toHaveBeenCalled()
  })

  it('loads further pages on request', async () => {
    library.listPrompts
      .mockResolvedValueOnce({ prompts: [owned], total: 2 })
      .mockResolvedValueOnce({ prompts: [teamUse], total: 2 })
    render(<PromptLibraryPanel />)
    await row('My reviewer')
    expect(screen.getByText('Showing 1 of 2')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await row('Team helper')
    expect(library.listPrompts).toHaveBeenLastCalledWith('all', '', 1)
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
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

  it('does not call an entry missing when the list is only a first page', () => {
    useChat.mockReturnValue({ ...base, activePromptKey: libraryPromptKey('far-away'), libraryPrompts: [owned],
      promptLibrary: { ...library, complete: false } })
    render(<PromptSelector />)
    fireEvent.click(screen.getByTitle(/Select custom prompts/))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('names the pinned version', () => {
    useChat.mockReturnValue({ ...base, activePromptKey: libraryPromptKey('p-team', 'v7'), libraryPrompts: [teamUse], promptLibrary: library })
    render(<PromptSelector />)
    expect(screen.getByText('Team helper (v7)')).toBeInTheDocument()
  })
})

describe('third review: search debounce and disclosure accessibility', () => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

  it('debounces search even though the library object changes every render', async () => {
    // The real hook returns a new object on every render; its functions are stable.
    useChat.mockImplementation(() => ({ promptLibrary: { ...library }, activePromptKey: null, makePromptActive }))
    render(<PromptLibraryPanel />)
    await row('My reviewer')
    await sleep(700)
    expect(library.listPrompts).toHaveBeenCalledTimes(1) // no re-fetch loop

    const search = screen.getByPlaceholderText('Search title or description')
    for (const value of ['t', 'to', 'ton', 'tone']) fireEvent.change(search, { target: { value } })
    await waitFor(() => expect(library.listPrompts).toHaveBeenLastCalledWith('all', 'tone', 0))
    await sleep(700)
    expect(library.listPrompts).toHaveBeenCalledTimes(2)
    expect(library.listPrompts.mock.calls.map(c => c[1])).toEqual(['', 'tone'])
  })

  it('Edit, History and Share are disclosure buttons that move focus in and back', async () => {
    render(<PromptLibraryPanel />)
    const mine = await row('My reviewer')
    const edit = within(mine).getByRole('button', { name: 'Edit My reviewer' })
    expect(edit).toHaveAttribute('aria-expanded', 'false')
    expect(edit).not.toHaveAttribute('aria-controls')

    fireEvent.click(edit)
    const region = await screen.findByRole('region', { name: 'Edit My reviewer' })
    expect(edit).toHaveAttribute('aria-expanded', 'true')
    expect(edit).toHaveAttribute('aria-controls', region.id)
    await waitFor(() => expect(region).toHaveFocus())

    fireEvent.keyDown(region, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Edit My reviewer' })).toBeNull())
    expect(edit).toHaveAttribute('aria-expanded', 'false')
    expect(edit).toHaveFocus()

    const history = within(mine).getByRole('button', { name: 'Version history for My reviewer' })
    fireEvent.click(history)
    expect(await screen.findByRole('region', { name: 'Version history for My reviewer' })).toBeInTheDocument()
    fireEvent.click(history) // the same button closes it
    await waitFor(() => expect(screen.queryByRole('region', { name: /Version history/ })).toBeNull())
    expect(history).toHaveFocus()

    const share = within(mine).getByRole('button', { name: 'Share My reviewer' })
    fireEvent.click(share)
    const sharing = await screen.findByRole('region', { name: 'Sharing for My reviewer' })
    expect(share).toHaveAttribute('aria-controls', sharing.id)
  })

  it('says when the server could not check every group', async () => {
    library.listPrompts.mockResolvedValue({ prompts: [owned], total: 1, incomplete: true })
    render(<PromptLibraryPanel />)
    expect(await screen.findByRole('status')).toHaveTextContent(/could not be checked/)
  })
})

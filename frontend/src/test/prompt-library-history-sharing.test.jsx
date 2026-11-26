/**
 * Team prompt library (issue #1169): listing completeness, a stable hook
 * interface, and the sharing / version-history panels not refetching (and so
 * not discarding edits) on unrelated renders. Version history is paged and
 * loads a version's text only when it is opened.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, renderHook, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { listingIsComplete, usePromptLibrary } from '../hooks/usePromptLibrary'
import PromptLibrarySharing from '../components/PromptLibrarySharing'
import PromptLibraryVersions from '../components/PromptLibraryVersions'

describe('listingIsComplete', () => {
  const list = [{ id: 'a' }, { id: 'b' }]

  it('is complete when the server returned everything', () => {
    expect(listingIsComplete({ prompts: list, total: 2 }, list)).toBe(true)
  })

  it('is never complete when the server says its checks were cut short', () => {
    expect(listingIsComplete({ prompts: list, total: 2, incomplete: true }, list)).toBe(false)
  })

  it('is incomplete when more entries exist than were returned', () => {
    expect(listingIsComplete({ prompts: list, total: 3 }, list)).toBe(false)
  })

  it('does not assume completeness when the total is missing', () => {
    expect(listingIsComplete({ prompts: list }, list)).toBe(false)
  })
})

describe('usePromptLibrary', () => {
  let fetchMock

  beforeEach(() => {
    fetchMock = vi.fn(async url => ({
      ok: true,
      json: async () => (String(url).startsWith('/api/prompts?')
        ? { prompts: [{ id: 'p1' }], total: 1, incomplete: true }
        : {}),
    }))
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('reports an incomplete listing as incomplete', async () => {
    const { result } = renderHook(() => usePromptLibrary(true))
    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(result.current.complete).toBe(false)
  })

  it('keeps one object and stable functions across unrelated renders', async () => {
    const { result, rerender } = renderHook(() => usePromptLibrary(true))
    await waitFor(() => expect(result.current.loaded).toBe(true))
    const first = result.current
    rerender()
    rerender()
    expect(result.current).toBe(first)
    expect(result.current.getSharing).toBe(first.getSharing)
    expect(result.current.listVersions).toBe(first.listVersions)
  })
})

function sharingLibrary() {
  return {
    getSharing: vi.fn(async () => ({ revision: 2, groups: ['eng'], users: [] })),
    listShareableGroups: vi.fn(async () => ({ groups: ['eng', 'ops'], incomplete: false })),
    setSharing: vi.fn(async () => ({})),
  }
}

describe('PromptLibrarySharing', () => {
  const prompt = { id: 'p1', is_owner: true }

  it('does not refetch -- or drop unsaved selections -- when the library object changes', async () => {
    const lib = sharingLibrary()
    const { rerender } = render(<PromptLibrarySharing prompt={prompt} library={lib} onClose={() => {}} />)
    const ops = await screen.findByLabelText('ops')
    fireEvent.click(ops) // unsaved edit
    expect(ops).toBeChecked()
    // The parent re-renders with a new library object (same functions), as it
    // does whenever the library's state changes.
    rerender(<PromptLibrarySharing prompt={prompt} library={{ ...lib }} onClose={() => {}} />)
    rerender(<PromptLibrarySharing prompt={{ ...prompt }} library={{ ...lib }} onClose={() => {}} />)
    await act(async () => {})
    expect(lib.getSharing).toHaveBeenCalledTimes(1)
    expect(lib.listShareableGroups).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('ops')).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: /Save sharing/ }))
    await waitFor(() => expect(lib.setSharing).toHaveBeenCalledWith('p1', {
      groups: ['eng', 'ops'], users: [], expected_revision: 2,
    }))
  })

  it('refetches when a different prompt is shown', async () => {
    const lib = sharingLibrary()
    const { rerender } = render(<PromptLibrarySharing prompt={prompt} library={lib} onClose={() => {}} />)
    await screen.findByLabelText('ops')
    rerender(<PromptLibrarySharing prompt={{ id: 'p2', is_owner: true }} library={lib} onClose={() => {}} />)
    await waitFor(() => expect(lib.getSharing).toHaveBeenLastCalledWith('p2'))
  })

  it('says when some groups could not be checked, and offers only confirmed ones', async () => {
    const lib = sharingLibrary()
    lib.listShareableGroups.mockResolvedValue({ groups: ['eng'], incomplete: true })
    render(<PromptLibrarySharing prompt={prompt} library={lib} onClose={() => {}} />)
    expect(await screen.findByRole('status')).toHaveTextContent(/could not be checked/)
    expect(screen.queryByLabelText('ops')).toBeNull()
  })

  it('still accepts the older bare-list response', async () => {
    const lib = sharingLibrary()
    lib.listShareableGroups.mockResolvedValue(['eng', 'ops'])
    render(<PromptLibrarySharing prompt={prompt} library={lib} onClose={() => {}} />)
    expect(await screen.findByLabelText('ops')).toBeInTheDocument()
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('PromptLibraryVersions', () => {
  const prompt = { id: 'p1', latest_version: { id: 'v3' } }
  const page = (from, count, total) => ({
    versions: Array.from({ length: count }, (_, i) => ({
      id: `v${from - i}`, version_number: from - i, author_email: 'a@x.com', change_description: `change ${from - i}`,
    })),
    total,
  })

  function versionsLibrary() {
    return {
      listVersions: vi.fn(async (id, offset) => (offset === 0 ? page(3, 2, 3) : page(1, 1, 3))),
      getVersion: vi.fn(async (id, vid) => ({ id: vid, content: `text of ${vid}` })),
    }
  }

  it('lists summaries, pages, and loads a version\'s text only when opened', async () => {
    const lib = versionsLibrary()
    render(<PromptLibraryVersions prompt={prompt} library={lib} onUseVersion={() => {}} onFork={() => {}} onClose={() => {}} />)
    expect(await screen.findByText('v3')).toBeInTheDocument()
    expect(screen.getByText('Showing 2 of 3')).toBeInTheDocument()
    expect(lib.getVersion).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('v3'))
    expect(await screen.findByText('text of v3')).toBeInTheDocument()
    expect(lib.getVersion).toHaveBeenCalledWith('p1', 'v3')
    fireEvent.click(screen.getByRole('button', { name: 'Load more versions' }))
    expect(await screen.findByText('v1')).toBeInTheDocument()
    expect(lib.listVersions).toHaveBeenLastCalledWith('p1', 2)
    expect(screen.queryByRole('button', { name: 'Load more versions' })).toBeNull()
  })

  it('does not refetch the history on unrelated renders', async () => {
    const lib = versionsLibrary()
    const props = { prompt, onUseVersion: () => {}, onFork: () => {}, onClose: () => {} }
    const { rerender } = render(<PromptLibraryVersions {...props} library={lib} />)
    await screen.findByText('v3')
    fireEvent.click(screen.getByText('v3'))
    await screen.findByText('text of v3')
    rerender(<PromptLibraryVersions {...props} library={{ ...lib }} activeVersionId="v2" />)
    rerender(<PromptLibraryVersions {...props} library={{ ...lib }} activeVersionId="v3" />)
    await act(async () => {})
    expect(lib.listVersions).toHaveBeenCalledTimes(1)
    expect(lib.getVersion).toHaveBeenCalledTimes(1)
    expect(screen.getByText('text of v3')).toBeInTheDocument()
  })
})

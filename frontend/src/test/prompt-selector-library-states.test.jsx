/**
 * The prompt picker's Team Library section (issue #1169, fifth review):
 * loading, a successful empty library, a failed request (with a working
 * retry) and a successful retry are four different states. A failed request
 * must not look like an empty library, and must not drop or flag a selection
 * that is still valid.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, renderHook, act, waitFor } from '@testing-library/react'
import PromptSelector from '../components/PromptSelector'
import { useChat } from '../contexts/ChatContext'
import { libraryPromptKey } from '../hooks/chat/useSelections'
import { usePromptLibrary } from '../hooks/usePromptLibrary'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: () => ({ isComplianceAccessible: () => true }),
}))

const entry = { id: 'p-team', title: 'Team helper', owner_email: 'lead@x.com', is_owner: false,
  latest_version: { id: 'v7', version_number: 7 } }

const base = {
  prompts: [], selectedPrompts: new Set(), makePromptActive: vi.fn(), clearActivePrompt: vi.fn(),
  removePrompts: vi.fn(), features: { custom_prompts: true }, userPrompts: [],
}

function show(library, entries, activePromptKey = null) {
  useChat.mockReturnValue({ ...base, activePromptKey, libraryPrompts: entries, promptLibrary: library })
  const view = render(<PromptSelector />)
  fireEvent.click(screen.getByTitle(/Select custom prompts/))
  return view
}

beforeEach(() => vi.clearAllMocks())

describe('picker library states', () => {
  it('says it is loading before the first answer', () => {
    show({ entries: [], loading: true, loaded: false, error: null, complete: true, refresh: vi.fn() }, [])
    expect(screen.getByRole('status')).toHaveTextContent(/Loading team prompts/)
    expect(screen.queryByText('No published team prompts available')).toBeNull()
  })

  it('says the library is empty only after a successful empty answer', () => {
    show({ entries: [], loading: false, loaded: true, error: null, complete: true, refresh: vi.fn() }, [])
    expect(screen.getByText('No published team prompts available')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('reports a failed request with a retry instead of an empty library', () => {
    const refresh = vi.fn()
    show({ entries: [], loading: false, loaded: false, error: 'Request failed (503)', complete: true, refresh }, [])
    expect(screen.getByRole('alert')).toHaveTextContent(/Team prompts could not be loaded/)
    expect(screen.queryByText('No published team prompts available')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry loading team prompts' }))
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('keeps a valid selection, and the entries it already had, through a failed refresh', () => {
    const refresh = vi.fn()
    show({ entries: [entry], loading: false, loaded: true, error: 'Request failed (503)', complete: true, refresh },
      [entry], libraryPromptKey('p-team'))
    expect(screen.getByRole('alert')).toHaveTextContent(/could not be loaded/)
    expect(screen.queryByText(/no longer available/)).toBeNull()
    expect(screen.getAllByText('Team helper').length).toBeGreaterThan(0)
    expect(base.makePromptActive).not.toHaveBeenCalled()
    expect(base.clearActivePrompt).not.toHaveBeenCalled()
  })

  it('does not call a selection unavailable while the first load is still failing', () => {
    show({ entries: [], loading: false, loaded: false, error: 'boom', complete: true, refresh: vi.fn() },
      [], libraryPromptKey('p-team'))
    expect(screen.queryByText(/no longer available/)).toBeNull()
    expect(screen.queryByText('Unavailable team prompt')).toBeNull()
  })
})

describe('usePromptLibrary refresh and retry', () => {
  const ok = body => ({ ok: true, json: async () => body })

  it('recovers on retry and clears the error', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({ detail: 'temporarily unavailable' }) })
      .mockResolvedValueOnce(ok({ prompts: [entry], total: 1 }))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => usePromptLibrary(true))
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.loaded).toBe(false)
    await act(async () => { await result.current.refresh() })
    expect(result.current.error).toBeNull()
    expect(result.current.loaded).toBe(true)
    expect(result.current.entries.map(e => e.id)).toEqual(['p-team'])
    vi.unstubAllGlobals()
  })

  it('keeps the last good entries when a later refresh fails', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(ok({ prompts: [entry], total: 1 }))
      .mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({ detail: 'down' }) })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => usePromptLibrary(true))
    await waitFor(() => expect(result.current.loaded).toBe(true))
    await act(async () => { await result.current.refresh() })
    expect(result.current.error).toBeTruthy()
    expect(result.current.entries.map(e => e.id)).toEqual(['p-team'])
    vi.unstubAllGlobals()
  })

  it('does not stay loading when the library is switched off mid-request (independent review)', async () => {
    const fetchMock = vi.fn(() => new Promise(() => {}))  // never answers
    vi.stubGlobal('fetch', fetchMock)
    const { result, rerender } = renderHook(({ on }) => usePromptLibrary(on), { initialProps: { on: true } })
    await waitFor(() => expect(result.current.loading).toBe(true))
    rerender({ on: false })
    await waitFor(() => expect(result.current.loading).toBe(false))
    vi.unstubAllGlobals()
  })

  it('ignores an older refresh that answers after a newer one', async () => {
    let releaseFirst
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise(res => { releaseFirst = () => res(ok({ prompts: [], total: 0 })) }))
      .mockResolvedValueOnce(ok({ prompts: [entry], total: 1 }))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => usePromptLibrary(true))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    await act(async () => { await result.current.refresh() })  // newer answer: [entry]
    expect(result.current.entries.map(e => e.id)).toEqual(['p-team'])
    await act(async () => { releaseFirst() })  // the mount-time request lands late with []
    expect(result.current.entries.map(e => e.id)).toEqual(['p-team'])
    expect(result.current.loading).toBe(false)
    vi.unstubAllGlobals()
  })
})

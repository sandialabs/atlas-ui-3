/**
 * The saved-conversations list can be walked back through (issue #1085): a
 * control at the bottom of the list fetches the next page of older
 * conversations and appends it, without replacing what is on screen.
 *
 * The history hook is the real one here -- the test drives the paging
 * through the API the sidebar actually calls.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'

const h = vi.hoisted(() => ({
  chat: null,
}))

vi.mock('../contexts/ChatContext', () => ({
  useChat: () => h.chat,
}))
vi.mock('../components/ui/toastContext', () => ({
  useToast: () => ({ error: vi.fn(), success: vi.fn(), info: vi.fn(), dismiss: vi.fn() }),
}))

import Sidebar from '../components/Sidebar'

const now = new Date().toISOString()
const conv = (n) => ({
  id: `c${n}`,
  title: `Chat ${n}`,
  preview: '',
  updated_at: now,
  message_count: 2,
})

// More conversations than one page holds, so there is an older page to walk
// back to.
const ALL = Array.from({ length: 60 }, (_, i) => conv(i + 1))

const jsonResponse = (body, ok = true) => ({
  ok,
  status: ok ? 200 : 500,
  json: async () => body,
})

// Lets a test fail or hold the next page request.
let nextPageBehaviour = 'ok'

const fetchMock = vi.fn(async (url) => {
  if (url.startsWith('/api/conversations?')) {
    const params = new URLSearchParams(url.split('?')[1])
    const offset = Number(params.get('offset') || 0)
    const limit = Number(params.get('limit') || 50)
    if (offset > 0) {
      if (nextPageBehaviour === 'fail') {
        return { ok: false, status: 500, json: async () => ({}) }
      }
      if (nextPageBehaviour === 'hang') {
        await new Promise(() => {})
      }
    }
    const page = ALL.slice(offset, offset + limit)
    return jsonResponse({
      conversations: page,
      total: ALL.length,
      has_more: offset + page.length < ALL.length,
    })
  }
  if (url === '/api/conversations/tags/list') return jsonResponse({ tags: [] })
  return { ok: false, status: 404, json: async () => ({}) }
})

function baseChat(overrides = {}) {
  return {
    features: { chat_history: true },
    activeConversationId: null,
    loadSavedConversation: vi.fn(),
    refreshJoinedConversation: vi.fn(),
    messages: [],
    saveMode: 'server',
    clearChat: vi.fn(),
    newChatCount: 0,
    runsByConversation: {},
    backgroundSaves: 0,
    runEndedConversationId: null,
    clearRunEndedConversation: vi.fn(),
    activeComplianceFilter: null,
    complianceEnabled: false,
    complianceLevels: [],
    user: 'tester@example.com',
    ...overrides,
  }
}

const listContainer = () => screen.getByRole('list', { name: 'Conversations' }).parentElement

describe('Sidebar load more (#1085)', () => {
  beforeEach(() => {
    localStorage.clear()
    h.chat = baseChat()
    fetchMock.mockClear()
    nextPageBehaviour = 'ok'
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('offers a load-more control while older conversations remain', async () => {
    render(<Sidebar />)

    await waitFor(() => expect(screen.getByText('Chat 1')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Load more' })).toBeTruthy()
  })

  it('appends the next page without replacing the conversations on screen', async () => {
    render(<Sidebar />)
    await waitFor(() => expect(screen.getByText('Chat 1')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))

    // The conversations already shown stay, and the older ones join them.
    await waitFor(() => expect(screen.getByText('Chat 60')).toBeTruthy())
    expect(screen.getByText('Chat 1')).toBeTruthy()
    expect(screen.getAllByRole('listitem')).toHaveLength(60)
  })

  it('says so when every conversation has been loaded', async () => {
    render(<Sidebar />)
    await waitFor(() => expect(screen.getByText('Chat 1')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))

    await waitFor(() => expect(screen.getByText('All conversations loaded')).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
  })

  it('asks the server for the rows after the ones on screen', async () => {
    render(<Sidebar />)
    await waitFor(() => expect(screen.getByText('Chat 1')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await waitFor(() => expect(screen.getByText('Chat 60')).toBeTruthy())

    const requested = fetchMock.mock.calls.map(call => String(call[0]))
    expect(requested).toContain('/api/conversations?limit=50&offset=0')
    expect(requested).toContain('/api/conversations?limit=50&offset=50')
  })

  it('leaves the scroll position alone when a page is appended', async () => {
    render(<Sidebar />)
    await waitFor(() => expect(screen.getByText('Chat 1')).toBeTruthy())

    const list = listContainer()
    list.scrollTop = 120

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await waitFor(() => expect(screen.getByText('Chat 60')).toBeTruthy())

    // The rows above the viewport keep their place, so the reader stays where
    // they were.
    expect(list.scrollTop).toBe(120)
  })

  it('keeps the open conversation open while older ones are loaded', async () => {
    h.chat = baseChat({ activeConversationId: 'c1' })
    render(<Sidebar />)
    await waitFor(() => expect(screen.getByText('Chat 1')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await waitFor(() => expect(screen.getByText('Chat 60')).toBeTruthy())

    expect(h.chat.loadSavedConversation).not.toHaveBeenCalled()
    expect(h.chat.clearChat).not.toHaveBeenCalled()
  })

  it('shows the fetch in progress and does not stack requests', async () => {
    nextPageBehaviour = 'hang'
    render(<Sidebar />)
    await waitFor(() => expect(screen.getByText('Chat 1')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))

    const button = await screen.findByRole('button', { name: /Loading older conversations/ })
    expect(button).toBeDisabled()
  })

  it('explains a failed page, keeps the list, and offers a retry', async () => {
    nextPageBehaviour = 'fail'
    render(<Sidebar />)
    await waitFor(() => expect(screen.getByText('Chat 1')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load older conversations.')
    // The conversations already on screen are untouched.
    expect(screen.getByText('Chat 1')).toBeTruthy()
    expect(screen.getAllByRole('listitem')).toHaveLength(50)

    nextPageBehaviour = 'ok'
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByText('Chat 60')).toBeTruthy())
  })
})

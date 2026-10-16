/**
 * Opening conversations from the history list out of order (issue #1014).
 *
 * A row click awaits the conversation fetch before the view switches. With
 * parallel runs the user hops between conversations, and a slow fetch for an
 * earlier click used to resolve after the later one and switch the view back
 * to a conversation the user had already left. Only the latest click -- or a
 * New Chat pressed while the fetch was out -- may decide what is on screen.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'

const h = vi.hoisted(() => ({
  chat: null,
  history: null,
}))

vi.mock('../contexts/ChatContext', () => ({
  useChat: () => h.chat,
}))
vi.mock('../hooks/useConversationHistory', () => ({
  useConversationHistory: () => h.history,
}))
vi.mock('../hooks/useLocalConversationHistory', () => ({
  useLocalConversationHistory: () => h.history,
}))
vi.mock('../components/ui/toastContext', () => ({
  useToast: () => ({ error: vi.fn(), success: vi.fn(), info: vi.fn(), dismiss: vi.fn() }),
}))

import Sidebar from '../components/Sidebar'

const CONVERSATIONS = [
  { id: 'conv-x', title: 'Conversation X', updated_at: new Date().toISOString(), message_count: 2 },
  { id: 'conv-y', title: 'Conversation Y', updated_at: new Date().toISOString(), message_count: 2 },
  { id: 'conv-start', title: 'Conversation On Screen', updated_at: new Date().toISOString(), message_count: 2 },
]

function deferred() {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}

describe('Sidebar conversation open race (#1014)', () => {
  let pending

  beforeEach(() => {
    pending = {}
    h.chat = {
      features: { chat_history: true },
      activeConversationId: 'conv-start',
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
    }
    h.history = {
      conversations: CONVERSATIONS,
      tags: [],
      activeTag: null,
      loading: false,
      searchQuery: '',
      fetchConversations: vi.fn(),
      fetchTags: vi.fn(),
      loadConversation: vi.fn((id) => {
        pending[id] = deferred()
        return pending[id].promise
      }),
    }
  })

  afterEach(cleanup)

  const full = (id) => ({ id, messages: [{ role: 'user', content: id }] })

  it('a slow fetch for an earlier click does not override the later click', async () => {
    render(<Sidebar />)
    fireEvent.click(screen.getByText('Conversation X'))
    fireEvent.click(screen.getByText('Conversation Y'))

    await act(async () => { pending['conv-y'].resolve(full('conv-y')) })
    await act(async () => { pending['conv-x'].resolve(full('conv-x')) })

    expect(h.chat.loadSavedConversation).toHaveBeenCalledTimes(1)
    expect(h.chat.loadSavedConversation.mock.calls[0][0].id).toBe('conv-y')
  })

  it('a fetch still out when New Chat is pressed does not reopen its conversation', async () => {
    const { rerender } = render(<Sidebar />)
    fireEvent.click(screen.getByText('Conversation X'))

    // New Chat clears the active conversation.
    h.chat = { ...h.chat, activeConversationId: null }
    rerender(<Sidebar />)

    await act(async () => { pending['conv-x'].resolve(full('conv-x')) })
    expect(h.chat.loadSavedConversation).not.toHaveBeenCalled()
  })

  it('New Chat pressed from a chat with no id yet still cancels a pending open', async () => {
    // Review follow-up: the active id is null before and after, so only the
    // New Chat counter changes.
    h.chat = { ...h.chat, activeConversationId: null }
    const { rerender } = render(<Sidebar />)
    fireEvent.click(screen.getByText('Conversation X'))

    h.chat = { ...h.chat, newChatCount: h.chat.newChatCount + 1 }
    rerender(<Sidebar />)

    await act(async () => { pending['conv-x'].resolve(full('conv-x')) })
    expect(h.chat.loadSavedConversation).not.toHaveBeenCalled()
  })

  it('clicking an optimistic (still saving) row does not cancel a pending open', async () => {
    // The unsaved chat on screen is listed as an optimistic "Saving..." row.
    h.chat = {
      ...h.chat,
      activeConversationId: null,
      messages: [{ role: 'user', content: 'unsaved prompt' }],
    }
    render(<Sidebar />)
    fireEvent.click(screen.getByText('Conversation X'))
    fireEvent.click(screen.getByText('unsaved prompt'))

    await act(async () => { pending['conv-x'].resolve(full('conv-x')) })
    expect(h.chat.loadSavedConversation).toHaveBeenCalledTimes(1)
    expect(h.chat.loadSavedConversation.mock.calls[0][0].id).toBe('conv-x')
  })

  it('clicking the conversation already on screen does not cancel a pending open', async () => {
    render(<Sidebar />)
    fireEvent.click(screen.getByText('Conversation X'))
    fireEvent.click(screen.getByText('Conversation On Screen'))

    await act(async () => { pending['conv-x'].resolve(full('conv-x')) })
    expect(h.chat.loadSavedConversation).toHaveBeenCalledTimes(1)
    expect(h.chat.loadSavedConversation.mock.calls[0][0].id).toBe('conv-x')
  })

  it('a single click still opens its conversation', async () => {
    render(<Sidebar />)
    fireEvent.click(screen.getByText('Conversation X'))
    await act(async () => { pending['conv-x'].resolve(full('conv-x')) })
    expect(h.chat.loadSavedConversation).toHaveBeenCalledTimes(1)
    expect(h.chat.loadSavedConversation.mock.calls[0][0].id).toBe('conv-x')
  })
})

/**
 * The history sidebar renders launched sub-conversations as a tree
 * (issue #1081): nested rows, a child count, expand/collapse, and an
 * aggregated run marker while a parent group is collapsed.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, fireEvent, cleanup, within, waitFor } from '@testing-library/react'

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

const now = new Date().toISOString()
const conv = (id, title, extra = {}) => ({
  id,
  title,
  preview: '',
  updated_at: now,
  message_count: 2,
  ...extra,
})

const CONVERSATIONS = [
  conv('parent', 'Parent task'),
  conv('child-a', 'Child A', { parent_conversation_id: 'parent' }),
  conv('grandchild', 'Grandchild', { parent_conversation_id: 'child-a' }),
  conv('child-b', 'Child B', { parent_conversation_id: 'parent' }),
  conv('independent', 'Independent chat'),
]

const full = (id) => ({ id, messages: [{ role: 'user', content: id }] })

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

function baseHistory(overrides = {}) {
  return {
    conversations: CONVERSATIONS,
    tags: [],
    activeTag: null,
    loading: false,
    searchQuery: '',
    fetchConversations: vi.fn(),
    fetchTags: vi.fn(),
    loadConversation: vi.fn(async (id) => full(id)),
    ...overrides,
  }
}

describe('Sidebar conversation tree (#1081)', () => {
  beforeEach(() => {
    localStorage.clear()
    h.chat = baseChat()
    h.history = baseHistory()
  })

  afterEach(cleanup)

  it('renders launched conversations nested under their parent', () => {
    render(<Sidebar />)

    const rows = screen.getAllByRole('listitem')
    expect(screen.getByText('Parent task')).toBeTruthy()
    expect(screen.getByText('Child A')).toBeTruthy()
    expect(screen.getByText('Grandchild')).toBeTruthy()
    expect(screen.getByText('Independent chat')).toBeTruthy()

    // The tree order is parent, its children (with grandchildren), then the
    // independent conversation.
    const order = rows.map(row => row.querySelector('.text-sm.text-gray-200')?.textContent)
    expect(order).toEqual(['Parent task', 'Child A', 'Grandchild', 'Child B', 'Independent chat'])

    // Indentation encodes depth: 0 for roots, 14px per level.
    expect(rows[0].style.paddingLeft).toBe('12px')
    expect(rows[1].style.paddingLeft).toBe('26px')
    expect(rows[2].style.paddingLeft).toBe('40px')
    expect(rows[4].style.paddingLeft).toBe('12px')
  })

  it('shows a child count on parent rows', () => {
    render(<Sidebar />)

    const parentRow = screen.getByText('Parent task').closest('[role="listitem"]')
    expect(within(parentRow).getByTestId('conversation-child-count')).toHaveTextContent('2')

    const childRow = screen.getByText('Child A').closest('[role="listitem"]')
    expect(within(childRow).getByTestId('conversation-child-count')).toHaveTextContent('1')

    const leafRow = screen.getByText('Independent chat').closest('[role="listitem"]')
    expect(within(leafRow).queryByTestId('conversation-child-count')).toBeNull()
  })

  it('collapses and expands a group without opening or stopping anything', () => {
    render(<Sidebar />)

    const parentRow = screen.getByText('Parent task').closest('[role="listitem"]')
    fireEvent.click(within(parentRow).getByLabelText('Collapse sub-conversations'))

    expect(screen.queryByText('Child A')).toBeNull()
    expect(screen.queryByText('Grandchild')).toBeNull()
    expect(screen.queryByText('Child B')).toBeNull()
    expect(screen.getByText('Independent chat')).toBeTruthy()
    const expandButton = within(parentRow).getByLabelText('Expand sub-conversations')
    expect(expandButton).toHaveAttribute('aria-expanded', 'false')
    // Collapsing is a view operation: nothing was opened or stopped.
    expect(h.history.loadConversation).not.toHaveBeenCalled()
    expect(h.chat.clearChat).not.toHaveBeenCalled()

    fireEvent.click(within(parentRow).getByLabelText('Expand sub-conversations'))
    expect(screen.getByText('Child A')).toBeTruthy()
    expect(screen.getByText('Grandchild')).toBeTruthy()
  })

  it('summarizes a collapsed subtree run state on the parent row', () => {
    h.chat = baseChat({
      runsByConversation: {
        'child-a': { run_id: 'r1', conversation_id: 'child-a', status: 'waiting_for_input' },
        'child-b': { run_id: 'r2', conversation_id: 'child-b', status: 'running' },
      },
    })
    render(<Sidebar />)

    const parentRow = screen.getByText('Parent task').closest('[role="listitem"]')
    fireEvent.click(within(parentRow).getByLabelText('Collapse sub-conversations'))

    // Needs-approval outranks running: the child that needs the user is the
    // one the collapsed marker must surface.
    expect(within(parentRow).getByText('Needs approval')).toBeTruthy()
  })

  it('opens a child conversation from the tree', async () => {
    render(<Sidebar />)

    fireEvent.click(screen.getByText('Child A'))

    await waitFor(() => {
      expect(h.history.loadConversation).toHaveBeenCalledWith('child-a', { complianceLevel: '' })
      expect(h.chat.loadSavedConversation).toHaveBeenCalledWith(full('child-a'))
    })
  })

  it('expands a collapsed group when the conversation inside it is open', async () => {
    localStorage.setItem(
      'chatui-collapsed-conversation-ids:tester@example.com',
      JSON.stringify(['parent'])
    )
    h.chat = baseChat({ activeConversationId: 'child-a' })
    render(<Sidebar />)

    await waitFor(() => {
      expect(screen.getByText('Child A')).toBeTruthy()
    })
    expect(screen.getByText('Grandchild')).toBeTruthy()
    const parentRow = screen.getByText('Parent task').closest('[role="listitem"]')
    expect(within(parentRow).getByLabelText('Collapse sub-conversations'))
      .toHaveAttribute('aria-expanded', 'true')
  })

  it('ignores a collapsed group while a search or tag filter is active', () => {
    localStorage.setItem(
      'chatui-collapsed-conversation-ids:tester@example.com',
      JSON.stringify(['parent'])
    )
    h.history = baseHistory({
      // The parent matches the query too, so it is in the result set and would
      // otherwise hide its matching child behind the persisted collapse.
      conversations: [
        conv('parent', 'Quarterly task'),
        conv('child-a', 'Quarterly child', { parent_conversation_id: 'parent' }),
      ],
      searchQuery: 'Quarterly',
    })
    render(<Sidebar />)

    expect(screen.getByText('Quarterly child')).toBeTruthy()
    const parentRow = screen.getByText('Quarterly task').closest('[role="listitem"]')
    expect(within(parentRow).getByLabelText('Collapse sub-conversations'))
      .toHaveAttribute('aria-expanded', 'true')
  })

  it('does not prune collapse state from a stale filtered list', () => {
    const key = 'chatui-collapsed-conversation-ids:tester@example.com'
    localStorage.setItem(key, JSON.stringify(['parent']))
    h.history = baseHistory({
      // Filter just cleared, but the unfiltered fetch has not landed yet: the
      // only row on screen is the previous search result.
      conversations: [conv('child-a', 'Child A', { parent_conversation_id: 'parent' })],
      loading: true,
    })
    render(<Sidebar />)

    // The effect must not treat the missing parent as a deleted conversation.
    expect(JSON.parse(localStorage.getItem(key))).toEqual(['parent'])
  })

  it('renders an orphaned child as a top-level conversation', () => {
    h.history = baseHistory({
      conversations: [
        conv('orphan', 'Orphaned child', { parent_conversation_id: 'deleted-parent' }),
        conv('normal', 'Normal chat'),
      ],
    })
    render(<Sidebar />)

    const orphanRow = screen.getByText('Orphaned child').closest('[role="listitem"]')
    expect(orphanRow.style.paddingLeft).toBe('12px')
    expect(within(orphanRow).queryByTestId('conversation-child-count')).toBeNull()
  })
})

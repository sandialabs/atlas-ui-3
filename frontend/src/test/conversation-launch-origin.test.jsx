/**
 * Conversations launched through the Agent Runs REST API carry a distinct
 * marker in the history sidebar (issue #1187), from their saved metadata
 * after a reload and from the live run before the first save. Ordinary
 * conversations are unchanged, and the sub-conversation tree markers
 * (issue #1081) keep working beside it.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup, within, renderHook, act } from '@testing-library/react'

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
import { useConversationRuns } from '../hooks/chat/useConversationRuns'
import { getDisplayConversations } from '../utils/getDisplayConversations'

const API = 'agent_runs_api'
const BADGE = 'conversation-launch-origin'
const now = new Date().toISOString()
const conv = (id, title, extra = {}) => ({
  id,
  title,
  preview: '',
  updated_at: now,
  message_count: 2,
  launch_origin: null,
  parent_conversation_id: null,
  ...extra,
})

function chat(overrides = {}) {
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

function history(conversations) {
  return {
    conversations,
    tags: [],
    activeTag: null,
    loading: false,
    searchQuery: '',
    fetchConversations: vi.fn(),
    fetchTags: vi.fn(),
    loadConversation: vi.fn(async (id) => ({ id, messages: [] })),
  }
}

const rowOf = (title) => screen.getByText(title).closest('[role="listitem"]')

describe('Sidebar launch-origin marker (#1187)', () => {
  beforeEach(() => {
    localStorage.clear()
    h.chat = chat()
  })

  afterEach(cleanup)

  it('marks a saved conversation the API launched, and only that one', () => {
    h.history = history([
      conv('api', 'Nightly report', { launch_origin: API }),
      conv('plain', 'Typed by the user'),
    ])
    render(<Sidebar />)

    const badge = within(rowOf('Nightly report')).getByTestId(BADGE)
    expect(badge).toHaveTextContent('API')
    expect(badge).toHaveAttribute('title', 'Launched by the Agent Runs REST API')
    expect(within(rowOf('Typed by the user')).queryByTestId(BADGE)).toBeNull()
    expect(screen.getAllByTestId(BADGE)).toHaveLength(1)
  })

  it('marks an in-flight API run that has no saved conversation yet', () => {
    h.history = history([conv('plain', 'Typed by the user')])
    h.chat = chat({
      runsByConversation: {
        'api-run': {
          run_id: 'r1', conversation_id: 'api-run', status: 'running',
          title: 'Summarize the build', launch_origin: API,
        },
        'user-run': {
          run_id: 'r2', conversation_id: 'user-run', status: 'running',
          title: 'Started in another tab', launch_origin: null,
        },
      },
    })
    render(<Sidebar />)

    const apiRow = rowOf('Summarize the build')
    expect(within(apiRow).getByTestId(BADGE)).toHaveTextContent('API')
    // The run state is still shown, separately from the origin.
    expect(within(apiRow).getByText('Running')).toBeTruthy()
    expect(within(rowOf('Started in another tab')).queryByTestId(BADGE)).toBeNull()
    expect(within(rowOf('Typed by the user')).queryByTestId(BADGE)).toBeNull()
  })

  it('ignores an origin it does not know', () => {
    h.history = history([conv('odd', 'Odd one', { launch_origin: 'something_else' })])
    render(<Sidebar />)

    expect(screen.queryByTestId(BADGE)).toBeNull()
  })

  it('keeps the sub-conversation tree markers beside the origin marker', () => {
    h.history = history([
      conv('api', 'API parent', { launch_origin: API }),
      conv('child', 'Launched child', { parent_conversation_id: 'api' }),
      conv('plain-parent', 'Plain parent'),
      conv('plain-child', 'Plain child', { parent_conversation_id: 'plain-parent' }),
    ])
    render(<Sidebar />)

    const apiParent = rowOf('API parent')
    expect(within(apiParent).getByTestId(BADGE)).toBeTruthy()
    expect(within(apiParent).getByTestId('conversation-child-count')).toHaveTextContent('1')
    expect(within(apiParent).getByLabelText('Collapse sub-conversations')).toBeTruthy()
    expect(apiParent.style.paddingLeft).toBe('12px')

    // A sub-conversation is nested and has no origin marker of its own.
    const child = rowOf('Launched child')
    expect(child.style.paddingLeft).toBe('26px')
    expect(within(child).queryByTestId(BADGE)).toBeNull()

    // A tree with no API conversation in it looks exactly as before.
    const plainParent = rowOf('Plain parent')
    expect(within(plainParent).queryByTestId(BADGE)).toBeNull()
    expect(within(plainParent).getByTestId('conversation-child-count')).toHaveTextContent('1')
    expect(rowOf('Plain child').style.paddingLeft).toBe('26px')
  })
})

describe('launch origin on in-flight rows (#1187)', () => {
  const base = {
    conversations: [],
    messages: [],
    activeConversationId: null,
    chatHistoryEnabled: true,
    saveMode: 'server',
  }

  it('copies the origin from the live run onto its placeholder row', () => {
    const rows = getDisplayConversations({
      ...base,
      runsByConversation: {
        api: { run_id: 'r1', conversation_id: 'api', status: 'running', launch_origin: API },
        user: { run_id: 'r2', conversation_id: 'user', status: 'running' },
      },
    })
    expect(rows.find(r => r.id === 'api').launch_origin).toBe(API)
    expect(rows.find(r => r.id === 'user').launch_origin).toBeNull()
  })

  it('leaves a saved row to its own stored origin', () => {
    const rows = getDisplayConversations({
      ...base,
      conversations: [conv('saved', 'Saved', { launch_origin: API })],
      runsByConversation: {
        saved: { run_id: 'r1', conversation_id: 'saved', status: 'running' },
      },
    })
    expect(rows).toHaveLength(1)
    expect(rows[0].launch_origin).toBe(API)
  })
})

describe('useConversationRuns launch origin (#1187)', () => {
  it('takes the origin from a snapshot and keeps it across status frames', () => {
    const { result } = renderHook(() => useConversationRuns())

    act(() => result.current.handleRunFrame({
      type: 'runs_snapshot',
      runs: [
        { run_id: 'r1', conversation_id: 'api', status: 'running', created_at: 1, launch_origin: API },
        { run_id: 'r2', conversation_id: 'user', status: 'running', created_at: 2, launch_origin: null },
      ],
    }))
    expect(result.current.runsByConversation.api.launch_origin).toBe(API)
    expect(result.current.runsByConversation.user.launch_origin).toBeNull()

    // A status frame that does not repeat the origin must not erase it.
    act(() => result.current.handleRunFrame({
      type: 'run_status',
      run: { run_id: 'r1', conversation_id: 'api', status: 'waiting_for_input' },
    }))
    expect(result.current.runsByConversation.api).toMatchObject({
      status: 'waiting_for_input',
      launch_origin: API,
    })

    act(() => result.current.handleRunFrame({
      type: 'run_status',
      run: { run_id: 'r2', conversation_id: 'user', status: 'completed' },
    }))
    expect(result.current.runsByConversation.user.launch_origin).toBeNull()
  })
})

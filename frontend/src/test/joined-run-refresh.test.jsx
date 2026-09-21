/**
 * Refreshing a joined conversation when its background run ends (issue #959).
 *
 * A conversation opened while its run was executing is reloaded from the
 * store when the run reaches a terminal state (PR #956). That reload
 * replaced the whole transcript, which reset the scroll position of a user
 * who had scrolled up to read and collapsed the tool rows they had expanded.
 * The fix appends only what the view is missing and leaves the existing rows
 * untouched; when the stored transcript has diverged from the view the full
 * reload runs as before.
 *
 * Renders the real ChatProvider and drives live rows in through the real
 * websocket handler, so the rows the refresh must match are the ones the app
 * actually creates -- not hand-built stand-ins.
 *
 * Guarantees pinned here:
 *   - Rows the view already shows are kept as the same objects, so React
 *     preserves their state (expanded tool rows) and the scroll anchor stands.
 *   - The run's output that only reached the store (this view was not on
 *     screen when it streamed) is appended, with the tail marked
 *     `_transcriptRefresh` so ChatArea does not force the scroll to the
 *     bottom over a reader who is scrolled up.
 *   - Tool rows align by tool_call_id across the persisted/live shape drift
 *     (persisted: role 'tool', elided arguments; live: role 'system', raw).
 *   - Live-only rows (agent status lines, agent-loop answers) are skipped
 *     during alignment instead of breaking the match and forcing a reload.
 *   - A diverged transcript refuses the refresh (returns false) and sends
 *     nothing; the caller's fallback full reload owns it.
 *   - The backend session is re-seeded with restore_conversation either way:
 *     it was seeded from the snapshot at open time and lacks the run's final
 *     turn, so the next message here would lose that context.
 */

import { describe, it, expect, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'

// Shared mock handles (hoisted so the vi.mock factories can close over them).
const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  toastInfo: vi.fn((() => { let n = 0; return () => ++n })()),
  toastDismiss: vi.fn(),
  applyWorkspace: vi.fn(),
  snapshotSelections: vi.fn(() => ({})),
  wsState: { workspaces: [], loaded: true, error: null },
  activeWorkspaceId: null,
  setActiveWorkspaceId: vi.fn(),
  configReady: true,
  workspacesEnabled: true,
  saveLocalConv: vi.fn(() => Promise.resolve()),
  saveMode: 'none',
  // Every websocket frame handler ChatContext registered; tests dispatch
  // frames through them the way the socket would.
  handlers: new Set(),
}))

vi.mock('../contexts/WSContext', () => ({
  useWS: () => ({
    sendMessage: h.sendMessage,
    isConnected: true,
    addMessageHandler: (fn) => {
      h.handlers.add(fn)
      return () => h.handlers.delete(fn)
    },
  }),
}))

vi.mock('../components/ui/toastContext', () => ({
  useToast: () => ({
    error: h.toastError,
    success: h.toastSuccess,
    info: h.toastInfo,
    dismiss: h.toastDismiss,
  }),
}))

vi.mock('../hooks/chat/useChatConfig', () => ({
  useChatConfig: () => ({
    currentModel: 'test-model',
    user: 'tester@example.com',
    ragServers: [],
    tools: [{ server: 'files', tools: ['read', 'write'] }],
    configReady: h.configReady,
    features: { workspaces: h.workspacesEnabled },
    prompts: [],
    appName: 'Atlas',
    isInAdminGroup: false,
    fileExtraction: {},
    setIsCanvasOpen: vi.fn(),
  }),
}))

vi.mock('../hooks/chat/useSelections', async (importActual) => {
  const actual = await importActual()
  return {
    ...actual,
    useSelections: () => ({
      selectedTools: new Set(),
      selectedPrompts: new Set(),
      activePrompts: [],
      activePromptKey: null,
      clearActivePrompt: vi.fn(),
      selectedDataSources: new Set(),
      ragEnabled: false,
      toggleRagEnabled: vi.fn(),
      complianceLevelFilter: '',
      addTools: vi.fn(),
      removeTools: vi.fn(),
      addPrompts: vi.fn(),
      removePrompts: vi.fn(),
      addDataSources: vi.fn(),
      clearDataSources: vi.fn(),
      toggleTool: vi.fn(),
      togglePrompt: vi.fn(),
      toggleDataSource: vi.fn(),
      makePromptActive: vi.fn(),
      setSinglePrompt: vi.fn(),
      clearToolsAndPrompts: vi.fn(),
      setComplianceLevelFilter: vi.fn(),
      setRagEnabled: vi.fn(),
      applyWorkspace: h.applyWorkspace,
      snapshotSelections: h.snapshotSelections,
    }),
  }
})

vi.mock('../hooks/useUserPrompts', () => ({ useUserPrompts: () => ({ prompts: [] }) }))

vi.mock('../hooks/chat/useAgentMode', () => ({
  useAgentMode: () => ({
    agentModeEnabled: false,
    agentMaxSteps: 10,
    setCurrentAgentStep: vi.fn(),
    setAgentPendingQuestion: vi.fn(),
    agentPendingQuestion: null,
  }),
}))

vi.mock('../hooks/chat/useFiles', () => ({
  useFiles: () => ({
    getTaggedFilesContent: () => ({}),
    setCanvasContent: vi.fn(),
    setCanvasFiles: vi.fn(),
    setCurrentCanvasFileIndex: vi.fn(),
    setCustomUIContent: vi.fn(),
    setSessionFiles: vi.fn(),
    getFileType: vi.fn(),
    canvasContent: null,
    sessionFiles: { files: [], total_files: 0, categories: {} },
  }),
}))

vi.mock('../utils/localConversationDB', () => ({
  saveConversation: (...args) => h.saveLocalConv(...args),
  getConversation: vi.fn(),
  listConversations: vi.fn(() => Promise.resolve([])),
  deleteConversation: vi.fn(),
}))

vi.mock('../hooks/useSettings', () => ({
  useSettings: () => ({ settings: {}, updateSettings: vi.fn() }),
}))

vi.mock('../hooks/chat/usePersistentState', () => ({
  usePersistentState: (key, initial) => {
    if (key === 'chatui-active-workspace') return [h.activeWorkspaceId, h.setActiveWorkspaceId]
    if (key === 'chatui-save-mode') return [h.saveMode, vi.fn()]
    return [initial, vi.fn()]
  },
}))

vi.mock('../hooks/useWorkspaces', () => ({
  useWorkspaces: () => ({
    workspaces: h.wsState.workspaces,
    loading: false,
    loaded: h.wsState.loaded,
    error: h.wsState.error,
    fetchWorkspaces: vi.fn(),
    createWorkspace: vi.fn(),
    updateWorkspace: vi.fn(),
    deleteWorkspace: vi.fn(),
  }),
  isStaleWorkspacePointer: () => false,
}))

import { ChatProvider, useChat } from '../contexts/ChatContext'

const wrapper = ({ children }) => <ChatProvider>{children}</ChatProvider>
const renderChat = () => renderHook(() => useChat(), { wrapper })

// Deliver a frame the way the socket would: through every handler the
// context registered. Frames for the conversation on screen apply to the
// view; the routing in the handler does the rest.
const dispatchFrame = (frame) => {
  act(() => {
    for (const fn of h.handlers) fn(frame)
  })
}

const restoreCalls = () =>
  h.sendMessage.mock.calls.map(c => c[0]).filter(m => m.type === 'restore_conversation')

// A stored row in the shape the repository returns: message_type + metadata.
const storedChat = (role, content) => ({
  role,
  content,
  timestamp: '2026-01-01T00:00:00Z',
  message_type: 'chat',
})
const storedToolCall = (toolCallId, toolName) => ({
  role: 'tool',
  content: `Tool call: ${toolCallId}`,
  timestamp: '2026-01-01T00:00:01Z',
  message_type: 'tool_call',
  metadata: {
    tool_call_id: toolCallId,
    tool_name: toolName,
    server_name: 'basic_fns',
    arguments: { seconds: 5 },
    result: 'slept',
    status: 'completed',
  },
})
// The live row a tagged tool event creates in the view for the same call.
const dispatchToolStart = (toolCallId, toolName) => dispatchFrame({
  type: 'tool_start',
  conversation_id: 'conv-1',
  run_id: 'r1',
  tool_call_id: toolCallId,
  tool_name: toolName,
  server_name: 'basic_fns',
  arguments: { seconds: 5 },
})
const dispatchToolComplete = (toolCallId, toolName) => dispatchFrame({
  type: 'tool_complete',
  conversation_id: 'conv-1',
  run_id: 'r1',
  tool_call_id: toolCallId,
  tool_name: toolName,
  success: true,
  result: 'slept',
})

const loadConversation = async (result, loaded) => {
  await act(async () => {
    await result.current.loadSavedConversation(loaded)
  })
}

describe('refreshJoinedConversation (issue #959)', () => {
  it('appends only the rows the view is missing and marks the tail', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'What is the weather'),
        storedChat('assistant', 'Clear skies'),
      ],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)
    const before = result.current.messages

    // The store gained the run's turn after the view was opened.
    const store = [
      ...loaded.messages,
      storedChat('user', 'And tomorrow?'),
      storedChat('assistant', 'Sunny'),
    ]
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: store, metadata: {} })
    })
    expect(ok).toBe(true)

    const after = result.current.messages
    // Only the tail was added; the shared prefix kept the same objects, so
    // the rendered rows keep their state (expanded tool rows, scroll anchor).
    expect(after.length).toBe(4)
    expect(after[0]).toBe(before[0])
    expect(after[1]).toBe(before[1])
    expect(after[2].role).toBe('user')
    expect(after[2].content).toBe('And tomorrow?')
    expect(after[3].content).toBe('Sunny')
    // The tail is marked so ChatArea treats it as a catch-up, not a new answer.
    expect(after[3]._transcriptRefresh).toBe(true)
    expect(after[2]._transcriptRefresh).toBeUndefined()

    // The backend session is re-seeded with the full stored transcript.
    const restore = restoreCalls().find(c => c.conversation_id === 'conv-1')
    expect(restore).toBeTruthy()
    expect(restore.messages).toHaveLength(4)
  })

  it('leaves the view untouched when the store adds nothing', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'What is the weather'),
        storedChat('assistant', 'Clear skies'),
      ],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)
    const before = result.current.messages
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: loaded.messages, metadata: {} })
    })
    expect(ok).toBe(true)
    expect(result.current.messages).toBe(before)
    // The session is still re-seeded: it was seeded from the snapshot at open
    // time and lacks the run's final turn even when the view already shows it.
    expect(restoreCalls()).toHaveLength(1)
  })

  it('aligns tool rows by tool_call_id across the persisted/live shape drift', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'Sleep for a bit'),
        storedToolCall('tc-1', 'atlas_sleep'),
        storedChat('assistant', 'Working on it'),
      ],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)

    // The run streamed this tool into the view after it was opened: the live
    // row the handler created differs from the persisted shape (role,
    // content, raw arguments), but is the same transcript row.
    dispatchToolStart('tc-2', 'atlas_sleep')
    dispatchToolComplete('tc-2', 'atlas_sleep')

    const store = [
      storedChat('user', 'Sleep for a bit'),
      storedToolCall('tc-1', 'atlas_sleep'),
      storedChat('assistant', 'Working on it'),
      storedToolCall('tc-2', 'atlas_sleep'),
      storedChat('assistant', 'Done sleeping'),
    ]
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: store, metadata: {} })
    })
    expect(ok).toBe(true)
    const after = result.current.messages
    // The live tool row matched its stored counterpart (no duplicate); only
    // the final answer the run streamed elsewhere is appended.
    expect(after.length).toBe(5)
    expect(after[3].tool_call_id).toBe('tc-2')
    expect(after[3].role).toBe('system')
    expect(after[4].content).toBe('Done sleeping')
    expect(after[4]._transcriptRefresh).toBe(true)
  })

  it('skips live-only rows (agent status lines) instead of breaking alignment', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'Multi-step task'),
        storedToolCall('tc-1', 'atlas_sleep'),
        storedChat('assistant', 'All done'),
      ],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)

    // The run streamed status chrome into this view between the stored rows.
    dispatchFrame({ type: 'agent_update', conversation_id: 'conv-1', run_id: 'r1', update_type: 'agent_start' })
    dispatchFrame({ type: 'agent_update', conversation_id: 'conv-1', run_id: 'r1', update_type: 'agent_reason', message: 'planning' })

    const store = [
      storedChat('user', 'Multi-step task'),
      storedToolCall('tc-1', 'atlas_sleep'),
      storedChat('assistant', 'All done'),
    ]
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: store, metadata: {} })
    })
    expect(ok).toBe(true)
    // Nothing appended; the status chrome the run produced stays on screen.
    expect(result.current.messages.length).toBe(5)
  })

  it('appends the tail past live rows the run streamed into this view', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'Multi-step task'),
        storedChat('assistant', 'Working on it'),
      ],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)

    // The run is still going: it streams a tool row into this view live.
    dispatchToolStart('tc-9', 'atlas_sleep')
    dispatchToolComplete('tc-9', 'atlas_sleep')

    // The store's final transcript for the same conversation. The streamed
    // assistant text never reached this view (it streamed elsewhere), so the
    // tool row plus the final answer are what the refresh must add.
    const store = [
      ...loaded.messages,
      storedToolCall('tc-9', 'atlas_sleep'),
      storedChat('assistant', 'Final answer'),
    ]
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: store, metadata: {} })
    })
    expect(ok).toBe(true)
    const after = result.current.messages
    expect(after.length).toBe(4)
    expect(after[3].content).toBe('Final answer')
    expect(after[3]._transcriptRefresh).toBe(true)
    // The tool row the view already had is untouched.
    expect(after[2].tool_call_id).toBe('tc-9')
  })

  it('matches agent narration persisted as agent_intermediate to its streamed row', async () => {
    vi.useFakeTimers()
    try {
    // The agent loop persists pre-tool narration with message_type
    // 'agent_intermediate' (atlas/application/chat/agent/agentic_loop.py),
    // while the same narration streams into the view as a plain assistant
    // row. The pair is the same transcript row and must not break the
    // alignment (review finding on this PR).
    const loaded = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'Let me check that for you.'),
        storedChat('assistant', 'Sure, one moment.'),
      ],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)

    // The run streamed this narration into the view after it was opened.
    dispatchFrame({ type: 'token_stream', conversation_id: 'conv-1', run_id: 'r1', is_first: true, token: 'On it.' })
    // Drain the buffered tokens the way the socket flush does. Fake timers so
    // this does not depend on a 50ms real-time margin holding on a loaded CI
    // runner -- the flush is scheduled, so advancing time is exact.
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    const narrationIdx = result.current.messages.findIndex(m => m.content === 'On it.')
    expect(narrationIdx).toBeGreaterThan(-1)

    const store = [
      storedChat('user', 'Let me check that for you.'),
      storedChat('assistant', 'Sure, one moment.'),
      // The same narration, in the shape the agent loop persists.
      {
        role: 'assistant',
        content: 'On it.',
        timestamp: '2026-01-01T00:00:02Z',
        message_type: 'agent_intermediate',
        metadata: { agent_mode: true, agent_intermediate: true, step: 1 },
      },
      storedChat('assistant', 'Working on it'),
    ]
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: store, metadata: {} })
    })
    expect(ok).toBe(true)
    // The streamed narration matched its persisted counterpart (despite the
    // different type); only the final answer was appended.
    const after = result.current.messages
    expect(after[after.length - 1].content).toBe('Working on it')
    expect(after[after.length - 1]._transcriptRefresh).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('skips view-only chrome the store never holds (approval, canvas error, socket error)', async () => {
    // A tracked run's transcript is written by the backend, which emits only
    // chat / tool_call / agent_intermediate. The approval row the run paused
    // on, a canvas error, and the socket's untyped `error` row are all view
    // chrome with no stored counterpart -- if alignment tripped on any of
    // them the approval-gated run (the PR's own fixture) would end every
    // refresh in the full reload.
    const loaded = {
      id: 'conv-1',
      messages: [storedChat('user', 'Sleep for a bit')],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)

    dispatchFrame({
      type: 'tool_approval_request',
      conversation_id: 'conv-1',
      run_id: 'r1',
      tool_call_id: 'tc-1',
      tool_name: 'atlas_sleep',
      arguments: { seconds: 5 },
    })
    dispatchFrame({ type: 'error', conversation_id: 'conv-1', run_id: 'r1', message: 'transient blip' })
    dispatchFrame({
      type: 'tool_error',
      conversation_id: 'conv-1',
      run_id: 'r1',
      tool_name: 'atlas_canvas',
      tool_call_id: 'tc-canvas',
      error: 'canvas boom',
    })
    const before = result.current.messages
    // All three are on screen.
    expect(before.some(m => m.type === 'tool_approval_request')).toBe(true)
    expect(before.some(m => m.type === 'canvas_error')).toBe(true)
    expect(before.some(m => !m.type && m.role === 'system' && m.content.startsWith('Error:'))).toBe(true)

    const store = [
      storedChat('user', 'Sleep for a bit'),
      storedToolCall('tc-1', 'atlas_sleep'),
      storedChat('assistant', 'Done sleeping'),
    ]
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: store, metadata: {} })
    })
    // Aligned past the chrome and appended, instead of refusing.
    expect(ok).toBe(true)
    // The chrome is left exactly where it was -- skipping is not deleting.
    for (const row of before) expect(result.current.messages).toContain(row)
    expect(result.current.messages[result.current.messages.length - 1].content).toBe('Done sleeping')
  })

  it('refuses a record for a conversation that is not the one on screen', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [storedChat('user', 'What is the weather')],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)
    const before = result.current.messages
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({
        id: 'conv-OTHER',
        messages: [storedChat('user', 'What is the weather'), storedChat('assistant', 'Not yours')],
        metadata: {},
      })
    })
    // Another conversation's rows must never be spliced into this view.
    expect(ok).toBe(false)
    expect(result.current.messages).toBe(before)
    expect(restoreCalls().filter(c => c.conversation_id === 'conv-OTHER')).toHaveLength(0)
  })

  it('refuses (returns false) when the stored transcript has diverged', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'What is the weather'),
        storedChat('assistant', 'Clear skies'),
      ],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)
    const before = result.current.messages
    h.sendMessage.mockClear()
    // The store's first row was rewritten (rewind in another tab).
    const store = [
      storedChat('user', 'Edited prompt'),
      storedChat('assistant', 'Clear skies'),
    ]
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: store, metadata: {} })
    })
    expect(ok).toBe(false)
    expect(result.current.messages).toBe(before)
    // No restore was sent on the refusal path; the fallback full reload owns it.
    expect(h.sendMessage).not.toHaveBeenCalled()
  })

  it('keeps view rows the store does not have instead of destroying them', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'What is the weather'),
        storedChat('assistant', 'Clear skies'),
      ],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)
    // The view gained a row the store never persisted.
    dispatchFrame({
      type: 'intermediate_update',
      conversation_id: 'conv-1',
      run_id: 'r1',
      update_type: 'system_message',
      data: { message: 'Watch out', subtype: 'info' },
    })
    const before = result.current.messages
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: loaded.messages, metadata: {} })
    })
    expect(ok).toBe(true)
    expect(result.current.messages.length).toBe(before.length)
    expect(result.current.messages[2].type).toBe('system')
  })

  it('refuses when the store is a strict prefix of the view (rewound elsewhere)', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'What is the weather'),
        storedChat('assistant', 'Clear skies'),
        storedChat('assistant', 'And humid'),
      ],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)
    h.sendMessage.mockClear()
    // Another tab rewound the conversation: the store is now shorter than
    // the view. The refresh cannot reconcile a shorter store against the
    // rows it drops, so it refuses; the caller's full reload takes the
    // store's copy (review finding on this PR).
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({
        id: 'conv-1',
        messages: [loaded.messages[0], loaded.messages[1]],
        metadata: {},
      })
    })
    expect(ok).toBe(false)
    expect(h.sendMessage).not.toHaveBeenCalled()
  })

  it('keeps informational system rows the store never persists', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'What is the weather'),
        storedChat('assistant', 'Clear skies'),
      ],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)
    // The view gained a system note the store never persists (server save
    // mode is the only mode with tracked runs, and these rows never reach
    // the backend history).
    dispatchFrame({
      type: 'intermediate_update',
      conversation_id: 'conv-1',
      run_id: 'r1',
      update_type: 'system_message',
      data: { message: 'Added report.csv to the session.', subtype: 'file-attached' },
    })
    const before = result.current.messages
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: loaded.messages, metadata: {} })
    })
    expect(ok).toBe(true)
    expect(result.current.messages.length).toBe(before.length)
    expect(result.current.messages[2].type).toBe('system')
  })

  it('schedules the post-run refresh when the run ends while the view loads', async () => {
    // The terminal run status can race the open: the GET returns the
    // in-flight snapshot, then reports the run completed before
    // loadSavedConversation's bookkeeping runs. The snapshot can be missing
    // the run's final rows, and no run-end event will arrive afterwards, so
    // the in_flight flag itself is the refresh obligation (review finding
    // on this PR).
    const snapshot = {
      id: 'conv-1',
      messages: [storedChat('user', 'What is the weather')],
      metadata: {},
      in_flight: true,
    }
    // Fake timers so the grace period below costs nothing and does not
    // depend on a real-time margin holding on a loaded CI runner. Installed
    // before the load, because that is where the timer is scheduled.
    vi.useFakeTimers()
    try {
      dispatchFrame({ type: 'run_status', run: { run_id: 'r1', conversation_id: 'conv-1', status: 'completed' } })
      const { result } = renderChat()
      await loadConversation(result, snapshot)
      expect(result.current.runEndedConversationId).toBeNull()
      // The grace period the run-end path uses delays the refresh so a stop
      // that reports cancelled before its interrupted turn is written does
      // not reload a transcript the save is about to change.
      await act(async () => { await vi.advanceTimersByTimeAsync(2800) })
      expect(result.current.runEndedConversationId).toBe('conv-1')
    } finally {
      vi.useRealTimers()
    }
  })

  // Both shapes of an in-flight record seed a replay placeholder (issue
  // #957): a half-streamed bubble when the run has an open segment, an empty
  // one when it is between steps. Neither is a transcript row -- they are
  // transient fragments the stored transcript supersedes -- so alignment must
  // skip them. Before this was fixed the placeholder was compared against the
  // run's finished answer, the match failed, and the refresh refused: the
  // full reload ran and the scroll jumped, in exactly the case #959 is about.
  it('appends over a half-streamed replay placeholder (in_flight + streaming_text)', async () => {
    const snapshot = {
      id: 'conv-1',
      messages: [storedChat('user', 'What is the weather')],
      metadata: {},
      in_flight: true,
      streaming_text: 'Clear sk',
    }
    const { result } = renderChat()
    await loadConversation(result, snapshot)
    // The placeholder is on screen, carrying the partial fragment.
    expect(result.current.messages.some(m => m._replayed)).toBe(true)

    const store = [
      storedChat('user', 'What is the weather'),
      storedChat('assistant', 'Clear skies ahead'),
    ]
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: store, metadata: {} })
    })
    expect(ok).toBe(true)

    const after = result.current.messages
    // The fragment is gone and the run's real answer took its place.
    expect(after.some(m => m._replayed)).toBe(false)
    expect(after.map(m => m.content)).toEqual(['What is the weather', 'Clear skies ahead'])
    expect(after[after.length - 1]._transcriptRefresh).toBe(true)
    expect(restoreCalls()).toHaveLength(1)
  })

  it('appends over an empty replay placeholder (in_flight, between steps)', async () => {
    const snapshot = {
      id: 'conv-1',
      messages: [storedChat('user', 'Sleep for a bit')],
      metadata: {},
      in_flight: true,
    }
    const { result } = renderChat()
    await loadConversation(result, snapshot)
    expect(result.current.messages.some(m => m._replayed)).toBe(true)

    const store = [
      storedChat('user', 'Sleep for a bit'),
      storedToolCall('tc-1', 'atlas_sleep'),
      storedChat('assistant', 'Done sleeping'),
    ]
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: store, metadata: {} })
    })
    expect(ok).toBe(true)

    const after = result.current.messages
    expect(after.some(m => m._replayed)).toBe(false)
    // No blank assistant bubble survives under the finished transcript.
    expect(after.some(m => m.role === 'assistant' && !m.content)).toBe(false)
    expect(after[after.length - 1].content).toBe('Done sleeping')
    expect(after[after.length - 1]._transcriptRefresh).toBe(true)
  })

  it('omits display-only rows from the re-seeded restore payload', async () => {
    // The re-seed must match loadSavedConversation's filter: a bare
    // role:'tool' row replays as an orphan tool message and persisted
    // agent_intermediate narration as a second assistant turn, either of
    // which breaks strict alternation in the backend session.
    const loaded = {
      id: 'conv-1',
      messages: [storedChat('user', 'Sleep for a bit')],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)

    const store = [
      storedChat('user', 'Sleep for a bit'),
      { ...storedChat('assistant', 'Let me check that'), message_type: 'agent_intermediate' },
      storedToolCall('tc-1', 'atlas_sleep'),
      storedChat('assistant', 'Done sleeping'),
    ]
    h.sendMessage.mockClear()
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({ id: 'conv-1', messages: store, metadata: {} })
    })
    expect(ok).toBe(true)

    const restore = restoreCalls().find(c => c.conversation_id === 'conv-1')
    expect(restore).toBeTruthy()
    expect(restore.messages).toEqual([
      { role: 'user', content: 'Sleep for a bit' },
      { role: 'assistant', content: 'Done sleeping' },
    ])
  })

  it('does not discharge the refresh obligation for a run this tab has not heard of', async () => {
    // `runs.getRun` returns undefined both for a run that has ended and for
    // one this tab has simply never seen. Taking the second for the first
    // would refresh against a mid-run store and clear the obligation, so the
    // real run-end would never fire a refresh at all. The list_runs answer
    // arrives during the grace period and the timer re-checks.
    const snapshot = {
      id: 'conv-1',
      messages: [storedChat('user', 'What is the weather')],
      metadata: {},
      in_flight: true,
    }
    vi.useFakeTimers()
    try {
      const { result } = renderChat()
      await loadConversation(result, snapshot)
      // The tab asked, and the server says the run is still going.
      dispatchFrame({ type: 'run_status', run: { run_id: 'r1', conversation_id: 'conv-1', status: 'running' } })
      await act(async () => { await vi.advanceTimersByTimeAsync(2800) })
      expect(result.current.runEndedConversationId).toBeNull()

      // When it really ends, the run-end effect still has its obligation.
      dispatchFrame({ type: 'run_status', run: { run_id: 'r1', conversation_id: 'conv-1', status: 'completed' } })
      await act(async () => { await vi.advanceTimersByTimeAsync(2800) })
      expect(result.current.runEndedConversationId).toBe('conv-1')
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps the refresh obligation when the fetched record is still in flight', async () => {
    // A second run started on this conversation while the first was settling,
    // so the GET returns a snapshot rather than the final transcript.
    // Appending it and returning would discharge the obligation against a
    // moving target and the new run's answer would never reach the view.
    const loaded = {
      id: 'conv-1',
      messages: [storedChat('user', 'What is the weather')],
      metadata: {},
    }
    // Fake timers from the start: the re-arm schedules its own delayed
    // refresh synchronously, so a real-timer schedule would never fire here.
    vi.useFakeTimers()
    try {
      const { result } = renderChat()
      await loadConversation(result, loaded)

      let ok
      act(() => {
        ok = result.current.refreshJoinedConversation({
          id: 'conv-1',
          messages: [storedChat('user', 'What is the weather'), storedChat('assistant', 'partial so far')],
          metadata: {},
          in_flight: true,
        })
      })
      // The reader keeps their place: the rows it does have are appended.
      expect(ok).toBe(true)
      expect(result.current.messages[result.current.messages.length - 1].content).toBe('partial so far')
      // Nothing discharged yet.
      expect(result.current.runEndedConversationId).toBeNull()

      // The obligation is still live and does not depend on a future run-map
      // change: the run this tab never heard of gets its delayed refresh.
      await act(async () => { await vi.advanceTimersByTimeAsync(2800) })
      expect(result.current.runEndedConversationId).toBe('conv-1')
    } finally {
      vi.useRealTimers()
    }
  })

  it('re-arms the grace period when the run ends late in the load window', async () => {
    // The load-path timer counts from the load, not from the run going
    // terminal. A run that ends just before it fires would otherwise be
    // aligned against a store that has not been written yet, losing the
    // answer until a manual reload.
    const snapshot = {
      id: 'conv-1',
      messages: [storedChat('user', 'What is the weather')],
      metadata: {},
      in_flight: true,
    }
    vi.useFakeTimers()
    try {
      const { result } = renderChat()
      await loadConversation(result, snapshot)
      dispatchFrame({ type: 'run_status', run: { run_id: 'r1', conversation_id: 'conv-1', status: 'running' } })
      // The run goes terminal late in the first grace window.
      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
      dispatchFrame({ type: 'run_status', run: { run_id: 'r1', conversation_id: 'conv-1', status: 'completed' } })
      // The original window elapses -- but the status changed, so it re-arms
      // rather than discharging against a store mid-save.
      await act(async () => { await vi.advanceTimersByTimeAsync(600) })
      expect(result.current.runEndedConversationId).toBeNull()
      // A full grace period after the run actually ended, it discharges.
      await act(async () => { await vi.advanceTimersByTimeAsync(2600) })
      expect(result.current.runEndedConversationId).toBe('conv-1')
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not let stored metadata override how an appended row renders', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [storedChat('user', 'hi')],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)
    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({
        id: 'conv-1',
        messages: [
          storedChat('user', 'hi'),
          {
            role: 'assistant',
            content: 'the real answer',
            timestamp: '2026-01-01T00:00:05Z',
            message_type: 'chat',
            // Stored data, not a rendering instruction.
            metadata: { role: 'system', content: 'spoofed', type: 'agent_error' },
          },
        ],
        metadata: {},
      })
    })
    expect(ok).toBe(true)
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.role).toBe('assistant')
    expect(last.content).toBe('the real answer')
    expect(last.type).toBe('chat')
  })

  it('appends over a bubble this tab is genuinely streaming into', async () => {
    // STREAM_TOKEN clears `_replayed` on the first live token, so a live
    // partial is an ordinary assistant row holding half an answer. Compared
    // against the stored finished answer it can never match, and the refresh
    // would refuse -- the full reload and scroll jump this PR removes.
    const loaded = {
      id: 'conv-1',
      messages: [storedChat('user', 'What is the weather')],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)

    // Real live tokens, not a replay frame.
    dispatchFrame({ type: 'token_stream', conversation_id: 'conv-1', run_id: 'r1', is_first: true, token: 'Clear sk' })
    await act(async () => { await new Promise(r => setTimeout(r, 60)) })
    const partial = result.current.messages.find(m => m._streaming)
    expect(partial).toBeTruthy()
    // Not a replay placeholder -- so the `_replayed` filter would miss it.
    expect(Boolean(partial._replayed)).toBe(false)

    let ok
    act(() => {
      ok = result.current.refreshJoinedConversation({
        id: 'conv-1',
        messages: [storedChat('user', 'What is the weather'), storedChat('assistant', 'Clear skies')],
        metadata: {},
      })
    })
    expect(ok).toBe(true)
    const after = result.current.messages
    // The half-written answer is gone, replaced by the whole one exactly once.
    expect(after.some(m => m._streaming)).toBe(false)
    expect(after.filter(m => (m.content || '').startsWith('Clear sk'))).toHaveLength(1)
    expect(after[after.length - 1].content).toBe('Clear skies')
  })

  it('keeps a retained in-flight bubble below the appended rows', async () => {
    // The still-in-flight path keeps the open bubble, but it must not sit
    // above the finished rows: it would go on filling over the top of them.
    const loaded = {
      id: 'conv-1',
      messages: [storedChat('user', 'What is the weather')],
      metadata: {},
    }
    vi.useFakeTimers()
    try {
      const { result } = renderChat()
      await loadConversation(result, loaded)
      act(() => { result.current.refreshJoinedConversation({
        id: 'conv-1',
        messages: [storedChat('user', 'What is the weather')],
        metadata: {},
        in_flight: true,
      }) })
      // Seed an open bubble the way an in-flight record does.
      dispatchFrame({ type: 'token_stream', conversation_id: 'conv-1', run_id: 'r2', is_first: true, token: 'working' })
      await act(async () => { await vi.advanceTimersByTimeAsync(60) })
      expect(result.current.messages.some(m => m._streaming)).toBe(true)

      act(() => { result.current.refreshJoinedConversation({
        id: 'conv-1',
        messages: [storedChat('user', 'What is the weather'), storedChat('assistant', 'first answer')],
        metadata: {},
        in_flight: true,
      }) })
      const after = result.current.messages
      // The open bubble is last; the appended finished row sits above it.
      expect(after[after.length - 1]._streaming).toBe(true)
      expect(after[after.length - 2].content).toBe('first answer')
    } finally {
      vi.useRealTimers()
    }
  })

  it('rejects malformed input without touching the view', async () => {
    const loaded = {
      id: 'conv-1',
      messages: [storedChat('user', 'hi')],
      metadata: {},
    }
    const { result } = renderChat()
    await loadConversation(result, loaded)
    const before = result.current.messages
    let ok1, ok2
    act(() => { ok1 = result.current.refreshJoinedConversation(null) })
    act(() => { ok2 = result.current.refreshJoinedConversation({ id: 'conv-1', metadata: {} }) })
    expect(ok1).toBe(false)
    expect(ok2).toBe(false)
    expect(result.current.messages).toBe(before)
  })
})
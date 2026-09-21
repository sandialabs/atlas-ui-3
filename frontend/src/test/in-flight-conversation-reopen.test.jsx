/**
 * Behavioral tests for issue #957: reopening a conversation whose run is
 * still in flight (loadSavedConversation's in-flight path).
 *
 * The component suites mock ChatContext, so the real `loadSavedConversation`
 * never runs there. This renders the *real* ChatProvider (with only leaf
 * hooks stubbed, the same harness as the #829 workspace-restore suite) so we
 * can pin the guarantees:
 *
 *   - an in-flight record carrying `streaming_text` seeds a replayed bubble
 *     with the segment, asks the server for a fresh runs snapshot, and still
 *     sends the restore frame;
 *   - an in-flight record with no open segment (the run is between steps or
 *     parked on an approval) still seeds the "answer in progress" marker
 *     bubble, so the transcript shows a sign of life;
 *   - a stored record that is not in flight seeds nothing and asks for no
 *     snapshot;
 *   - the visible-conversation ref moves synchronously with the load, so the
 *     replay frame answering the restore is applied -- not dropped by the
 *     conversation-routing gate for arriving before the next render.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

// Shared mock handles (hoisted so the vi.mock factories can close over them).
const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  wsHandler: null,
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  toastInfo: vi.fn(),
  applyWorkspace: vi.fn(),
  snapshotSelections: vi.fn(() => ({})),
  wsState: { workspaces: [], loaded: true, error: null },
  activeWorkspaceId: null,
  setActiveWorkspaceId: vi.fn(),
  configReady: true,
  workspacesEnabled: true,
  saveLocalConv: vi.fn(() => Promise.resolve()),
  saveMode: 'none',
}))

vi.mock('../contexts/WSContext', () => ({
  useWS: () => ({
    sendMessage: h.sendMessage,
    isConnected: true,
    // Capture the real handler so tests can dispatch frames through the
    // conversation-routing gate.
    addMessageHandler: (fn) => { h.wsHandler = fn; return () => {} },
  }),
}))

vi.mock('../components/ui/toastContext', () => ({
  useToast: () => ({ error: h.toastError, success: h.toastSuccess, info: h.toastInfo }),
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
      selectedTools: new Set(['canvas_canvas']),
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

const makeConversation = (overrides = {}) => ({
  id: 'conv-1',
  messages: [
    { role: 'user', content: 'Hello', message_type: 'chat', timestamp: '2026-01-01T00:00:00Z' },
  ],
  metadata: {},
  ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  h.sendMessage.mockImplementation(() => true)
  h.wsHandler = null
  h.wsState.workspaces = []
  h.wsState.loaded = true
  h.wsState.error = null
  h.activeWorkspaceId = null
  h.configReady = true
  h.workspacesEnabled = true
  h.saveLocalConv.mockImplementation(() => Promise.resolve())
  h.saveMode = 'none'
})

describe('loadSavedConversation in-flight reopen (issue #957)', () => {
  it('seeds the replayed bubble from streaming_text and asks for a runs snapshot', () => {
    const { result } = renderChat()
    act(() => {
      result.current.loadSavedConversation(makeConversation({
        in_flight: true,
        run_id: 'run-1',
        streaming_text: 'The answer starts here',
      }))
    })

    const seeded = result.current.messages.find(m => m._streaming)
    expect(seeded).toBeTruthy()
    expect(seeded.content).toBe('The answer starts here')
    expect(seeded._replayed).toBe(true)

    // The reload-on-run-end keys off this tab's run tracker, which may never
    // have seen the run -- so the load asks the server for a fresh snapshot.
    const listRuns = h.sendMessage.mock.calls.find(c => c[0]?.type === 'list_runs')
    expect(listRuns).toBeTruthy()
    expect(listRuns[0].conversation_id).toBe('conv-1')

    // The restore frame still goes out.
    expect(h.sendMessage.mock.calls.some(c => c[0]?.type === 'restore_conversation')).toBe(true)
  })

  it('seeds the marker bubble even when there is no open segment', () => {
    const { result } = renderChat()
    act(() => {
      result.current.loadSavedConversation(makeConversation({ in_flight: true, run_id: 'run-1' }))
    })

    // Parked on an approval or between steps: no streaming_text, but the
    // transcript still gets its "answer in progress" sign of life.
    const seeded = result.current.messages.find(m => m._streaming)
    expect(seeded).toBeTruthy()
    expect(seeded.content).toBe('')
    expect(seeded._replayed).toBe(true)
  })

  it('seeds nothing and asks for no snapshot for a stored, idle conversation', () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation()) })

    expect(result.current.messages.some(m => m._streaming)).toBe(false)
    expect(h.sendMessage.mock.calls.some(c => c[0]?.type === 'list_runs')).toBe(false)
    expect(h.sendMessage.mock.calls.some(c => c[0]?.type === 'restore_conversation')).toBe(true)
  })

  it('applies the replay frame that answers the restore, even before the next render', () => {
    const { result } = renderChat()
    act(() => {
      result.current.loadSavedConversation(makeConversation({
        in_flight: true,
        run_id: 'run-1',
        streaming_text: 'earlier snapshot',
      }))
    })
    expect(h.wsHandler).toBeTruthy()

    // The restore's replay frame arrives tagged with the run's ids. The
    // routing gate reads the visible-conversation ref, which the load moved
    // synchronously -- so the frame replaces the seeded bubble instead of
    // being filed as background activity and dropped.
    act(() => {
      h.wsHandler({
        type: 'token_stream',
        token: 'newer longer snapshot of the same segment',
        replay: true,
        run_id: 'run-1',
        conversation_id: 'conv-1',
      })
    })

    const bubble = result.current.messages.find(m => m._streaming)
    expect(bubble).toBeTruthy()
    expect(bubble.content).toBe('newer longer snapshot of the same segment')
    expect(bubble._replayed).toBe(true)
  })

  it('still drops a run frame that belongs to a different conversation', () => {
    const { result } = renderChat()
    act(() => {
      result.current.loadSavedConversation(makeConversation({
        in_flight: true,
        run_id: 'run-1',
        streaming_text: 'mine',
      }))
    })

    act(() => {
      h.wsHandler({
        type: 'token_stream',
        token: 'someone else',
        replay: true,
        run_id: 'run-2',
        conversation_id: 'conv-2',
      })
    })

    const bubble = result.current.messages.find(m => m._streaming)
    expect(bubble.content).toBe('mine')
  })

  it('closes the seeded bubble when the user sends a new turn', () => {
    const { result } = renderChat()
    act(() => {
      result.current.loadSavedConversation(makeConversation({
        in_flight: true,
        run_id: 'run-1',
        streaming_text: 'fragment of the old answer',
      }))
    })
    expect(result.current.messages.some(m => m._streaming)).toBe(true)

    // In a tab that receives no further frames nothing else ends the seeded
    // stream, and STREAM_TOKEN's append lookup targets the last _streaming
    // row -- so the send path closes it, or the new reply would accumulate
    // into the stale bubble above the user's message.
    act(() => { result.current.sendChatMessage('a new question') })

    const stale = result.current.messages.find(m => m.content === 'fragment of the old answer')
    expect(stale._streaming).toBe(false)
    // The user's message landed after it, as its own row.
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.role).toBe('user')
    expect(last.content).toBe('a new question')
  })

  it('removes the empty seeded bubble outright when a send ends its stream', () => {
    const { result } = renderChat()
    act(() => {
      result.current.loadSavedConversation(makeConversation({ in_flight: true, run_id: 'run-1' }))
    })
    expect(result.current.messages.some(m => m._streaming)).toBe(true)

    act(() => { result.current.sendChatMessage('a new question') })

    // No permanently blank assistant row is left behind (STREAM_END drops an
    // empty placeholder rather than freezing it).
    expect(result.current.messages.some(m => m.role === 'assistant' && !m.content)).toBe(false)
  })

  it('does not close a genuinely live bubble on send (steering mid-run)', async () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation()) })

    // A live stream in this tab (not a replay placeholder): tokens are
    // arriving here, so _replayed is false. The handler buffers tokens and
    // flushes on a 30ms timer, so wait for the flush.
    act(() => {
      h.wsHandler({ type: 'token_stream', token: 'live answer', is_first: true, is_last: false })
    })
    await act(() => new Promise(r => setTimeout(r, 60)))
    const live = result.current.messages.find(m => m._streaming)
    expect(live).toBeTruthy()
    expect(live._replayed).toBeFalsy()

    act(() => { result.current.sendChatMessage('steer: also do this') })

    // The live bubble keeps streaming: ending it would split the segment
    // into two bubbles and persist the truncated fragment as a finished reply.
    const still = result.current.messages.find(m => m.content === 'live answer')
    expect(still._streaming).toBe(true)
  })

  it('excludes replayed placeholder rows from the local autosave', () => {
    vi.useFakeTimers()
    try {
      h.saveMode = 'local'
      const { result } = renderChat()
      act(() => {
        result.current.loadSavedConversation(makeConversation({
          in_flight: true,
          run_id: 'run-1',
          streaming_text: 'fragment of the old answer',
        }))
      })

      // The autosave debounces by a second; run the timer deterministically.
      act(() => { vi.advanceTimersByTime(1100) })

      expect(h.saveLocalConv).toHaveBeenCalled()
      const saved = h.saveLocalConv.mock.calls[h.saveLocalConv.mock.calls.length - 1][0]
      expect(saved.messages.some(m => m.content === 'fragment of the old answer')).toBe(false)
      expect(saved.messages.some(m => m.role === 'user')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('excludes replayed placeholder rows from the New Chat undo snapshot', () => {
    const { result } = renderChat()
    act(() => {
      result.current.loadSavedConversation(makeConversation({
        in_flight: true,
        run_id: 'run-1',
        streaming_text: 'fragment of the old answer',
      }))
    })

    // New Chat snapshots the view for the Undo offer. The seeded bubble no
    // longer counts as streaming (it is a placeholder, not this tab's live
    // stream), so the snapshot is taken -- but the placeholder is not in it.
    act(() => { result.current.clearChat() })
    const toastCall = h.toastInfo.mock.calls.find(c => c[1]?.action?.onClick)
    expect(toastCall).toBeTruthy()

    // Clicking Undo restores the snapshot through loadSavedConversation;
    // observe what comes back.
    act(() => { toastCall[1].action.onClick() })

    expect(result.current.messages.some(m => m.content === 'fragment of the old answer')).toBe(false)
    expect(result.current.messages.some(m => m.role === 'user' && m.content === 'Hello')).toBe(true)
  })

  it('excludes display-only narration rows from the restore payload', () => {
    const { result } = renderChat()
    act(() => {
      result.current.loadSavedConversation(makeConversation({
        messages: [
          { role: 'user', content: 'Hello', message_type: 'chat', timestamp: '2026-01-01T00:00:00Z' },
          { role: 'assistant', content: 'narration', message_type: 'agent_intermediate', timestamp: '2026-01-01T00:00:01Z' },
          { role: 'tool', content: 'Tool call: calc', message_type: 'tool_call', timestamp: '2026-01-01T00:00:02Z' },
          { role: 'assistant', content: 'final answer', message_type: 'chat', timestamp: '2026-01-01T00:00:03Z' },
        ],
      }))
    })

    const restore = h.sendMessage.mock.calls.find(c => c[0]?.type === 'restore_conversation')
    expect(restore).toBeTruthy()
    // With no conversation repository configured the client payload is
    // canonical: a display-only row replayed as a second assistant turn
    // would break strict alternation.
    expect(restore[0].messages).toEqual([
      { role: 'user', content: 'Hello' },
      { role: 'assistant', content: 'final answer' },
    ])
  })
})

/**
 * Mid-run refresh of a joined conversation (parallel-run tool-call visibility).
 *
 * A conversation opened while its run is still executing receives none of the
 * frames the run goes on to emit -- they stay bound to the socket that started
 * it -- so a tool call that lands afterwards is invisible until the run ends
 * and the final reload runs. ChatContext now polls the run's live record while
 * this view is the joined one and appends what has appeared since, using the
 * same reconciliation as the run-end reload.
 *
 * Renders the real ChatProvider (leaf hooks stubbed) and drives the real
 * websocket handler, so the gate that decides whether to poll is the one the
 * app actually uses.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { applyRunEndRefresh } from '../utils/runEndRefresh'

const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  handlers: new Set(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  toastInfo: vi.fn(),
  applyWorkspace: vi.fn(),
  snapshotSelections: vi.fn(() => ({})),
  wsState: { workspaces: [], loaded: true, error: null },
  activeWorkspaceId: null,
  setActiveWorkspaceId: vi.fn(),
  configReady: true,
  chatHistoryEnabled: true,
  isConnected: true,
  saveLocalConv: vi.fn(() => Promise.resolve()),
  saveMode: 'server',
  fetchMock: vi.fn(),
}))

vi.mock('../contexts/WSContext', () => ({
  useWS: () => ({
    sendMessage: h.sendMessage,
    isConnected: h.isConnected,
    addMessageHandler: (fn) => {
      h.handlers.add(fn)
      return () => h.handlers.delete(fn)
    },
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
    features: { chat_history: h.chatHistoryEnabled, workspaces: false },
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
vi.mock('../hooks/usePersonas', () => ({ usePersonas: () => ({ personas: [], loading: false, loaded: true, error: null }) }))

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

const dispatchFrame = (frame) => {
  act(() => {
    for (const fn of h.handlers) fn(frame)
  })
}

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
    server_name: toolName.split('_')[0],
    arguments: { seconds: 5 },
    result: 'ok',
    status: 'completed',
  },
})

const liveRecord = (extraToolRow, streamingText) => ({
  id: 'conv-1',
  in_flight: true,
  run_id: 'run-1',
  metadata: {},
  streaming_text: streamingText,
  messages: [
    storedChat('user', 'What is the weather'),
    storedToolCall('call-bash', 'basic_fns_bash'),
    ...(extraToolRow ? [extraToolRow] : []),
  ],
})

beforeEach(() => {
  vi.clearAllMocks()
  h.sendMessage.mockImplementation(() => true)
  h.handlers.clear()
  h.wsState.workspaces = []
  h.wsState.loaded = true
  h.wsState.error = null
  h.activeWorkspaceId = null
  h.configReady = true
  h.chatHistoryEnabled = true
  h.isConnected = true
  h.saveMode = 'server'
  h.saveLocalConv.mockImplementation(() => Promise.resolve())
  vi.stubGlobal('fetch', h.fetchMock)
  h.fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) })
})

describe('same-conversation WebSocket reconnect (issue #1005)', () => {
  const setConnected = async (rerender, connected) => {
    await act(async () => {
      h.isConnected = connected
      rerender()
    })
  }

  it('reloads missed rows and partial text, then keeps polling until the run ends', async () => {
    vi.useFakeTimers()
    try {
      const { result, rerender, unmount } = renderChat()
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1', messages: [storedChat('user', 'What is the weather')],
        })
      })
      dispatchFrame({ type: 'run_started', conversation_id: 'conv-1', run_id: 'run-1' })
      dispatchFrame({
        type: 'tool_start', conversation_id: 'conv-1', run_id: 'run-1',
        tool_call_id: 'call-bash', tool_name: 'basic_fns_bash', arguments: {},
      })
      dispatchFrame({
        type: 'token_stream', conversation_id: 'conv-1', run_id: 'run-1', token: 'Working', is_first: true,
      })
      await act(async () => { await vi.advanceTimersByTimeAsync(30) })
      expect(result.current.messages.find(m => m._streaming)?.content).toBe('Working')
      expect(h.fetchMock.mock.calls.filter(([url]) => String(url).includes('/api/conversations/'))).toHaveLength(0)

      await setConnected(rerender, false)
      let record = liveRecord(storedToolCall('call-sleep', 'atlas_sleep'), 'Working on it')
      h.fetchMock.mockImplementation(async (url) => ({
        ok: true, json: async () => String(url).includes('/api/conversations/conv-1') ? record : {},
      }))
      await setConnected(rerender, true)
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ conversation_id: 'conv-1', run_id: 'run-1', status: 'running' }],
      })
      await act(async () => { await vi.advanceTimersByTimeAsync(30) })
      expect(result.current.activeConversationId).toBe('conv-1')
      expect(result.current.messages.filter(m => m.tool_call_id === 'call-bash')).toHaveLength(1)
      expect(result.current.messages.filter(m => m.tool_call_id === 'call-sleep')).toHaveLength(1)
      expect(result.current.messages.filter(m => m._streaming)).toHaveLength(1)
      expect(result.current.messages.find(m => m._streaming)).toMatchObject({
        content: 'Working on it', _replayed: true,
      })
      expect(h.sendMessage).toHaveBeenCalledWith({ type: 'list_runs', conversation_id: 'conv-1' })
      expect(h.sendMessage).toHaveBeenCalledWith(expect.objectContaining({
        type: 'restore_conversation', conversation_id: 'conv-1',
      }))

      record = liveRecord(storedToolCall('call-next', 'atlas_sleep'), 'More progress')
      record.messages.splice(2, 0, storedToolCall('call-sleep', 'atlas_sleep'))
      await act(async () => { await vi.advanceTimersByTimeAsync(3100) })
      expect(result.current.messages.filter(m => m.tool_call_id === 'call-next')).toHaveLength(1)
      expect(result.current.messages.find(m => m._streaming)?.content).toBe('More progress')

      record = { id: 'conv-1', messages: [...record.messages, storedChat('assistant', 'All done')] }
      dispatchFrame({
        type: 'run_status',
        run: { conversation_id: 'conv-1', run_id: 'run-1', status: 'completed' },
      })
      await act(async () => { await vi.advanceTimersByTimeAsync(2600) })
      expect(result.current.runEndedConversationId).toBe('conv-1')
      // Sidebar consumes this obligation through the shared run-end path.
      await act(async () => {
        applyRunEndRefresh({
          fullConv: record,
          refreshJoinedConversation: result.current.refreshJoinedConversation,
          loadSavedConversation: result.current.loadSavedConversation,
        })
        result.current.clearRunEndedConversation()
      })
      expect(result.current.messages.at(-1).content).toBe('All done')
      expect(result.current.messages.some(m => m._streaming)).toBe(false)
      expect(result.current.messages.filter(m => m.tool_call_id === 'call-bash')).toHaveLength(1)
      unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows the final transcript when the run completed while disconnected', async () => {
    const { result, rerender, unmount } = renderChat()
    await act(async () => {
      await result.current.loadSavedConversation({
        id: 'conv-1', messages: [storedChat('user', 'What is the weather')],
      })
    })
    dispatchFrame({ type: 'run_started', conversation_id: 'conv-1', run_id: 'run-1' })
    await setConnected(rerender, false)
    const record = {
      id: 'conv-1',
      messages: [
        storedChat('user', 'What is the weather'),
        storedToolCall('call-bash', 'basic_fns_bash'),
        storedChat('assistant', 'Clear skies'),
      ],
    }
    h.fetchMock.mockResolvedValue({ ok: true, json: async () => record })
    await setConnected(rerender, true)
    dispatchFrame({ type: 'runs_snapshot', runs: [] })
    expect(result.current.messages.map(m => m.content)).toEqual(record.messages.map(m => m.content))
    expect(result.current.messages.some(m => m._streaming)).toBe(false)
    unmount()
  })

  it.each(['navigation', 'disconnect'])('ignores a reconnect response after %s', async (change) => {
    const { result, rerender, unmount } = renderChat()
    await act(async () => {
      await result.current.loadSavedConversation({
        id: 'conv-1', messages: [storedChat('user', 'Original')],
      })
    })
    await setConnected(rerender, false)
    let resolveFetch
    h.fetchMock.mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))
    await setConnected(rerender, true)
    expect(resolveFetch).toBeTypeOf('function')
    if (change === 'navigation') {
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-2', messages: [storedChat('user', 'Another conversation')],
        })
      })
    } else {
      await setConnected(rerender, false)
    }
    const before = result.current.messages
    await act(async () => {
      resolveFetch({ ok: true, json: async () => liveRecord(null, 'Stale response') })
    })
    expect(result.current.messages).toBe(before)
    unmount()
  })

  it.each(['local', 'none'])('does not reload browser-only conversations in %s save mode', async (saveMode) => {
    h.saveMode = saveMode
    const { result, rerender, unmount } = renderChat()
    await act(async () => {
      await result.current.loadSavedConversation({
        id: 'conv-1', messages: [storedChat('user', 'Browser only')],
      })
    })
    await setConnected(rerender, false)
    h.fetchMock.mockClear()
    await setConnected(rerender, true)
    expect(h.fetchMock.mock.calls.filter(([url]) => String(url).includes('/api/conversations/'))).toHaveLength(0)
    unmount()
  })
})

describe('mid-run live refresh of a joined conversation', () => {
  it('appends a tool row that lands while the answer is still in progress', async () => {
    vi.useFakeTimers()
    try {
      const sleepRow = storedToolCall('call-sleep', 'atlas_sleep')
      h.fetchMock.mockImplementation(async (url) => {
        if (String(url).includes('/api/conversations/conv-1')) {
          return { ok: true, json: async () => liveRecord(sleepRow, 'Working on it') }
        }
        return { ok: false, status: 404, json: async () => ({}) }
      })

      const { result } = renderChat()
      // The run is tracked as active, then the conversation is opened from a
      // tab that receives no further frames (that is what the placeholder
      // marks).
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ run_id: 'run-1', conversation_id: 'conv-1', status: 'running', created_at: 1 }],
      })
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1',
          in_flight: true,
          run_id: 'run-1',
          streaming_text: 'Working',
          metadata: {},
          messages: [storedChat('user', 'What is the weather'), storedToolCall('call-bash', 'basic_fns_bash')],
        })
      })

      // The poll fires immediately on enable, then again each interval.
      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })

      const toolIds = result.current.messages.filter(m => m.type === 'tool_call').map(m => m.tool_call_id)
      expect(toolIds).toEqual(['call-bash', 'call-sleep'])
      // The open bubble tracks the newest segment rather than the seed.
      const bubble = result.current.messages.find(m => m._streaming)
      expect(bubble.content).toBe('Working on it')
      // The mid-run pass did not claim the run-end obligation.
      expect(result.current.runEndedConversationId).toBeNull()

      // A later poll that finds nothing new dispatches nothing: the message
      // list keeps its identity, so an idle poll does not re-render the
      // transcript (or re-stamp the bubble's timestamp) every interval.
      const settledMessages = result.current.messages
      await act(async () => { await vi.advanceTimersByTimeAsync(3200) })
      expect(result.current.messages).toBe(settledMessages)

      // A changed streaming segment with the same rows is real movement and
      // must still reach the bubble on the next pass.
      h.fetchMock.mockImplementation(async (url) => {
        if (String(url).includes('/api/conversations/conv-1')) {
          return { ok: true, json: async () => liveRecord(sleepRow, 'Working on it some more') }
        }
        return { ok: false, status: 404, json: async () => ({}) }
      })
      await act(async () => { await vi.advanceTimersByTimeAsync(3200) })
      const bubbleAfter = result.current.messages.find(m => m._streaming)
      expect(bubbleAfter.content).toBe('Working on it some more')
    } finally {
      vi.useRealTimers()
    }
  })

  it('stops polling once the run reaches a terminal state', async () => {
    vi.useFakeTimers()
    try {
      h.fetchMock.mockImplementation(async (url) => {
        if (String(url).includes('/api/conversations/conv-1')) {
          return { ok: true, json: async () => liveRecord(null, 'Working on it') }
        }
        return { ok: false, status: 404, json: async () => ({}) }
      })

      const { result } = renderChat()
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ run_id: 'run-1', conversation_id: 'conv-1', status: 'running', created_at: 1 }],
      })
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1',
          in_flight: true,
          run_id: 'run-1',
          streaming_text: 'Working',
          metadata: {},
          messages: [storedChat('user', 'What is the weather')],
        })
      })
      await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
      const callsWhileRunning = h.fetchMock.mock.calls.length
      expect(callsWhileRunning).toBeGreaterThan(0)

      dispatchFrame({ type: 'run_status', run: { run_id: 'run-1', conversation_id: 'conv-1', status: 'completed' } })
      await act(async () => { await vi.advanceTimersByTimeAsync(10000) })

      expect(h.fetchMock.mock.calls.length).toBe(callsWhileRunning)
    } finally {
      vi.useRealTimers()
    }
  })

  it('wires the fetch timeout, the backoff base and the backoff cap', async () => {
    vi.useFakeTimers()
    try {
      // Fail from the first poll: then the retry delay is the backoff, not the
      // normal interval, so this actually pins backoffBase.
      h.fetchMock.mockImplementation(async () => ({ ok: false, status: 503, json: async () => ({}) }))
      const { result } = renderChat()
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ run_id: 'run-1', conversation_id: 'conv-1', status: 'running', created_at: 1 }],
      })
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1',
          in_flight: true,
          run_id: 'run-1',
          streaming_text: 'Working',
          metadata: {},
          messages: [storedChat('user', 'What is the weather')],
        })
      })
      // The request carries an abort signal (the 15s hang guard).
      const firstCall = h.fetchMock.mock.calls.find(c => String(c[0]).includes('/api/conversations/'))
      expect(firstCall?.[1]?.signal).toBeTruthy()
      expect(typeof firstCall[1].signal.aborted).toBe('boolean')

      // The 503 is retried on the live poll's 3s backoffBase, not the hook's
      // 1s default: at 1.5s there is no second attempt; by 3.5s there is.
      const callsAtStart = h.fetchMock.mock.calls.length
      await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
      expect(h.fetchMock.mock.calls.length).toBe(callsAtStart)
      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
      expect(h.fetchMock.mock.calls.length).toBeGreaterThan(callsAtStart)
    } finally {
      vi.useRealTimers()
    }
  })

  it('caps the live poll backoff at 30s', async () => {
    vi.useFakeTimers()
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0.5) // no jitter
    try {
      h.fetchMock.mockImplementation(async () => ({ ok: false, status: 503, json: async () => ({}) }))
      const { result } = renderChat()
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ run_id: 'run-1', conversation_id: 'conv-1', status: 'running', created_at: 1 }],
      })
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1',
          in_flight: true,
          run_id: 'run-1',
          streaming_text: 'Working',
          metadata: {},
          messages: [storedChat('user', 'What is the weather')],
        })
      })
      const liveCalls = () => h.fetchMock.mock.calls
        .filter(c => String(c[0]).includes('/api/conversations/')).length

      // The immediate poll plus retries at 3s, 6s, 12s and 24s. The fifth
      // failure's uncapped backoff would be 48s; the live poll caps at 30s.
      for (const step of [3000, 6000, 12000, 24000]) {
        await act(async () => { await vi.advanceTimersByTimeAsync(step) })
      }
      const afterCap = liveCalls()
      await act(async () => { await vi.advanceTimersByTimeAsync(29000) })
      expect(liveCalls()).toBe(afterCap)
      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
      expect(liveCalls()).toBeGreaterThan(afterCap)
    } finally {
      randomSpy.mockRestore()
      vi.useRealTimers()
    }
  })

  it('does not poll while the tab is hidden', async () => {
    vi.useFakeTimers()
    const originalHidden = Object.getOwnPropertyDescriptor(document, 'hidden')
    try {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
      const { result } = renderChat()
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ run_id: 'run-1', conversation_id: 'conv-1', status: 'running', created_at: 1 }],
      })
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1',
          in_flight: true,
          run_id: 'run-1',
          streaming_text: 'Working',
          metadata: {},
          messages: [storedChat('user', 'What is the weather')],
        })
      })
      await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
      expect(h.fetchMock.mock.calls.some(c => String(c[0]).includes('/api/conversations/'))).toBe(false)
    } finally {
      if (originalHidden) Object.defineProperty(document, 'hidden', originalHidden)
      else delete document.hidden
      vi.useRealTimers()
    }
  })

  it('does not poll the tab that owns the stream (no replay placeholder)', async () => {
    vi.useFakeTimers()
    try {
      const { result } = renderChat()
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ run_id: 'run-1', conversation_id: 'conv-1', status: 'running', created_at: 1 }],
      })
      // A stored, idle load seeds no replay placeholder: this is the tab that
      // started (or is live-streaming) the run, so it must not be polled.
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1',
          messages: [storedChat('user', 'What is the weather')],
          metadata: {},
        })
      })
      await act(async () => { await vi.advanceTimersByTimeAsync(10000) })

      expect(h.fetchMock.mock.calls.some(c => String(c[0]).includes('/api/conversations/'))).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a settled or errored record leaves the view untouched', async () => {
    vi.useFakeTimers()
    try {
      const { result } = renderChat()
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ run_id: 'run-1', conversation_id: 'conv-1', status: 'running', created_at: 1 }],
      })
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1',
          in_flight: true,
          run_id: 'run-1',
          streaming_text: 'seed text',
          metadata: {},
          messages: [storedChat('user', 'What is the weather')],
        })
      })
      const before = result.current.messages.map(m => m.content)

      // The run settled between polls: applying a stored record here would
      // discharge the run-end obligation early. The non-OK case backs off.
      h.fetchMock.mockImplementation(async () => ({ ok: false, status: 503, json: async () => ({}) }))
      await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
      expect(result.current.messages.map(m => m.content)).toEqual(before)

      h.fetchMock.mockImplementation(async () => ({ ok: true, json: async () => ({ id: 'conv-1', in_flight: false, metadata: {}, messages: [] }) }))
      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(result.current.messages.map(m => m.content)).toEqual(before)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a settled record holding the run\'s final rows is not applied mid-run', async () => {
    // The `in_flight` guard exists so a record the run has settled belongs to
    // the run-end path, not to the poll. A settled record whose transcript
    // holds rows the view lacks must be refused whole: appending them here
    // would discharge the run-end obligation against a record that was
    // already final. (An empty transcript cannot pin this -- alignment would
    // refuse it anyway -- so this record genuinely aligns.)
    vi.useFakeTimers()
    try {
      h.fetchMock.mockImplementation(async (url) => {
        if (String(url).includes('/api/conversations/conv-1')) {
          return {
            ok: true,
            json: async () => ({
              id: 'conv-1',
              in_flight: false,
              metadata: {},
              streaming_text: '',
              messages: [
                storedChat('user', 'What is the weather'),
                storedToolCall('call-bash', 'basic_fns_bash'),
                storedChat('assistant', 'Done sleeping'),
              ],
            }),
          }
        }
        return { ok: false, status: 404, json: async () => ({}) }
      })

      const { result } = renderChat()
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ run_id: 'run-1', conversation_id: 'conv-1', status: 'running', created_at: 1 }],
      })
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1',
          in_flight: true,
          run_id: 'run-1',
          streaming_text: 'Working',
          metadata: {},
          messages: [storedChat('user', 'What is the weather')],
        })
      })
      const before = result.current.messages
      const restoreCountBefore = h.sendMessage.mock.calls.filter(c => c[0].type === 'restore_conversation').length

      await act(async () => { await vi.advanceTimersByTimeAsync(7000) })

      // Neither the tool row nor the final answer may reach the view, and the
      // final transcript must not be re-seeded into the backend session.
      expect(result.current.messages.map(m => m.content || '')).toEqual(before.map(m => m.content || ''))
      expect(result.current.messages.some(m => m.type === 'tool_call')).toBe(false)
      expect(h.sendMessage.mock.calls.filter(c => c[0].type === 'restore_conversation').length).toBe(restoreCountBefore)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a poll response that resolves after the run went terminal leaves the view untouched', async () => {
    // The run can reach a terminal state while a poll is in the air. The
    // record in hand is the run's session as it stood at fetch time -- a
    // snapshot, not the final transcript -- and the run-end path is already
    // working from the tracker's terminal status. The poll must re-check the
    // run state after the await instead of appending the snapshot.
    vi.useFakeTimers()
    try {
      let resolvePoll
      h.fetchMock.mockImplementation((url) => {
        if (String(url).includes('/api/conversations/conv-1')) {
          return new Promise((resolve) => {
            resolvePoll = () => resolve({
              ok: true,
              json: async () => liveRecord(storedToolCall('call-late', 'atlas_sleep'), 'late segment'),
            })
          })
        }
        return Promise.resolve({ ok: true, json: async () => ({}) })
      })

      const { result } = renderChat()
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ run_id: 'run-1', conversation_id: 'conv-1', status: 'running', created_at: 1 }],
      })
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1',
          in_flight: true,
          run_id: 'run-1',
          streaming_text: 'Working',
          metadata: {},
          messages: [storedChat('user', 'What is the weather')],
        })
      })
      // The immediate poll is in the air when the run goes terminal.
      expect(typeof resolvePoll).toBe('function')
      dispatchFrame({ type: 'run_status', run: { run_id: 'run-1', conversation_id: 'conv-1', status: 'completed' } })

      await act(async () => {
        resolvePoll()
        await Promise.resolve()
        await Promise.resolve()
      })

      const contents = result.current.messages.map(m => m.content || '')
      expect(contents.some(c => c.includes('late segment'))).toBe(false)
      expect(result.current.messages.some(m => m.tool_call_id === 'call-late')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a stale response for the conversation the user left cannot touch the new view', async () => {
    vi.useFakeTimers()
    try {
      let resolveOld
      h.fetchMock.mockImplementation((url) => {
        if (String(url).includes('/api/conversations/conv-1')) {
          return new Promise((resolve) => {
            resolveOld = () => resolve({
              ok: true,
              json: async () => liveRecord(storedToolCall('call-late', 'atlas_sleep'), 'conv-1 late segment'),
            })
          })
        }
        return Promise.resolve({ ok: true, json: async () => ({}) })
      })

      const { result } = renderChat()
      dispatchFrame({
        type: 'runs_snapshot',
        runs: [{ run_id: 'run-1', conversation_id: 'conv-1', status: 'running', created_at: 1 }],
      })
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-1',
          in_flight: true,
          run_id: 'run-1',
          streaming_text: 'conv-1 partial',
          metadata: {},
          messages: [storedChat('user', 'What is the weather')],
        })
      })
      // The immediate poll is now in flight against conv-1.
      expect(typeof resolveOld).toBe('function')

      // The user switches to another conversation before it resolves.
      await act(async () => {
        await result.current.loadSavedConversation({
          id: 'conv-2',
          in_flight: true,
          run_id: 'run-2',
          streaming_text: 'conv-2 partial',
          metadata: {},
          messages: [storedChat('user', 'A different question')],
        })
      })

      await act(async () => {
        resolveOld()
        await Promise.resolve()
        await Promise.resolve()
      })

      // The stale conv-1 text and tool row must not appear in conv-2's view.
      const contents = result.current.messages.map(m => m.content || '')
      expect(contents.some(c => c.includes('conv-1 late'))).toBe(false)
      expect(result.current.messages.some(m => m.tool_call_id === 'call-late')).toBe(false)
      const bubble = result.current.messages.find(m => m._streaming)
      expect(bubble.content).toBe('conv-2 partial')
    } finally {
      vi.useRealTimers()
    }
  })
})

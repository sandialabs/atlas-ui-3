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
  saveLocalConv: vi.fn(() => Promise.resolve()),
  saveMode: 'server',
  fetchMock: vi.fn(),
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
  h.saveMode = 'server'
  h.saveLocalConv.mockImplementation(() => Promise.resolve())
  vi.stubGlobal('fetch', h.fetchMock)
  h.fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) })
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
})

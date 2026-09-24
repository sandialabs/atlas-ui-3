/**
 * Background auto-approve and the joined-run reload fallback (issue #884
 * follow-up, #956 review).
 *
 * The real ChatProvider with leaf hooks stubbed (same harness as
 * agent-mode-payload-gating.test.jsx): the websocket handler the provider
 * registers is captured, and run frames are delivered to it directly.
 *
 * - Auto-approve answers a background run's tool approval request when the
 *   setting is on, never when it is off or the tool is admin-pinned.
 * - It does not answer for a conversation the model launched (atlas_launch
 *   child run): those arguments are model-chosen, and the on/off toggle must
 *   not silently broaden to approving them unattended.
 * - A failed send surfaces as a toast instead of leaving the run parked
 *   silently.
 * - A conversation opened while its run was executing reloads from the store
 *   once the run ends, even when the save event was never delivered (the
 *   grace-period fallback).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  toastInfo: vi.fn(),
  settings: {},
  handlers: [],
}))

vi.mock('../contexts/WSContext', () => ({
  useWS: () => ({
    sendMessage: h.sendMessage,
    isConnected: true,
    // Capture every handler the provider registers so tests can deliver
    // websocket frames to it directly.
    addMessageHandler: (handler) => {
      h.handlers.push(handler)
      return () => {
        h.handlers = h.handlers.filter(x => x !== handler)
      }
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
    configReady: false,
    features: { chat_history: true },
    prompts: [],
    appName: 'Atlas',
    isInAdminGroup: false,
    fileExtraction: {},
    setIsCanvasOpen: vi.fn(),
    agentMaxStepsLimit: 50,
    agentCeilingConfirmed: true,
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
    }),
  }
})

vi.mock('../hooks/useUserPrompts', () => ({ useUserPrompts: () => ({ prompts: [] }) }))

vi.mock('../hooks/chat/useAgentMode', () => ({
  useAgentMode: () => ({
    agentModeEnabled: false,
    agentModeAvailable: true,
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

vi.mock('../hooks/useSettings', () => ({
  useSettings: () => ({ settings: h.settings, updateSettings: vi.fn() }),
}))

vi.mock('../hooks/chat/usePersistentState', () => ({
  usePersistentState: (_key, initial) => [initial, vi.fn()],
}))

import { ChatProvider, useChat } from '../contexts/ChatContext'

const wrapper = ({ children }) => <ChatProvider>{children}</ChatProvider>
const renderChat = () => renderHook(() => useChat(), { wrapper })

// The raw frame exactly as the server tags it; the websocket handler does
// the background_activity wrapping, as it does in production.
const approvalFrame = (overrides = {}) => ({
  type: 'tool_approval_request',
  tool_call_id: 't1',
  tool_name: 'some_tool',
  arguments: { value: 'x' },
  run_id: 'r1',
  conversation_id: 'conv-1',
  ...overrides,
})

// Mount-time traffic (the list_runs request) must not count as an answer.
const approvalSends = () =>
  h.sendMessage.mock.calls.filter(c => c[0]?.type === 'tool_approval_response')

const runStarted = (overrides = {}) => ({
  type: 'run_started',
  run_id: 'r1',
  conversation_id: 'conv-1',
  title: 'Long task',
  ...overrides,
})

const runHandler = () => {
  const handler = [...h.handlers].reverse().find(x => typeof x === 'function')
  if (!handler) throw new Error('no websocket handler registered')
  return handler
}

beforeEach(() => {
  vi.clearAllMocks()
  h.handlers = []
  h.sendMessage.mockImplementation(() => true)
  h.settings = { autoApproveTools: true }
})

describe('background auto-approve of tool approvals', () => {
  it('answers an off-screen run when the setting is on', () => {
    renderChat()
    const handler = runHandler()

    act(() => handler(approvalFrame()))

    expect(approvalSends()).toHaveLength(1)
    expect(approvalSends()[0][0]).toEqual(
      expect.objectContaining({
        type: 'tool_approval_response',
        tool_call_id: 't1',
        approved: true,
        conversation_id: 'conv-1',
      })
    )
    expect(h.toastError).not.toHaveBeenCalled()
  })

  it('answers nothing when the setting is off', () => {
    h.settings = { autoApproveTools: false }
    renderChat()
    const handler = runHandler()

    act(() => handler(approvalFrame()))

    expect(approvalSends()).toHaveLength(0)
  })

  it('answers nothing for an admin-pinned tool', () => {
    renderChat()
    const handler = runHandler()

    act(() => handler(approvalFrame({ admin_required: true })))

    expect(approvalSends()).toHaveLength(0)
  })

  it('answers nothing for a conversation the model launched', () => {
    const { result } = renderChat()
    const handler = runHandler()

    // The tab learns the run is a child, then the user navigates away --
    // exactly the state in which the background answer would fire.
    act(() => handler(runStarted({ parent_run_id: 'parent-1' })))
    act(() => { result.current.loadSavedConversation({ id: 'other', messages: [] }) })
    act(() => handler(approvalFrame()))

    expect(approvalSends()).toHaveLength(0)
  })

  it('still answers a user-started run that merely carries no parent info', () => {
    const { result } = renderChat()
    const handler = runHandler()

    act(() => handler(runStarted()))
    act(() => { result.current.loadSavedConversation({ id: 'other', messages: [] }) })
    act(() => handler(approvalFrame()))

    expect(approvalSends()).toHaveLength(1)
  })

  it('toasts when the answer cannot be sent', () => {
    h.sendMessage.mockImplementation(() => false)
    renderChat()
    const handler = runHandler()

    act(() => handler(approvalFrame()))

    expect(approvalSends()).toHaveLength(1)
    expect(h.toastError).toHaveBeenCalledTimes(1)
  })
})

describe('joined-run reload fallback (no conversation_saved frame)', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('reloads the conversation after the grace period once its run ends', () => {
    vi.useFakeTimers()
    const { result } = renderChat()
    const handler = runHandler()

    // The run starts, and the user opens the (unsaved) conversation.
    act(() => handler(runStarted()))
    act(() => {
      result.current.loadSavedConversation({
        id: 'conv-1',
        messages: [{ role: 'user', content: 'hello', message_type: 'chat' }],
      })
    })
    expect(result.current.runEndedConversationId).toBeNull()

    // The run ends. The save frame never arrives -- the fallback timer is
    // what reloads the transcript.
    act(() => handler({
      type: 'run_status',
      run: {
        run_id: 'r1',
        conversation_id: 'conv-1',
        status: 'completed',
        created_at: 1,
        updated_at: 2,
      },
    }))
    act(() => { vi.advanceTimersByTime(2600) })

    expect(result.current.runEndedConversationId).toBe('conv-1')
  })

  it('does not reload a conversation whose run is still going', () => {
    vi.useFakeTimers()
    const { result } = renderChat()
    const handler = runHandler()

    act(() => handler(runStarted()))
    act(() => {
      result.current.loadSavedConversation({
        id: 'conv-1',
        messages: [{ role: 'user', content: 'hello', message_type: 'chat' }],
      })
    })

    act(() => handler({
      type: 'run_status',
      run: {
        run_id: 'r1',
        conversation_id: 'conv-1',
        status: 'running',
        created_at: 1,
        updated_at: 2,
      },
    }))
    act(() => { vi.advanceTimersByTime(2600) })

    expect(result.current.runEndedConversationId).toBeNull()
  })
})

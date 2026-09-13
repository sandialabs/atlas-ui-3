/**
 * The agent_mode wire flag and the max-steps ceiling (issue #849).
 *
 * The real ChatProvider must gate the outgoing agent_mode flag on feature
 * availability: agent mode now defaults to on, so a deployment with the
 * feature disabled (toggle hidden) must not have the default preference leak
 * a live agent_mode onto the wire. Also pins the agent_max_steps payload
 * clamp against the admin-configured ceiling.
 *
 * Harness mirrors agent-mode-allows-no-tools.test.jsx: the real provider with
 * leaf hooks stubbed.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  toastInfo: vi.fn(),
  selectedTools: new Set(['server_tool1']),
  ragEnabled: false,
  agentMode: { enabled: true, available: true },
  settings: { maxIterations: 10, llmTemperature: 0.7 },
  agentMaxStepsLimit: 50,
  agentCeilingConfirmed: true,
}))

vi.mock('../contexts/WSContext', () => ({
  useWS: () => ({
    sendMessage: h.sendMessage,
    isConnected: true,
    addMessageHandler: () => () => {},
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
    features: {},
    prompts: [],
    appName: 'Atlas',
    isInAdminGroup: false,
    fileExtraction: {},
    setIsCanvasOpen: vi.fn(),
    agentMaxStepsLimit: h.agentMaxStepsLimit,
    agentCeilingConfirmed: h.agentCeilingConfirmed,
  }),
}))

vi.mock('../hooks/chat/useSelections', async (importActual) => {
  const actual = await importActual()
  return {
    ...actual,
    useSelections: () => ({
      selectedTools: h.selectedTools,
      selectedPrompts: new Set(),
      activePrompts: [],
      activePromptKey: null,
      clearActivePrompt: vi.fn(),
      selectedDataSources: new Set(),
      ragEnabled: h.ragEnabled,
      toggleRagEnabled: vi.fn(),
      complianceLevelFilter: '',
    }),
  }
})

vi.mock('../hooks/useUserPrompts', () => ({ useUserPrompts: () => ({ prompts: [] }) }))

vi.mock('../hooks/chat/useAgentMode', () => ({
  useAgentMode: () => ({
    agentModeEnabled: h.agentMode.enabled,
    agentModeAvailable: h.agentMode.available,
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

beforeEach(() => {
  vi.clearAllMocks()
  h.sendMessage.mockImplementation(() => true)
  h.selectedTools = new Set(['server_tool1'])
  h.agentMode = { enabled: true, available: true }
  h.settings = { maxIterations: 10, llmTemperature: 0.7 }
  h.agentMaxStepsLimit = 50
  h.agentCeilingConfirmed = true
})

describe('agent_mode wire flag gating (issue #849)', () => {
  it('sends agent_mode true when available and enabled', () => {
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('do a task') })

    const payload = h.sendMessage.mock.calls[0][0]
    expect(payload.agent_mode).toBe(true)
  })

  it('does not leak agent_mode true when the feature is unavailable', () => {
    h.agentMode = { enabled: true, available: false }
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('do a task') })

    const payload = h.sendMessage.mock.calls[0][0]
    expect(payload.agent_mode).toBe(false)
  })
})

describe('agent_max_steps payload ceiling (issue #849)', () => {
  it('clamps a user setting above the admin-configured limit', () => {
    h.agentMaxStepsLimit = 5
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('do a task') })

    const payload = h.sendMessage.mock.calls[0][0]
    expect(payload.agent_max_steps).toBe(5)
  })

  it('keeps a user setting at or below the limit', () => {
    h.agentMaxStepsLimit = 30
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('do a task') })

    const payload = h.sendMessage.mock.calls[0][0]
    expect(payload.agent_max_steps).toBe(10)
  })

  it('sends the raw preference before the ceiling is confirmed (issue #849 review)', () => {
    // A stale cache (or the fallback 10) can sit below the deployment's real
    // ceiling, and the server can only clamp down: clamping an early turn
    // would silently shrink the user's intended 30 to 10. The authoritative
    // server clamp bounds the unclamped value instead.
    h.agentCeilingConfirmed = false
    h.agentMaxStepsLimit = 5
    h.settings = { maxIterations: 30, llmTemperature: 0.7 }
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('do a task') })

    const payload = h.sendMessage.mock.calls[0][0]
    expect(payload.agent_max_steps).toBe(30)
  })
})
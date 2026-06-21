/**
 * Guard: Agent mode needs at least one selected tool.
 *
 * With agent mode on but no tools selected, the agent loop has nothing to call
 * and tool-seeking prompts can drive the model to emit a tool call the provider
 * rejects ("tool_choice is none, but model called a tool"), which surfaces as an
 * empty/failed response. The real ChatProvider must block the send (returning
 * false, no WS frame) and toast the user instead. When a tool IS selected, the
 * send goes through with agent_mode: true.
 *
 * The component suites mock ChatContext, so the real `sendChatMessage` never
 * runs there -- this renders the *real* provider with leaf hooks stubbed.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  selectedTools: new Set(),
}))

vi.mock('../contexts/WSContext', () => ({
  useWS: () => ({
    sendMessage: h.sendMessage,
    isConnected: true,
    addMessageHandler: () => () => {},
  }),
}))

vi.mock('../components/ui/toastContext', () => ({
  useToast: () => ({ error: h.toastError, success: h.toastSuccess, info: vi.fn() }),
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
      ragEnabled: false,
      toggleRagEnabled: vi.fn(),
      complianceLevelFilter: '',
    }),
  }
})

vi.mock('../hooks/useUserPrompts', () => ({ useUserPrompts: () => ({ prompts: [] }) }))

vi.mock('../hooks/chat/useAgentMode', () => ({
  useAgentMode: () => ({
    agentModeEnabled: true,
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
  useSettings: () => ({ settings: {}, updateSettings: vi.fn() }),
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
  h.selectedTools = new Set()
})

describe('Agent mode requires a tool (real ChatProvider)', () => {
  it('blocks the send and toasts when agent mode is on with no tools', () => {
    const { result } = renderChat()
    let ret
    act(() => { ret = result.current.sendChatMessage('do a task') })

    expect(ret).toBe(false)
    expect(h.sendMessage).not.toHaveBeenCalled()
    expect(h.toastError).toHaveBeenCalledWith(
      expect.stringMatching(/agent mode needs at least one tool/i)
    )
  })

  it('allows the send with agent_mode: true once a tool is selected', () => {
    h.selectedTools = new Set(['server_tool1'])
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('do a task') })

    expect(h.sendMessage).toHaveBeenCalledTimes(1)
    const payload = h.sendMessage.mock.calls[0][0]
    expect(payload.agent_mode).toBe(true)
    expect(payload.selected_tools).toEqual(['server_tool1'])
  })
})

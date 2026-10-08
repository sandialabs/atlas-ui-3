/**
 * The open conversation keeps the level it was recorded at (issue #1042):
 * the real ChatProvider sends its level on restore, and refuses a send after
 * a level switch before the prompt reaches the transcript (where the local
 * autosave would persist it under the recorded level). Same harness as the
 * #957 in-flight reopen suite, with compliance levels enabled.
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
  level: 'UUR',
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
    features: { workspaces: h.workspacesEnabled, compliance_levels: true },
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
      complianceLevelFilter: h.level,
      addTools: vi.fn(),
      removeTools: vi.fn(),
      addPrompts: vi.fn(),
      removePrompts: vi.fn(),
      addDataSources: vi.fn(),
      clearDataSources: vi.fn(),
      removeDataSources: vi.fn(),
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

vi.mock('../hooks/chat/useComplianceLevels', () => ({
  useComplianceLevels: () => ({
    complianceLevels: [
      { name: 'UUR', aliases: [], allowed_with: ['UUR'] },
      { name: 'CUI', aliases: [], allowed_with: ['CUI'] },
    ],
    complianceMode: 'explicit_allowlist',
    defaultComplianceLevel: null,
  }),
}))

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


const cuiConversation = {
  id: 'conv-cui',
  messages: [{ role: 'user', content: 'synthetic placeholder', message_type: 'chat', timestamp: '2026-01-01T00:00:00Z' }],
  metadata: { data_classification: 'CUI' },
  data_classification: 'CUI',
  data_classification_state: 'classified',
}

const chatFrames = () => h.sendMessage.mock.calls.map(c => c[0]).filter(f => f.type === 'chat')

beforeEach(() => {
  vi.clearAllMocks()
  h.sendMessage.mockImplementation(() => true)
  h.saveMode = 'local'
})

describe('conversation classification in the chat context (issue #1042)', () => {
  it('sends the active level with the restore frame', () => {
    h.level = 'UUR'
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(cuiConversation) })
    const restore = h.sendMessage.mock.calls.map(c => c[0]).find(f => f.type === 'restore_conversation')
    expect(restore.compliance_level_filter).toBe('UUR')
  })

  it('refuses a send under another level without touching the transcript', () => {
    h.level = 'UUR'
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(cuiConversation) })
    const before = result.current.messages.length
    let sent
    act(() => { sent = result.current.sendChatMessage('second synthetic prompt') })
    expect(sent).toBe(false)
    expect(h.toastError).toHaveBeenCalledWith(expect.stringMatching(/saved under CUI/))
    expect(chatFrames()).toHaveLength(0)
    expect(result.current.messages).toHaveLength(before)
  })

  it('sends under the recorded level', () => {
    h.level = 'CUI'
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(cuiConversation) })
    act(() => { result.current.sendChatMessage('second synthetic prompt') })
    expect(chatFrames()).toHaveLength(1)
    expect(chatFrames()[0].compliance_level_filter).toBe('CUI')
  })
})

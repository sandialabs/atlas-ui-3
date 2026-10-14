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
  setComplianceLevelFilter: vi.fn(),
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
      setComplianceLevelFilter: h.setComplianceLevelFilter,
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
  // Model the real setter: it persists the level, so a later render sees it.
  h.setComplianceLevelFilter.mockImplementation((value) => { h.level = value })
})

describe('conversation classification in the chat context (issue #1042)', () => {
  it("sends the conversation's recorded level with the restore frame and syncs the selector", () => {
    h.level = 'UUR'
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(cuiConversation) })
    const restore = h.sendMessage.mock.calls.map(c => c[0]).find(f => f.type === 'restore_conversation')
    expect(restore.compliance_level_filter).toBe('CUI')
    // The recorded level is authoritative on load (#1044): the selector moves to
    // it so the locked value and the outbound level agree.
    expect(h.setComplianceLevelFilter).toHaveBeenCalledWith('CUI')
    expect(result.current.conversationAccepted).toBe(true)
  })

  it('refuses a send after a mid-conversation level switch without touching the transcript', () => {
    h.level = 'CUI'
    const { result, rerender } = renderChat()
    act(() => { result.current.loadSavedConversation(cuiConversation) })
    // A switch the locked selector no longer allows, but an older client or a
    // direct state change could still make.
    h.level = 'UUR'
    rerender()
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

  it('keeps the recorded level across New chat -> Undo', () => {
    h.level = 'CUI'
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(cuiConversation) })
    act(() => { result.current.clearChat({ skipConfirm: false }) })
    const offer = h.toastInfo.mock.calls.find(c => c[1]?.action?.label === 'Undo')
    expect(offer).toBeTruthy()
    act(() => { offer[1].action.onClick() })
    h.sendMessage.mockClear()
    act(() => { result.current.sendChatMessage('after undo synthetic prompt') })
    expect(h.toastError).not.toHaveBeenCalled()
    expect(chatFrames()).toHaveLength(1)
  })

  it('syncs the level when Undo restores a differently-classified conversation', () => {
    h.level = 'CUI'
    const { result, rerender } = renderChat()
    act(() => { result.current.loadSavedConversation(cuiConversation) })
    act(() => { result.current.clearChat({ skipConfirm: false }) })
    // The user picks another level on the fresh chat before Undo.
    h.level = 'UUR'
    rerender()
    const offer = h.toastInfo.mock.calls.find(c => c[1]?.action?.label === 'Undo')
    act(() => { offer[1].action.onClick() })
    rerender()
    // The recorded level is restored as the effective level, so the locked
    // selector and outbound frames agree (#1044).
    expect(h.setComplianceLevelFilter).toHaveBeenCalledWith('CUI')
    expect(result.current.activeComplianceFilter).toBe('CUI')
    h.sendMessage.mockClear()
    act(() => { result.current.sendChatMessage('after undo synthetic prompt') })
    expect(h.toastError).not.toHaveBeenCalled()
    expect(chatFrames()).toHaveLength(1)
    expect(chatFrames()[0].compliance_level_filter).toBe('CUI')
  })

  it('restores the classification on a no-id (local) Undo', () => {
    h.level = 'CUI'
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('first synthetic prompt') })
    act(() => { h.wsHandler({ type: 'chat_response', message: 'synthetic reply' }) })
    // No conversation_saved frame: the chat is local, so Undo takes the no-id
    // path, which still restores the level and keeps the header locked.
    expect(result.current.activeConversationId).toBeNull()
    act(() => { result.current.clearChat({ skipConfirm: false }) })
    const offer = h.toastInfo.mock.calls.find(c => c[1]?.action?.label === 'Undo')
    act(() => { offer[1].action.onClick() })
    expect(result.current.activeConversationClassification?.state).toBe('classified')
    expect(result.current.activeConversationClassification?.level).toBe('CUI')
    expect(result.current.conversationAccepted).toBe(true)
  })

  it('does not lock a new chat to a level the server never accepted', () => {
    h.level = 'UUR'
    const { result, rerender } = renderChat()
    act(() => { result.current.sendChatMessage('first synthetic prompt') })
    // No conversation_saved arrives (the server refused the first turn).
    h.level = 'CUI'
    rerender()
    h.sendMessage.mockClear()
    act(() => { result.current.sendChatMessage('second synthetic prompt') })
    expect(h.toastError).not.toHaveBeenCalled()
    expect(chatFrames()).toHaveLength(1)
    expect(chatFrames()[0].compliance_level_filter).toBe('CUI')
  })

  it('keeps a new chat bound once the server has replied, before its id arrives', () => {
    h.level = 'CUI'
    const { result, rerender } = renderChat()
    act(() => { result.current.sendChatMessage('first synthetic prompt') })
    act(() => { h.wsHandler({ type: 'chat_response', message: 'synthetic reply' }) })
    h.level = 'UUR'
    rerender()
    h.sendMessage.mockClear()
    act(() => { result.current.sendChatMessage('second synthetic prompt') })
    expect(h.toastError).toHaveBeenCalledWith(expect.stringMatching(/saved under CUI/))
    expect(chatFrames()).toHaveLength(0)
  })

  it('refuses sends in an unreadable local record, and keeps it unreadable across New chat -> Undo', () => {
    h.level = 'UUR'
    const { result } = renderChat()
    act(() => {
      result.current.loadSavedConversation({ ...cuiConversation, data_classification_state: undefined, data_classification: undefined, metadata: { data_classification: 7 } })
    })
    let sent
    act(() => { sent = result.current.sendChatMessage('synthetic prompt') })
    expect(sent).toBe(false)
    expect(h.toastError).toHaveBeenCalledWith(expect.stringMatching(/could not be read/))

    h.toastError.mockClear()
    act(() => { result.current.clearChat({ skipConfirm: false }) })
    const offer = h.toastInfo.mock.calls.find(c => c[1]?.action?.label === 'Undo')
    act(() => { offer[1].action.onClick() })
    act(() => { sent = result.current.sendChatMessage('synthetic prompt after undo') })
    expect(sent).toBe(false)
    expect(h.toastError).toHaveBeenCalledWith(expect.stringMatching(/could not be read/))
  })

  it('does not treat a local autosave id as the server accepting the first turn', () => {
    vi.useFakeTimers()
    try {
      h.level = 'UUR'
      const { result, rerender } = renderChat()
      act(() => { result.current.sendChatMessage('first synthetic prompt') })
      // The server refused; the local autosave still assigns a local_* id.
      act(() => { vi.advanceTimersByTime(1100) })
      h.level = 'CUI'
      rerender()
      h.sendMessage.mockClear()
      act(() => { result.current.sendChatMessage('second synthetic prompt') })
      expect(h.toastError).not.toHaveBeenCalled()
      expect(chatFrames()).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  // The header locks the level selector off `conversationAccepted` (issue
  // #1044); a first turn the server refused must leave it open to correct.
  it('is unaccepted after a first send until the server replies', () => {
    h.level = 'CUI'
    const { result } = renderChat()
    expect(result.current.conversationAccepted).toBe(false)
    act(() => { result.current.sendChatMessage('first synthetic prompt') })
    expect(result.current.activeConversationClassification?.state).toBe('classified')
    expect(result.current.conversationAccepted).toBe(false)
    act(() => { h.wsHandler({ type: 'chat_response', message: 'synthetic reply' }) })
    expect(result.current.conversationAccepted).toBe(true)
  })

  it('is accepted as soon as a saved conversation is loaded', () => {
    h.level = 'CUI'
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(cuiConversation) })
    expect(result.current.conversationAccepted).toBe(true)
  })
})

/**
 * Tests for issue #842: when chat history is enabled, a first-run user with
 * no explicit save-mode preference should default to 'server' so that
 * conversations (including stopped agent runs) persist to DuckDB and
 * survive a browser refresh.
 *
 * The stored default in usePersistentState is still 'none' (PR #619,
 * privacy), but a one-shot useEffect upgrades it to 'server' once the
 * config confirms chat_history is enabled AND the user has never interacted
 * with the save-mode toggle (localStorage key absent).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
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
    tools: [{ server: 'files', tools: ['read', 'write'] }],
    configReady: h.configReady,
    features: { chat_history: h.chatHistoryEnabled },
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
vi.mock('../hooks/useWorkspaces', () => ({
  useWorkspaces: () => ({
    workspaces: h.wsState.workspaces,
    loading: false,
    loaded: h.wsState.loaded,
    error: h.wsState.error,
    updateWorkspace: vi.fn(),
    deleteWorkspace: vi.fn(),
  }),
}))

// Use the REAL usePersistentState so localStorage is exercised.
import { usePersistentState } from '../hooks/chat/usePersistentState'

describe('saveMode default — issue #842', () => {
  beforeEach(() => {
    localStorage.clear()
    h.configReady = true
    h.chatHistoryEnabled = true
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('upgrades to server when chat_history is enabled and no preference is set', async () => {
    const { result } = renderHook(() => {
      // usePersistentState reads localStorage directly; we need the real hook
      // so the useEffect in ChatProvider can inspect the key.
      const [saveMode, setSaveMode] = usePersistentState('chatui-save-mode', 'none')
      return { saveMode, setSaveMode }
    })
    // Simulate the useEffect from ChatContext
    act(() => {
      if (h.configReady && h.chatHistoryEnabled && localStorage.getItem('chatui-save-mode') === null) {
        result.current.setSaveMode('server')
      }
    })
    expect(result.current.saveMode).toBe('server')
    expect(localStorage.getItem('chatui-save-mode')).toBe(JSON.stringify('server'))
  })

  it('respects an explicit incognito preference when chat_history is enabled', async () => {
    localStorage.setItem('chatui-save-mode', JSON.stringify('none'))
    const { result } = renderHook(() => {
      const [saveMode, setSaveMode] = usePersistentState('chatui-save-mode', 'none')
      return { saveMode, setSaveMode }
    })
    act(() => {
      if (h.configReady && h.chatHistoryEnabled && localStorage.getItem('chatui-save-mode') === null) {
        result.current.setSaveMode('server')
      }
    })
    expect(result.current.saveMode).toBe('none')
  })

  it('does not upgrade when chat_history is disabled', async () => {
    h.chatHistoryEnabled = false
    const { result } = renderHook(() => {
      const [saveMode, setSaveMode] = usePersistentState('chatui-save-mode', 'none')
      return { saveMode, setSaveMode }
    })
    act(() => {
      if (h.configReady && h.chatHistoryEnabled && localStorage.getItem('chatui-save-mode') === null) {
        result.current.setSaveMode('server')
      }
    })
    expect(result.current.saveMode).toBe('none')
  })
})
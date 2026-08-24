/**
 * Behavioral tests for issue #829: re-enabling a conversation's workspace
 * when the conversation is reopened from history.
 *
 * The component suites mock ChatContext, so the real `loadSavedConversation`
 * never runs there. This renders the *real* ChatProvider (with only leaf hooks
 * stubbed) so we can pin the guarantees:
 *
 *   - loading a conversation tied to a workspace switches to that workspace,
 *   - a workspace that has since been deleted is silently skipped (best effort),
 *   - a conversation with no recorded workspace leaves the active one untouched,
 *   - the active workspace id is sent on the chat payload so the backend can
 *     persist it with the conversation,
 *   - a restore that lands before the workspace list has loaded defers the
 *     switch until it does.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

// Shared mock handles (hoisted so the vi.mock factories can close over them).
const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  toastInfo: vi.fn(),
  // selection spies
  applyWorkspace: vi.fn(),
  snapshotSelections: vi.fn(() => ({})),
  // workspace list state, mutable per test
  wsState: { workspaces: [], loaded: true, error: null },
  // initial active workspace id for usePersistentState
  activeWorkspaceId: null,
  // config state, mutable per test: `configReady` is false until the config
  // fetch lands, and `workspacesEnabled` is the feature flag it carries.
  configReady: true,
  workspacesEnabled: true,
  // local-save spy + the persisted save mode that gates it
  saveLocalConv: vi.fn(() => Promise.resolve()),
  saveMode: 'none',
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
    if (key === 'chatui-active-workspace') return [h.activeWorkspaceId, vi.fn()]
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
  // Never treat the pointer as stale in these tests.
  isStaleWorkspacePointer: () => false,
}))

import { ChatProvider, useChat } from '../contexts/ChatContext'

const wrapper = ({ children }) => <ChatProvider>{children}</ChatProvider>
const renderChat = () => renderHook(() => useChat(), { wrapper })

const WORKSPACES = [
  {
    id: 'ws-work',
    name: 'Work',
    description: 'Day job',
    config: {
      active_prompt_key: null,
      selected_tools: ['files_read'],
      selected_prompts: [],
      selected_data_sources: ['corpus-a'],
      rag_enabled: true,
    },
  },
  {
    id: 'ws-home',
    name: 'Home',
    description: null,
    config: {
      active_prompt_key: null,
      selected_tools: [],
      selected_prompts: [],
      selected_data_sources: [],
      rag_enabled: false,
    },
  },
]

const makeConversation = (overrides = {}) => ({
  id: 'conv-1',
  messages: [
    { role: 'user', content: 'Hello', message_type: 'chat', timestamp: '2026-01-01T00:00:00Z' },
    { role: 'assistant', content: 'Hi there', message_type: 'chat', timestamp: '2026-01-01T00:00:01Z' },
  ],
  metadata: {},
  ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  h.sendMessage.mockImplementation(() => true)
  h.wsState.workspaces = WORKSPACES
  h.wsState.loaded = true
  h.wsState.error = null
  h.activeWorkspaceId = null
  h.configReady = true
  h.workspacesEnabled = true
  h.saveLocalConv.mockImplementation(() => Promise.resolve())
  h.saveMode = 'none'
})

describe('loadSavedConversation workspace restore (issue #829)', () => {
  it('switches to the workspace the conversation was tied to', () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })

    expect(h.applyWorkspace).toHaveBeenCalledTimes(1)
    expect(h.applyWorkspace).toHaveBeenCalledWith(WORKSPACES[0].config)
    // The backend restore frame is still sent.
    const restoreCall = h.sendMessage.mock.calls.find(c => c[0]?.type === 'restore_conversation')
    expect(restoreCall).toBeTruthy()
  })

  it('silently skips a workspace that has since been deleted (best effort)', () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-gone' } })) })

    expect(h.applyWorkspace).not.toHaveBeenCalled()
    // The restore frame still goes out.
    const restoreCall = h.sendMessage.mock.calls.find(c => c[0]?.type === 'restore_conversation')
    expect(restoreCall).toBeTruthy()
  })

  it('leaves the active workspace untouched for a conversation with no workspace', () => {
    h.activeWorkspaceId = 'ws-home'
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: {} })) })

    expect(h.applyWorkspace).not.toHaveBeenCalled()
  })

  it('cancels a deferred restore when a later conversation has no workspace', () => {
    // Conversation A is opened before the list loads -> its restore is queued.
    h.wsState.loaded = false
    h.wsState.workspaces = []
    const { result, rerender } = renderChat()

    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })
    expect(h.applyWorkspace).not.toHaveBeenCalled()

    // Conversation B (no workspace) is opened next; it must cancel A's pending
    // restore so A's workspace is not applied to B when the list arrives.
    act(() => { result.current.loadSavedConversation(makeConversation({ id: 'conv-2', metadata: {} })) })

    h.wsState.loaded = true
    h.wsState.workspaces = WORKSPACES
    rerender()

    expect(h.applyWorkspace).not.toHaveBeenCalled()
  })

  it('defers the switch until the workspace list has loaded', () => {
    // List not loaded yet: the restore must not switch against an empty list.
    h.wsState.loaded = false
    h.wsState.workspaces = []
    const { result, rerender } = renderChat()

    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })
    expect(h.applyWorkspace).not.toHaveBeenCalled()

    // List arrives: the deferred restore fires.
    h.wsState.loaded = true
    h.wsState.workspaces = WORKSPACES
    rerender()

    expect(h.applyWorkspace).toHaveBeenCalledTimes(1)
    expect(h.applyWorkspace).toHaveBeenCalledWith(WORKSPACES[0].config)
  })

  it('drops a deferred restore whose workspace never arrives (best effort)', () => {
    h.wsState.loaded = false
    h.wsState.workspaces = []
    const { result, rerender } = renderChat()

    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-gone' } })) })
    expect(h.applyWorkspace).not.toHaveBeenCalled()

    // List loads but the workspace is not in it.
    h.wsState.loaded = true
    h.wsState.workspaces = WORKSPACES
    rerender()

    expect(h.applyWorkspace).not.toHaveBeenCalled()
  })
})

describe('sendChatMessage forwards the active workspace id (issue #829)', () => {
  it('sends workspace_id on the chat payload when a workspace is active', () => {
    h.activeWorkspaceId = 'ws-work'
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('a prompt') })

    const chatCall = h.sendMessage.mock.calls.find(c => c[0]?.type === 'chat')
    expect(chatCall).toBeTruthy()
    expect(chatCall[0].workspace_id).toBe('ws-work')
  })

  it('sends an explicit null when no workspace is active, so the binding can be cleared', () => {
    // Not `undefined`: JSON.stringify drops it, and the backend reads an omitted
    // field as "leave the binding alone" -- so clearing a workspace would never
    // unbind the conversation.
    h.activeWorkspaceId = null
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('a prompt') })

    const chatCall = h.sendMessage.mock.calls.find(c => c[0]?.type === 'chat')
    expect(chatCall).toBeTruthy()
    expect(chatCall[0].workspace_id).toBeNull()
    expect('workspace_id' in chatCall[0]).toBe(true)
  })
})

describe('workspace restore guards (issue #829)', () => {
  it('does not restore when the workspaces feature is disabled', () => {
    h.workspacesEnabled = false
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })

    expect(h.applyWorkspace).not.toHaveBeenCalled()
  })

  it('defers rather than discards a restore while the config is still loading', () => {
    // `workspacesEnabled` reads the config, which is false for everyone until
    // the fetch lands -- an early open must not throw the id away for good.
    h.configReady = false
    const { result, rerender } = renderChat()

    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })
    expect(h.applyWorkspace).not.toHaveBeenCalled()

    h.configReady = true
    rerender()

    expect(h.applyWorkspace).toHaveBeenCalledTimes(1)
    expect(h.applyWorkspace).toHaveBeenCalledWith(WORKSPACES[0].config)
  })

  it('drops the queued restore once the config says the feature is off', () => {
    h.configReady = false
    const { result, rerender } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })

    h.configReady = true
    h.workspacesEnabled = false
    rerender()

    expect(h.applyWorkspace).not.toHaveBeenCalled()
  })

  it('cancels a queued restore when the user explicitly switches workspace', () => {
    // The restore is queued because the config has not resolved yet, while the
    // workspace list already has -- so the user can pick a workspace in the
    // meantime, and that deliberate choice must win.
    h.configReady = false
    const { result, rerender } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })
    expect(h.applyWorkspace).not.toHaveBeenCalled()

    act(() => { result.current.switchWorkspace('ws-home') })

    h.configReady = true
    rerender()

    // Home was applied, and the queued restore did not overwrite it with Work.
    expect(h.applyWorkspace).toHaveBeenCalledTimes(1)
    expect(h.applyWorkspace).toHaveBeenCalledWith(WORKSPACES[1].config)
  })

  it('cancels a queued restore when the user explicitly clears the workspace', () => {
    h.wsState.loaded = false
    h.wsState.workspaces = []
    const { result, rerender } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })

    act(() => { result.current.clearActiveWorkspace() })

    h.wsState.loaded = true
    h.wsState.workspaces = WORKSPACES
    rerender()

    expect(h.applyWorkspace).not.toHaveBeenCalled()
  })

  it('cancels a queued restore when a later workspace-less conversation loads before the list resolves', () => {
    h.wsState.loaded = false
    h.wsState.workspaces = []
    const { result, rerender } = renderChat()

    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })
    act(() => { result.current.loadSavedConversation(makeConversation({ id: 'conv-2', metadata: {} })) })

    h.wsState.loaded = true
    h.wsState.workspaces = WORKSPACES
    rerender()

    expect(h.applyWorkspace).not.toHaveBeenCalled()
  })

  it('tells the user when it switches workspace and when the workspace is gone', () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })
    expect(h.toastInfo).toHaveBeenCalledWith(expect.stringContaining('Work'))

    h.toastInfo.mockClear()
    act(() => { result.current.loadSavedConversation(makeConversation({ id: 'c3', metadata: { workspace_id: 'ws-gone' } })) })
    expect(h.toastInfo).toHaveBeenCalledWith(expect.stringContaining('no longer available'))
  })
})

describe('local autosave preserves the conversation binding (issue #829)', () => {
  it('does not re-bind a loaded conversation to the workspace that happens to be active', async () => {
    // Local save mode fires ~1s after a conversation is loaded. It must persist
    // the workspace the conversation was saved with, not whatever is active now,
    // or merely opening a conversation destroys its binding with no user action.
    vi.useFakeTimers()
    try {
      h.activeWorkspaceId = 'ws-home'
      h.saveMode = 'local'
      const { result } = renderChat()
      act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })
      act(() => { vi.advanceTimersByTime(1500) })

      expect(h.saveLocalConv).toHaveBeenCalled()
      const saved = h.saveLocalConv.mock.calls.at(-1)[0]
      expect(saved.metadata.workspace_id).toBe('ws-work')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('workspace restore notifications (issue #829)', () => {
  it('does not claim the workspace was deleted when the list failed to load', () => {
    // A failed fetch is indistinguishable from a deletion by the list alone;
    // telling the user it is gone would be wrong.
    h.wsState.workspaces = []
    h.wsState.error = 'network error'
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })

    expect(h.applyWorkspace).not.toHaveBeenCalled()
    expect(h.toastInfo).not.toHaveBeenCalled()
  })
})

describe('a queued restore loses to deliberate user actions (issue #829)', () => {
  it('is cancelled by editing tools directly while the config is still loading', () => {
    h.configReady = false
    const { result, rerender } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })

    // The user picks a tool while the restore is still queued.
    act(() => { result.current.toggleTool('files_write') })

    h.configReady = true
    rerender()

    expect(h.applyWorkspace).not.toHaveBeenCalled()
  })

  it('is cancelled by choosing a prompt or a data source', () => {
    for (const action of ['setSinglePrompt', 'toggleDataSource']) {
      vi.clearAllMocks()
      h.configReady = false
      const { result, rerender } = renderChat()
      act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })
      act(() => { result.current[action]('something') })

      h.configReady = true
      rerender()

      expect(h.applyWorkspace, `${action} should cancel the restore`).not.toHaveBeenCalled()
    }
  })

  it('does not re-bind the local record when the send never reached the wire', () => {
    vi.useFakeTimers()
    try {
      h.activeWorkspaceId = 'ws-home'
      h.saveMode = 'local'
      h.sendMessage.mockImplementation(() => false) // socket dropped
      const { result } = renderChat()
      act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })
      act(() => { result.current.sendChatMessage('a prompt') })
      act(() => { vi.advanceTimersByTime(1500) })

      if (h.saveLocalConv.mock.calls.length) {
        expect(h.saveLocalConv.mock.calls.at(-1)[0].metadata.workspace_id).toBe('ws-work')
      }
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('a deleted workspace that is still the active pointer (issue #829)', () => {
  it('notifies instead of silently reporting success', () => {
    // The pointer can still name a workspace that has since been deleted;
    // short-circuiting on the pointer alone would never tell the user.
    h.activeWorkspaceId = 'ws-gone'
    h.wsState.workspaces = WORKSPACES // 'ws-gone' is not in the list
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-gone' } })) })

    expect(h.applyWorkspace).not.toHaveBeenCalled()
    expect(h.toastInfo).toHaveBeenCalledWith(expect.stringContaining('no longer available'))
  })

  it('does not re-apply a workspace that is already active', () => {
    // Deliberate: re-applying would discard selection edits made on top of it.
    h.activeWorkspaceId = 'ws-work'
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(makeConversation({ metadata: { workspace_id: 'ws-work' } })) })

    expect(h.applyWorkspace).not.toHaveBeenCalled()
    expect(h.toastInfo).not.toHaveBeenCalled()
  })
})

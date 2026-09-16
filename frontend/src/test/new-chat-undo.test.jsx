/**
 * "New Chat" friction + Undo (mobile / in-car usability).
 *
 * Renders the real ChatProvider so the actual clearChat runs.
 *
 * Guarantees pinned here:
 *   - Starting a new chat over an existing transcript no longer raises a
 *     blocking window.confirm. It clears immediately and offers Undo.
 *   - The Undo action restores the transcript AND re-seeds the backend via
 *     restore_conversation, so the model still has the prior context.
 *   - Undo never sends a fabricated conversation id: the backend rejects any
 *     id its repository does not know, so a made-up one would produce an error
 *     frame and no re-seed while the UI showed a "successful" restore. With no
 *     real id (incognito, the default) Undo restores locally and says in the
 *     timeline that the context is gone.
 *   - The offer is retired the moment the replacement chat is touched, so Undo
 *     can never discard an exchange that is not saved anywhere.
 *
 * The confirm that survives -- an untracked reply still generating -- is
 * covered by new-chat-stops-generation.test.js.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

// Shared mock handles (hoisted so the vi.mock factories can close over them).
const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  toastInfo: vi.fn((() => { let n = 0; return () => ++n })()),
  toastDismiss: vi.fn(),
  // selection spies
  applyWorkspace: vi.fn(),
  snapshotSelections: vi.fn(() => ({})),
  // workspace list state, mutable per test
  wsState: { workspaces: [], loaded: true, error: null },
  // initial active workspace id for usePersistentState, plus the setter, so
  // tests can assert the header pointer actually moves and not merely that the
  // selections were applied.
  activeWorkspaceId: null,
  setActiveWorkspaceId: vi.fn(),
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
  useToast: () => ({
    error: h.toastError,
    success: h.toastSuccess,
    info: h.toastInfo,
    dismiss: h.toastDismiss,
  }),
}))

vi.mock('../hooks/chat/useChatConfig', () => ({
  useChatConfig: () => ({
    currentModel: 'test-model',
    user: 'tester@example.com',
    ragServers: [],
    // Needed by the bulk select/deselect helpers.
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
  // Never treat the pointer as stale in these tests.
  isStaleWorkspacePointer: () => false,
}))

import { ChatProvider, useChat } from '../contexts/ChatContext'

const wrapper = ({ children }) => <ChatProvider>{children}</ChatProvider>
const renderChat = () => renderHook(() => useChat(), { wrapper })

const CONVERSATION = {
  id: 'conv-mobile-1',
  messages: [
    { role: 'user', content: 'What is the weather', message_type: 'chat', timestamp: '2026-01-01T00:00:00Z' },
    { role: 'assistant', content: 'Clear skies', message_type: 'chat', timestamp: '2026-01-01T00:00:01Z' },
  ],
  metadata: {},
}

// Grab the action the Undo toast was pushed with.
const lastUndoAction = () => {
  const call = [...h.toastInfo.mock.calls].reverse().find(c => c[1]?.action?.label === 'Undo')
  return call?.[1]?.action
}

const restoreFrames = () =>
  h.sendMessage.mock.calls.map(c => c[0]).filter(m => m.type === 'restore_conversation')

beforeEach(() => {
  vi.clearAllMocks()
  h.sendMessage.mockImplementation(() => true)
  h.wsState.workspaces = []
  h.wsState.loaded = true
  h.wsState.error = null
  h.configReady = true
  h.workspacesEnabled = false
  h.activeWorkspaceId = null
  h.saveMode = 'none'
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('New Chat over an existing transcript', () => {
  it('clears without a blocking confirm dialog', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { result } = renderChat()

    await act(async () => { await result.current.loadSavedConversation(CONVERSATION) })
    expect(result.current.messages.length).toBe(2)

    let cleared
    await act(async () => { cleared = result.current.clearChat() })

    expect(confirmSpy).not.toHaveBeenCalled()
    expect(cleared).toBe(true)
    expect(result.current.messages.length).toBe(0)
    expect(h.sendMessage.mock.calls.map(c => c[0].type)).toContain('reset_session')
  })

  it('offers an Undo action that restores the transcript and the backend context', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { result } = renderChat()

    await act(async () => { await result.current.loadSavedConversation(CONVERSATION) })
    await act(async () => { result.current.clearChat() })

    const action = lastUndoAction()
    expect(action).toBeTruthy()

    h.sendMessage.mockClear()
    await act(async () => { await action.onClick() })

    expect(result.current.messages.map(m => m.content)).toEqual([
      'What is the weather',
      'Clear skies',
    ])
    const restore = h.sendMessage.mock.calls.map(c => c[0]).find(m => m.type === 'restore_conversation')
    expect(restore).toBeTruthy()
    expect(restore.messages).toEqual([
      { role: 'user', content: 'What is the weather' },
      { role: 'assistant', content: 'Clear skies' },
    ])
  })

  it('does not offer Undo when there was nothing to lose', async () => {
    const { result } = renderChat()
    await act(async () => { result.current.clearChat() })
    expect(lastUndoAction()).toBeUndefined()
  })

  it('skipConfirm suppresses the Undo toast too', async () => {
    const { result } = renderChat()
    await act(async () => { await result.current.loadSavedConversation(CONVERSATION) })
    await act(async () => { result.current.clearChat({ skipConfirm: true }) })
    expect(lastUndoAction()).toBeUndefined()
  })
})

describe('Undo never fabricates a conversation id', () => {
  it('skips the backend re-seed when the cleared chat was never persisted', async () => {
    // Incognito (saveMode 'none') is the default: activeConversationId stays
    // null, so there is nothing the server could restore from. A null id with
    // messages on screen is exactly that state.
    const { result } = renderChat()
    await act(async () => { await result.current.loadSavedConversation({ ...CONVERSATION, id: null }) })
    await act(async () => { result.current.clearChat() })

    const action = lastUndoAction()
    expect(action).toBeTruthy()

    h.sendMessage.mockClear()
    await act(async () => { await action.onClick() })

    // No restore_conversation at all -- certainly not one carrying an invented id.
    expect(restoreFrames()).toEqual([])
    // The transcript is back...
    expect(result.current.messages.some(m => m.content === 'What is the weather')).toBe(true)
    // ...and the timeline says the assistant lost the context, rather than
    // letting Undo look like a full recovery.
    const note = result.current.messages.find(m => m.type === 'system')
    expect(note).toBeTruthy()
    // Assert on `content`: Message.jsx renders an unrecognised system subtype
    // from content, so a note that only sets `text` renders as a blank row.
    expect(note.content).toMatch(/does not have them in context/)
    expect(note.text).toBe(note.content)
  })

  it('uses the real id, and re-seeds the backend, when there is one', async () => {
    const { result } = renderChat()
    await act(async () => { await result.current.loadSavedConversation(CONVERSATION) })
    await act(async () => { result.current.clearChat() })

    h.sendMessage.mockClear()
    await act(async () => { await lastUndoAction().onClick() })

    const frames = restoreFrames()
    expect(frames).toHaveLength(1)
    expect(frames[0].conversation_id).toBe(CONVERSATION.id)
    expect(frames[0].conversation_id).not.toMatch(/^undo_/)
  })
})

describe('Undo is retired once the replacement chat is touched', () => {
  it('is dismissed and inert after a turn is sent into the new chat', async () => {
    const { result } = renderChat()
    await act(async () => { await result.current.loadSavedConversation(CONVERSATION) })
    await act(async () => { result.current.clearChat() })

    const action = lastUndoAction()
    const toastId = h.toastInfo.mock.results.at(-1).value

    // The user types into the fresh chat during the toast's window.
    await act(async () => { await result.current.sendChatMessage('Replacement question') })
    expect(h.toastDismiss).toHaveBeenCalledWith(toastId)

    // A stale tap on the toast must not wipe out that exchange.
    h.sendMessage.mockClear()
    await act(async () => { await action.onClick() })
    expect(result.current.messages.some(m => m.content === 'Replacement question')).toBe(true)
    expect(result.current.messages.some(m => m.content === 'Clear skies')).toBe(false)
    expect(restoreFrames()).toEqual([])
  })

  it('is dismissed when a conversation is loaded from history instead', async () => {
    const { result } = renderChat()
    await act(async () => { await result.current.loadSavedConversation(CONVERSATION) })
    await act(async () => { result.current.clearChat() })
    const toastId = h.toastInfo.mock.results.at(-1).value

    await act(async () => { await result.current.loadSavedConversation({ ...CONVERSATION, id: 'conv-other' }) })
    expect(h.toastDismiss).toHaveBeenCalledWith(toastId)
  })

  it('a second New Chat supersedes the first offer', async () => {
    // The second clear lands mid-generation (sendChatMessage sets isThinking),
    // which is the one path that still confirms -- jsdom has no window.confirm.
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { result } = renderChat()
    await act(async () => { await result.current.loadSavedConversation(CONVERSATION) })
    await act(async () => { result.current.clearChat() })
    const firstToastId = h.toastInfo.mock.results.at(-1).value
    await act(async () => { await result.current.sendChatMessage('Another question') })
    await act(async () => { result.current.clearChat() })
    expect(h.toastDismiss).toHaveBeenCalledWith(firstToastId)
  })
})

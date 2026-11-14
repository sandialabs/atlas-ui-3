/**
 * Team prompt library on the real ChatProvider send path (issue #1169):
 * only a reference goes out, a revoked selection is refused instead of
 * silently running the default prompt, choosing another prompt sends an
 * explicit null, and reopening a conversation re-selects its pinned version.
 * Same harness as the #1042 send-guard suite.
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
  activeKey: null,
  makePromptActive: vi.fn(),
  clearActivePrompt: vi.fn(),
  library: { entries: [], loaded: true, error: null, testDraft: null, setTestDraft: () => {} },
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
  useToast: () => ({ error: h.toastError, success: h.toastSuccess, info: h.toastInfo, dismiss: () => {}, show: () => 'toast-id' }),
}))

vi.mock('../hooks/chat/useChatConfig', () => ({
  useChatConfig: () => ({
    currentModel: 'test-model',
    user: 'tester@example.com',
    ragServers: [],
    tools: [{ server: 'files', tools: ['read', 'write'] }],
    configReady: h.configReady,
    features: { workspaces: h.workspacesEnabled, custom_prompts: true },
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
      activePromptKey: h.activeKey,
      clearActivePrompt: h.clearActivePrompt,
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
      makePromptActive: h.makePromptActive,
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
vi.mock('../hooks/usePromptLibrary', () => ({ usePromptLibrary: () => h.library }))

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
import { libraryPromptKey } from '../hooks/chat/useSelections'

const wrapper = ({ children }) => <ChatProvider>{children}</ChatProvider>
const renderChat = () => renderHook(() => useChat(), { wrapper })
const chatFrames = () => h.sendMessage.mock.calls.map(c => c[0]).filter(f => f.type === 'chat')

const entry = { id: 'p1', title: 'Team reviewer', latest_version: { id: 'v2', version_number: 2 } }

beforeEach(() => {
  vi.clearAllMocks()
  h.sendMessage.mockImplementation(() => true)
  h.saveMode = 'local'
  h.level = null
  h.activeKey = null
  h.library = { entries: [entry], loaded: true, error: null, testDraft: null, setTestDraft: () => {} }
})

describe('team prompt library on the chat send path (issue #1169)', () => {
  it('sends only a library reference, never the prompt text', () => {
    h.activeKey = libraryPromptKey('p1')
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('hello') })
    const [frame] = chatFrames()
    expect(frame.library_prompt).toEqual({ prompt_id: 'p1', version_id: null })
    expect(frame.custom_system_prompt).toBeUndefined()
    expect(frame.selected_prompts).toEqual([])
  })

  it('sends a pinned version id when one was chosen', () => {
    h.activeKey = libraryPromptKey('p1', 'v1')
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('hello') })
    expect(chatFrames()[0].library_prompt).toEqual({ prompt_id: 'p1', version_id: 'v1' })
  })

  it('refuses a selection that is no longer available, leaving the transcript alone', () => {
    h.activeKey = libraryPromptKey('p1')
    h.library = { ...h.library, entries: [] }
    const { result } = renderChat()
    let sent
    act(() => { sent = result.current.sendChatMessage('hello') })
    expect(sent).toBe(false)
    expect(chatFrames()).toHaveLength(0)
    expect(result.current.messages).toHaveLength(0)
    expect(h.toastError).toHaveBeenCalledWith(expect.stringMatching(/no longer available/))
  })

  it('sends an explicit null when another prompt is active', () => {
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('hello') })
    expect(chatFrames()[0]).toHaveProperty('library_prompt', null)
  })

  it('sends a draft under test inline and never as a library reference', () => {
    h.activeKey = 'libdraft:p1'
    h.library = { ...h.library, testDraft: { promptId: 'p1', content: 'draft text' } }
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('hello') })
    const [frame] = chatFrames()
    expect(frame.custom_system_prompt).toBe('draft text')
    expect(frame.library_prompt).toBeNull()
  })

  it("re-selects a reopened conversation's pinned version", () => {
    const { result } = renderChat()
    act(() => {
      result.current.loadSavedConversation({
        id: 'conv-1',
        messages: [{ role: 'user', content: 'hi', message_type: 'chat', timestamp: '2026-01-01T00:00:00Z' }],
        metadata: { prompt_library: { prompt_id: 'p1', version_id: 'v1', version_number: 1 } },
      })
    })
    expect(h.makePromptActive).toHaveBeenCalledWith(libraryPromptKey('p1', 'v1'))
  })
})

describe('workspace restore never overrides a pinned version (issue #1169)', () => {
  const ws = { id: 'ws-1', name: 'Work', config: { active_prompt_key: 'userprompt:other' } }
  const pinnedConversation = {
    id: 'conv-ws',
    messages: [{ role: 'user', content: 'hi', message_type: 'chat', timestamp: '2026-01-01T00:00:00Z' }],
    metadata: { workspace_id: 'ws-1', prompt_library: { prompt_id: 'p1', version_id: 'v1', version_number: 1 } },
  }
  const pinKey = libraryPromptKey('p1', 'v1')
  const lastOrder = spy => Math.max(...spy.mock.invocationCallOrder)

  beforeEach(() => {
    h.activeWorkspaceId = null
    h.wsState = { workspaces: [ws], loaded: true, error: null }
  })

  it('re-asserts the pin after an immediate workspace restore', () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(pinnedConversation) })
    expect(h.applyWorkspace).toHaveBeenCalledWith(ws.config)
    expect(h.makePromptActive).toHaveBeenLastCalledWith(pinKey)
    expect(lastOrder(h.makePromptActive)).toBeGreaterThan(lastOrder(h.applyWorkspace))
  })

  it('re-asserts the pin after a deferred workspace restore', () => {
    h.wsState = { workspaces: [ws], loaded: false, error: null }
    const { result, rerender } = renderChat()
    act(() => { result.current.loadSavedConversation(pinnedConversation) })
    expect(h.applyWorkspace).not.toHaveBeenCalled()
    h.wsState = { workspaces: [ws], loaded: true, error: null }
    rerender()
    expect(h.applyWorkspace).toHaveBeenCalledWith(ws.config)
    expect(h.makePromptActive).toHaveBeenLastCalledWith(pinKey)
    expect(lastOrder(h.makePromptActive)).toBeGreaterThan(lastOrder(h.applyWorkspace))
  })

  it('leaves an explicit workspace switch by the user alone', () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(pinnedConversation) })
    h.makePromptActive.mockClear()
    h.applyWorkspace.mockClear()
    act(() => { result.current.clearActiveWorkspace() })
    act(() => { result.current.switchWorkspace('ws-1') })
    expect(h.applyWorkspace).toHaveBeenCalledWith(ws.config)
    expect(h.makePromptActive).not.toHaveBeenCalled()
  })

  it('does not carry a pin into a conversation without one', () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(pinnedConversation) })
    h.makePromptActive.mockClear()
    act(() => {
      result.current.loadSavedConversation({ ...pinnedConversation, id: 'conv-plain', metadata: { workspace_id: 'ws-1' } })
    })
    expect(h.makePromptActive).not.toHaveBeenCalled()
  })
})


describe('listing completeness and the send path (issue #1169)', () => {
  const missing = { id: 'p-other', title: 'Other', latest_version: { id: 'w1', version_number: 1 } }

  it('sends a selection a complete listing contains', () => {
    h.activeKey = libraryPromptKey('p1')
    h.library = { ...h.library, complete: true }
    const { result } = renderChat()
    let sent
    act(() => { sent = result.current.sendChatMessage('hello') })
    expect(sent).toBe(true)
    expect(chatFrames()[0].library_prompt).toEqual({ prompt_id: 'p1', version_id: null })
  })

  it('refuses a genuinely revoked selection (absent from a complete listing)', () => {
    h.activeKey = libraryPromptKey('p1')
    h.library = { ...h.library, entries: [missing], complete: true }
    const { result } = renderChat()
    let sent
    act(() => { sent = result.current.sendChatMessage('hello') })
    expect(sent).toBe(false)
    expect(chatFrames()).toHaveLength(0)
    expect(h.toastError).toHaveBeenCalledWith(expect.stringMatching(/no longer available/))
  })

  it('lets the server decide when the listing is incomplete and omits a valid selection', () => {
    h.activeKey = libraryPromptKey('p1')
    h.library = { ...h.library, entries: [missing], complete: false }
    const { result } = renderChat()
    let sent
    act(() => { sent = result.current.sendChatMessage('hello') })
    expect(sent).toBe(true)
    expect(chatFrames()[0].library_prompt).toEqual({ prompt_id: 'p1', version_id: null })
    expect(h.toastError).not.toHaveBeenCalled()
  })
})

describe('pin revisions on the send path (issue #1169)', () => {
  const load = (result, metadata) => act(() => {
    result.current.loadSavedConversation({
      id: 'conv-1',
      messages: [{ role: 'user', content: 'hi', message_type: 'chat', timestamp: '2026-01-01T00:00:00Z' }],
      metadata,
    })
  })

  it('sends no revision for a chat the server has not stored', () => {
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('hello') })
    expect(chatFrames()[0].library_prompt_revision).toBeUndefined()
  })

  it('sends the revision of the loaded conversation, 0 when it never changed', () => {
    const { result } = renderChat()
    load(result, { prompt_library: { prompt_id: 'p1', version_id: 'v1' }, prompt_library_pin_revision: 3 })
    act(() => { result.current.sendChatMessage('hello') })
    expect(chatFrames()[0].library_prompt_revision).toBe(3)
    load(result, {})
    act(() => { result.current.sendChatMessage('again') })
    expect(chatFrames()[1].library_prompt_revision).toBe(0)
  })

  it('follows the revision a conversation_saved frame reports', () => {
    const { result } = renderChat()
    load(result, { prompt_library_pin_revision: 1 })
    act(() => {
      h.wsHandler({ type: 'conversation_saved', conversation_id: 'conv-1', prompt_library: null, prompt_library_pin_revision: 2 })
    })
    act(() => { result.current.sendChatMessage('hello') })
    expect(chatFrames()[0].library_prompt_revision).toBe(2)
  })

  it('adopts the stored pin when the server reports a conflict', () => {
    const { result } = renderChat()
    load(result, { prompt_library_pin_revision: 1 })
    h.makePromptActive.mockClear()
    act(() => {
      h.wsHandler({
        type: 'error', error_type: 'prompt_pin_conflict', conversation_id: 'conv-1',
        message: 'changed elsewhere', prompt_library: { prompt_id: 'p1', version_id: 'v2' },
        prompt_library_pin_revision: 4,
      })
    })
    expect(h.makePromptActive).toHaveBeenCalledWith(libraryPromptKey('p1', 'v2'))
    act(() => { result.current.sendChatMessage('hello') })
    expect(chatFrames()[0].library_prompt_revision).toBe(4)
    act(() => {
      h.wsHandler({
        type: 'error', error_type: 'prompt_pin_conflict', conversation_id: 'conv-1',
        message: 'cleared elsewhere', prompt_library: null, prompt_library_pin_revision: 5,
      })
    })
    expect(h.clearActivePrompt).toHaveBeenCalled()
  })

  it('ignores pin state for a conversation that is not on screen', () => {
    const { result } = renderChat()
    load(result, { prompt_library_pin_revision: 1 })
    act(() => {
      h.wsHandler({ type: 'conversation_saved', conversation_id: 'other', prompt_library_pin_revision: 9 })
    })
    act(() => { result.current.sendChatMessage('hello') })
    expect(chatFrames()[0].library_prompt_revision).toBe(1)
  })
})

describe('live (in-flight) conversations keep their pin (issue #1169)', () => {
  const live = {
    id: 'conv-live',
    in_flight: true,
    run_id: 'run-1',
    messages: [{ role: 'user', content: 'hi', message_type: 'chat', timestamp: '2026-01-01T00:00:00Z' }],
    metadata: {
      agent_mode: false,
      prompt_library: { prompt_id: 'p1', version_id: 'v1', version_number: 1 },
      prompt_library_pin_revision: 1,
    },
  }

  it('re-selects the pin of a conversation whose run is still in flight', () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(live) })
    expect(h.makePromptActive).toHaveBeenCalledWith(libraryPromptKey('p1', 'v1'))
  })

  it('sends the pinned reference and revision next, never an accidental null', () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(live) })
    // The selection the load made is what the composer sends with.
    h.activeKey = libraryPromptKey('p1', 'v1')
    const { result: next } = renderChat()
    act(() => { next.current.loadSavedConversation(live) })
    act(() => { next.current.sendChatMessage('continue') })
    const frame = chatFrames().at(-1)
    expect(frame.library_prompt).toEqual({ prompt_id: 'p1', version_id: 'v1' })
    expect(frame.library_prompt_revision).toBe(1)
  })

  it('moves from the live view to the saved record without losing the revision', () => {
    const { result } = renderChat()
    act(() => { result.current.loadSavedConversation(live) })
    act(() => {
      h.wsHandler({ type: 'conversation_saved', conversation_id: 'conv-live',
        prompt_library: { prompt_id: 'p1', version_id: 'v1' }, prompt_library_pin_revision: 1 })
    })
    act(() => { result.current.sendChatMessage('after save') })
    expect(chatFrames().at(-1).library_prompt_revision).toBe(1)
  })
})

describe('pin-conflict errors reach only their own conversation (issue #1169)', () => {
  const conflict = extra => ({
    type: 'error', error_type: 'prompt_pin_conflict', message: 'changed elsewhere',
    prompt_library: { prompt_id: 'p1', version_id: 'v9' }, prompt_library_pin_revision: 7, ...extra,
  })
  const load = (result, id, metadata) => act(() => {
    result.current.loadSavedConversation({
      id,
      messages: [{ role: 'user', content: 'hi', message_type: 'chat', timestamp: '2026-01-01T00:00:00Z' }],
      metadata,
    })
  })

  it('applies a conflict that names the conversation on screen', () => {
    const { result } = renderChat()
    load(result, 'conv-a', { prompt_library_pin_revision: 1 })
    h.makePromptActive.mockClear()
    act(() => { h.wsHandler(conflict({ conversation_id: 'conv-a', run_id: 'run-a' })) })
    expect(h.makePromptActive).toHaveBeenCalledWith(libraryPromptKey('p1', 'v9'))
  })

  it('ignores a conflict for another open conversation, with or without a run id', () => {
    const { result } = renderChat()
    load(result, 'conv-a', { prompt_library_pin_revision: 1 })
    h.makePromptActive.mockClear()
    h.clearActivePrompt.mockClear()
    act(() => { h.wsHandler(conflict({ conversation_id: 'conv-b', run_id: 'run-b' })) })
    act(() => { h.wsHandler(conflict({ conversation_id: 'conv-b' })) })
    act(() => { h.wsHandler(conflict({ conversation_id: 'conv-b', prompt_library: null })) })
    expect(h.makePromptActive).not.toHaveBeenCalled()
    expect(h.clearActivePrompt).not.toHaveBeenCalled()
    act(() => { result.current.sendChatMessage('next') })
    expect(chatFrames().at(-1).library_prompt_revision).toBe(1)
  })

  it('ignores a conflict that names no conversation at all', () => {
    const { result } = renderChat()
    load(result, 'conv-a', { prompt_library_pin_revision: 1 })
    h.makePromptActive.mockClear()
    act(() => { h.wsHandler(conflict({})) })
    expect(h.makePromptActive).not.toHaveBeenCalled()
    act(() => { result.current.sendChatMessage('next') })
    expect(chatFrames().at(-1).library_prompt_revision).toBe(1)
  })

  it('ignores a conflict that arrives after the user moved to a new chat', () => {
    const { result } = renderChat()
    load(result, 'conv-a', { prompt_library_pin_revision: 1 })
    act(() => { result.current.clearChat() })
    h.makePromptActive.mockClear()
    h.clearActivePrompt.mockClear()
    act(() => { h.wsHandler(conflict({ conversation_id: 'conv-a', run_id: 'run-a' })) })
    act(() => { h.wsHandler(conflict({ conversation_id: 'conv-a' })) })
    expect(h.makePromptActive).not.toHaveBeenCalled()
    expect(h.clearActivePrompt).not.toHaveBeenCalled()
    act(() => { result.current.sendChatMessage('fresh chat') })
    expect(chatFrames().at(-1).library_prompt_revision).toBeUndefined()
  })

  it("still records a new chat's revision from its first conversation_saved", () => {
    const { result } = renderChat()
    act(() => {
      h.wsHandler({ type: 'conversation_saved', conversation_id: 'conv-new',
        prompt_library: { prompt_id: 'p1', version_id: 'v1' }, prompt_library_pin_revision: 1 })
    })
    act(() => { result.current.sendChatMessage('second turn') })
    const frame = chatFrames().at(-1)
    expect(frame.conversation_id).toBe('conv-new')
    expect(frame.library_prompt_revision).toBe(1)
  })

  it('ignores a conflict for a conversation the user switched away from mid-turn', () => {
    const { result } = renderChat()
    load(result, 'conv-a', { prompt_library_pin_revision: 1 })
    load(result, 'conv-b', { prompt_library_pin_revision: 5 })
    h.makePromptActive.mockClear()
    act(() => { h.wsHandler(conflict({ conversation_id: 'conv-a', run_id: 'run-a' })) })
    expect(h.makePromptActive).not.toHaveBeenCalled()
    act(() => { result.current.sendChatMessage('in b') })
    expect(chatFrames().at(-1).library_prompt_revision).toBe(5)
  })
})

/**
 * The built-in search tool's automatic state, through the real ChatProvider
 * (issue #1102).
 *
 * The component suites mock ChatContext, so the real derivation never runs
 * there. This renders the *real* provider with the leaf hooks stubbed, and
 * pins the two rules that only the provider can show:
 *
 *   - ticking `atlas_search` with nothing selected means "search everything
 *     I can reach", so the wire payload carries every source -- and that must
 *     NOT lock the tool on, or the user could never untick it;
 *   - the sources the user picked do imply the tool, and the panel's lock
 *     follows the sources the turn would really carry.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  toastInfo: vi.fn(),
  selectedTools: new Set(),
  selectedDataSources: new Set(),
  ragEnabled: false,
  setAgentPendingQuestion: vi.fn(),
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
    ragServers: [{ server: 'srv', sources: [{ id: 'src1' }] }],
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
      selectedDataSources: h.selectedDataSources,
      ragEnabled: h.ragEnabled,
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
    setAgentPendingQuestion: h.setAgentPendingQuestion,
    agentPendingQuestion: null,
  }),
}))

vi.mock('../hooks/chat/useFiles', () => ({
  useFiles: () => ({
    getTaggedFilesContent: () => ({}),
    setCanvasContent: vi.fn(),
    setCanvasFiles: vi.fn(),
    setCurrentCanvasFileIndex: vi.fn(),
    setCustomUiContent: vi.fn(),
    setSessionFiles: vi.fn(),
    setFileType: vi.fn(),
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
  h.selectedDataSources = new Set()
  h.ragEnabled = false
})

describe('The search tool and the sources a turn carries (real ChatProvider)', () => {
  it('ticking the search tool alone carries every source, without locking the tool', () => {
    // Ticking `atlas_search` with nothing picked means "search everything I
    // can reach" -- so the wire payload carries every source. The panels
    // must not read that as "the sources imply the tool", or the tool would
    // lock itself on and could never be unticked.
    h.selectedTools = new Set(['atlas_search'])
    const { result } = renderChat()

    expect(result.current.effectiveDataSources).toEqual([])
    expect(result.current.searchToolReason).toBeNull()

    act(() => { result.current.sendChatMessage('search for cars') })
    const payload = h.sendMessage.mock.calls[0][0]
    expect(payload.selected_data_sources.length).toBeGreaterThan(0)
  })

  it('picked sources imply the tool and show up as such', () => {
    h.selectedDataSources = new Set(['srv:src1'])
    const { result } = renderChat()

    expect(result.current.effectiveDataSources).toEqual(['srv:src1'])
    expect(result.current.searchToolReason).toBe('sources')

    act(() => { result.current.sendChatMessage('search for cars') })
    const payload = h.sendMessage.mock.calls[0][0]
    expect(payload.selected_data_sources).toEqual(['srv:src1'])
  })

  it('the RAG toggle carries every source, and says so', () => {
    h.ragEnabled = true
    const { result } = renderChat()

    expect(result.current.effectiveDataSources.length).toBeGreaterThan(0)
    // Nothing was hand-picked: the toggle is what put the sources in play.
    expect(result.current.searchToolReason).toBe('rag')

    act(() => { result.current.sendChatMessage('search for cars') })
    const payload = h.sendMessage.mock.calls[0][0]
    expect(payload.selected_data_sources.length).toBeGreaterThan(0)
    expect(payload.data_sources_auto).toBe(true)
  })

  it('no sources and no toggle means no sources on the wire', () => {
    const { result } = renderChat()

    expect(result.current.effectiveDataSources).toEqual([])
    expect(result.current.searchToolReason).toBeNull()

    act(() => { result.current.sendChatMessage('hello') })
    const payload = h.sendMessage.mock.calls[0][0]
    expect(payload.selected_data_sources).toEqual([])
  })
})

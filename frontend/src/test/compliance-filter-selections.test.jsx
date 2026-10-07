/**
 * The header compliance filter against the real ChatProvider.
 *
 * Pins three behaviors found by driving the app in a browser:
 *   - nothing the filter hides is sent: untagged or out-of-allowlist tools,
 *     MCP prompts and data sources are dropped from the payload even when a
 *     persisted selection still holds them (the MCP tool path has no
 *     server-side compliance check);
 *   - persisted selections are pruned against the filter with the allowlist
 *     rule (HIPAA keeps SOC2), and nothing is pruned before the level
 *     definitions load;
 *   - a level switch keeps an allowlisted persona and moves the model off a
 *     level the filter excludes.
 *
 * Harness mirrors agent-mode-payload-gating.test.jsx: the real provider with
 * leaf hooks stubbed.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

const LEVELS = [
  { name: 'Public', aliases: [], allowed_with: ['Public'] },
  { name: 'SOC2', aliases: [], allowed_with: ['SOC2'] },
  { name: 'HIPAA', aliases: [], allowed_with: ['HIPAA', 'SOC2'] },
]

const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  toastInfo: vi.fn(),
  setCurrentModel: vi.fn(),
  currentModel: 'public-model',
  levels: [],
  filter: 'HIPAA',
  selectedTools: new Set(),
  selectedPrompts: new Set(),
  activePrompts: [],
  activePromptKey: null,
  selectedDataSources: new Set(),
  ragEnabled: false,
  removeTools: vi.fn(),
  removePrompts: vi.fn(),
  removeDataSources: vi.fn(),
  clearActivePrompt: vi.fn(),
  setComplianceLevelFilter: vi.fn(),
  personas: [],
}))

vi.mock('../contexts/WSContext', () => ({
  useWS: () => ({
    sendMessage: h.sendMessage,
    isConnected: true,
    addMessageHandler: () => () => {},
  }),
}))

vi.mock('../components/ui/toastContext', () => ({
  useToast: () => ({ error: vi.fn(), success: vi.fn(), info: h.toastInfo }),
}))

vi.mock('../hooks/chat/useComplianceLevels', () => ({
  useComplianceLevels: () => ({ complianceLevels: h.levels, complianceMode: 'explicit_allowlist' }),
}))

vi.mock('../hooks/usePersonas', () => ({
  usePersonas: () => ({ personas: h.personas, loading: false, error: null, fetchPersonas: vi.fn() }),
}))

const TOOLS = [
  { server: 'soc2srv', compliance_level: 'SOC2', tools: ['evaluate'] },
  { server: 'pubsrv', compliance_level: 'Public', tools: ['evaluate'] },
  { server: 'loose', tools: ['plan'] },
  // Built-in server: tagged Public by /api/config, exempt from the filter.
  { server: 'atlas', compliance_level: 'Public', tools: ['canvas'] },
]
const PROMPTS = [
  { server: 'hipaasrv', compliance_level: 'HIPAA', prompts: [{ name: 'intake' }] },
  { server: 'pubprompts', compliance_level: 'Public', prompts: [{ name: 'hello' }] },
]
const RAG_SERVERS = [{
  server: 'rag',
  complianceLevel: 'Internal',
  sources: [
    { id: 'audit', complianceLevel: 'SOC2' },
    { id: 'patients', complianceLevel: 'HIPAA' },
    { id: 'public', complianceLevel: 'Public' },
    { id: 'untagged' },
  ],
}]
const MODELS = [
  { name: 'public-model', compliance_level: 'Public' },
  { name: 'soc2-model', compliance_level: 'SOC2' },
  { name: 'hipaa-model', compliance_level: 'HIPAA' },
]

vi.mock('../hooks/chat/useChatConfig', () => ({
  useChatConfig: () => ({
    currentModel: h.currentModel,
    setCurrentModel: h.setCurrentModel,
    models: MODELS,
    user: 'tester@example.com',
    tools: TOOLS,
    prompts: PROMPTS,
    ragServers: RAG_SERVERS,
    configReady: false,
    features: { compliance_levels: true },
    appName: 'Atlas',
    isInAdminGroup: false,
    fileExtraction: {},
    setIsCanvasOpen: vi.fn(),
    agentMaxStepsLimit: 10,
    agentCeilingConfirmed: true,
  }),
}))

vi.mock('../hooks/chat/useSelections', async (importActual) => {
  const actual = await importActual()
  return {
    ...actual,
    useSelections: () => ({
      selectedTools: h.selectedTools,
      selectedPrompts: h.selectedPrompts,
      activePrompts: h.activePrompts,
      activePromptKey: h.activePromptKey,
      clearActivePrompt: h.clearActivePrompt,
      selectedDataSources: h.selectedDataSources,
      ragEnabled: h.ragEnabled,
      toggleRagEnabled: vi.fn(),
      removeTools: h.removeTools,
      removePrompts: h.removePrompts,
      removeDataSources: h.removeDataSources,
      complianceLevelFilter: h.filter,
      setComplianceLevelFilter: h.setComplianceLevelFilter,
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
  useSettings: () => ({ settings: { maxIterations: 10, llmTemperature: 0.7 }, updateSettings: vi.fn() }),
}))

vi.mock('../hooks/chat/usePersistentState', () => ({
  usePersistentState: (_key, initial) => [initial, vi.fn()],
}))

import { ChatProvider, useChat } from '../contexts/ChatContext'

const wrapper = ({ children }) => <ChatProvider>{children}</ChatProvider>
const renderChat = () => renderHook(() => useChat(), { wrapper })
const lastPayload = () => h.sendMessage.mock.calls.at(-1)[0]

beforeEach(() => {
  vi.clearAllMocks()
  h.sendMessage.mockImplementation(() => true)
  h.levels = LEVELS
  h.filter = 'HIPAA'
  h.currentModel = 'public-model'
  h.selectedTools = new Set(['soc2srv_evaluate', 'pubsrv_evaluate', 'loose_plan', 'atlas_canvas'])
  h.selectedPrompts = new Set(['hipaasrv_intake', 'pubprompts_hello'])
  h.activePrompts = ['hipaasrv_intake', 'pubprompts_hello']
  h.activePromptKey = null
  h.selectedDataSources = new Set(['rag:audit', 'rag:patients', 'rag:public', 'rag:untagged'])
  h.ragEnabled = false
  h.personas = []
})

describe('compliance filter: outgoing payload', () => {
  it('sends only what the filter allows, untagged resources excluded', () => {
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('hi') })

    const payload = lastPayload()
    expect(payload.selected_tools).toEqual(['soc2srv_evaluate', 'atlas_canvas'])
    expect(payload.selected_prompts).toEqual(['hipaasrv_intake'])
    expect(payload.selected_data_sources).toEqual(['rag:audit', 'rag:patients'])
    expect(payload.compliance_level_filter).toBe('HIPAA')
  })

  it('filters the RAG-toggle "all sources" expansion too', () => {
    h.ragEnabled = true
    h.selectedDataSources = new Set()
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('hi') })

    expect(lastPayload().selected_data_sources).toEqual(['rag:audit', 'rag:patients'])
  })

  it('fails closed when a filter is set but the level definitions are missing', () => {
    // The pickers deny everything without definitions; the payload must not
    // carry what they hide.
    h.levels = []
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('hi') })

    expect(lastPayload().selected_tools).toEqual(['atlas_canvas'])
    expect(lastPayload().selected_data_sources).toEqual([])
  })

  it('holds back keys it cannot place yet instead of sending them unjudged', () => {
    // e.g. a persisted tool from a server /api/config has not reported yet
    h.selectedTools = new Set(['soc2srv_evaluate', 'notloaded_tool'])
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('hi') })

    expect(lastPayload().selected_tools).toEqual(['soc2srv_evaluate'])
  })

  it('sends everything when no filter is set', () => {
    h.filter = null
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('hi') })

    expect(lastPayload().selected_tools).toEqual(['soc2srv_evaluate', 'pubsrv_evaluate', 'loose_plan', 'atlas_canvas'])
    expect(lastPayload().selected_data_sources).toHaveLength(4)
  })
})

describe('compliance filter: selection pruning', () => {
  it('prunes persisted selections with the allowlist rule', () => {
    renderChat()
    expect(h.removeTools).toHaveBeenCalledWith(['pubsrv_evaluate', 'loose_plan'])
    expect(h.removePrompts).toHaveBeenCalledWith(['pubprompts_hello'])
    expect(h.removeDataSources).toHaveBeenCalledWith(['rag:public', 'rag:untagged'])
  })

  it('prunes nothing before the level definitions load', () => {
    h.levels = []
    renderChat()
    for (const fn of [h.removeTools, h.removePrompts, h.removeDataSources]) {
      for (const call of fn.mock.calls) expect(call[0]).toEqual([])
    }
  })

  it('drops a persisted filter naming a level that no longer exists', () => {
    h.filter = 'Retired'
    renderChat()
    expect(h.setComplianceLevelFilter).toHaveBeenCalledWith(null)
  })
})

describe('compliance filter: level switch', () => {
  it('keeps an allowlisted persona and switches to an exact-level model', () => {
    h.filter = null
    h.activePromptKey = 'persona:auditor'
    h.personas = [{ id: 'auditor', compliance_level: 'SOC2' }]
    const { result } = renderChat()
    act(() => { result.current.setComplianceLevelFilter('HIPAA') })

    expect(h.clearActivePrompt).not.toHaveBeenCalled()
    expect(h.setCurrentModel).toHaveBeenCalledWith('hipaa-model')
    expect(h.toastInfo).toHaveBeenCalled()
    expect(h.setComplianceLevelFilter).toHaveBeenCalledWith('HIPAA')
  })

  it('clears a persona the new level excludes and leaves a compliant model alone', () => {
    h.filter = null
    h.currentModel = 'soc2-model'
    h.activePromptKey = 'persona:greeter'
    h.personas = [{ id: 'greeter', compliance_level: 'Public' }]
    const { result } = renderChat()
    act(() => { result.current.setComplianceLevelFilter('HIPAA') })

    expect(h.clearActivePrompt).toHaveBeenCalled()
    expect(h.setCurrentModel).not.toHaveBeenCalled()
  })
})

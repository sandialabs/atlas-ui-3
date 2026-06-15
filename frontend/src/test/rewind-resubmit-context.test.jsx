/**
 * Behavioral tests for the rewind/edit logic that lives inside ChatProvider
 * (issue #142). The component suites mock ChatContext, so the real
 * `rewindAndResubmit` / `sendChatMessage` / `answerAgentQuestion` never run
 * there. This renders the *real* provider (with only leaf hooks stubbed) so we
 * can pin the headline guarantees:
 *
 *   - the streaming guard (no resend, no truncation, toasts) while a reply is in
 *     flight,
 *   - the empty-content guard,
 *   - that the local transcript is truncated ONLY after the send is confirmed on
 *     the wire (a failed/disconnected send must not drop the conversation tail),
 *   - that agent-loop answers are tagged `_agentInput` so they stay out of the
 *     rewind ordinal.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { isRewindableUserMessage } from '../utils/userMessageOrdinal'

// Shared mock handles (hoisted so the vi.mock factories can close over them).
const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  ws: { handler: null },
}))

vi.mock('../contexts/WSContext', () => ({
  useWS: () => ({
    sendMessage: h.sendMessage,
    isConnected: true,
    addMessageHandler: (fn) => { h.ws.handler = fn; return () => {} },
  }),
}))

vi.mock('../components/ui/toastContext', () => ({
  useToast: () => ({ error: h.toastError, success: h.toastSuccess }),
}))

vi.mock('../hooks/chat/useChatConfig', () => ({
  useChatConfig: () => ({
    currentModel: 'test-model',
    user: 'tester@example.com',
    ragServers: [],
    configReady: false, // skips the custom-prompt-clear effect
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
    ...actual, // keep isUserPromptKey / userPromptIdFromKey real
    useSelections: () => ({
      selectedTools: new Set(),
      selectedPrompts: new Set(),
      activePrompts: [],
      activePromptKey: null,
      clearActivePrompt: vi.fn(),
      selectedDataSources: new Set(),
      ragEnabled: false,
      toggleRagEnabled: vi.fn(),
      toolChoiceRequired: false,
      complianceLevelFilter: '',
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

vi.mock('../hooks/useSettings', () => ({
  useSettings: () => ({ settings: {}, updateSettings: vi.fn() }),
}))

vi.mock('../hooks/chat/usePersistentState', () => ({
  usePersistentState: (_key, initial) => [initial, vi.fn()],
}))

import { ChatProvider, useChat } from '../contexts/ChatContext'

const wrapper = ({ children }) => <ChatProvider>{children}</ChatProvider>
const renderChat = () => renderHook(() => useChat(), { wrapper })

// Reset isThinking between turns by feeding the captured WS handler a completion.
const completeResponse = () => act(() => { h.ws.handler?.({ type: 'response_complete' }) })

const userContents = (messages) =>
  messages.filter(isRewindableUserMessage).map(m => m.content)

beforeEach(() => {
  vi.clearAllMocks()
  h.sendMessage.mockImplementation(() => true)
  h.ws.handler = null
})

describe('rewindAndResubmit (real ChatProvider)', () => {
  it('returns false and does not send on empty/whitespace content', () => {
    const { result } = renderChat()
    let ret
    act(() => { ret = result.current.rewindAndResubmit(0, '   ') })
    expect(ret).toBe(false)
    expect(h.sendMessage).not.toHaveBeenCalled()
  })

  it('is blocked while a response is streaming: no resend, no truncation, toasts', () => {
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('first prompt') })
    expect(result.current.isThinking).toBe(true)
    expect(h.sendMessage).toHaveBeenCalledTimes(1)

    let ret
    act(() => { ret = result.current.rewindAndResubmit(0, 'edited while busy') })

    expect(ret).toBe(false)
    expect(h.toastError).toHaveBeenCalled()
    // No second send, and the transcript is untouched.
    expect(h.sendMessage).toHaveBeenCalledTimes(1)
    expect(userContents(result.current.messages)).toEqual(['first prompt'])
  })

  it('truncates and resubmits on the happy path, forwarding rewind_to_user_index', () => {
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('u0') })
    completeResponse()
    act(() => { result.current.sendChatMessage('u1') })
    completeResponse()
    expect(userContents(result.current.messages)).toEqual(['u0', 'u1'])

    act(() => { result.current.rewindAndResubmit(0, 'u0 edited') })

    // Everything from u0 on was dropped; the edited prompt took its place.
    expect(userContents(result.current.messages)).toEqual(['u0 edited'])
    const lastPayload = h.sendMessage.mock.calls.at(-1)[0]
    expect(lastPayload.rewind_to_user_index).toBe(0)
    expect(lastPayload.content).toBe('u0 edited')
  })

  it('does NOT truncate the transcript when the send fails (data-loss guard)', () => {
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('u0') })
    completeResponse()
    act(() => { result.current.sendChatMessage('u1') })
    completeResponse()
    expect(userContents(result.current.messages)).toEqual(['u0', 'u1'])

    // Socket drops: the underlying send returns falsy.
    h.sendMessage.mockImplementation(() => false)
    let ret
    act(() => { ret = result.current.rewindAndResubmit(0, 'should not apply') })

    expect(ret).toBe(false)
    // The conversation tail must still be intact -- nothing dropped.
    expect(userContents(result.current.messages)).toEqual(['u0', 'u1'])
  })
})

describe('answerAgentQuestion (real ChatProvider)', () => {
  it('tags agent-loop answers with _agentInput so they stay out of the rewind ordinal', () => {
    const { result } = renderChat()
    act(() => { result.current.sendChatMessage('real prompt') })
    completeResponse()
    act(() => { result.current.answerAgentQuestion('agent answer') })

    const msgs = result.current.messages
    const answerRow = msgs.find(m => m.content === 'agent answer')
    expect(answerRow).toBeTruthy()
    expect(answerRow._agentInput).toBe(true)
    // It is a user row but must NOT count toward the rewind ordinal.
    expect(answerRow.role).toBe('user')
    expect(isRewindableUserMessage(answerRow)).toBe(false)
    expect(userContents(msgs)).toEqual(['real prompt'])
  })
})

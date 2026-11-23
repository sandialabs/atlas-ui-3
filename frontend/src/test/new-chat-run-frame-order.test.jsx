/**
 * A new agent-mode chat renders its answer whichever of `run_started` and
 * the first `run_status` reaches the client first, and however quickly the
 * run's first tagged frames follow (issue #1216).
 *
 * The client routes a tagged frame by comparing its conversation id with a
 * ref that used to catch up only when React rendered. A new chat adopts the
 * server-minted id from `run_started`; frames that landed before the next
 * render were filed as background activity and the answer never reached the
 * transcript. Delivering the frames in one React batch reproduces that.
 *
 * The real ChatProvider with leaf hooks stubbed; frames are delivered to the
 * websocket handler it registers.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

const h = vi.hoisted(() => ({
  sendMessage: vi.fn(() => true),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  toastInfo: vi.fn(),
  settings: {},
  handlers: [],
}))

vi.mock('../contexts/WSContext', () => ({
  useWS: () => ({
    sendMessage: h.sendMessage,
    isConnected: true,
    // Capture every handler the provider registers so tests can deliver
    // websocket frames to it directly.
    addMessageHandler: (handler) => {
      h.handlers.push(handler)
      return () => {
        h.handlers = h.handlers.filter(x => x !== handler)
      }
    },
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
    configReady: false,
    features: { chat_history: true },
    prompts: [],
    appName: 'Atlas',
    isInAdminGroup: false,
    fileExtraction: {},
    setIsCanvasOpen: vi.fn(),
    agentMaxStepsLimit: 50,
    agentCeilingConfirmed: true,
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
  useSettings: () => ({ settings: h.settings, updateSettings: vi.fn() }),
}))

vi.mock('../hooks/chat/usePersistentState', () => ({
  usePersistentState: (_key, initial) => [initial, vi.fn()],
}))

import { ChatProvider, useChat } from '../contexts/ChatContext'

const wrapper = ({ children }) => <ChatProvider>{children}</ChatProvider>
const renderChat = () => renderHook(() => useChat(), { wrapper })

const CONV = 'conv-new'
const RUN = 'run-1'
const runRecord = (status) => ({
  run_id: RUN,
  conversation_id: CONV,
  status,
  created_at: 1,
  updated_at: 2,
  connection_bound: false,
})
const frames = {
  queued: { type: 'run_status', run: runRecord('queued') },
  started: { type: 'run_started', run_id: RUN, conversation_id: CONV, title: null, connection_bound: false },
  running: { type: 'run_status', run: runRecord('running') },
  stream: (token, extra = {}) => ({
    type: 'token_stream', token, run_id: RUN, conversation_id: CONV, ...extra,
  }),
}

const runHandler = () => {
  const handler = [...h.handlers].reverse().find(x => typeof x === 'function')
  if (!handler) throw new Error('no websocket handler registered')
  return handler
}

beforeEach(() => {
  vi.clearAllMocks()
  h.handlers = []
  h.sendMessage.mockImplementation(() => true)
  h.settings = {}
})

// `burst` delivers everything the server sends after the user's message in one
// React batch, which is what the browser sees when the frames arrive faster
// than it renders: no commit lands between `run_started` and the run's first
// tagged frames.
async function playTurn(order, { burst }) {
  const { result } = renderChat()
  const handler = runHandler()
  // Connection-level frames, then the user sends into a brand-new chat.
  act(() => handler({ type: 'connection_session', session_id: 's1' }))
  act(() => handler({ type: 'runs_snapshot', runs: [] }))
  act(() => { result.current.sendChatMessage('do a task') })
  const deliver = [
    ...order.map(name => frames[name]),
    { type: 'agent_start', run_id: RUN, conversation_id: CONV },
    frames.stream('The answer ', { is_first: true }),
    frames.stream('is 42.'),
    frames.stream('', { is_last: true }),
    { type: 'response_complete', run_id: RUN, conversation_id: CONV },
    { type: 'conversation_saved', conversation_id: CONV, run_id: RUN },
    { type: 'run_status', run: runRecord('completed') },
  ]
  if (burst) {
    act(() => { deliver.forEach(f => handler(f)) })
  } else {
    deliver.forEach(f => act(() => handler(f)))
  }
  await waitFor(() => {
    expect(result.current.messages.some(m => m.role === 'assistant')).toBe(true)
  })
  return result
}

const answerText = (result) => result.current.messages
  .filter(m => m.role === 'assistant')
  .map(m => m.content)
  .join('')

describe('new agent chat answer rendering (#1216)', () => {
  const orders = {
    'run_started first': ['started', 'queued', 'running'],
    'run_status(queued) first': ['queued', 'started', 'running'],
  }
  for (const [label, order] of Object.entries(orders)) {
    for (const burst of [false, true]) {
      it(`renders the answer: ${label}, ${burst ? 'one batch' : 'a commit per frame'}`, async () => {
        const result = await playTurn(order, { burst })
        expect(answerText(result)).toContain('The answer is 42.')
        expect(result.current.messages.some(m => m.role === 'user')).toBe(true)
      })
    }
  }
})

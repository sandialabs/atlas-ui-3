/**
 * The marker-only replay seed next to live tool activity.
 *
 * Reopening a conversation while its run is between segments (issue #957)
 * seeds an empty `_seed` placeholder so later stream frames can reconcile
 * into it. It is internal state: it must not render as a separate gray
 * assistant bubble, and while a tool row (an atlas_sleep, say) or an approval
 * card already shows the run is alive it must not render at all -- that row
 * is the progress indicator. Text streamed after the sleep must land after
 * the sleep row, in chronological order, not where the seed happened to sit.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, renderHook } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import Message from '../components/Message'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { useMessages } from '../hooks/chat/useMessages'
import { currentTurnHasActiveRow } from '../utils/executionState'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: () => ({ isComplianceAccessible: () => true }),
  useOptionalMarketplace: () => null,
}))
vi.mock('../contexts/WSContext')

const userRow = { role: 'user', content: 'Sleep then answer', timestamp: '2026-11-05T00:00:00Z' }
const sleepRow = (status) => ({
  role: 'system',
  type: 'tool_call',
  tool_call_id: 'call_sleep',
  invocation_id: 'inv_sleep',
  tool_name: 'atlas_sleep',
  server_name: 'atlas',
  arguments: { seconds: 30 },
  status,
  content: 'atlas_sleep',
  timestamp: '2026-11-05T00:00:01Z',
})
const seedRow = {
  role: 'assistant',
  content: '',
  timestamp: '2026-11-05T00:00:02Z',
  _streaming: true,
  _replayed: true,
  _seed: true,
}

describe('replay seed - reducer keeps later text in chronological position', () => {
  const reopenedWithSleep = () => {
    const { result } = renderHook(() => useMessages())
    act(() => { result.current.bulkAdd([userRow]) })
    // Reopen: the run is between segments, so the view gets the empty seed.
    act(() => { result.current.streamToken('', true) })
    // The execution snapshot then adds the sleeping tool row after it.
    act(() => { result.current.addMessage(sleepRow('sleeping')) })
    return result
  }

  it('a replayed segment after the sleep lands below the sleep row', () => {
    const result = reopenedWithSleep()
    act(() => { result.current.streamToken('Woke up, here is the answer', true) })

    const rows = result.current.messages
    expect(rows.map(m => m.type || m.role)).toEqual(['user', 'tool_call', 'assistant'])
    expect(rows[2].content).toBe('Woke up, here is the answer')
    expect(rows[2]._seed).toBe(false)
    expect(rows.some(m => m._seed)).toBe(false)
  })

  it('a live token after the sleep lands below the sleep row and drops the seed', () => {
    const result = reopenedWithSleep()
    act(() => { result.current.streamToken('Woke') })
    act(() => { result.current.streamToken(' up') })

    const rows = result.current.messages
    expect(rows.map(m => m.type || m.role)).toEqual(['user', 'tool_call', 'assistant'])
    expect(rows[2].content).toBe('Woke up')
    expect(rows.some(m => m._seed)).toBe(false)
  })

  it('a replay that continues an existing text placeholder still updates it in place', () => {
    const { result } = renderHook(() => useMessages())
    act(() => { result.current.bulkAdd([userRow]) })
    act(() => { result.current.streamToken('The answer', true) })
    act(() => { result.current.streamToken('The answer starts here', true) })

    const rows = result.current.messages
    expect(rows).toHaveLength(2)
    expect(rows[1].content).toBe('The answer starts here')
    expect(rows[1]._replayed).toBe(true)
  })
})

describe('currentTurnHasActiveRow', () => {
  it('is true for a sleeping, calling or in-progress tool row of the current turn', () => {
    expect(currentTurnHasActiveRow([userRow, sleepRow('sleeping')])).toBe(true)
    expect(currentTurnHasActiveRow([userRow, sleepRow('calling')])).toBe(true)
    expect(currentTurnHasActiveRow([userRow, sleepRow('in_progress'), seedRow])).toBe(true)
  })

  it('is true for a pending approval card', () => {
    const card = { role: 'system', type: 'tool_approval_request', status: 'pending' }
    expect(currentTurnHasActiveRow([userRow, card, seedRow])).toBe(true)
  })

  it('is false once the tool settles, and ignores rows from earlier turns', () => {
    expect(currentTurnHasActiveRow([userRow, sleepRow('completed'), seedRow])).toBe(false)
    expect(currentTurnHasActiveRow([sleepRow('sleeping'), userRow, seedRow])).toBe(false)
    expect(currentTurnHasActiveRow([])).toBe(false)
    expect(currentTurnHasActiveRow(undefined)).toBe(false)
  })
})

describe('replay seed - rendering', () => {
  const chatContext = (messages) => ({
    messages,
    isWelcomeVisible: false,
    isThinking: false,
    sendChatMessage: vi.fn(),
    currentModel: 'gpt-4',
    models: [{ name: 'gpt-4', display_name: 'GPT-4', supports_tools: true }],
    tools: [],
    prompts: [],
    selectedTools: new Set(),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    sessionFiles: { files: [], total_files: 0, categories: {} },
    agentModeAvailable: true,
    agentModeEnabled: true,
    setAgentModeEnabled: vi.fn(),
    isAgentRunning: false,
    currentAgentStep: 0,
    agentPendingQuestion: null,
    setAgentPendingQuestion: vi.fn(),
    stopAgent: vi.fn(),
    answerAgentQuestion: vi.fn(),
    followUpSuggestions: [],
    setFollowUpSuggestions: vi.fn(),
    downloadFile: vi.fn(),
    appName: 'ATLAS',
    settings: {},
  })

  beforeEach(() => {
    vi.clearAllMocks()
    useWS.mockReturnValue({ isConnected: true, connectionStatus: 'Connected', sendMessage: vi.fn() })
  })

  const renderChat = (messages) => {
    useChat.mockReturnValue(chatContext(messages))
    return render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )
  }

  it('does not render the seed while an atlas_sleep row shows progress', () => {
    renderChat([userRow, sleepRow('sleeping'), seedRow])

    expect(screen.queryByTestId('stream-replay-seed')).not.toBeInTheDocument()
    expect(screen.queryByText(/answer in progress/i)).not.toBeInTheDocument()
    // The sleep row itself is still there to carry the progress.
    expect(screen.getAllByText(/atlas_sleep/).length).toBeGreaterThan(0)
  })

  it('does not render the seed even when the seed sits before the sleep row', () => {
    renderChat([userRow, seedRow, sleepRow('sleeping')])

    expect(screen.queryByTestId('stream-replay-seed')).not.toBeInTheDocument()
  })

  it('renders text streamed after the sleep below the sleep row', () => {
    const answer = { role: 'assistant', content: 'Woke up, here is the answer', timestamp: '2026-11-05T00:00:40Z', _streaming: true }
    renderChat([userRow, sleepRow('completed'), answer])

    const sleep = screen.getAllByText(/atlas_sleep/)[0]
    const text = screen.getByText('Woke up, here is the answer')
    expect(sleep.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('with no active row, shows the seed as a status line, not a gray bubble', async () => {
    renderChat([userRow, sleepRow('completed'), seedRow])

    const seed = screen.getByTestId('stream-replay-seed')
    expect(seed.className).not.toMatch(/bg-gray-800/)
    expect(seed.querySelector('.bg-gray-800')).toBeNull()
    expect(seed.querySelector('.rounded-lg')).toBeNull()
    expect(await screen.findByTestId('stream-replay-in-progress')).toBeInTheDocument()
  })

  it('a standalone seed Message renders no bubble chrome', () => {
    render(<Message message={seedRow} onRewind={null} userIndex={null} />)

    const seed = screen.getByTestId('stream-replay-seed')
    expect(seed.querySelector('.bg-gray-800')).toBeNull()
    expect(seed.className).not.toMatch(/bg-gray-800|rounded-lg/)
  })
})

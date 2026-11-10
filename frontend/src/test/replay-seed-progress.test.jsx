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
import { currentTurnHasActiveRow, currentTurnProgress } from '../utils/executionState'

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

  it('a tool row that settles after the seed keeps the seed below it', () => {
    // [user, seed] then the tool row arrives and completes: the marker is the
    // only progress left, and it must sit under the row, not above it.
    const { result } = renderHook(() => useMessages())
    act(() => { result.current.bulkAdd([userRow]) })
    act(() => { result.current.streamToken('', true) })
    act(() => { result.current.addMessage(sleepRow('completed')) })

    const rows = result.current.messages
    expect(rows.map(m => m.type || m.role)).toEqual(['user', 'tool_call', 'assistant'])
    expect(rows[2]._seed).toBe(true)
  })

  it('a snapshot reconcile that appends rows keeps the seed last', () => {
    const { result } = renderHook(() => useMessages())
    act(() => { result.current.bulkAdd([userRow]) })
    act(() => { result.current.streamToken('', true) })
    act(() => { result.current.mapMessages(prev => [...prev, sleepRow('completed')]) })

    expect(result.current.messages.map(m => m.type || m.role)).toEqual(['user', 'tool_call', 'assistant'])
    expect(result.current.messages[2]._seed).toBe(true)
  })

  it('never moves the seed past a real user message', () => {
    const { result } = renderHook(() => useMessages())
    act(() => { result.current.bulkAdd([userRow]) })
    act(() => { result.current.streamToken('', true) })
    act(() => { result.current.addMessage({ role: 'user', content: 'next question' }) })

    expect(result.current.messages.map(m => m.role)).toEqual(['user', 'assistant', 'user'])
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

describe('replay seed - notice lifecycle follows the step, not the run (issue #1193)', () => {
  const toolRow = (n, status) => ({
    role: 'system',
    type: 'tool_call',
    tool_call_id: `call_${n}`,
    invocation_id: `inv_${n}`,
    tool_name: 'notes_search',
    server_name: 'notes',
    arguments: {},
    status,
    content: 'notes_search',
    timestamp: '2026-11-05T00:00:01Z',
  })
  const reopened = (rows = [userRow]) => {
    const { result } = renderHook(() => useMessages())
    act(() => { result.current.bulkAdd(rows) })
    act(() => { result.current.streamToken('', true) })
    return result
  }
  const seedOf = result => result.current.messages.find(m => m._seed)

  it('a fresh seed has not advanced, whatever the loaded transcript already holds', () => {
    const result = reopened([userRow, toolRow(1, 'completed'), toolRow(2, 'completed')])
    expect(seedOf(result)._stepAdvanced).toBe(false)
  })

  it('a tool row that lands after the reopen advances the seed', () => {
    const result = reopened()
    act(() => { result.current.addMessage(toolRow(1, 'calling')) })
    expect(seedOf(result)._stepAdvanced).toBe(true)
  })

  it('a tool row that settles after the reopen advances the seed', () => {
    const result = reopened([userRow, toolRow(1, 'in_progress')])
    expect(seedOf(result)._stepAdvanced).toBe(false)
    act(() => { result.current.updateToolResult('call_1', { invocation_id: 'inv_1', status: 'completed', result: 'ok' }) })
    expect(seedOf(result)._stepAdvanced).toBe(true)
  })

  it('a snapshot reconcile or a transcript refresh that adds rows advances the seed', () => {
    const viaMap = reopened()
    act(() => { viaMap.current.mapMessages(prev => [...prev, toolRow(1, 'completed')]) })
    expect(seedOf(viaMap)._stepAdvanced).toBe(true)

    const viaRefresh = reopened()
    act(() => { viaRefresh.current.refreshAppend([toolRow(1, 'completed')], true) })
    expect(seedOf(viaRefresh)._stepAdvanced).toBe(true)
    expect(viaRefresh.current.messages.at(-1)._seed).toBe(true)
  })

  it('a narration row appended after the reopen advances the seed', () => {
    const viaAdd = reopened()
    act(() => { viaAdd.current.addMessage({ role: 'assistant', content: 'Looking through the notebooks first.', type: 'agent_intermediate' }) })
    expect(seedOf(viaAdd)._stepAdvanced).toBe(true)

    const viaRefresh = reopened([userRow, toolRow(1, 'completed')])
    act(() => { viaRefresh.current.refreshAppend([{ role: 'assistant', content: 'Now the tags.', type: 'chat' }], true) })
    expect(seedOf(viaRefresh)._stepAdvanced).toBe(true)
  })

  it('a row dropped before the seed advanced lowers the mark, so the next step still retires the notice', () => {
    const card = { role: 'system', type: 'tool_approval_request', tool_call_id: 'call_1', status: 'pending', content: 'Tool Approval Required' }
    const result = reopened([userRow, card])
    // The snapshot says the run is no longer waiting on the card.
    act(() => { result.current.mapMessages(prev => prev.filter(m => m.type !== 'tool_approval_request')) })
    expect(seedOf(result)._stepAdvanced).toBe(false)
    // The tool row that takes its place is a step, not a return to the mark.
    act(() => { result.current.addMessage(toolRow(1, 'calling')) })
    expect(seedOf(result)._stepAdvanced).toBe(true)
  })

  it('a user row that restarts the count does not strand the notice', () => {
    const result = reopened([userRow, toolRow(1, 'completed'), toolRow(2, 'completed')])
    act(() => { result.current.addMessage({ role: 'user', content: 'also check the archive' }) })
    expect(seedOf(result)._stepAdvanced).toBe(false)
    act(() => { result.current.addMessage(toolRow(3, 'calling')) })
    expect(seedOf(result)._stepAdvanced).toBe(true)
  })

  it('a change that is not progress leaves the seed alone', () => {
    const result = reopened([userRow, toolRow(1, 'in_progress')])
    act(() => { result.current.updateToolResult('call_1', { invocation_id: 'inv_1', status: 'in_progress', progress: 0.5 }) })
    expect(seedOf(result)._stepAdvanced).toBe(false)
  })

  it('stays advanced: another empty snapshot or a dropped row does not bring the notice back', () => {
    const result = reopened()
    act(() => { result.current.addMessage(toolRow(1, 'completed')) })
    act(() => { result.current.streamToken('', true) })
    expect(seedOf(result)._stepAdvanced).toBe(true)
    act(() => { result.current.mapMessages(prev => prev.filter(m => m.type !== 'tool_call')) })
    expect(seedOf(result)._stepAdvanced).toBe(true)
    expect(result.current.messages.filter(m => m._streaming)).toHaveLength(1)
  })

  it('a text placeholder is a genuinely incomplete segment and is never marked advanced', () => {
    const { result } = renderHook(() => useMessages())
    act(() => { result.current.bulkAdd([userRow]) })
    act(() => { result.current.streamToken('Half an ans', true) })
    act(() => { result.current.addMessage(toolRow(1, 'completed')) })
    const bubble = result.current.messages.find(m => m._streaming)
    expect(bubble._seed).toBe(false)
    expect(bubble._replayed).toBe(true)
    expect(bubble._stepAdvanced).toBeUndefined()
    expect(bubble.content).toBe('Half an ans')
  })

  it('a segment that closes leaves a seed that is already advanced', () => {
    const { result } = renderHook(() => useMessages())
    act(() => { result.current.bulkAdd([userRow]) })
    act(() => { result.current.streamToken('Let me look that up', true) })
    act(() => { result.current.streamToken('', true) })
    expect(result.current.messages.filter(m => m._streaming)).toHaveLength(1)
    expect(seedOf(result)._stepAdvanced).toBe(true)
  })

  it('the next open segment is marked incomplete again, then retired when it closes', () => {
    const result = reopened()
    act(() => { result.current.addMessage(toolRow(1, 'completed')) })
    act(() => { result.current.streamToken('Second thoughts', true) })
    const bubble = result.current.messages.find(m => m._streaming)
    expect(bubble._seed).toBe(false)
    expect(bubble._stepAdvanced).toBeUndefined()
    act(() => { result.current.streamToken('', true) })
    expect(seedOf(result)._stepAdvanced).toBe(true)
  })

  it('a reload starts over: the new seed is measured against the reloaded transcript', () => {
    const result = reopened()
    act(() => { result.current.addMessage(toolRow(1, 'completed')) })
    expect(seedOf(result)._stepAdvanced).toBe(true)
    const rows = result.current.messages.filter(m => !m._streaming)
    act(() => { result.current.resetMessages() })
    act(() => { result.current.bulkAdd(rows) })
    act(() => { result.current.streamToken('', true) })
    expect(seedOf(result)._stepAdvanced).toBe(false)
  })

  it('holds one advanced seed across 25 tool iterations', () => {
    const result = reopened()
    for (let n = 1; n <= 25; n += 1) {
      act(() => { result.current.addMessage(toolRow(n, 'calling')) })
      act(() => { result.current.updateToolResult(`call_${n}`, { invocation_id: `inv_${n}`, status: 'completed', result: 'ok' }) })
      const streaming = result.current.messages.filter(m => m._streaming)
      expect(streaming).toHaveLength(1)
      expect(streaming[0]._stepAdvanced).toBe(true)
      expect(result.current.messages.at(-1)).toBe(streaming[0])
    }
    expect(result.current.messages.filter(m => m.type === 'tool_call')).toHaveLength(25)
  })
})

describe('currentTurnProgress', () => {
  it('counts settled rows of the current turn and a terminal tool row twice', () => {
    expect(currentTurnProgress([userRow])).toBe(0)
    expect(currentTurnProgress([userRow, sleepRow('calling')])).toBe(1)
    expect(currentTurnProgress([userRow, sleepRow('completed')])).toBe(2)
    expect(currentTurnProgress([userRow, sleepRow('failed'), seedRow])).toBe(2)
  })

  it('stops at the last real user message but scans past an agent-loop answer', () => {
    expect(currentTurnProgress([userRow, sleepRow('completed'), { role: 'user', content: 'next' }])).toBe(0)
    expect(currentTurnProgress([userRow, sleepRow('completed'), { role: 'user', content: 'ok', _agentInput: true }])).toBe(3)
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

  it('ignores an auto-approved card: it is mounted but hidden, so it shows nothing', () => {
    const card = { role: 'system', type: 'tool_approval_request', status: 'pending' }
    expect(currentTurnHasActiveRow([userRow, card, seedRow], { autoApproveTools: true })).toBe(false)
    expect(currentTurnHasActiveRow([userRow, { ...card, auto_approved: true }, seedRow])).toBe(false)
    // An admin-required card is never auto-approved, so it is still visible.
    expect(currentTurnHasActiveRow([userRow, { ...card, admin_required: true }, seedRow], { autoApproveTools: true })).toBe(true)
  })

  it('scans past an agent-loop answer, which belongs to the running turn', () => {
    const answer = { role: 'user', content: 'use the second file', _agentInput: true }
    expect(currentTurnHasActiveRow([userRow, sleepRow('sleeping'), answer, seedRow])).toBe(true)
  })

  it('is false once the tool settles, and ignores rows from earlier turns', () => {
    expect(currentTurnHasActiveRow([userRow, sleepRow('completed'), seedRow])).toBe(false)
    expect(currentTurnHasActiveRow([sleepRow('sleeping'), userRow, seedRow])).toBe(false)
    expect(currentTurnHasActiveRow([])).toBe(false)
    expect(currentTurnHasActiveRow(undefined)).toBe(false)
  })
})

describe('replay seed - rendering', () => {
  const chatContext = (messages, settings = {}, overrides = {}) => ({
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
    settings,
    ...overrides,
  })

  beforeEach(() => {
    vi.clearAllMocks()
    useWS.mockReturnValue({ isConnected: true, connectionStatus: 'Connected', sendMessage: vi.fn() })
  })

  const renderChat = (messages, settings, overrides) => {
    useChat.mockReturnValue(chatContext(messages, settings, overrides))
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

  it('keeps the seed marker when the only pending card is auto-approved (hidden)', async () => {
    const card = {
      role: 'system',
      type: 'tool_approval_request',
      tool_call_id: 'call_x',
      tool_name: 'atlas_sleep',
      arguments: {},
      status: 'pending',
      content: 'Tool Approval Required: atlas_sleep',
      timestamp: '2026-11-05T00:00:01Z',
    }
    renderChat([userRow, card, seedRow], { autoApproveTools: true })

    expect(screen.getByTestId('stream-replay-seed')).toBeInTheDocument()
    expect(await screen.findByTestId('stream-replay-in-progress')).toBeInTheDocument()
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

  it('once the tool row settles, the marker shows below it', async () => {
    renderChat([userRow, sleepRow('completed'), seedRow])

    const sleep = screen.getAllByText(/atlas_sleep/)[0]
    const marker = await screen.findByTestId('stream-replay-in-progress')
    expect(sleep.compareDocumentPosition(marker) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  // Issue #1193: once the view has shown a newer step, the seed no longer
  // marks an unfinished answer.
  const advancedSeed = { ...seedRow, _stepAdvanced: true }

  it('an advanced seed is not drawn while the agent-run status shows the run', async () => {
    renderChat([userRow, sleepRow('completed'), advancedSeed], {}, { isAgentRunning: true, currentAgentStep: 3 })

    expect(screen.queryByTestId('stream-replay-seed')).not.toBeInTheDocument()
    expect(screen.queryByText(/answer in progress/i)).not.toBeInTheDocument()
    // The run itself stays visible until it completes.
    expect(screen.getByTestId('agent-run-status')).toBeInTheDocument()
    expect(screen.getByTestId('agent-busy-indicator')).toBeInTheDocument()
  })

  it('a seed that has not advanced still shows the notice during an agent run', async () => {
    renderChat([userRow, sleepRow('completed'), seedRow], {}, { isAgentRunning: true })

    expect(await screen.findByTestId('stream-replay-in-progress')).toBeInTheDocument()
  })

  it('an advanced seed with no other run indicator says the run is going, not that the view is stale', async () => {
    renderChat([userRow, sleepRow('completed'), advancedSeed])

    const marker = await screen.findByTestId('stream-replay-run-active')
    expect(marker).toHaveTextContent(/still working/i)
    expect(screen.queryByText(/answer in progress/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/refresh when the response finishes/i)).not.toBeInTheDocument()
  })

  it('a replayed text bubble keeps the incomplete-answer notice during an agent run', async () => {
    const partial = { role: 'assistant', content: 'Half an ans', timestamp: '2026-11-05T00:00:03Z', _streaming: true, _replayed: true, _seed: false }
    renderChat([userRow, sleepRow('completed'), partial], {}, { isAgentRunning: true })

    expect(screen.getByText('Half an ans')).toBeInTheDocument()
    expect(await screen.findByTestId('stream-replay-in-progress')).toHaveTextContent(/answer in progress/i)
  })

  it('a standalone seed Message renders no bubble chrome', () => {
    render(<Message message={seedRow} onRewind={null} userIndex={null} />)

    const seed = screen.getByTestId('stream-replay-seed')
    expect(seed.querySelector('.bg-gray-800')).toBeNull()
    expect(seed.className).not.toMatch(/bg-gray-800|rounded-lg/)
  })
})

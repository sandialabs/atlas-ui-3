// Issue #1218: a run the server cancels because its coordination claim lapsed
// is told to the user, in the conversation on screen and in the history list.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, renderHook, act } from '@testing-library/react'

import { useConversationRuns } from '../hooks/chat/useConversationRuns'
import { createWebSocketHandler, resetInterruptedRunNotices } from '../handlers/chat/websocketHandlers'
import { interruptedRunNotice } from '../utils/interruptedRunNotice'
import { getDisplayConversations } from '../utils/getDisplayConversations'
import { arrangeConversations } from '../utils/conversationTree'
import { RunIndicator } from '../components/ConversationTreeRow'

const makeHandler = (visibleConversationId = 'conv-1') => {
  const deps = {
    addMessage: vi.fn(),
    mapMessages: vi.fn(),
    setIsThinking: vi.fn(),
    setIsAgentRunning: vi.fn(),
    setIsSynthesizing: vi.fn(),
    setCurrentAgentStep: vi.fn(),
    streamToken: vi.fn(),
    streamEnd: vi.fn(),
    onRunStatus: vi.fn(),
    getVisibleConversationId: () => visibleConversationId,
  }
  return { deps, handler: createWebSocketHandler(deps) }
}

const run = (overrides = {}) => ({
  run_id: 'run-1',
  conversation_id: 'conv-1',
  status: 'failed',
  end_reason: 'fenced',
  ...overrides,
})

const statusFrame = (r) => ({ type: 'run_status', run: r })

describe('interrupted run notice in the transcript', () => {
  beforeEach(() => resetInterruptedRunNotices())

  it('shows a plain-language notice for a fenced run in the conversation on screen', () => {
    const { deps, handler } = makeHandler()
    handler(statusFrame(run()))

    expect(deps.addMessage).toHaveBeenCalledTimes(1)
    const message = deps.addMessage.mock.calls[0][0]
    expect(message.role).toBe('system')
    expect(message.content).toContain('interrupted')
    expect(message.content).toContain('coordination service')
    expect(message.content).toContain('not saved')
    expect(message.content).toContain('Send your message again')
    // The idle state is restored: nothing is left "thinking".
    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
    expect(deps.setIsAgentRunning).toHaveBeenCalledWith(false)
    // The run tracker still sees the frame (it drives the sidebar).
    expect(deps.onRunStatus).toHaveBeenCalledTimes(1)
  })

  it('shows nothing for a normal user stop', () => {
    const { deps, handler } = makeHandler()
    handler(statusFrame(run({ status: 'cancelled', end_reason: 'user_cancelled' })))
    expect(deps.addMessage).not.toHaveBeenCalled()
  })

  it('shows nothing for a completed run, a live run or a shutdown', () => {
    const { deps, handler } = makeHandler()
    handler(statusFrame(run({ status: 'completed', end_reason: 'completed' })))
    handler(statusFrame(run({ run_id: 'run-2', status: 'running', end_reason: null })))
    handler(statusFrame(run({ run_id: 'run-3', end_reason: 'replica_shutdown' })))
    expect(deps.addMessage).not.toHaveBeenCalled()
  })

  it('does not put the notice into a conversation that is not on screen', () => {
    const { deps, handler } = makeHandler('another-conversation')
    handler(statusFrame(run()))
    expect(deps.addMessage).not.toHaveBeenCalled()
    expect(deps.onRunStatus).toHaveBeenCalledTimes(1)
  })

  it('shows the notice once per run', () => {
    const { deps, handler } = makeHandler()
    handler(statusFrame(run()))
    handler(statusFrame(run()))
    expect(deps.addMessage).toHaveBeenCalledTimes(1)
  })

  it('does not warn about a run that already delivered its answer', () => {
    const { deps, handler } = makeHandler()
    handler(statusFrame(run({ status: 'completed', end_reason: 'completed' })))
    handler(statusFrame(run()))
    expect(deps.addMessage).not.toHaveBeenCalled()
  })

  it('remembers a finished run across a rebuilt handler', () => {
    const first = makeHandler()
    first.handler(statusFrame(run({ status: 'completed', end_reason: 'completed' })))
    const second = makeHandler()
    second.handler(statusFrame(run()))
    expect(second.deps.addMessage).not.toHaveBeenCalled()
  })

  it('does not announce runs from a reconnect snapshot', () => {
    const { deps, handler } = makeHandler()
    handler({ type: 'runs_snapshot', runs: [run()] })
    expect(deps.addMessage).not.toHaveBeenCalled()
  })

  it('has a notice only for the fenced reason', () => {
    expect(interruptedRunNotice(run({ end_reason: 'owner_lost' }))).toBeNull()
    expect(interruptedRunNotice(run({ end_reason: 'error' }))).toBeNull()
  })

  it('does not warn about a run that failed or shut down and then saved its turn', () => {
    for (const end_reason of ['error', 'replica_shutdown']) {
      const { deps, handler } = makeHandler()
      const id = `run-${end_reason}`
      handler(statusFrame(run({ end_reason, run_id: id })))
      handler({ type: 'conversation_saved', conversation_id: 'conv-1', run_id: id })
      handler(statusFrame(run({ run_id: id })))
      expect(deps.addMessage).not.toHaveBeenCalled()
    }
  })

  it('remembers a save that arrived while the conversation was off screen', () => {
    const { handler } = makeHandler('another-conversation')
    handler({ type: 'conversation_saved', conversation_id: 'conv-1', run_id: 'run-1' })
    handler(statusFrame(run({ status: 'cancelled', end_reason: 'user_cancelled' })))
    const back = makeHandler('conv-1')
    back.handler(statusFrame(run()))
    expect(back.deps.addMessage).not.toHaveBeenCalled()
  })

  it('still warns when a stopped run is fenced before its turn was saved', () => {
    const { deps, handler } = makeHandler()
    handler(statusFrame(run({ status: 'cancelled', end_reason: 'user_cancelled' })))
    handler(statusFrame(run()))
    expect(deps.addMessage).toHaveBeenCalledTimes(1)
  })

  it('treats a fence for the current run as current after a reconnect snapshot', () => {
    const { deps, handler } = makeHandler()
    handler({ type: 'run_started', run_id: 'run-old', conversation_id: 'conv-1' })
    handler({ type: 'runs_snapshot', runs: [] })
    handler(statusFrame(run({ run_id: 'run-old' })))
    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
  })

  it('rebuilds the current-run map from a snapshot, so an older late fence keeps Stop', () => {
    const { deps, handler } = makeHandler()
    handler({ type: 'runs_snapshot', runs: [run({ run_id: 'run-new', status: 'running', end_reason: null })] })
    handler(statusFrame(run({ run_id: 'run-old' })))
    expect(deps.addMessage).toHaveBeenCalledTimes(1)
    expect(deps.setIsThinking).not.toHaveBeenCalled()
  })

  it('settles the run a rebinding save names, and tells the tracker', () => {
    const { deps, handler } = makeHandler()
    handler(statusFrame(run({ status: 'cancelled', end_reason: 'user_cancelled' })))
    handler({ type: 'conversation_saved', conversation_id: 'conv-branch', saved_run_id: 'run-1', saved_from_conversation_id: 'conv-1' })
    handler(statusFrame(run()))
    expect(deps.addMessage).not.toHaveBeenCalled()
    expect(deps.onRunStatus).toHaveBeenCalledWith({ type: 'run_saved', run_id: 'run-1', conversation_id: 'conv-1' })
  })

  it('does not settle the visible run for a save from a background conversation', () => {
    const { deps, handler } = makeHandler('conv-1')
    handler({ type: 'conversation_saved', conversation_id: 'bg-branch', saved_run_id: 'bg-run', saved_from_conversation_id: 'bg-conv' })
    handler(statusFrame(run({ run_id: 'visible-run' })))
    expect(deps.addMessage).toHaveBeenCalledTimes(1)
  })

  it('does not treat an ended run as the current one', () => {
    const { deps, handler } = makeHandler()
    handler({ type: 'run_started', run_id: 'run-a', conversation_id: 'conv-1' })
    handler(statusFrame(run({ run_id: 'run-a', status: 'completed', end_reason: 'completed' })))
    handler({ type: 'run_started', run_id: 'run-b', conversation_id: 'conv-1' })
    handler(statusFrame(run({ run_id: 'run-b', status: 'completed', end_reason: 'completed' })))
    handler(statusFrame(run({ run_id: 'run-c' })))
    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
  })

  it('leaves the busy indicators alone when a newer run owns the conversation', () => {
    const { deps, handler } = makeHandler()
    handler({ type: 'run_started', run_id: 'run-old', conversation_id: 'conv-1' })
    handler({ type: 'run_started', run_id: 'run-new', conversation_id: 'conv-1' })
    handler(statusFrame(run({ run_id: 'run-old' })))
    expect(deps.addMessage).toHaveBeenCalledTimes(1)
    expect(deps.setIsThinking).not.toHaveBeenCalled()
    expect(deps.setIsAgentRunning).not.toHaveBeenCalled()
    expect(deps.setIsSynthesizing).not.toHaveBeenCalled()
  })

  it('clears the busy indicators when the fenced run is the current one', () => {
    const { deps, handler } = makeHandler()
    handler({ type: 'run_started', run_id: 'run-1', conversation_id: 'conv-1' })
    handler(statusFrame(run()))
    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
  })
})

describe('interrupted run in the history list', () => {
  const base = {
    conversations: [],
    messages: [],
    activeConversationId: null,
    chatHistoryEnabled: true,
    saveMode: 'server',
  }

  it('keeps the row of a fenced run whose turn was never saved', () => {
    const list = getDisplayConversations({
      ...base,
      runsByConversation: { c: run({ conversation_id: 'c', title: 'Long task', created_at: 1789879000 }) },
    })
    expect(list[0]).toMatchObject({ id: 'c', title: 'Long task', _run: true })
  })

  it('still drops a run that was stopped or completed', () => {
    const list = getDisplayConversations({
      ...base,
      runsByConversation: {
        a: run({ conversation_id: 'a', status: 'cancelled', end_reason: 'user_cancelled' }),
        b: run({ conversation_id: 'b', status: 'completed', end_reason: 'completed' }),
      },
    })
    expect(list).toEqual([])
  })

  it('labels the fenced run Interrupted, not Failed', () => {
    render(<RunIndicator run={run()} />)
    expect(screen.getByText('Interrupted')).toBeTruthy()
    expect(screen.queryByText('Failed')).toBeNull()
  })

  it('keeps Failed for an ordinary failed run', () => {
    render(<RunIndicator run={run({ end_reason: 'error' })} />)
    expect(screen.getByText('Failed')).toBeTruthy()
  })
})

describe('completed run later fenced', () => {
  it('stays completed in the run tracker, so the history list does not flag it', () => {
    const { result } = renderHook(() => useConversationRuns())
    act(() => result.current.handleRunFrame(statusFrame(run({ status: 'completed', end_reason: 'completed' }))))
    act(() => result.current.handleRunFrame(statusFrame(run())))
    expect(result.current.runsByConversation['conv-1'].status).toBe('completed')
  })

  it.each(['error', 'replica_shutdown', 'user_cancelled'])('ignores a fence after the run ended with %s and saved', (end_reason) => {
    const { result } = renderHook(() => useConversationRuns())
    const status = end_reason === 'user_cancelled' ? 'cancelled' : 'failed'
    act(() => result.current.handleRunFrame(statusFrame(run({ status, end_reason }))))
    act(() => result.current.handleRunFrame({ type: 'run_saved', run_id: 'run-1', conversation_id: 'conv-1' }))
    act(() => result.current.handleRunFrame(statusFrame(run())))
    expect(result.current.runsByConversation['conv-1'].end_reason).toBe(end_reason)
  })

  it('records a fence on a stopped run whose turn was not saved yet', () => {
    const { result } = renderHook(() => useConversationRuns())
    act(() => result.current.handleRunFrame(statusFrame(run({ status: 'cancelled', end_reason: 'user_cancelled' }))))
    act(() => result.current.handleRunFrame(statusFrame(run())))
    expect(result.current.runsByConversation['conv-1'].end_reason).toBe('fenced')
  })

  it('ignores a late fence for an older run even when the newer run already ended', () => {
    const { result } = renderHook(() => useConversationRuns())
    act(() => result.current.handleRunFrame(statusFrame(run({ run_id: 'run-new', status: 'completed', end_reason: 'completed' }))))
    act(() => result.current.handleRunFrame(statusFrame(run({ run_id: 'run-old' }))))
    expect(result.current.runsByConversation['conv-1']).toMatchObject({ run_id: 'run-new', status: 'completed' })
  })

  it('keeps the saved marker through a reconnect snapshot', () => {
    const { result } = renderHook(() => useConversationRuns())
    act(() => result.current.handleRunFrame(statusFrame(run({ status: 'cancelled', end_reason: 'user_cancelled' }))))
    act(() => result.current.handleRunFrame({ type: 'run_saved', run_id: 'run-1', conversation_id: 'conv-1' }))
    act(() => result.current.handleRunFrame({ type: 'runs_snapshot', runs: [run({ status: 'cancelled', end_reason: 'user_cancelled' })] }))
    act(() => result.current.handleRunFrame(statusFrame(run())))
    expect(result.current.runsByConversation['conv-1'].end_reason).toBe('user_cancelled')
  })

  it('finds the run by id when a save names a rebound conversation', () => {
    const { result } = renderHook(() => useConversationRuns())
    act(() => result.current.handleRunFrame(statusFrame(run({ status: 'cancelled', end_reason: 'user_cancelled' }))))
    act(() => result.current.handleRunFrame({ type: 'run_saved', run_id: 'run-1', conversation_id: 'conv-branch' }))
    act(() => result.current.handleRunFrame(statusFrame(run())))
    expect(result.current.runsByConversation['conv-1'].end_reason).toBe('user_cancelled')
  })

  it('ignores a late fence for an older run while a newer run is live', () => {
    const { result } = renderHook(() => useConversationRuns())
    act(() => result.current.handleRunFrame(statusFrame(run({ run_id: 'run-new', status: 'running', end_reason: null }))))
    act(() => result.current.handleRunFrame(statusFrame(run({ run_id: 'run-old' }))))
    expect(result.current.runsByConversation['conv-1']).toMatchObject({ run_id: 'run-new', status: 'running' })
  })

  it('records a fence on a run that was still running', () => {
    const { result } = renderHook(() => useConversationRuns())
    act(() => result.current.handleRunFrame(statusFrame(run({ status: 'running', end_reason: null }))))
    act(() => result.current.handleRunFrame(statusFrame(run())))
    expect(result.current.runsByConversation['conv-1'].end_reason).toBe('fenced')
  })
})

describe('collapsed parent of a fenced child', () => {
  const conv = (id, extra = {}) => ({ id, title: id, updated_at: '2026-11-23T00:00:00Z', ...extra })

  it('carries the end reason so the marker reads Interrupted', () => {
    const rows = arrangeConversations([conv('p'), conv('c', { parent_conversation_id: 'p' })], {
      c: { conversation_id: 'c', status: 'failed', end_reason: 'fenced' },
    })
    expect(rows[0]._aggregateRun).toMatchObject({ status: 'failed', end_reason: 'fenced' })
  })

  it('reads as a plain failure when the failed children ended differently', () => {
    const rows = arrangeConversations([
      conv('p'), conv('a', { parent_conversation_id: 'p' }), conv('b', { parent_conversation_id: 'p' }),
    ], {
      a: { conversation_id: 'a', status: 'failed', end_reason: 'fenced' },
      b: { conversation_id: 'b', status: 'failed', end_reason: 'error' },
    })
    expect(rows[0]._aggregateRun.end_reason).toBeNull()
  })
})

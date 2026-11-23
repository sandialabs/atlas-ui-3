// Issue #1218: a run the server cancels because its coordination claim lapsed
// is told to the user, in the conversation on screen and in the history list.

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'
import { interruptedRunNotice } from '../utils/interruptedRunNotice'
import { getDisplayConversations } from '../utils/getDisplayConversations'
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

  it('does not announce runs from a reconnect snapshot', () => {
    const { deps, handler } = makeHandler()
    handler({ type: 'runs_snapshot', runs: [run()] })
    expect(deps.addMessage).not.toHaveBeenCalled()
  })

  it('words an orphaned run differently from a lost coordination service', () => {
    expect(interruptedRunNotice(run({ end_reason: 'owner_lost' }))).toContain('stopped responding')
    expect(interruptedRunNotice(run({ end_reason: 'error' }))).toBeNull()
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

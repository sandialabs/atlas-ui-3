// Frontend routing for parallel conversation runs (issue #884).
//
// Two things have to hold on the client: an event for a conversation that is
// not on screen must never be spliced into the visible transcript, and a run in
// another conversation must still be visible as an indicator.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

import { createWebSocketHandler, cleanupStreamState } from '../handlers/chat/websocketHandlers'
import { useConversationRuns, isRunActive } from '../hooks/chat/useConversationRuns'

const makeDeps = (overrides = {}) => ({
  addMessage: vi.fn(),
  mapMessages: vi.fn(),
  setIsThinking: vi.fn(),
  setIsAgentRunning: vi.fn(),
  setCurrentAgentStep: vi.fn(),
  setIsSynthesizing: vi.fn(),
  setActiveConversationId: vi.fn(),
  streamToken: vi.fn(),
  streamEnd: vi.fn(),
  ...overrides,
})

describe('event routing by conversation', () => {
  beforeEach(() => cleanupStreamState())

  it('applies events for the conversation on screen', () => {
    const deps = makeDeps({ getVisibleConversationId: () => 'conv-a' })
    const handler = createWebSocketHandler(deps)

    handler({ type: 'tool_start', tool_name: 'search', tool_call_id: 't1', conversation_id: 'conv-a', run_id: 'run-a' })

    expect(deps.addMessage).toHaveBeenCalled()
  })

  it('drops events belonging to a background conversation', () => {
    const onRunStatus = vi.fn()
    const deps = makeDeps({ getVisibleConversationId: () => 'conv-a', onRunStatus })
    const handler = createWebSocketHandler(deps)

    handler({ type: 'tool_start', tool_name: 'search', tool_call_id: 't1', conversation_id: 'conv-b', run_id: 'run-b' })

    expect(deps.addMessage).not.toHaveBeenCalled()
    expect(onRunStatus).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'background_activity', conversation_id: 'conv-b' })
    )
  })

  it('does not let a background run steal the active conversation id', () => {
    const deps = makeDeps({ getVisibleConversationId: () => 'conv-a' })
    const handler = createWebSocketHandler(deps)

    handler({ type: 'conversation_saved', conversation_id: 'conv-b', run_id: 'run-b' })

    expect(deps.setActiveConversationId).not.toHaveBeenCalled()
  })

  it('does not end the visible turn when a background run completes', () => {
    const deps = makeDeps({ getVisibleConversationId: () => 'conv-a' })
    const handler = createWebSocketHandler(deps)

    handler({ type: 'response_complete', conversation_id: 'conv-b', run_id: 'run-b' })

    expect(deps.setIsThinking).not.toHaveBeenCalled()
  })

  it('applies untagged events, so the single-run path is unchanged', () => {
    const deps = makeDeps({ getVisibleConversationId: () => 'conv-a' })
    const handler = createWebSocketHandler(deps)

    handler({ type: 'response_complete' })

    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
  })

  it('does not fill a fresh empty chat with a background run\'s output', () => {
    // Nothing is on screen yet, so there is no conversation to compare against
    // -- but a tagged event still belongs to a run somewhere else.
    const deps = makeDeps({ getVisibleConversationId: () => null })
    const handler = createWebSocketHandler(deps)

    handler({ type: 'response_complete', conversation_id: 'conv-b', run_id: 'run-b' })

    expect(deps.setIsThinking).not.toHaveBeenCalled()
  })

  it('applies untagged events even when no conversation is selected', () => {
    const deps = makeDeps({ getVisibleConversationId: () => null })
    const handler = createWebSocketHandler(deps)

    handler({ type: 'response_complete', conversation_id: 'conv-b' })

    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
  })

  it('routes run lifecycle frames to the run tracker, not the transcript', () => {
    const onRunStatus = vi.fn()
    const deps = makeDeps({ getVisibleConversationId: () => 'conv-a', onRunStatus })
    const handler = createWebSocketHandler(deps)

    handler({ type: 'run_started', run_id: 'run-1', conversation_id: 'conv-b' })
    handler({ type: 'run_status', run: { run_id: 'run-1', conversation_id: 'conv-b', status: 'running' } })

    expect(deps.addMessage).not.toHaveBeenCalled()
    expect(onRunStatus).toHaveBeenCalledTimes(2)
  })
})

describe('useConversationRuns', () => {
  it('tracks runs per conversation and counts the active ones', () => {
    const { result } = renderHook(() => useConversationRuns())

    act(() => {
      result.current.handleRunFrame({ type: 'run_started', run_id: 'run-a', conversation_id: 'conv-a' })
      result.current.handleRunFrame({ type: 'run_started', run_id: 'run-b', conversation_id: 'conv-b' })
    })

    expect(result.current.activeRunCount).toBe(2)
    expect(result.current.runsByConversation['conv-a'].run_id).toBe('run-a')
  })

  it('stops counting a run once it reaches a terminal state', () => {
    const { result } = renderHook(() => useConversationRuns())

    act(() => {
      result.current.handleRunFrame({ type: 'run_started', run_id: 'run-a', conversation_id: 'conv-a' })
      result.current.handleRunFrame({
        type: 'run_status',
        run: { run_id: 'run-a', conversation_id: 'conv-a', status: 'completed' },
      })
    })

    expect(result.current.activeRunCount).toBe(0)
    expect(result.current.runsByConversation['conv-a'].status).toBe('completed')
  })

  it('rebuilds state from a snapshot after reconnecting', () => {
    const { result } = renderHook(() => useConversationRuns())

    act(() => {
      result.current.handleRunFrame({
        type: 'runs_snapshot',
        max_concurrent_runs_per_user: 5,
        runs: [
          { run_id: 'r1', conversation_id: 'conv-a', status: 'running', created_at: 2 },
          { run_id: 'r0', conversation_id: 'conv-a', status: 'completed', created_at: 1 },
          { run_id: 'r2', conversation_id: 'conv-b', status: 'waiting_for_input', created_at: 3 },
        ],
      })
    })

    // Newest run wins for a conversation that has had several.
    expect(result.current.runsByConversation['conv-a'].run_id).toBe('r1')
    expect(result.current.activeRunCount).toBe(2)
    expect(result.current.maxConcurrentRuns).toBe(5)
  })

  it('treats an approval-paused run as active', () => {
    expect(isRunActive({ status: 'waiting_for_input' })).toBe(true)
    expect(isRunActive({ status: 'running' })).toBe(true)
    expect(isRunActive({ status: 'cancelled' })).toBe(false)
    expect(isRunActive(null)).toBe(false)
  })
})

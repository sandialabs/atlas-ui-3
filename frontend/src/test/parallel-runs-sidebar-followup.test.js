// Follow-up to issue #884: the history list keeps a running conversation
// visible after the user navigates away from it, and the run tracker keeps
// the name it was given before the conversation is saved.

import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'

import { getDisplayConversations } from '../utils/getDisplayConversations'
import { useConversationRuns } from '../hooks/chat/useConversationRuns'

const base = {
  conversations: [{ id: 'saved-1', title: 'Saved one', updated_at: '2026-09-19T00:00:00Z', message_count: 2 }],
  messages: [],
  activeConversationId: null,
  chatHistoryEnabled: true,
  saveMode: 'server',
}

describe('getDisplayConversations with runs in flight', () => {
  it('lists an active run whose conversation is not saved yet', () => {
    const list = getDisplayConversations({
      ...base,
      runsByConversation: {
        'run-conv': { run_id: 'r1', conversation_id: 'run-conv', status: 'running', title: 'Long task', created_at: 1789879000 },
      },
    })
    expect(list[0]).toMatchObject({ id: 'run-conv', title: 'Long task', _run: true })
    expect(list[1].id).toBe('saved-1')
  })

  it('names an unnamed run rather than dropping it', () => {
    const list = getDisplayConversations({
      ...base,
      runsByConversation: { c: { run_id: 'r1', conversation_id: 'c', status: 'waiting_for_input' } },
    })
    expect(list[0].title).toBe('Conversation in progress')
  })

  it('does not duplicate a run whose conversation is already listed', () => {
    const list = getDisplayConversations({
      ...base,
      runsByConversation: { 'saved-1': { run_id: 'r1', conversation_id: 'saved-1', status: 'running' } },
    })
    expect(list.filter(c => c.id === 'saved-1')).toHaveLength(1)
    expect(list[0]._run).toBeUndefined()
  })

  it('drops the run row once the run is terminal', () => {
    for (const status of ['completed', 'failed', 'cancelled']) {
      const list = getDisplayConversations({
        ...base,
        runsByConversation: { c: { run_id: 'r1', conversation_id: 'c', status } },
      })
      expect(list.some(c => c.id === 'c')).toBe(false)
    }
  })

  it('shows nothing extra when history is off or the save mode is none', () => {
    const runs = { c: { run_id: 'r1', conversation_id: 'c', status: 'running' } }
    expect(getDisplayConversations({ ...base, chatHistoryEnabled: false, runsByConversation: runs })).toHaveLength(1)
    expect(getDisplayConversations({ ...base, saveMode: 'none', runsByConversation: runs })).toHaveLength(1)
  })
})

describe('useConversationRuns titles', () => {
  it('keeps the title given at run_started through status frames and snapshots', () => {
    const { result } = renderHook(() => useConversationRuns())
    act(() => {
      result.current.handleRunFrame({ type: 'run_started', run_id: 'r1', conversation_id: 'c', title: 'First prompt' })
    })
    expect(result.current.runsByConversation.c.title).toBe('First prompt')

    act(() => {
      result.current.handleRunFrame({ type: 'run_status', run: { run_id: 'r1', conversation_id: 'c', status: 'waiting_for_input' } })
    })
    expect(result.current.runsByConversation.c).toMatchObject({ status: 'waiting_for_input', title: 'First prompt' })

    act(() => {
      result.current.handleRunFrame({ type: 'runs_snapshot', runs: [{ run_id: 'r1', conversation_id: 'c', status: 'running', created_at: 1 }] })
    })
    expect(result.current.runsByConversation.c.title).toBe('First prompt')
  })

  it('prefers a title the server sends', () => {
    const { result } = renderHook(() => useConversationRuns())
    act(() => {
      result.current.handleRunFrame({ type: 'runs_snapshot', runs: [{ run_id: 'r1', conversation_id: 'c', status: 'running', title: 'From server', created_at: 1 }] })
    })
    expect(result.current.runsByConversation.c.title).toBe('From server')
    act(() => {
      result.current.handleRunFrame({ type: 'run_status', run: { run_id: 'r1', conversation_id: 'c', status: 'running', title: 'Renamed' } })
    })
    expect(result.current.runsByConversation.c.title).toBe('Renamed')
  })
})

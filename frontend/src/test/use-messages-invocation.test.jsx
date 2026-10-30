import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { useMessages } from '../hooks/chat/useMessages'

describe('useMessages invocation updates', () => {
  it('updates only the invocation a patch names', () => {
    const { result } = renderHook(() => useMessages())
    act(() => {
      result.current.addMessage({ tool_call_id: 'call_0', invocation_id: 'run-1:0:call_0', status: 'completed' })
      result.current.addMessage({ tool_call_id: 'call_0', invocation_id: 'run-1:1:call_0', status: 'calling' })
      result.current.addMessage({ tool_call_id: 'call_0', status: 'calling' })
    })
    act(() => {
      result.current.updateToolResult('call_0', { invocation_id: 'run-1:1:call_0', status: 'in_progress' })
    })
    const [first, second, legacy] = result.current.messages
    expect(first.status).toBe('completed')
    expect(second.status).toBe('in_progress')
    expect(legacy.status).toBe('calling')
  })

  it('patches a legacy row when no exact invocation exists yet', () => {
    const { result } = renderHook(() => useMessages())
    act(() => {
      result.current.addMessage({ tool_call_id: 'call_0', status: 'calling' })
    })
    act(() => {
      result.current.updateToolResult('call_0', { invocation_id: 'run-1:0:call_0', status: 'completed' })
    })
    expect(result.current.messages[0].status).toBe('completed')
    expect(result.current.messages[0].invocation_id).toBe('run-1:0:call_0')
  })

  it('does not patch an invocation row from a bare tool_call_id', () => {
    const { result } = renderHook(() => useMessages())
    act(() => {
      result.current.addMessage({ tool_call_id: 'call_0', invocation_id: 'run-1:0:call_0', status: 'completed' })
      result.current.addMessage({ tool_call_id: 'call_0', status: 'calling' })
    })
    act(() => {
      result.current.updateToolResult('call_0', { status: 'failed' })
    })
    expect(result.current.messages[0].status).toBe('completed')
    expect(result.current.messages[1].status).toBe('failed')
  })

  it('does not patch an earlier turn\'s settled legacy row with a new invocation', () => {
    const { result } = renderHook(() => useMessages())
    act(() => {
      result.current.addMessage({ tool_call_id: 'call_0', status: 'completed', result: 'old turn' })
    })
    act(() => {
      result.current.updateToolResult('call_0', { invocation_id: 'run-2:0:call_0', status: 'rejected' })
    })
    expect(result.current.messages[0].status).toBe('completed')
    expect(result.current.messages[0].invocation_id).toBeUndefined()
  })
})

import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useMessages } from '../hooks/chat/useMessages'

describe('useMessages - reasoning streaming actions', () => {
  it('STREAM_REASONING_TOKEN creates a new streaming message when none exists', () => {
    const { result } = renderHook(() => useMessages())

    act(() => result.current.streamReasoningToken('Let me think'))

    expect(result.current.messages).toHaveLength(1)
    const msg = result.current.messages[0]
    expect(msg.role).toBe('assistant')
    expect(msg.reasoning_content).toBe('Let me think')
    expect(msg.content).toBe('')
    expect(msg._streaming).toBe(true)
    expect(msg._reasoningStreaming).toBe(true)
  })

  it('STREAM_REASONING_TOKEN appends to existing streaming message', () => {
    const { result } = renderHook(() => useMessages())

    act(() => result.current.streamReasoningToken('Step 1. '))
    act(() => result.current.streamReasoningToken('Step 2.'))

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].reasoning_content).toBe('Step 1. Step 2.')
  })

  it('STREAM_REASONING_END clears _reasoningStreaming but keeps _streaming', () => {
    const { result } = renderHook(() => useMessages())

    act(() => result.current.streamReasoningToken('thinking'))
    act(() => result.current.streamReasoningEnd())

    const msg = result.current.messages[0]
    expect(msg._reasoningStreaming).toBe(false)
    expect(msg._streaming).toBe(true) // still streaming (content may follow)
  })

  it('STREAM_TOKEN after reasoning appends content to same message', () => {
    const { result } = renderHook(() => useMessages())

    act(() => result.current.streamReasoningToken('reasoning'))
    act(() => result.current.streamReasoningEnd())
    act(() => result.current.streamToken('Answer: 42'))

    const msg = result.current.messages[0]
    expect(msg.reasoning_content).toBe('reasoning')
    expect(msg.content).toBe('Answer: 42')
    expect(msg._reasoningStreaming).toBe(false)
  })

  it('STREAM_END clears both _streaming and _reasoningStreaming', () => {
    const { result } = renderHook(() => useMessages())

    act(() => result.current.streamReasoningToken('thinking'))
    act(() => result.current.streamEnd())

    const msg = result.current.messages[0]
    expect(msg._streaming).toBe(false)
    expect(msg._reasoningStreaming).toBe(false)
  })

  it('new STREAM_REASONING_TOKEN after STREAM_END creates a separate message', () => {
    const { result } = renderHook(() => useMessages())

    // First message: reasoning then end
    act(() => result.current.streamReasoningToken('first reasoning'))
    act(() => result.current.streamEnd())

    // Add a system message (tool call) between
    act(() => result.current.addMessage({ role: 'system', content: 'Tool called' }))

    // Second message: synthesis reasoning
    act(() => result.current.streamReasoningToken('synthesis reasoning'))

    expect(result.current.messages).toHaveLength(3)
    expect(result.current.messages[0].reasoning_content).toBe('first reasoning')
    expect(result.current.messages[0]._streaming).toBe(false)
    expect(result.current.messages[1].role).toBe('system')
    expect(result.current.messages[2].reasoning_content).toBe('synthesis reasoning')
    expect(result.current.messages[2]._streaming).toBe(true)
  })

  it('reasoning-only message (no content) renders with empty content', () => {
    const { result } = renderHook(() => useMessages())

    act(() => result.current.streamReasoningToken('I should call a tool'))
    act(() => result.current.streamReasoningEnd())
    act(() => result.current.streamEnd())

    const msg = result.current.messages[0]
    expect(msg.reasoning_content).toBe('I should call a tool')
    expect(msg.content).toBe('')
    expect(msg._streaming).toBe(false)
  })
})

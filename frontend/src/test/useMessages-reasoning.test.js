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

  it('STREAM_REASONING_TOKEN appends to the existing streaming message', () => {
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
    // Still streaming: the content answer follows the reasoning.
    expect(msg._streaming).toBe(true)
  })

  it('STREAM_REASONING_END with content reconciles the authoritative reasoning text', () => {
    const { result } = renderHook(() => useMessages())

    // Buffered tokens may be coalesced or dropped; the final event carries the
    // backend's full text and should replace whatever was accumulated.
    act(() => result.current.streamReasoningToken('partial think'))
    act(() => result.current.streamReasoningEnd('full reasoning text'))

    expect(result.current.messages[0].reasoning_content).toBe('full reasoning text')
    expect(result.current.messages[0]._reasoningStreaming).toBe(false)
  })

  it('STREAM_REASONING_END without content preserves the accumulated reasoning', () => {
    const { result } = renderHook(() => useMessages())

    act(() => result.current.streamReasoningToken('accumulated'))
    act(() => result.current.streamReasoningEnd())

    expect(result.current.messages[0].reasoning_content).toBe('accumulated')
  })

  it('STREAM_TOKEN after reasoning appends content to the same message', () => {
    const { result } = renderHook(() => useMessages())

    act(() => result.current.streamReasoningToken('reasoning'))
    act(() => result.current.streamReasoningEnd())
    act(() => result.current.streamToken('Answer: 42'))

    const msg = result.current.messages[0]
    expect(msg.reasoning_content).toBe('reasoning')
    expect(msg.content).toBe('Answer: 42')
    expect(msg._reasoningStreaming).toBe(false)
  })

  it('STREAM_TOKEN ends reasoning even without an explicit reasoning end event', () => {
    const { result } = renderHook(() => useMessages())

    act(() => result.current.streamReasoningToken('reasoning'))
    act(() => result.current.streamToken('Answer'))

    expect(result.current.messages[0]._reasoningStreaming).toBe(false)
  })

  it('STREAM_END clears both _streaming and _reasoningStreaming', () => {
    const { result } = renderHook(() => useMessages())

    act(() => result.current.streamReasoningToken('thinking'))
    act(() => result.current.streamEnd())

    const msg = result.current.messages[0]
    expect(msg._streaming).toBe(false)
    expect(msg._reasoningStreaming).toBe(false)
  })

  it('reasoning after a closed stream starts a separate message', () => {
    const { result } = renderHook(() => useMessages())

    // Pre-tool reasoning, then the stream closes before the tool rows render.
    act(() => result.current.streamReasoningToken('first reasoning'))
    act(() => result.current.streamEnd())
    act(() => result.current.addMessage({ role: 'system', content: 'Tool called' }))
    // Post-tool synthesis reasoning must not append to the stale message.
    act(() => result.current.streamReasoningToken('synthesis reasoning'))

    expect(result.current.messages).toHaveLength(3)
    expect(result.current.messages[0].reasoning_content).toBe('first reasoning')
    expect(result.current.messages[0]._streaming).toBe(false)
    expect(result.current.messages[1].role).toBe('system')
    expect(result.current.messages[2].reasoning_content).toBe('synthesis reasoning')
    expect(result.current.messages[2]._streaming).toBe(true)
  })

  it('a reasoning-only turn finalizes with empty content', () => {
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

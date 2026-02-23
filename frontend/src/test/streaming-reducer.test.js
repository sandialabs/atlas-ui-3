import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useMessages } from '../hooks/chat/useMessages'

describe('useMessages - STREAM_TOKEN / STREAM_END actions', () => {
  it('STREAM_TOKEN creates a new streaming message when none exists', () => {
    const { result } = renderHook(() => useMessages())

    act(() => {
      result.current.streamToken('Hello')
    })

    expect(result.current.messages).toHaveLength(1)
    const msg = result.current.messages[0]
    expect(msg.role).toBe('assistant')
    expect(msg.content).toBe('Hello')
    expect(msg._streaming).toBe(true)
  })

  it('STREAM_TOKEN appends to existing streaming message', () => {
    const { result } = renderHook(() => useMessages())

    act(() => {
      result.current.streamToken('Hello')
    })
    act(() => {
      result.current.streamToken(' World')
    })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].content).toBe('Hello World')
    expect(result.current.messages[0]._streaming).toBe(true)
  })

  it('STREAM_END clears _streaming flag on last message', () => {
    const { result } = renderHook(() => useMessages())

    act(() => {
      result.current.streamToken('content')
    })
    act(() => {
      result.current.streamEnd()
    })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].content).toBe('content')
    expect(result.current.messages[0]._streaming).toBe(false)
  })

  it('STREAM_END is a no-op when no streaming message exists', () => {
    const { result } = renderHook(() => useMessages())

    act(() => {
      result.current.addMessage({ role: 'user', content: 'hi' })
    })
    act(() => {
      result.current.streamEnd()
    })

    // Should not modify the non-streaming message
    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0]._streaming).toBeUndefined()
  })

  it('multiple tokens accumulate correctly across many dispatches', () => {
    const { result } = renderHook(() => useMessages())
    const chunks = ['The ', 'quick ', 'brown ', 'fox']

    chunks.forEach(chunk => {
      act(() => {
        result.current.streamToken(chunk)
      })
    })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].content).toBe('The quick brown fox')
    expect(result.current.messages[0]._streaming).toBe(true)
  })

  it('STREAM_TOKEN after STREAM_END creates a new streaming message', () => {
    const { result } = renderHook(() => useMessages())

    // First stream
    act(() => { result.current.streamToken('first') })
    act(() => { result.current.streamEnd() })

    // Second stream
    act(() => { result.current.streamToken('second') })

    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[0].content).toBe('first')
    expect(result.current.messages[0]._streaming).toBe(false)
    expect(result.current.messages[1].content).toBe('second')
    expect(result.current.messages[1]._streaming).toBe(true)
  })

  it('STREAM_TOKEN finds streaming message even when interleaved with other messages', () => {
    const { result } = renderHook(() => useMessages())

    // Start streaming
    act(() => { result.current.streamToken('Hello') })

    // Interleaved tool message appended after the streaming message
    act(() => {
      result.current.addMessage({
        role: 'system',
        content: 'Tool Call: search',
        type: 'tool_call',
      })
    })

    // Continue streaming - should find the _streaming message, not create a new one
    act(() => { result.current.streamToken(' World') })

    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[0].content).toBe('Hello World')
    expect(result.current.messages[0]._streaming).toBe(true)
    expect(result.current.messages[1].type).toBe('tool_call')
  })

  it('STREAM_END finds streaming message even when it is not the last message', () => {
    const { result } = renderHook(() => useMessages())

    act(() => { result.current.streamToken('content') })
    act(() => {
      result.current.addMessage({ role: 'system', content: 'interleaved' })
    })
    act(() => { result.current.streamEnd() })

    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[0].content).toBe('content')
    expect(result.current.messages[0]._streaming).toBe(false)
  })
})

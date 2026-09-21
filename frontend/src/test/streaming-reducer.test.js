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

describe('useMessages - STREAM_TOKEN replace (stream replay, issue #957)', () => {
  it('seeds a replayed bubble when no streaming message exists', () => {
    const { result } = renderHook(() => useMessages())

    act(() => {
      result.current.addMessage({ role: 'user', content: 'question' })
    })
    act(() => {
      result.current.streamToken('The answer starts here', true)
    })

    expect(result.current.messages).toHaveLength(2)
    const msg = result.current.messages[1]
    expect(msg.content).toBe('The answer starts here')
    expect(msg._streaming).toBe(true)
    expect(msg._replayed).toBe(true)
  })

  it('replaces an already-seeded bubble instead of duplicating it', () => {
    const { result } = renderHook(() => useMessages())

    // The REST record seeded an earlier snapshot of the same segment.
    act(() => { result.current.streamToken('begin', true) })
    // The restore replay carries a newer, longer snapshot.
    act(() => { result.current.streamToken('begin plus more text', true) })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].content).toBe('begin plus more text')
    expect(result.current.messages[0]._streaming).toBe(true)
    expect(result.current.messages[0]._replayed).toBe(true)
  })

  it('a replaced bubble keeps streaming appends after it', () => {
    const { result } = renderHook(() => useMessages())

    act(() => { result.current.streamToken('replayed prefix', true) })
    act(() => { result.current.streamToken(' and live continuation') })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].content).toBe('replayed prefix and live continuation')
    expect(result.current.messages[0]._streaming).toBe(true)
    // Live tokens arriving means this tab owns the stream: the bubble is
    // refreshing itself, so the "will refresh when the run finishes" marker
    // no longer applies. A tab that gets no live frames keeps the marker.
    expect(result.current.messages[0]._replayed).toBe(false)
  })

  it('STREAM_END closes a replaced bubble and clears the marker with it', () => {
    const { result } = renderHook(() => useMessages())

    act(() => { result.current.streamToken('done', true) })
    act(() => { result.current.streamEnd() })

    expect(result.current.messages[0]._streaming).toBe(false)
    // The reply is complete: the row is no longer a placeholder, so it must
    // not be dropped by the persistence paths that skip _replayed rows.
    expect(result.current.messages[0]._replayed).toBe(false)
  })

  it('STREAM_END removes an empty placeholder row instead of freezing it', () => {
    const { result } = renderHook(() => useMessages())

    // The seed for a run parked between segments (an approval wait): an
    // empty bubble carrying only the marker. If the stream ends with nothing
    // ever appended, the row must go -- a permanently blank assistant bubble
    // is not a message.
    act(() => { result.current.addMessage({ role: 'user', content: 'question' }) })
    act(() => { result.current.streamToken('', true) })
    expect(result.current.messages.some(m => m._streaming)).toBe(true)

    act(() => { result.current.streamEnd() })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].role).toBe('user')
  })

  it('STREAM_END keeps a placeholder that live tokens filled', () => {
    const { result } = renderHook(() => useMessages())

    act(() => { result.current.streamToken('', true) })
    act(() => { result.current.streamToken('the answer') })
    act(() => { result.current.streamEnd() })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].content).toBe('the answer')
    expect(result.current.messages[0]._streaming).toBe(false)
  })

  it('a marker-only seed does not absorb a later segment: the first real token replaces it at the end', () => {
    const { result } = renderHook(() => useMessages())

    // Reopen a run parked on an approval: the transcript loads, the empty
    // seed carries the marker, then the approval is answered and a tool row
    // lands before the next segment streams.
    act(() => { result.current.addMessage({ role: 'user', content: 'question' }) })
    act(() => { result.current.streamToken('', true) })
    act(() => { result.current.addMessage({ role: 'system', content: '**Tool Call: calc**', type: 'tool_call', tool_call_id: 't1' }) })

    act(() => { result.current.streamToken('narration after the tool') })

    // The seed is gone; the new segment is its own bubble AFTER the tool row
    // it chronologically follows -- not absorbed into the seed above it.
    expect(result.current.messages).toHaveLength(3)
    expect(result.current.messages[2].content).toBe('narration after the tool')
    expect(result.current.messages[2]._streaming).toBe(true)
    expect(result.current.messages.some(m => m._seed)).toBe(false)
  })

  it('a replay frame with text fills the seed rather than spawning a second bubble', () => {
    const { result } = renderHook(() => useMessages())

    act(() => { result.current.streamToken('', true) })
    act(() => { result.current.streamToken('the segment text', true) })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].content).toBe('the segment text')
    expect(result.current.messages[0]._replayed).toBe(true)
    expect(result.current.messages[0]._seed).toBe(false)
  })

  it('a late replay frame never overwrites a genuinely live bubble', () => {
    const { result } = renderHook(() => useMessages())

    // A newer turn is streaming live in this tab (not a placeholder).
    act(() => { result.current.streamToken('new turn answer so far') })

    // A replay frame for the old segment arrives late.
    act(() => { result.current.streamToken('stale old segment', true) })

    // The live bubble is untouched and not re-marked _replayed (which would
    // have hidden it from the persistence paths).
    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].content).toBe('new turn answer so far')
    expect(result.current.messages[0]._replayed).toBeFalsy()
  })

  it('seed, live append, then replay: the replay extends the same segment instead of leaving a hole', () => {
    const { result } = renderHook(() => useMessages())

    // The REST record seeds an early snapshot; a live token lands before the
    // replay frame and flips the bubble live.
    act(() => { result.current.streamToken('w1 w2', true) })
    act(() => { result.current.streamToken(' w3') })
    expect(result.current.messages[0]._replayed).toBe(false)

    // The replay frame carries a newer snapshot of the same segment. It must
    // still apply: dropping it would leave the tokens it covers missing
    // between the bubble and the next live frame.
    act(() => { result.current.streamToken('w1 w2 w3 w4', true) })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].content).toBe('w1 w2 w3 w4')
    // The bubble stays live-owned: no _replayed flag, so the persistence
    // paths do not skip it.
    expect(result.current.messages[0]._replayed).toBe(false)

    // And the live stream continues on top of it.
    act(() => { result.current.streamToken(' w5') })
    expect(result.current.messages[0].content).toBe('w1 w2 w3 w4 w5')
  })

  it('a replay shorter than the live bubble does not rewind it', () => {
    const { result } = renderHook(() => useMessages())

    act(() => { result.current.streamToken('w1 w2 w3 w4 w5') })
    act(() => { result.current.streamToken('w1 w2', true) })

    expect(result.current.messages[0].content).toBe('w1 w2 w3 w4 w5')
  })

  it('discarding placeholders removes fragments and seeds but keeps live rows', () => {
    const { result } = renderHook(() => useMessages())

    act(() => { result.current.addMessage({ role: 'user', content: 'question' }) })
    act(() => { result.current.streamToken('fragment', true) })
    act(() => { result.current.streamToken('', true) })

    act(() => { result.current.discardReplayPlaceholders() })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].role).toBe('user')
  })
})

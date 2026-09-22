/**
 * The REFRESH_APPEND reducer case (issue #959).
 *
 * The joined-run refresh lands its tail and settles any open bubble in a
 * single dispatch. Doing it in two left a window where the list held a
 * half-written answer beside the whole one, and -- on the still-in-flight
 * path -- rendered the finished rows underneath a bubble that went on
 * filling above them.
 *
 * Exercised through the real hook, so these are the semantics the app gets.
 */

import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useMessages } from '../hooks/chat/useMessages'

// Build a view with two open bubbles and a settled row between them.
const seed = (result) => {
  act(() => {
    result.current.bulkAdd([
      { role: 'user', content: 'first' },
      { role: 'assistant', content: 'settled answer' },
    ])
  })
}

describe('REFRESH_APPEND', () => {
  it('drops every streaming row when the refresh settles (keepStreaming false)', () => {
    const { result } = renderHook(() => useMessages())
    seed(result)
    act(() => { result.current.streamToken('half writ', true) })
    act(() => { result.current.bulkAdd([{ role: 'system', content: 'a settled row between them' }]) })
    act(() => { result.current.streamToken('another open bubble') })
    expect(result.current.messages.filter(m => m._streaming).length).toBeGreaterThanOrEqual(1)

    act(() => { result.current.refreshAppend([{ role: 'assistant', content: 'the whole answer' }], false) })

    const rows = result.current.messages
    // No open bubble survives, and the settled row between them is kept.
    expect(rows.some(m => m._streaming)).toBe(false)
    expect(rows.some(m => m.content === 'a settled row between them')).toBe(true)
    expect(rows[rows.length - 1].content).toBe('the whole answer')
    // The half-written text does not linger beside the finished answer.
    expect(rows.some(m => m.content === 'half writ')).toBe(false)
  })

  it('keeps a retained bubble AFTER the appended rows (keepStreaming true)', () => {
    const { result } = renderHook(() => useMessages())
    seed(result)
    act(() => { result.current.streamToken('still writing', true) })

    act(() => { result.current.refreshAppend([{ role: 'assistant', content: 'earlier run answer' }], true) })

    const rows = result.current.messages
    // The open bubble is last: the finished rows must not render under it.
    expect(rows[rows.length - 1]._streaming).toBe(true)
    expect(rows[rows.length - 1].content).toBe('still writing')
    expect(rows[rows.length - 2].content).toBe('earlier run answer')
  })

  it('preserves row identities for an empty append', () => {
    // The expanded-tool-row and scroll-anchor guarantee depends on React
    // seeing the same objects for rows that did not change.
    const { result } = renderHook(() => useMessages())
    seed(result)
    const before = result.current.messages

    act(() => { result.current.refreshAppend([], false) })

    const after = result.current.messages
    expect(after).toHaveLength(before.length)
    for (let i = 0; i < before.length; i += 1) expect(after[i]).toBe(before[i])
  })
})

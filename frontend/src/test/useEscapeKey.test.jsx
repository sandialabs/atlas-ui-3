/**
 * Contract of the shared close-on-Escape hook (issue #1038 review).
 *
 * Every overlay in the app leans on this hook, and it grew a shouldHandle
 * predicate and latest-ref handler storage to support stacked overlays --
 * each of those behaviors is pinned here:
 *
 * - the LATEST handler runs after a rerender (an inline arrow must not mean
 *   a stale close),
 * - ONE subscription exists across rerenders (no remove/add churn while
 *   open),
 * - a declining shouldHandle passes the key to later listeners untouched --
 *   the exact defect where a standing-down drawer swallowed the Tools and
 *   Settings modal's Escape, because stopPropagation ran before the
 *   stand-down check,
 * - the default (no predicate) stops propagation and calls the handler.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render } from '@testing-library/react'
import { useState } from 'react'
import { useEscapeKey } from '../hooks/useEscapeKey'

const captureListenersInFlight = () => {
  const seen = []
  return {
    seen,
    captureListener: (event) => {
      if (event.key === 'Escape') seen.push('capture')
    },
    bubbleListener: (event) => {
      if (event.key === 'Escape') seen.push('bubble')
    },
  }
}

function Probe({ onEscape, shouldHandle }) {
  useEscapeKey(true, onEscape, shouldHandle ? { shouldHandle } : {})
  return null
}

/** Rerenders Probe with a fresh handler each time (the inline-arrow case). */
function RerenderingProbe({ calls }) {
  const [, setTick] = useState(0)
  return (
    <>
      <button onClick={() => setTick(t => t + 1)}>rerender</button>
      <Probe onEscape={() => calls.push(`call-${calls.length + 1}`)} />
    </>
  )
}

let ordered

beforeEach(() => {
  ordered = captureListenersInFlight()
  document.addEventListener('keydown', ordered.captureListener, true)
  document.addEventListener('keydown', ordered.bubbleListener)
})

afterEach(() => {
  document.removeEventListener('keydown', ordered.captureListener, true)
  document.removeEventListener('keydown', ordered.bubbleListener)
})

const pressEscape = () => {
  // A real Escape targets the focused element; dispatching on <body> with
  // bubbling reaches the document listeners in the same order.
  document.body.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
  )
}

describe('useEscapeKey', () => {
  it('calls the latest handler after a rerender changes its identity', () => {
    const calls = []
    const { getByText } = render(<RerenderingProbe calls={calls} />)

    pressEscape()
    getByText('rerender').click()
    pressEscape()

    expect(calls).toEqual(['call-1', 'call-2'])
  })

  it('keeps a single subscription across rerenders', () => {
    const addSpy = vi.spyOn(document, 'addEventListener')
    const removeSpy = vi.spyOn(document, 'removeEventListener')
    const { getByText } = render(<RerenderingProbe calls={[]} />)

    const keydownAdds = () =>
      addSpy.mock.calls.filter(([type]) => type === 'keydown').length
    const keydownRemoves = () =>
      removeSpy.mock.calls.filter(([type]) => type === 'keydown').length

    const addsAfterMount = keydownAdds()
    const removesAfterMount = keydownRemoves()
    getByText('rerender').click()
    getByText('rerender').click()

    expect(keydownAdds()).toBe(addsAfterMount)
    expect(keydownRemoves()).toBe(removesAfterMount)

    addSpy.mockRestore()
    removeSpy.mockRestore()
  })

  it('lets the event propagate untouched when shouldHandle declines it', () => {
    const onEscape = vi.fn()
    render(<Probe onEscape={onEscape} shouldHandle={() => false} />)

    pressEscape()

    expect(onEscape).not.toHaveBeenCalled()
    // Both the capture-phase listener registered before the hook and the
    // bubble-phase one after it still see the key.
    expect(ordered.seen).toEqual(['capture', 'bubble'])
  })

  it('stops propagation and fires by default (no predicate)', () => {
    const onEscape = vi.fn()
    render(<Probe onEscape={onEscape} />)

    pressEscape()

    expect(onEscape).toHaveBeenCalledTimes(1)
    expect(ordered.seen).toEqual(['capture'])
  })
})

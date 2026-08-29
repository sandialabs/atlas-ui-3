/**
 * Regression cover for the header overlap fix.
 *
 * The header's desktop button cluster used to be gated on a viewport media
 * query (`min-[1280px]:flex`). The header is laid out beside a 256px sidebar,
 * so at a 1280px viewport the header itself was only ~1024px wide: the cluster
 * appeared with nowhere to go, ran ~211px past the header's right edge, and the
 * model selector painted over the save-mode button.
 *
 * useElementWidth is what replaced that query, so these tests pin its contract:
 * it measures the element itself, it measures before paint, and it keeps up
 * with later resizes.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, act } from '@testing-library/react'
import { useElementWidth } from '../hooks/useElementWidth'

// Header width at which the desktop cluster is allowed to render, mirroring
// DESKTOP_ACTIONS_MIN_WIDTH in Header.jsx.
const DESKTOP_ACTIONS_MIN_WIDTH = 1320

const originalResizeObserver = globalThis.ResizeObserver

// Minimal ResizeObserver stand-in: jsdom has none, and the tests need to drive
// the callback by hand to simulate a resize.
let observerCallbacks = []

class FakeResizeObserver {
  constructor(callback) {
    this.callback = callback
    observerCallbacks.push(callback)
  }
  observe() {}
  disconnect() {
    observerCallbacks = observerCallbacks.filter((cb) => cb !== this.callback)
  }
}

// jsdom lays nothing out, so every element reports width 0. Drive the measured
// width directly instead.
let stubbedWidth = 0
const originalGetBoundingClientRect = Element.prototype.getBoundingClientRect

const Probe = () => {
  const [ref, width] = useElementWidth()
  return (
    <div ref={ref} data-testid="probe">
      <span data-testid="width">{width}</span>
      <span data-testid="mode">
        {width >= DESKTOP_ACTIONS_MIN_WIDTH ? 'desktop' : 'compact'}
      </span>
    </div>
  )
}

describe('useElementWidth', () => {
  beforeEach(() => {
    observerCallbacks = []
    globalThis.ResizeObserver = FakeResizeObserver
    Element.prototype.getBoundingClientRect = function () {
      return { width: stubbedWidth, height: 0, top: 0, left: 0, right: stubbedWidth, bottom: 0, x: 0, y: 0 }
    }
  })

  afterEach(() => {
    globalThis.ResizeObserver = originalResizeObserver
    Element.prototype.getBoundingClientRect = originalGetBoundingClientRect
    vi.restoreAllMocks()
  })

  it('reports the element width on first render, before paint', () => {
    stubbedWidth = 1600
    const { getByTestId } = render(<Probe />)
    // Not 0: a wide header must not flash its compact layout on mount.
    expect(getByTestId('width').textContent).toBe('1600')
    expect(getByTestId('mode').textContent).toBe('desktop')
  })

  it('updates when the element is resized', () => {
    stubbedWidth = 1600
    const { getByTestId } = render(<Probe />)
    expect(getByTestId('mode').textContent).toBe('desktop')

    // Sidebar opens: the header shrinks even though the viewport did not.
    stubbedWidth = 1600 - 256
    act(() => {
      observerCallbacks.forEach((cb) => cb())
    })
    expect(getByTestId('width').textContent).toBe('1344')
    expect(getByTestId('mode').textContent).toBe('desktop')
  })

  it('selects the compact layout at the width the old viewport query got wrong', () => {
    // The original bug: 1280px viewport, 256px sidebar, so the header is 1024px
    // wide. The viewport query said "desktop"; the header width says "compact".
    stubbedWidth = 1280 - 256
    const { getByTestId } = render(<Probe />)
    expect(getByTestId('width').textContent).toBe('1024')
    expect(getByTestId('mode').textContent).toBe('compact')
  })

  it('rounds sub-pixel widths so fractional reflows do not churn renders', () => {
    stubbedWidth = 1023.4
    const { getByTestId } = render(<Probe />)
    expect(getByTestId('width').textContent).toBe('1023')
  })

  it('still measures once when ResizeObserver is unavailable', () => {
    globalThis.ResizeObserver = undefined
    stubbedWidth = 1400
    const { getByTestId } = render(<Probe />)
    expect(getByTestId('width').textContent).toBe('1400')
  })

  it('disconnects the observer on unmount', () => {
    stubbedWidth = 1400
    const { unmount } = render(<Probe />)
    expect(observerCallbacks).toHaveLength(1)
    unmount()
    expect(observerCallbacks).toHaveLength(0)
  })
})

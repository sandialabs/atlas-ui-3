/**
 * Regression cover for the header overlap fix.
 *
 * The header's desktop button cluster used to be gated on a viewport media
 * query (`min-[1280px]:flex`). The header is laid out beside a 256px sidebar,
 * so at a 1280px viewport the header itself was only ~1024px wide: the cluster
 * appeared with nowhere to go, ran ~211px past the header's right edge, and the
 * model selector painted over the save-mode button.
 *
 * useElementSize is what replaced that query, so these tests pin its contract:
 * it measures the element itself, it measures before paint, and it keeps up
 * with later resizes. The bar's shapes are pinned in
 * header-responsive-tiers.test.jsx.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, act } from '@testing-library/react'
import { useElementSize } from '../hooks/useElementSize'
// Imported, never re-declared: a local copy of the thresholds drifted from the
// component's (1320 here vs the shipped value) and left these cases asserting
// nothing (PR #839 review).
import {
  ACTION_LABELS_MIN_WIDTH,
  COMPACT_ACTIONS_MIN_WIDTH,
  SECURITY_LEVEL_MIN_WIDTH,
} from '../components/Header'

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
  const [ref, size] = useElementSize()
  const width = size.width
  return (
    <div ref={ref} data-testid="probe">
      <span data-testid="width">{width}</span>
      <span data-testid="mode">
        {width >= COMPACT_ACTIONS_MIN_WIDTH ? 'setup-group' : 'minimal'}
      </span>
      <span data-testid="labels">
        {width >= ACTION_LABELS_MIN_WIDTH ? 'labels' : 'icons-only'}
      </span>
    </div>
  )
}

describe('useElementSize', () => {
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
    expect(getByTestId('mode').textContent).toBe('setup-group')
  })

  it('updates when the element is resized', () => {
    stubbedWidth = 1600
    const { getByTestId } = render(<Probe />)
    expect(getByTestId('mode').textContent).toBe('setup-group')

    // Sidebar opens: the header shrinks even though the viewport did not.
    stubbedWidth = 1600 - 256
    act(() => {
      observerCallbacks.forEach((cb) => cb())
    })
    expect(getByTestId('width').textContent).toBe('1344')
    expect(getByTestId('mode').textContent).toBe('setup-group')
  })

  it('selects the minimal layout at the width the old viewport query got wrong', () => {
    // The original bug: 1280px viewport, 256px sidebar, so the header is 1024px
    // wide. The viewport query said "desktop"; the header width says "compact".
    stubbedWidth = 1280 - 256
    const { getByTestId } = render(<Probe />)
    expect(getByTestId('width').textContent).toBe('1024')
    expect(getByTestId('mode').textContent).toBe('minimal')
  })

  it('uses the thresholds the header actually ships', () => {
    // Pins the imported values themselves. The point of importing rather than
    // re-declaring is that a change to Header.jsx has to come here too.
    expect(COMPACT_ACTIONS_MIN_WIDTH).toBe(1160)
    expect(SECURITY_LEVEL_MIN_WIDTH).toBe(420)
    expect(ACTION_LABELS_MIN_WIDTH).toBe(800)
    // The bar narrows from the outside in: the labels go first, then the
    // setup group, and the security level is the last thing to leave.
    expect(SECURITY_LEVEL_MIN_WIDTH).toBeLessThan(ACTION_LABELS_MIN_WIDTH)
    expect(ACTION_LABELS_MIN_WIDTH).toBeLessThan(COMPACT_ACTIONS_MIN_WIDTH)
  })

  it('drops the button text labels below ACTION_LABELS_MIN_WIDTH', () => {
    // Between the two thresholds: the setup group has already gone, and the
    // remaining left-hand buttons still carry their words.
    stubbedWidth = ACTION_LABELS_MIN_WIDTH
    const { getByTestId, unmount } = render(<Probe />)
    expect(getByTestId('labels').textContent).toBe('labels')
    expect(getByTestId('mode').textContent).toBe('minimal')
    unmount()

    // One pixel narrower and "New chat" et al. go icon-only, which is the
    // collapse the viewport-based `hidden md:inline` query got wrong.
    stubbedWidth = ACTION_LABELS_MIN_WIDTH - 1
    const narrow = render(<Probe />)
    expect(narrow.getByTestId('labels').textContent).toBe('icons-only')
  })

  it('keeps the setup group exactly at COMPACT_ACTIONS_MIN_WIDTH', () => {
    stubbedWidth = COMPACT_ACTIONS_MIN_WIDTH
    const { getByTestId, unmount } = render(<Probe />)
    expect(getByTestId('mode').textContent).toBe('setup-group')
    unmount()

    stubbedWidth = COMPACT_ACTIONS_MIN_WIDTH - 1
    const narrow = render(<Probe />)
    expect(narrow.getByTestId('mode').textContent).toBe('minimal')
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

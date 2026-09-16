/**
 * Regression: the feedback button used to sit on top of the Send button.
 *
 * The button was pinned at a constant `bottom-32` (128px) on narrow screens,
 * but the composer grows well past that once the textarea auto-resizes for a
 * long message and the warning banners / controls row are stacked under it.
 * The two controls then overlapped in the bottom-right corner, so a thumb aimed
 * at Send opened the feedback modal instead -- the worst case being a phone in
 * a car mount.
 *
 * The fix publishes the live composer height as --atlas-composer-height and
 * anchors the button above it. These tests pin both halves.
 */

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { useRef } from 'react'
import { vi } from 'vitest'
import { useComposerHeightVar, COMPOSER_HEIGHT_VAR } from '../hooks/useComposerHeightVar'

vi.mock('../contexts/ChatContext', () => ({
  useChat: () => ({
    messages: [],
    currentModel: 'test-model',
    user: 'tester',
    appName: 'Atlas',
    selectedTools: new Set(),
    selectedDataSources: new Set(),
    agentModeEnabled: false,
    canvasContent: '',
    features: {},
  }),
}))

import FeedbackButton from '../components/FeedbackButton'

afterEach(() => {
  cleanup()
  document.documentElement.style.removeProperty(COMPOSER_HEIGHT_VAR)
})

function Harness({ height }) {
  const ref = useRef(null)
  useComposerHeightVar(ref)
  return (
    <footer
      ref={ref}
      // jsdom has no layout engine, so getBoundingClientRect is stubbed per node.
      style={{ height: `${height}px` }}
    />
  )
}

const stubHeight = (height) => {
  // Every element reports the same height; the hook only measures the footer.
  Element.prototype.getBoundingClientRect = () => ({ height, width: 0, top: 0, left: 0, right: 0, bottom: 0, x: 0, y: 0 })
}

describe('useComposerHeightVar', () => {
  it('publishes the composer height on the document root', () => {
    stubHeight(216)
    render(<Harness height={216} />)
    expect(document.documentElement.style.getPropertyValue(COMPOSER_HEIGHT_VAR)).toBe('216px')
  })

  it('clears the variable on unmount so a stale offset cannot linger', () => {
    stubHeight(216)
    const { unmount } = render(<Harness height={216} />)
    unmount()
    expect(document.documentElement.style.getPropertyValue(COMPOSER_HEIGHT_VAR)).toBe('')
  })
})

describe('feedback button placement', () => {
  it('is anchored above the composer rather than at a constant bottom offset', () => {
    const { getByTestId } = render(<FeedbackButton />)
    const btn = getByTestId('feedback-button')
    // Offset is derived from the live composer height, so it tracks the
    // composer as it grows instead of colliding with Send.
    expect(btn.style.bottom).toContain(COMPOSER_HEIGHT_VAR)
    // A constant `bottom-<n>` utility is exactly what caused the overlap.
    expect([...btn.classList].some((c) => /^bottom-\d/.test(c))).toBe(false)
  })

  it('is a comfortable touch target (>= 44px)', () => {
    const { getByTestId } = render(<FeedbackButton />)
    const cls = [...getByTestId('feedback-button').classList]
    // Tailwind w-14 / h-14 = 56px.
    expect(cls).toContain('w-14')
    expect(cls).toContain('h-14')
  })
})

/**
 * Tests for composer auto-resize not displacing the transcript (#866).
 *
 * jsdom has no layout engine, so the scroll container is modelled explicitly:
 * it is a flex sibling of the composer, so its clientHeight is
 * (shellHeight - composerHeight), and -- like a real browser -- it clamps
 * scrollTop into [0, scrollHeight - clientHeight] whenever that range changes.
 */

import { describe, it, expect } from 'vitest'
import {
  autoResizeComposer,
  resetComposerHeight,
  COMPOSER_MAX_HEIGHT,
} from '../utils/composerAutoResize'

const SHELL_HEIGHT = 600
const CONTENT_HEIGHT = 2000
const MIN_COMPOSER = 48
const LINE = 20

function makeHarness({ lines = 1, scrollTop = null } = {}) {
  const container = {
    scrollHeight: CONTENT_HEIGHT,
    _scrollTop: 0,
    get clientHeight() {
      return SHELL_HEIGHT - textarea._renderedHeight
    },
    get maxScrollTop() {
      return this.scrollHeight - this.clientHeight
    },
    // A browser clamps scrollTop into the current range whenever layout is
    // recomputed, and the clamp is permanent -- re-growing the viewport does
    // not restore the old offset.
    settle() {
      this._scrollTop = Math.max(0, Math.min(this._scrollTop, this.maxScrollTop))
    },
    get scrollTop() {
      this.settle()
      return this._scrollTop
    },
    set scrollTop(v) {
      this._scrollTop = Math.max(0, Math.min(v, this.maxScrollTop))
    },
  }

  const textarea = {
    _renderedHeight: Math.max(MIN_COMPOSER, lines * LINE),
    _contentLines: lines,
    style: {
      _height: '',
      get height() {
        return this._height
      },
      set height(v) {
        this._height = v
        textarea._renderedHeight =
          v === 'auto' || v === ''
            ? MIN_COMPOSER
            : Math.max(MIN_COMPOSER, parseInt(v, 10))
      },
    },
    // Natural height of the content. Reading it forces layout in a real
    // browser, which is where the container's scrollTop gets clamped.
    get scrollHeight() {
      container.settle()
      return Math.max(MIN_COMPOSER, this._contentLines * LINE)
    },
  }

  textarea.style.height = textarea._renderedHeight + 'px'
  container.scrollTop = scrollTop === null ? container.maxScrollTop : scrollTop
  return { container, textarea }
}

describe('autoResizeComposer', () => {
  it('sizes the textarea to its content height', () => {
    const { container, textarea } = makeHarness({ lines: 3 })
    expect(autoResizeComposer(textarea, container, 128)).toBe('60px')
    expect(textarea.style.height).toBe('60px')
  })

  it('caps the height at maxHeight', () => {
    const { container, textarea } = makeHarness({ lines: 40 })
    expect(autoResizeComposer(textarea, container, 128)).toBe('128px')
  })

  it('keeps a mid-transcript scroll position across a keystroke', () => {
    const { container, textarea } = makeHarness({ lines: 3 })
    // User scrolled up to read a previous response, but is close enough to the
    // bottom that the temporary collapse would clamp scrollTop.
    container.scrollTop = container.maxScrollTop - 5
    const before = container.scrollTop
    autoResizeComposer(textarea, container, 128)
    expect(container.scrollTop).toBe(before)
  })

  it('keeps the transcript pinned when it was already at the bottom', () => {
    const { container, textarea } = makeHarness({ lines: 3 })
    autoResizeComposer(textarea, container, 128)
    expect(container.scrollTop).toBe(container.maxScrollTop)
  })

  it('does not drift over many keystrokes in a multi-line composer', () => {
    const { container, textarea } = makeHarness({ lines: 3 })
    container.scrollTop = container.maxScrollTop - 5
    const before = container.scrollTop
    for (let i = 0; i < 25; i++) autoResizeComposer(textarea, container, 128)
    expect(container.scrollTop).toBe(before)
  })

  it('regression: collapsing to auto without restoring displaces the transcript', () => {
    // The pre-fix implementation, verbatim -- proves the harness reproduces the bug.
    const { container, textarea } = makeHarness({ lines: 3 })
    container.scrollTop = container.maxScrollTop - 5
    const before = container.scrollTop
    textarea.style.height = 'auto'
    textarea.style.height = Math.min(textarea.scrollHeight, 128) + 'px'
    expect(container.scrollTop).toBeLessThan(before)
  })

  it('tolerates a missing scroll container', () => {
    const { textarea } = makeHarness({ lines: 2 })
    expect(() => autoResizeComposer(textarea, null, 128)).not.toThrow()
    expect(textarea.style.height).toBe('48px')
  })

  it('is a no-op without a textarea', () => {
    expect(autoResizeComposer(null, null, 128)).toBe('')
  })

  it('exposes the height cap it defaults to', () => {
    expect(COMPOSER_MAX_HEIGHT).toBe(128)
    const { container, textarea } = makeHarness({ lines: 40 })
    expect(autoResizeComposer(textarea, container)).toBe(COMPOSER_MAX_HEIGHT + 'px')
  })
})

describe('resetComposerHeight', () => {
  it('drops the inline height so the stylesheet takes over', () => {
    const { container, textarea } = makeHarness({ lines: 3 })
    autoResizeComposer(textarea, container)
    expect(textarea.style.height).toBe('60px')

    resetComposerHeight(textarea, container)
    expect(textarea.style.height).toBe('')
  })

  it('does not re-measure the not-yet-cleared value', () => {
    // Called right after setInputValue(''), before React writes the empty value
    // to the DOM: measuring here would keep the composer at the sent text's
    // height. _contentLines still reports the sent message.
    const { container, textarea } = makeHarness({ lines: 5 })
    resetComposerHeight(textarea, container)
    expect(textarea._renderedHeight).toBe(48)
  })

  it('keeps a mid-transcript scroll position, as far as the new range allows', () => {
    const { container, textarea } = makeHarness({ lines: 3 })
    autoResizeComposer(textarea, container)
    container.scrollTop = container.maxScrollTop - 5
    const before = container.scrollTop
    resetComposerHeight(textarea, container)
    // Collapsing the composer genuinely grows the transcript's viewport, so the
    // old offset can fall outside the new range -- that clamp is the composer
    // legitimately shrinking on send, not the #866 displacement. What matters
    // is that nothing moves beyond it.
    expect(container.scrollTop).toBe(Math.min(before, container.maxScrollTop))
  })

  it('is a no-op without a textarea', () => {
    expect(resetComposerHeight(null, null)).toBe('')
  })
})

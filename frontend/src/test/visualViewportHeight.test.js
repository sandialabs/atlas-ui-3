import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  APP_VIEWPORT_HEIGHT_VAR,
  updateAppViewportHeight,
  watchAppViewportHeight,
} from '../utils/visualViewportHeight'

const setVisualViewport = (viewport) => {
  Object.defineProperty(window, 'visualViewport', {
    configurable: true,
    value: viewport,
  })
}

const originalInnerHeight = Object.getOwnPropertyDescriptor(window, 'innerHeight')

const setInnerHeight = (height) => {
  Object.defineProperty(window, 'innerHeight', {
    configurable: true,
    writable: true,
    value: height,
  })
}

const readVar = () =>
  document.documentElement.style.getPropertyValue(APP_VIEWPORT_HEIGHT_VAR)

afterEach(() => {
  document.documentElement.style.removeProperty(APP_VIEWPORT_HEIGHT_VAR)
  setVisualViewport(undefined)
  // Restore jsdom's own innerHeight so these tests cannot leak into other suites.
  if (originalInnerHeight) {
    Object.defineProperty(window, 'innerHeight', originalInnerHeight)
  } else {
    delete window.innerHeight
  }
})

describe('visual viewport height utilities', () => {
  it('uses the visual viewport height when available', () => {
    setVisualViewport({ height: 512 })

    updateAppViewportHeight()

    expect(readVar()).toBe('512px')
  })

  it('falls back to window.innerHeight when visual viewport is unavailable', () => {
    setVisualViewport(undefined)
    setInnerHeight(731)

    updateAppViewportHeight()

    expect(readVar()).toBe('731px')
  })

  it('ignores a zero or non-finite viewport height', () => {
    setVisualViewport({ height: 0 })
    setInnerHeight(0)

    updateAppViewportHeight()

    expect(readVar()).toBe('')
  })

  it('falls back to the layout viewport while the page is pinch-zoomed', () => {
    setVisualViewport({ height: 300, scale: 2 })
    setInnerHeight(800)

    updateAppViewportHeight()

    expect(readVar()).toBe('800px')
  })

  it('updates the CSS variable when the visual viewport resizes', () => {
    const listeners = new Map()
    const viewport = {
      height: 640,
      addEventListener: vi.fn((event, handler) => listeners.set(event, handler)),
      removeEventListener: vi.fn(),
    }
    setVisualViewport(viewport)

    const cleanup = watchAppViewportHeight()
    viewport.height = 420
    listeners.get('resize')()

    expect(readVar()).toBe('420px')

    cleanup()
    expect(viewport.removeEventListener).toHaveBeenCalledWith('resize', expect.any(Function))
    expect(viewport.removeEventListener).toHaveBeenCalledWith('scroll', expect.any(Function))
  })

  it('coalesces visual viewport scroll updates through requestAnimationFrame', async () => {
    const listeners = new Map()
    const viewport = {
      height: 640,
      addEventListener: vi.fn((event, handler) => listeners.set(event, handler)),
      removeEventListener: vi.fn(),
    }
    setVisualViewport(viewport)

    const cleanup = watchAppViewportHeight()
    expect(readVar()).toBe('640px')

    viewport.height = 500
    const onScroll = listeners.get('scroll')
    onScroll()
    onScroll()
    onScroll()

    // Still the pre-scroll value: the write is deferred to the next frame.
    expect(readVar()).toBe('640px')

    await new Promise((resolve) => requestAnimationFrame(resolve))

    expect(readVar()).toBe('500px')
    cleanup()
  })

  it('removes the CSS variable on cleanup so the 100vh fallback is reachable', () => {
    setVisualViewport({
      height: 480,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })

    const cleanup = watchAppViewportHeight()
    expect(readVar()).toBe('480px')

    cleanup()
    expect(readVar()).toBe('')
  })
})

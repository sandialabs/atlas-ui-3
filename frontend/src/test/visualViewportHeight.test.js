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

afterEach(() => {
  document.documentElement.style.removeProperty(APP_VIEWPORT_HEIGHT_VAR)
  setVisualViewport(undefined)
})

describe('visual viewport height utilities', () => {
  it('uses the visual viewport height when available', () => {
    setVisualViewport({ height: 512 })

    updateAppViewportHeight()

    expect(document.documentElement.style.getPropertyValue(APP_VIEWPORT_HEIGHT_VAR)).toBe('512px')
  })

  it('falls back to window.innerHeight when visual viewport is unavailable', () => {
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: 731,
    })

    updateAppViewportHeight()

    expect(document.documentElement.style.getPropertyValue(APP_VIEWPORT_HEIGHT_VAR)).toBe('731px')
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

    expect(document.documentElement.style.getPropertyValue(APP_VIEWPORT_HEIGHT_VAR)).toBe('420px')

    cleanup()
    expect(viewport.removeEventListener).toHaveBeenCalledWith('resize', expect.any(Function))
    expect(viewport.removeEventListener).toHaveBeenCalledWith('scroll', expect.any(Function))
  })
})

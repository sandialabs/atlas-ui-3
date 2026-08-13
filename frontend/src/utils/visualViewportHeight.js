export const APP_VIEWPORT_HEIGHT_VAR = '--app-viewport-height'

// While the page is pinch-zoomed, visualViewport.height reports the magnified
// region rather than the usable viewport. Sizing the shell from it would
// collapse the layout on every pan frame, so fall back to the layout viewport.
const ZOOM_THRESHOLD = 1.01

const getCurrentViewportHeight = () => {
  if (typeof window === 'undefined') return null
  const viewport = window.visualViewport
  if (!viewport) return window.innerHeight
  if (viewport.scale > ZOOM_THRESHOLD) return window.innerHeight
  return viewport.height || window.innerHeight
}

export const updateAppViewportHeight = () => {
  if (typeof document === 'undefined') return

  const height = getCurrentViewportHeight()
  if (!Number.isFinite(height) || height <= 0) return

  const next = `${Math.round(height)}px`
  const root = document.documentElement
  // Skip no-op writes: visualViewport `scroll` fires at frame rate on mobile
  // and each style write invalidates layout for the whole message list.
  if (root.style.getPropertyValue(APP_VIEWPORT_HEIGHT_VAR) === next) return

  root.style.setProperty(APP_VIEWPORT_HEIGHT_VAR, next)
}

export const watchAppViewportHeight = () => {
  if (typeof window === 'undefined') return () => {}

  const viewport = window.visualViewport
  let scrollFrame = null

  // Coalesce the high-frequency scroll stream into at most one update per frame.
  const scheduleUpdate = () => {
    if (scrollFrame !== null) return
    scrollFrame = window.requestAnimationFrame(() => {
      scrollFrame = null
      updateAppViewportHeight()
    })
  }

  updateAppViewportHeight()

  window.addEventListener('resize', updateAppViewportHeight)
  window.addEventListener('orientationchange', updateAppViewportHeight)
  viewport?.addEventListener('resize', updateAppViewportHeight)
  viewport?.addEventListener('scroll', scheduleUpdate)

  return () => {
    if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame)
    window.removeEventListener('resize', updateAppViewportHeight)
    window.removeEventListener('orientationchange', updateAppViewportHeight)
    viewport?.removeEventListener('resize', updateAppViewportHeight)
    viewport?.removeEventListener('scroll', scheduleUpdate)
    // Drop the variable so the `var(..., 100vh)` fallback is reachable again
    // instead of leaving a keyboard-shrunk height frozen on the document.
    document.documentElement.style.removeProperty(APP_VIEWPORT_HEIGHT_VAR)
  }
}

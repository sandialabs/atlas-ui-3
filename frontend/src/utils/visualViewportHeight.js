export const APP_VIEWPORT_HEIGHT_VAR = '--app-viewport-height'

const getCurrentViewportHeight = () => {
  if (typeof window === 'undefined') return null
  return window.visualViewport?.height || window.innerHeight
}

export const updateAppViewportHeight = () => {
  if (typeof document === 'undefined') return

  const height = getCurrentViewportHeight()
  if (!Number.isFinite(height) || height <= 0) return

  document.documentElement.style.setProperty(
    APP_VIEWPORT_HEIGHT_VAR,
    `${Math.round(height)}px`
  )
}

export const watchAppViewportHeight = () => {
  if (typeof window === 'undefined') return () => {}

  const viewport = window.visualViewport
  updateAppViewportHeight()

  window.addEventListener('resize', updateAppViewportHeight)
  window.addEventListener('orientationchange', updateAppViewportHeight)
  viewport?.addEventListener('resize', updateAppViewportHeight)
  viewport?.addEventListener('scroll', updateAppViewportHeight)

  return () => {
    window.removeEventListener('resize', updateAppViewportHeight)
    window.removeEventListener('orientationchange', updateAppViewportHeight)
    viewport?.removeEventListener('resize', updateAppViewportHeight)
    viewport?.removeEventListener('scroll', updateAppViewportHeight)
  }
}

import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import {
  DEFAULT_THEME_ID, RETRO_THEMES_IN_BUILD, effectiveThemeId, getRegisteredTheme, isKnownThemeId,
} from '../themes/themeRegistry'

const ThemeContext = createContext()

// Same key useChatConfig caches /api/config under; the first-paint bootstrap
// (public/theme-bootstrap.js) reads it the same way.
const CONFIG_CACHE_KEY = 'chatui-config-cache'

function getInitialTheme() {
  try {
    const saved = localStorage.getItem('atlas-theme')
    if (isKnownThemeId(saved)) return saved
  } catch { /* Storage may be unavailable in privacy modes. */ }
  return DEFAULT_THEME_ID
}

/**
 * The run-time FEATURE_RETRO_THEMES_ENABLED value from the cached config:
 * true/false when known, undefined before any config has been cached (the
 * saved theme is then trusted until /api/config/shell answers, matching the
 * first-paint bootstrap so the two never disagree).
 */
function getCachedRetroRuntime() {
  try {
    const cached = JSON.parse(localStorage.getItem(CONFIG_CACHE_KEY) || 'null')
    const value = cached?.features?.retro_themes
    return typeof value === 'boolean' ? value : undefined
  } catch {
    return undefined
  }
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}

export function ThemeProvider({ children }) {
  // The saved choice, kept even while retro themes are unavailable so that
  // re-enabling the feature restores it.
  const [selectedTheme, setSelectedTheme] = useState(getInitialTheme)
  const [retroRuntime, setRetroRuntime] = useState(getCachedRetroRuntime)
  const retroThemesAvailable = RETRO_THEMES_IN_BUILD && retroRuntime !== false
  const theme = effectiveThemeId(selectedTheme, retroThemesAvailable)
  const definition = getRegisteredTheme(theme)

  useEffect(() => {
    const root = document.documentElement
    // data-theme remains dark/light for compatibility with existing Atlas CSS.
    // data-atlas-theme names the exact registered visual identity.
    root.setAttribute('data-theme', definition.mode)
    root.setAttribute('data-atlas-theme', theme)
    try {
      localStorage.setItem('atlas-theme', isKnownThemeId(selectedTheme) ? selectedTheme : theme)
    } catch { /* ignore */ }
  }, [theme, definition.mode, selectedTheme])

  const setTheme = useCallback(id => {
    setSelectedTheme(current => {
      const next = typeof id === 'function' ? id(current) : id
      return isKnownThemeId(next) ? next : current
    })
  }, [])

  // Called with features.retro_themes once /api/config(/shell) answers.
  // Anything but a boolean (an older backend without the field) is ignored.
  const setRetroThemesEnabled = useCallback(value => {
    if (typeof value === 'boolean') setRetroRuntime(value)
  }, [])

  // Atlas intentionally defaults to dark, not the OS color-scheme preference.
  // Keep the legacy toggle API while the new gallery supports every preset.
  const toggleTheme = () => setSelectedTheme(() => (definition.mode === 'dark' ? 'light' : 'dark'))

  return (
    <ThemeContext.Provider value={{
      theme,
      selectedTheme,
      setTheme,
      toggleTheme,
      themeDefinition: definition,
      retroThemesAvailable,
      retroThemesInBuild: RETRO_THEMES_IN_BUILD,
      setRetroThemesEnabled,
    }}>
      {children}
    </ThemeContext.Provider>
  )
}

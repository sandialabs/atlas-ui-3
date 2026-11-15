import { createContext, useCallback, useContext, useEffect, useReducer, useRef, useState } from 'react'
import {
  DEFAULT_THEME_ID, RETRO_THEMES_IN_BUILD, effectiveThemeId, getRegisteredTheme, isKnownThemeId,
  isThemePermitted, normalizeAllowedThemes,
} from '../themes/themeRegistry'
import { forgetFailedRetroThemeCss, loadRetroThemeCss, retroThemeCssStatus } from '../themes/retroThemeCss'

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
 * The run-time FEATURE_RETRO_THEMES_ENABLED value before /api/config answers.
 * The backend stamps the live flag on the bootstrap <script> tag
 * (data-retro-runtime, see atlas/core/spa_index.py); that wins. Otherwise
 * (Vite dev server, tests) the cached config: true/false when known,
 * undefined before any config has been cached (the saved theme is then
 * trusted until /api/config/shell answers). Same order as the first-paint
 * bootstrap, so the two never disagree.
 */
function getStampedRetroRuntime() {
  try {
    const stamped = document.querySelector('script[data-retro-runtime]')?.getAttribute('data-retro-runtime')
    if (stamped === 'true' || stamped === 'false') return stamped === 'true'
  } catch { /* No DOM. */ }
  return undefined
}

// First-paint policy comes from a server-stamped tag, never a stale config
// cache. Without that tag (development/older servers), default to Light/Dark
// until the current config bridge supplies an explicit allowlist.
function getStampedAllowedThemes() {
  try {
    const raw = document.querySelector('script[data-allowed-themes]')
      ?.getAttribute('data-allowed-themes')
    return raw == null ? null : normalizeAllowedThemes(raw)
  } catch {
    return null
  }
}

function getCachedRetroRuntime() {
  const stamped = getStampedRetroRuntime()
  if (stamped !== undefined) return stamped
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
  // The backend's stamp is the live value for this page load. useChat's
  // features start from the same localStorage cache, so until /api/config
  // answers they would replay the stale value; ignore them when stamped.
  const [stamped] = useState(getStampedRetroRuntime)
  const [stampedAllowedThemes] = useState(getStampedAllowedThemes)
  const [allowedThemeIds, setAllowedThemeIds] = useState(
    () => stampedAllowedThemes ?? normalizeAllowedThemes(null),
  )
  const retroThemesAvailable = RETRO_THEMES_IN_BUILD && retroRuntime !== false
  const wanted = effectiveThemeId(selectedTheme, retroThemesAvailable, allowedThemeIds)
  // Retro CSS loads per theme on demand (themes/retroThemeCss.js). Until the
  // wanted theme's sheet is in, keep showing the last theme whose CSS was
  // ready, so a switch never paints an unstyled retro ID; if it fails, use
  // the theme's light/dark base. On mount the bootstrap has already
  // injected the saved theme's sheet, so the wanted theme shows directly.
  const [, cssSettled] = useReducer(count => count + 1, 0)
  const shown = useRef(null)
  const cssStatus = retroThemeCssStatus(wanted)
  // A revoked theme must not remain visible while the replacement stylesheet
  // is loading. Light/Dark are always available as a safe local fallback.
  const previousPermitted = isThemePermitted(shown.current, retroThemesAvailable, allowedThemeIds)
  const theme = cssStatus === 'loaded' ? wanted
    : cssStatus === 'failed' ? getRegisteredTheme(wanted).mode
      : (shown.current === null ? wanted
        : previousPermitted ? shown.current : getRegisteredTheme(wanted).mode)
  const definition = getRegisteredTheme(theme)

  useEffect(() => {
    if (cssStatus !== 'pending') return undefined
    let live = true
    loadRetroThemeCss(wanted).then(() => { if (live) cssSettled() })
    return () => { live = false }
  }, [wanted, cssStatus])

  useEffect(() => {
    const root = document.documentElement
    // data-theme remains dark/light for compatibility with existing Atlas CSS.
    // data-atlas-theme names the exact registered visual identity.
    root.setAttribute('data-theme', definition.mode)
    root.setAttribute('data-atlas-theme', theme)
    shown.current = theme
    try {
      localStorage.setItem('atlas-theme', isKnownThemeId(selectedTheme) ? selectedTheme : theme)
    } catch { /* ignore */ }
  }, [theme, definition.mode, selectedTheme])

  const setTheme = useCallback(id => {
    const next = typeof id === 'function' ? id(selectedTheme) : id
    // An injected UI event cannot write a disallowed retro selection or
    // initiate a stylesheet fetch for it.
    if (!isKnownThemeId(next) ||
        !isThemePermitted(next, retroThemesAvailable, allowedThemeIds)) return
    forgetFailedRetroThemeCss(next)
    cssSettled()
    setSelectedTheme(next)
  }, [selectedTheme, retroThemesAvailable, allowedThemeIds])

  // The stamped policy is authoritative for this page load. The ChatContext
  // may replay stale cached features before /api/config has answered; those
  // cached bytes cannot reinstate a revoked theme.
  const setAllowedThemes = useCallback(value => {
    if (stampedAllowedThemes !== null) return
    const next = normalizeAllowedThemes(value)
    setAllowedThemeIds(current =>
      current.length === next.length && current.every((id, index) => id === next[index])
        ? current : next,
    )
  }, [stampedAllowedThemes])

  // Called with features.retro_themes once /api/config(/shell) answers.
  // Anything but a boolean (an older backend without the field) is ignored,
  // and so is every value when the backend stamped the flag on index.html.
  const setRetroThemesEnabled = useCallback(value => {
    if (typeof value === 'boolean' && stamped === undefined) setRetroRuntime(value)
  }, [stamped])

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
      allowedThemeIds,
      setAllowedThemes,
      setRetroThemesEnabled,
    }}>
      {children}
    </ThemeContext.Provider>
  )
}

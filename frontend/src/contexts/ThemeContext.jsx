import { createContext, useContext, useEffect, useState } from 'react'
import { DEFAULT_THEME_ID, getRegisteredTheme, resolveThemeId } from '../themes/themeRegistry'

const ThemeContext = createContext()

function getInitialTheme() {
  try {
    return resolveThemeId(localStorage.getItem('atlas-theme'))
  } catch { /* Storage may be unavailable in privacy modes. */ }
  return DEFAULT_THEME_ID
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}

export function ThemeProvider({ children }) {
  const [selectedTheme, setSelectedTheme] = useState(getInitialTheme)
  const theme = resolveThemeId(selectedTheme)
  const definition = getRegisteredTheme(theme)

  useEffect(() => {
    const root = document.documentElement
    // data-theme remains dark/light for compatibility with existing Atlas CSS.
    // data-atlas-theme names the exact registered visual identity.
    root.setAttribute('data-theme', definition.mode)
    root.setAttribute('data-atlas-theme', theme)
    try {
      localStorage.setItem('atlas-theme', theme)
    } catch { /* ignore */ }
  }, [theme, definition.mode])

  // Atlas intentionally defaults to dark, not the OS color-scheme preference.
  // Keep the legacy toggle API while the new gallery supports every preset.
  const toggleTheme = () => setSelectedTheme(current => current === 'dark' ? 'light' : 'dark')

  return (
    <ThemeContext.Provider value={{ theme, setTheme: setSelectedTheme, toggleTheme, themeDefinition: definition }}>
      {children}
    </ThemeContext.Provider>
  )
}

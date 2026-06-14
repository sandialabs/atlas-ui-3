import { createContext, useContext, useEffect, useState } from 'react'

const ThemeContext = createContext()

function getInitialTheme() {
  try {
    const stored = localStorage.getItem('atlas-theme')
    if (stored === 'light' || stored === 'dark') return stored
  } catch { /* ignore */ }
  return 'dark'
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(getInitialTheme)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('atlas-theme', theme)
    } catch { /* ignore */ }
  }, [theme])

  // Note: Atlas deliberately defaults first-run users to dark mode and does not
  // follow the OS `prefers-color-scheme` setting. Explicit user choices (saved in
  // localStorage) are always preserved.

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

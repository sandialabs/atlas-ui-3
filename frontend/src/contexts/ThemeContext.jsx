import { createContext, useContext, useEffect, useState } from 'react'

const ThemeContext = createContext()

function getSystemTheme() {
  if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
    return 'light'
  }
  return 'dark'
}

function getInitialTheme() {
  try {
    const stored = localStorage.getItem('atlas-theme')
    if (stored === 'light' || stored === 'dark') return stored
  } catch { /* ignore */ }
  return getSystemTheme()
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

  // Listen for system preference changes
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: light)')
    const handler = () => {
      // Only follow system if user hasn't explicitly set a preference
      const stored = localStorage.getItem('atlas-theme')
      if (!stored) {
        setTheme(mq.matches ? 'light' : 'dark')
      }
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

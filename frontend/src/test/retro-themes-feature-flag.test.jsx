/**
 * Build-time (VITE_FEATURE_RETRO_THEMES) and run-time
 * (FEATURE_RETRO_THEMES_ENABLED -> features.retro_themes) gates for the Retro
 * Design System gallery (issue #1119, maintainer request on PR #1175).
 */
import { readFileSync } from 'node:fs'
import { act, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPanel from '../components/SettingsPanel'
import { ThemeProvider, useTheme } from '../contexts/ThemeContext'
import { useChat } from '../contexts/ChatContext'
import {
  THEME_REGISTRY, effectiveThemeId, isKnownThemeId, RETRO_THEMES_IN_BUILD,
} from '../themes/themeRegistry'
import { RETRO_THEME_MODES } from '../themes/retroThemeModes.generated'
import * as disabledModule from '../themes/retro/retroThemes.disabled'
import * as enabledModule from '../themes/retro/retroThemes.enabled'
import { retroThemesBuildEnabled, retroThemesModulePath } from '../themes/retro/retroThemesBuildFlag'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))
vi.mock('../hooks/useGlobusAuth', () => ({
  useGlobusAuth: () => ({
    authStatus: null, loading: false, error: null, fetchAuthStatus: vi.fn(),
    login: vi.fn(), logout: vi.fn(), isAuthenticated: false,
  }),
}))
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: () => ({ isComplianceAccessible: () => true }),
}))
vi.mock('../components/ToolsPanel', () => ({ default: () => <div>tools</div> }))
vi.mock('../components/DataSourcesSelector', () => ({ default: () => <div>sources</div> }))
vi.mock('../components/admin/AdminQuickPanel', () => ({ default: () => <div>admin</div> }))
vi.mock('../components/PromptManager', () => ({ default: () => <div>prompts</div> }))

// Mirrors App.jsx's RetroThemeFeatureBridge with a controllable value.
function RuntimeFlag({ value }) {
  const { setRetroThemesEnabled } = useTheme()
  useEffect(() => { setRetroThemesEnabled(value) }, [value, setRetroThemesEnabled])
  return null
}
function ActiveTheme() {
  const { theme, selectedTheme } = useTheme()
  return <output data-testid="active-theme" data-selected={selectedTheme}>{theme}</output>
}
function renderSettings(runtimeFlag) {
  useChat.mockReturnValue({
    settings: { autoApproveTools: false }, updateSettings: vi.fn(),
    features: {}, agentModeAvailable: false, isInAdminGroup: false,
  })
  return render(
    <ThemeProvider>
      <RuntimeFlag value={runtimeFlag} />
      <ActiveTheme />
      <SettingsPanel isOpen onClose={vi.fn()} initialTab="general" />
    </ThemeProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.removeAttribute('data-atlas-theme')
})

describe('VITE_FEATURE_RETRO_THEMES build flag', () => {
  it('defaults on; only the literal "false" compiles the catalog out', () => {
    expect(retroThemesBuildEnabled({})).toBe(true)
    expect(retroThemesBuildEnabled({ VITE_FEATURE_RETRO_THEMES: 'true' })).toBe(true)
    expect(retroThemesBuildEnabled({ VITE_FEATURE_RETRO_THEMES: '' })).toBe(true)
    expect(retroThemesBuildEnabled({ VITE_FEATURE_RETRO_THEMES: 'false' })).toBe(false)
    expect(retroThemesBuildEnabled({ VITE_FEATURE_RETRO_THEMES: ' FALSE ' })).toBe(false)
  })

  it('aliases @atlas/retro-themes to the enabled or the empty module', () => {
    expect(retroThemesModulePath(true)).toMatch(/retroThemes\.enabled\.js$/)
    expect(retroThemesModulePath(false)).toMatch(/retroThemes\.disabled\.js$/)
    const vite = readFileSync('vite.config.js', 'utf8')
    expect(vite).toContain("'@atlas/retro-themes': retroThemesModulePath(retroThemesEnabled)")
  })

  it('the compiled-out module ships no catalog and imports no stylesheet', () => {
    expect(disabledModule.RETRO_CATALOG).toEqual([])
    expect(disabledModule.RETRO_THEMES_BUILT).toBe(false)
    const source = readFileSync('src/themes/retro/retroThemes.disabled.js', 'utf8')
    expect(source).not.toMatch(/\bimport\b/)
    expect(enabledModule.RETRO_CATALOG).toHaveLength(53)
    expect(enabledModule.RETRO_THEMES_BUILT).toBe(true)
  })

  it('keeps every retro stylesheet behind the alias, never in the always-loaded entry', () => {
    const enabled = readFileSync('src/themes/retro/retroThemes.enabled.js', 'utf8')
    for (const sheet of ['tokens.css', 'retroCatalog.generated.css', 'retroChrome.generated.css', 'retroChrome.css']) {
      expect(enabled).toContain(sheet)
      for (const entry of ['src/main.jsx', 'src/App.jsx', 'src/index.css']) {
        expect(readFileSync(entry, 'utf8'), `${entry} must not import ${sheet}`).not.toMatch(
          new RegExp(`import\\s+['"][^'"]*${sheet.replace('.', '\\.')}['"]`))
      }
    }
    // Dark/Light content aliases stay in every build.
    expect(readFileSync('src/main.jsx', 'utf8')).toContain("import './themes/baseThemes.generated.css'")
  })

  it('this test build includes the catalog (55 registered themes)', () => {
    expect(RETRO_THEMES_IN_BUILD).toBe(true)
    expect(THEME_REGISTRY).toHaveLength(55)
  })
})

describe('saved theme fallback', () => {
  it('maps every retro ID to its own base mode when retro themes are unavailable', () => {
    expect(Object.keys(RETRO_THEME_MODES)).toHaveLength(53)
    for (const [id, mode] of Object.entries(RETRO_THEME_MODES)) {
      expect(effectiveThemeId(id, true)).toBe(id)
      expect(effectiveThemeId(id, false)).toBe(mode)
      expect(isKnownThemeId(id)).toBe(true)
    }
    expect(effectiveThemeId('light', false)).toBe('light')
    expect(effectiveThemeId('dark', false)).toBe('dark')
    for (const bad of [null, '', 'removed-theme', '__proto__', 'constructor']) {
      expect(effectiveThemeId(bad, false)).toBe('dark')
      expect(effectiveThemeId(bad, true)).toBe('dark')
      expect(isKnownThemeId(bad)).toBe(false)
    }
  })
})

describe('FEATURE_RETRO_THEMES_ENABLED run-time flag', () => {
  it('shows the gallery when the server enables it', () => {
    renderSettings(true)
    expect(screen.getByRole('group', { name: 'Registered interface themes' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Switch to light mode/ })).toBeInTheDocument()
  })

  it('hides the gallery but keeps the Light/Dark toggle when the server disables it', () => {
    renderSettings(false)
    expect(screen.queryByRole('group', { name: 'Registered interface themes' })).not.toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: 'Windows 95' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Switch to light mode/ })).toBeInTheDocument()
  })

  it('falls a saved retro theme back to its base mode and restores it when re-enabled', () => {
    localStorage.setItem('atlas-theme', 'windows-xp-luna')
    const { rerender } = renderSettings(false)
    expect(screen.getByTestId('active-theme')).toHaveTextContent('light')
    expect(document.documentElement.getAttribute('data-atlas-theme')).toBe('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    // The user's choice survives so turning the feature back on restores it.
    expect(localStorage.getItem('atlas-theme')).toBe('windows-xp-luna')
    expect(screen.getByText(/Currently using Light theme/)).toBeInTheDocument()

    rerender(
      <ThemeProvider>
        <RuntimeFlag value={true} />
        <ActiveTheme />
        <SettingsPanel isOpen onClose={vi.fn()} initialTab="general" />
      </ThemeProvider>,
    )
    expect(screen.getByTestId('active-theme')).toHaveTextContent('windows-xp-luna')
    expect(document.documentElement.getAttribute('data-atlas-theme')).toBe('windows-xp-luna')
  })

  it('a dark retro theme falls back to Dark, not the Light default of light retro themes', () => {
    localStorage.setItem('atlas-theme', 'crt-phosphor')
    renderSettings(false)
    expect(screen.getByTestId('active-theme')).toHaveTextContent('dark')
  })

  it('starts from the cached config so first paint and React agree before /api/config answers', () => {
    localStorage.setItem('atlas-theme', 'beos')
    localStorage.setItem('chatui-config-cache', JSON.stringify({ features: { retro_themes: false } }))
    renderSettings(undefined)
    expect(screen.getByTestId('active-theme')).toHaveTextContent('light')
  })

  it('ignores a config without the field (older backend) instead of hiding the gallery', () => {
    localStorage.setItem('atlas-theme', 'beos')
    renderSettings(undefined)
    expect(screen.getByTestId('active-theme')).toHaveTextContent('beos')
  })

  it('switching the runtime flag off live moves the document off the retro theme', () => {
    localStorage.setItem('atlas-theme', 'win95')
    function Harness() {
      const { setRetroThemesEnabled, theme } = useTheme()
      return (
        <>
          <output data-testid="t">{theme}</output>
          <button type="button" onClick={() => setRetroThemesEnabled(false)}>off</button>
        </>
      )
    }
    render(<ThemeProvider><Harness /></ThemeProvider>)
    expect(screen.getByTestId('t')).toHaveTextContent('win95')
    act(() => { screen.getByRole('button', { name: 'off' }).click() })
    expect(screen.getByTestId('t')).toHaveTextContent('light')
    expect(document.documentElement.getAttribute('data-atlas-theme')).toBe('light')
  })
})

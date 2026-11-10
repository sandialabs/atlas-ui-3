/**
 * A build with VITE_FEATURE_RETRO_THEMES=false: vite.config.js aliases
 * `@atlas/retro-themes` to retroThemes.disabled.js. Mock the alias the same
 * way to check the registry, first render and Settings in such a build.
 */
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@atlas/retro-themes', () => import('../themes/retro/retroThemes.disabled'))
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

const { THEME_REGISTRY, RETRO_THEMES_IN_BUILD } = await import('../themes/themeRegistry')
const { ThemeProvider, useTheme } = await import('../contexts/ThemeContext')
const { default: SettingsPanel } = await import('../components/SettingsPanel')
const { useChat } = await import('../contexts/ChatContext')

function ActiveTheme() {
  const { theme, retroThemesAvailable, retroThemesInBuild } = useTheme()
  return (
    <output data-testid="active" data-available={String(retroThemesAvailable)}
      data-in-build={String(retroThemesInBuild)}>{theme}</output>
  )
}

beforeEach(() => {
  localStorage.clear()
  useChat.mockReturnValue({
    settings: {}, updateSettings: vi.fn(),
    // Even a server that enables the feature cannot add themes the build lacks.
    features: { retro_themes: true }, agentModeAvailable: false, isInAdminGroup: false,
  })
})

describe('build with VITE_FEATURE_RETRO_THEMES=false', () => {
  it('registers only Light and Dark', () => {
    expect(RETRO_THEMES_IN_BUILD).toBe(false)
    expect(THEME_REGISTRY.map(t => t.id)).toEqual(['dark', 'light'])
  })

  it('falls a saved retro ID back to its base mode and hides the gallery', () => {
    localStorage.setItem('atlas-theme', 'aqua-osx')
    localStorage.setItem('chatui-config-cache', JSON.stringify({ features: { retro_themes: true } }))
    render(
      <ThemeProvider>
        <ActiveTheme />
        <SettingsPanel isOpen onClose={vi.fn()} initialTab="general" />
      </ThemeProvider>,
    )
    const active = screen.getByTestId('active')
    expect(active).toHaveTextContent('light')
    expect(active).toHaveAttribute('data-available', 'false')
    expect(active).toHaveAttribute('data-in-build', 'false')
    expect(document.documentElement.getAttribute('data-atlas-theme')).toBe('light')
    expect(screen.queryByRole('group', { name: 'Registered interface themes' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Switch to dark mode/ })).toBeInTheDocument()
  })
})

import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ThemeProvider, useTheme } from '../contexts/ThemeContext'
import ThemePicker from '../themes/ThemePicker'
import {
  DEFAULT_THEME_ID, THEME_REGISTRY, resolveThemeId,
  normalizeAllowedThemes, isThemePermitted, effectiveThemeId,
} from '../themes/themeRegistry'
import manifest from '../themes/vendor/retro-design-system/manifest.json'

function Probe() {
  const { theme, toggleTheme } = useTheme()
  return (
    <>
      <output data-testid="selected-theme">{theme}</output>
      <button onClick={toggleTheme}>Toggle classic light/dark</button>
    </>
  )
}

describe('build-time theme registry', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-atlas-theme')
    const policy = document.createElement('script')
    policy.setAttribute('data-allowed-themes', THEME_REGISTRY.map(theme => theme.id).join(','))
    policy.id = 'test-theme-admin-policy'
    document.head.appendChild(policy)
  })
  afterEach(() => {
    document.getElementById('test-theme-admin-policy')?.remove()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-atlas-theme')
  })

  it('does not accept unknown themes or remote CSS identifiers', () => {
    expect(resolveThemeId('https://remote.invalid/theme.css')).toBe(DEFAULT_THEME_ID)
    expect(resolveThemeId('removed-preset')).toBe(DEFAULT_THEME_ID)
    expect(resolveThemeId('bbs-ansi')).toBe('bbs-ansi')
    expect(resolveThemeId('obsolete-theme')).toBe(DEFAULT_THEME_ID)
    expect(new Set(THEME_REGISTRY.map(t => t.id)).size).toBe(THEME_REGISTRY.length)
    expect(THEME_REGISTRY).toHaveLength(55)
    expect(THEME_REGISTRY.filter(t => t.reference).map(t => t.reference)).toEqual(
      manifest.systems.map(system => system.slug),
    )
    expect(THEME_REGISTRY.every(t => ['dark', 'light'].includes(t.mode))).toBe(true)
  })

  it.each([
    ['dark', 'dark'],
    ['light', 'light'],
    ['crt-phosphor', 'dark'],
    ['bbs-ansi', 'dark'],
    ['default-browser', 'light'],
    ['win95', 'light'],
    ['mac7', 'light'],
  ])('restores %s with backwards-compatible %s base selectors', (id, mode) => {
    localStorage.setItem('atlas-theme', id)
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>
    )
    expect(screen.getByTestId('selected-theme')).toHaveTextContent(id)
    expect(document.documentElement).toHaveAttribute('data-theme', mode)
    expect(document.documentElement).toHaveAttribute('data-atlas-theme', id)
  })

  it('automatically falls back from an unregistered saved preset', () => {
    localStorage.setItem('atlas-theme', 'deleted-theme')
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>
    )
    expect(screen.getByTestId('selected-theme')).toHaveTextContent('dark')
    expect(localStorage.getItem('atlas-theme')).toBe('dark')
  })

  it('switches from default to win95 and back through the native radio gallery', () => {
    render(
      <ThemeProvider>
        <Probe />
        <ThemePicker />
      </ThemeProvider>
    )
    const win95 = screen.getByRole('radio', { name: 'Windows 95' })
    const mac7 = screen.getByRole('radio', { name: 'Mac System 7' })
    expect(win95).not.toBeChecked()

    fireEvent.click(win95)
    expect(win95).toBeChecked()
    expect(document.documentElement).toHaveAttribute('data-atlas-theme', 'win95')
    expect(document.documentElement).toHaveAttribute('data-theme', 'light')
    expect(localStorage.getItem('atlas-theme')).toBe('win95')

    fireEvent.click(mac7)
    expect(mac7).toBeChecked()
    expect(document.documentElement).toHaveAttribute('data-atlas-theme', 'mac7')

    fireEvent.click(screen.getByRole('button', { name: 'Toggle classic light/dark' }))
    expect(screen.getByTestId('selected-theme')).toHaveTextContent('dark')
  })

  it('filters the real theme registry by name, era, and description without changing the active theme', () => {
    render(
      <ThemeProvider>
        <Probe />
        <ThemePicker />
      </ThemeProvider>
    )
    const search = screen.getByRole('searchbox', { name: 'Find a theme' })
    expect(search).toHaveAttribute('maxlength', '80')
    fireEvent.change(search, { target: { value: 'Windows XP' } })
    expect(screen.getAllByRole('radio')).toHaveLength(1)
    expect(screen.getByRole('radio', { name: 'Windows XP Luna' })).toBeInTheDocument()
    expect(screen.getByText('Showing 1 of 55 themes')).toBeInTheDocument()
    expect(screen.getByTestId('selected-theme')).toHaveTextContent('dark')
    fireEvent.change(search, { target: { value: 'no-such-desktop' } })
    expect(screen.queryAllByRole('radio')).toHaveLength(0)
    expect(screen.getByText(/No matching themes/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear theme filter' }))
    expect(search).toHaveValue('')
    expect(screen.getAllByRole('radio')).toHaveLength(55)
    expect(screen.getByTestId('selected-theme')).toHaveTextContent('dark')
  })

  it('filters all 53 designs by category and tags, with a complete reset', () => {
    render(
      <ThemeProvider>
        <Probe />
        <ThemePicker />
      </ThemeProvider>
    )
    const category = screen.getByRole('combobox', { name: 'Design category' })
    fireEvent.change(category, { target: { value: 'Desktop OS' } })
    expect(screen.getByRole('radio', { name: 'Windows 95' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Mac OS X Aqua' })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: 'CRT Phosphor Terminal' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('radio').length).toBeGreaterThan(4)
    fireEvent.change(screen.getByRole('searchbox', { name: 'Find a theme' }), {
      target: { value: 'luna' },
    })
    expect(screen.getAllByRole('radio')).toHaveLength(1)
    expect(screen.getByRole('radio', { name: 'Windows XP Luna' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear theme filter' }))
    expect(category).toHaveValue('all')
    expect(screen.getAllByRole('radio')).toHaveLength(55)
    expect(screen.getByTestId('selected-theme')).toHaveTextContent('dark')
  })

  it('keeps all registered choices accessible without relying on a preview image', () => {
    render(
      <ThemeProvider>
        <ThemePicker />
      </ThemeProvider>
    )
    const radios = screen.getAllByRole('radio')
    expect(radios).toHaveLength(THEME_REGISTRY.length)
    for (const entry of THEME_REGISTRY) {
      expect(screen.getByRole('radio', { name: entry.name })).toHaveAttribute('value', entry.id)
    }
    expect(screen.getByRole('radiogroup', { name: 'Interface theme' })).toBeInTheDocument()
    const source = screen.getByRole('link', { name: 'NovusGFX Retro Design System' })
    expect(source).toHaveAttribute('href', 'https://github.com/NovusGFX/retro-design-system')
    expect(source).toHaveAttribute('rel', 'noopener noreferrer')
    expect(source).toHaveAttribute('target', '_blank')
  })
})


describe('administrator theme policy normalization', () => {
  it('permits only Light and Dark with no administrator policy', () => {
    expect(normalizeAllowedThemes(null)).toEqual(['dark', 'light'])
    expect(normalizeAllowedThemes([])).toEqual(['dark', 'light'])
    expect(isThemePermitted('win95')).toBe(false)
    expect(isThemePermitted('light')).toBe(true)
  })
  it('includes explicit known themes, rejects unknown IDs and never removes base themes', () => {
    const allowed = normalizeAllowedThemes(['win95', 'win95', '__proto__', 'unknown', ' LIGHT '])
    expect(allowed).toEqual(['dark', 'light', 'win95'])
    expect(isThemePermitted('win95', true, allowed)).toBe(true)
    expect(isThemePermitted('mac7', true, allowed)).toBe(false)
    expect(isThemePermitted('win95', false, allowed)).toBe(false)
    expect(effectiveThemeId('mac7', true, allowed)).toBe('light')
    expect(effectiveThemeId('win95', true, allowed)).toBe('win95')
    expect(effectiveThemeId('win95', false, allowed)).toBe('light')
  })
})

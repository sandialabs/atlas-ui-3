import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider, useTheme } from '../contexts/ThemeContext'

function ThemeProbe() {
  const { theme } = useTheme()
  return <div data-testid="theme">{theme}</div>
}

// matchMedia reporting that the OS prefers light. Atlas deliberately ignores this
// for first-run users (dark default), so the mock exists to prove the OS signal is
// NOT followed — not because the provider reacts to it.
function mockMatchMediaPrefersLight() {
  window.matchMedia = vi.fn().mockImplementation(query => ({
    matches: query === '(prefers-color-scheme: light)',
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
}

describe('ThemeProvider', () => {
  // matchMedia is assigned directly on window (a manual global, not a vi spy), so
  // vi.restoreAllMocks() won't revert it. Save and restore it explicitly to avoid
  // leaking the mock into other test files and causing order-dependent failures.
  const originalMatchMedia = window.matchMedia

  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    mockMatchMediaPrefersLight()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    window.matchMedia = originalMatchMedia
  })

  it('defaults to dark mode for first-run users even when the OS prefers light', async () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
    await waitFor(() => {
      expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
      expect(localStorage.getItem('atlas-theme')).toBe('dark')
    })
  })

  it('preserves a saved light-mode preference', () => {
    localStorage.setItem('atlas-theme', 'light')

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
  })

  it('falls back to dark and rewrites storage for an invalid/legacy stored value', async () => {
    localStorage.setItem('atlas-theme', 'system')

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
    await waitFor(() => {
      expect(localStorage.getItem('atlas-theme')).toBe('dark')
    })
  })

  it('still renders and resolves to dark when localStorage throws', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })

    expect(() =>
      render(
        <ThemeProvider>
          <ThemeProbe />
        </ThemeProvider>
      )
    ).not.toThrow()
    expect(screen.getByTestId('theme')).toHaveTextContent('dark')

    getItem.mockRestore()
    setItem.mockRestore()
  })
})

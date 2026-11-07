import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { ThemeProvider, useTheme } from '../contexts/ThemeContext'
import ThemePicker from '../themes/ThemePicker'
import { FAVORITES_STORAGE_KEY, readThemeFavorites } from '../themes/themeFavorites'

function ActiveTheme() {
  const { theme } = useTheme()
  return <output data-testid="active-theme">{theme}</output>
}
function Gallery() {
  return (
    <ThemeProvider>
      <ActiveTheme />
      <ThemePicker />
    </ThemeProvider>
  )
}

describe('the complete theme catalog favorites and selection recovery', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-atlas-theme')
  })

  it('persists favorite registered IDs and restores them when the gallery reopens', () => {
    const { unmount } = render(<Gallery />)
    const favorite = screen.getByRole('button', { name: 'Favorite current theme' })
    expect(favorite).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(favorite)
    expect(screen.getByRole('button', { name: 'Remove current from favorites' }))
      .toHaveAttribute('aria-pressed', 'true')
    expect(readThemeFavorites()).toEqual(['dark'])
    fireEvent.click(screen.getByRole('checkbox', { name: 'Favorites only (1)' }))
    expect(screen.getAllByRole('radio')).toHaveLength(1)
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked()
    unmount()

    render(<Gallery />)
    expect(screen.getByRole('checkbox', { name: 'Favorites only (1)' })).not.toBeChecked()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Favorites only (1)' }))
    expect(screen.getAllByRole('radio')).toHaveLength(1)
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: 'Remove current from favorites' }))
    expect(readThemeFavorites()).toEqual([])
    expect(screen.getByText(/No matching themes/)).toBeInTheDocument()
  })

  it('reveals the current theme and focuses its native radio after filtering it away', () => {
    render(<Gallery />)
    const search = screen.getByRole('searchbox', { name: 'Find a theme' })
    fireEvent.change(search, { target: { value: 'NeXTSTEP' } })
    expect(screen.getByTestId('active-theme')).toHaveTextContent('dark')
    expect(screen.queryByRole('radio', { name: 'Dark' })).not.toBeInTheDocument()
    expect(screen.getByText('Current theme:')).toHaveTextContent('Current theme: Dark')
    const reveal = screen.getByRole('button', { name: 'Show selected theme' })
    fireEvent.click(reveal)
    expect(search).toHaveValue('')
    expect(screen.getByRole('radio', { name: 'Dark' })).toHaveFocus()
    expect(screen.queryByRole('button', { name: 'Show selected theme' })).not.toBeInTheDocument()
  })

  it('recovers a hidden selection from Favorites only, preserving actual theme identity', () => {
    render(<Gallery />)
    fireEvent.click(screen.getByRole('radio', { name: 'Windows 95' }))
    expect(screen.getByTestId('active-theme')).toHaveTextContent('win95')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Favorites only (0)' }))
    expect(screen.queryAllByRole('radio')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Show selected theme' }))
    expect(screen.getByRole('checkbox', { name: 'Favorites only (0)' })).not.toBeChecked()
    expect(screen.getByRole('radio', { name: 'Windows 95' })).toHaveFocus()
    expect(screen.getByRole('radio', { name: 'Windows 95' })).toBeChecked()
    expect(localStorage.getItem('atlas-theme')).toBe('win95')
    fireEvent.click(screen.getByRole('button', { name: 'Favorite current theme' }))
    expect(readThemeFavorites()).toEqual(['win95'])
  })

  it('rejects malformed, duplicate and retired favorites without losing the gallery', () => {
    localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify([
      'win95', 'win95', 'unknown-theme', 'https://example.test/unsafe.css', 4, 'mac7',
    ]))
    render(<Gallery />)
    expect(readThemeFavorites()).toEqual(['win95', 'mac7'])
    expect(screen.getByRole('checkbox', { name: 'Favorites only (2)' })).toBeInTheDocument()
    expect(screen.getAllByRole('radio')).toHaveLength(55)
    localStorage.setItem(FAVORITES_STORAGE_KEY, '{not-json')
    expect(readThemeFavorites()).toEqual([])
  })
})

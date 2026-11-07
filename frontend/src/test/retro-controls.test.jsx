import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider, useTheme } from '../contexts/ThemeContext'
import { RetroButton, RetroInput, RetroWindow, RetroWorkbench } from '../themes/RetroControls'
import ThemePicker from '../themes/ThemePicker'

function SetPreset() {
  const { setTheme } = useTheme()
  return <button onClick={() => setTheme('win95')}>Select Windows 95</button>
}

describe('Retro Design System semantic components', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-atlas-theme')
  })
  afterEach(() => {
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-atlas-theme')
  })

  it('renders a titled section, not an unsolicited modal dialog', () => {
    render(<RetroWindow title="Samples" footer="Local status">Visible data</RetroWindow>)
    const window = screen.getByRole('region', { name: 'Samples' })
    expect(window).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Samples' })).toBeInTheDocument()
    expect(screen.getByText('Local status')).toBeInTheDocument()
    expect(screen.getByText('Visible data')).toBeInTheDocument()
    expect(window.querySelectorAll('button')).toHaveLength(0)
    expect(window.querySelector('.atlas-retro-window-glyphs')).toHaveAttribute('aria-hidden', 'true')
  })

  it('has a functional, accessible optional close action rather than a fake close glyph', () => {
    const onClose = vi.fn()
    render(
      <RetroWindow title="Example window" onClose={onClose}>
        Example content
      </RetroWindow>
    )
    const close = screen.getByRole('button', { name: 'Close Example window' })
    expect(close).toHaveAttribute('type', 'button')
    fireEvent.click(close)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('uses native keyboard-focusable controls with labels and disabled state', () => {
    render(
      <div>
        <RetroInput label="Source name" placeholder="Example" />
        <RetroButton disabled>Inactive action</RetroButton>
        <RetroButton>Active action</RetroButton>
      </div>
    )
    expect(screen.getByRole('textbox', { name: 'Source name' })).toHaveAttribute('placeholder', 'Example')
    expect(screen.getByRole('button', { name: 'Inactive action' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Active action' })).toHaveAttribute('type', 'button')
  })

  it('supports local preview interaction and switching themes without executing commands', () => {
    render(
      <ThemeProvider>
        <SetPreset />
        <RetroWorkbench />
      </ThemeProvider>
    )
    expect(screen.getByRole('status')).toHaveTextContent('Ready')
    const input = screen.getByRole('textbox', { name: 'Sample label' })
    fireEvent.change(input, { target: { value: 'HELLO BBS' } })
    fireEvent.click(screen.getByRole('button', { name: 'Preview label' }))
    expect(screen.getByRole('status')).toHaveTextContent('Preview: HELLO BBS')

    fireEvent.click(screen.getByRole('button', { name: 'Select Windows 95' }))
    expect(document.documentElement).toHaveAttribute('data-atlas-theme', 'win95')
    expect(screen.getByText('Atlas / Windows 95 / local demo')).toBeInTheDocument()

    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Preview label' }))
    expect(screen.getByRole('status')).toHaveTextContent('Enter a sample label to preview')
  })

  it('lets the Gallery reveal the real component preview on request', () => {
    render(
      <ThemeProvider>
        <ThemePicker />
      </ThemeProvider>
    )
    const summary = screen.getByText('Try live retro controls')
    expect(summary.closest('details')).not.toHaveAttribute('open')
    fireEvent.click(summary)
    expect(summary.closest('details')).toHaveAttribute('open')
    expect(screen.getByRole('region', { name: 'Theme workbench' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Sample label' })).toHaveAttribute('maxlength', '80')
    fireEvent.change(screen.getByRole('textbox', { name: 'Sample label' }), { target: { value: '<script>alert(1)</script>' } })
    fireEvent.click(screen.getByRole('button', { name: 'Preview label' }))
    expect(screen.getByRole('status')).toHaveTextContent('Preview: <script>alert(1)</script>')
    expect(screen.queryByText('alert(1)')).not.toBeInTheDocument()
    expect(document.querySelectorAll('script')).toHaveLength(0)
  })
})

/**
 * Agent Mode explanation is progressive disclosure.
 *
 * The header toggle is icon-only, so a question-mark button next to it opens a
 * plain-language explanation. People who use the feature already -- or who turn
 * on "Reduce helper text" -- do not get the extra button at all.
 */

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AgentModeHelpButton, AgentModeMenuHint } from '../components/AgentModeHelp'
import { OPEN_SETTINGS_EVENT } from '../utils/settingsPanelEvents'

describe('AgentModeHelpButton', () => {
  it('renders a help button by default', () => {
    render(<AgentModeHelpButton />)
    expect(screen.getByRole('button', { name: 'What Agent Mode does' })).toBeInTheDocument()
  })

  it('is not rendered when helper text is reduced', () => {
    render(<AgentModeHelpButton quiet />)
    expect(screen.queryByRole('button', { name: 'What Agent Mode does' })).not.toBeInTheDocument()
  })

  it('explains multi-step tool use and the no-tools fallback', () => {
    render(<AgentModeHelpButton />)

    fireEvent.click(screen.getByRole('button', { name: 'What Agent Mode does' }))

    const dialog = screen.getByRole('dialog', { name: 'What Agent Mode does' })
    expect(dialog).toHaveTextContent(/multiple steps/i)
    expect(dialog).toHaveTextContent(/calls the tools you have selected/i)
    expect(dialog).toHaveTextContent(/inspects the results/i)
    expect(dialog).toHaveTextContent(/behaves like normal chat/i)
    expect(dialog).toHaveTextContent(/model that supports tool calling/i)
  })

  it('does not offer the tools picker when tools are disabled', () => {
    render(<AgentModeHelpButton toolsEnabled={false} />)

    fireEvent.click(screen.getByRole('button', { name: 'What Agent Mode does' }))

    const dialog = screen.getByRole('dialog', { name: 'What Agent Mode does' })
    expect(screen.queryByRole('button', { name: /choose tools/i })).not.toBeInTheDocument()
    expect(dialog).toHaveTextContent(/tools are disabled/i)
    expect(dialog).toHaveTextContent(/multiple steps/i)
  })

  it('opens the tools picker from the explanation', () => {
    const listener = vi.fn()
    window.addEventListener(OPEN_SETTINGS_EVENT, listener)
    render(<AgentModeHelpButton />)

    fireEvent.click(screen.getByRole('button', { name: 'What Agent Mode does' }))
    fireEvent.click(screen.getByRole('button', { name: /choose tools/i }))

    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener.mock.calls[0][0].detail).toEqual({ tab: 'tools' })
    window.removeEventListener(OPEN_SETTINGS_EVENT, listener)
  })
})

describe('AgentModeMenuHint', () => {
  it('gives the compact menu a one-line summary', () => {
    render(<AgentModeMenuHint />)
    expect(screen.getByText(/multi-step tool use/i)).toBeInTheDocument()
    expect(screen.getByText(/normal chat when no tools/i)).toBeInTheDocument()
  })
})

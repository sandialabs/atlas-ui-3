/**
 * Conversation setup cluster: security level first, an explicit three-state
 * save choice, and visible lock guidance. These are UI contract tests for the
 * information-architecture fix; enforcement stays server-side.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react'
import ConversationSetupCluster, {
  SaveChatControl,
  SecurityLevelControl,
  SetupHelp,
} from '../components/ConversationSetupControls'

const LEVELS = [
  { name: 'Public', description: '', aliases: [], allowed_with: ['Public'] },
  { name: 'Internal', description: '', aliases: [], allowed_with: ['Internal', 'Public'] },
]

const unlocked = { locked: false, level: null, placeholder: null, guidance: null }
const lockedInternal = {
  locked: true,
  level: 'Internal',
  placeholder: 'Internal',
  guidance: 'Start a new chat to change security level.',
}

const renderSecurity = (over = {}) => render(
  <SecurityLevelControl
    complianceOptions={LEVELS}
    complianceRequired={false}
    value="Internal"
    onChange={vi.fn()}
    lock={unlocked}
    {...over}
  />
)

afterEach(cleanup)

describe('security level control', () => {
  it('has an explicit visible label and exposes the selected value', () => {
    renderSecurity()
    expect(screen.getByText('Security level')).toBeTruthy()
    const select = screen.getByLabelText('Security level')
    expect(select.value).toBe('Internal')
    expect(select.tagName).toBe('SELECT')
  })

  it('shows a lock icon, inline status, and guidance when locked', () => {
    const { container } = renderSecurity({ lock: lockedInternal, value: 'Public' })
    const select = screen.getByLabelText('Security level')

    expect(select.disabled).toBe(true)
    expect(select.value).toBe('Internal')
    expect(container.querySelector('svg.lucide-lock')).toBeTruthy()
    expect(screen.getByText('Locked for this chat')).toBeTruthy()
    expect(screen.getByText(/Start a new chat to change security level/)).toBeTruthy()
    // Not hover-only: the guidance is the select's accessible description.
    const guidanceId = select.getAttribute('aria-describedby')
    expect(guidanceId).toBeTruthy()
    expect(document.getElementById(guidanceId).textContent)
      .toMatch(/Start a new chat to change security level/)
  })

  it('stays editable before the conversation starts', () => {
    const onChange = vi.fn()
    renderSecurity({ onChange })
    fireEvent.change(screen.getByLabelText('Security level'), { target: { value: 'Public' } })
    expect(onChange).toHaveBeenCalledWith('Public')
  })
})

describe('save-this-chat control', () => {
  it('opens a three-choice menu instead of cycling on click', () => {
    const onChange = vi.fn()
    render(<SaveChatControl saveMode="none" onChange={onChange} />)

    const button = screen.getByRole('button', { name: /Save this chat: Not saved/ })
    fireEvent.click(button)
    expect(onChange).not.toHaveBeenCalled()

    const menu = screen.getByRole('menu', { name: 'Save this chat' })
    const choices = within(menu).getAllByRole('menuitemradio')
    expect(choices.map(choice => choice.textContent)).toEqual([
      expect.stringContaining('Not saved'),
      expect.stringContaining('Saved in this browser'),
      expect.stringContaining('Saved to server'),
    ])
    expect(within(menu).getByText('How is this chat saved?')).toBeTruthy()
    expect(within(menu).getByText(/Conversations already in your history stay visible/)).toBeTruthy()
  })

  it('selects an explicit state and reflects it on the button', () => {
    const onChange = vi.fn()
    const { rerender } = render(<SaveChatControl saveMode="none" onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: /Save this chat: Not saved/ }))
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Saved to server/ }))
    expect(onChange).toHaveBeenCalledWith('server')

    rerender(<SaveChatControl saveMode="server" onChange={onChange} />)
    expect(screen.getByRole('button', { name: /Save this chat: Saved to server/ })).toBeTruthy()
  })

  it('closes the menu when keyboard focus moves on', () => {
    render(<SaveChatControl saveMode="none" onChange={vi.fn()} />)
    const button = screen.getByRole('button', { name: /Save this chat: Not saved/ })
    fireEvent.click(button)
    const menu = screen.getByRole('menu', { name: 'Save this chat' })
    fireEvent.keyDown(menu, { key: 'Tab' })
    expect(screen.queryByRole('menu', { name: 'Save this chat' })).toBeNull()
    expect(document.activeElement).toBe(button)
  })

  it('moves focus with arrow keys and returns it to the button on Escape', () => {
    render(<SaveChatControl saveMode="none" onChange={vi.fn()} />)
    const button = screen.getByRole('button', { name: /Save this chat: Not saved/ })
    fireEvent.click(button)

    const menu = screen.getByRole('menu', { name: 'Save this chat' })
    const items = within(menu).getAllByRole('menuitemradio')
    expect(document.activeElement).toBe(items[0])

    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(items[1])
    fireEvent.keyDown(menu, { key: 'ArrowUp' })
    expect(document.activeElement).toBe(items[0])

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu', { name: 'Save this chat' })).toBeNull()
    expect(document.activeElement).toBe(button)
  })

  it('warns that switching to server mid-chat does not upload earlier turns', () => {
    const { rerender } = render(
      <SaveChatControl saveMode="none" onChange={vi.fn()} hasMessages />
    )
    fireEvent.click(screen.getByRole('button', { name: /Save this chat: Not saved/ }))
    expect(screen.getByText('Earlier turns in this chat are not uploaded.')).toBeTruthy()

    rerender(<SaveChatControl saveMode="none" onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Save this chat: Not saved/ }))
    expect(screen.queryByText('Earlier turns in this chat are not uploaded.')).toBeNull()
  })

  it('uses a native radio group in the compact menu layout', () => {
    const onChange = vi.fn()
    render(<SaveChatControl saveMode="local" onChange={onChange} variant="menu" />)

    const group = screen.getByRole('radiogroup', { name: 'Save this chat' })
    const radios = within(group).getAllByRole('radio')
    expect(radios).toHaveLength(3)
    expect(radios[1].checked).toBe(true)
    fireEvent.click(radios[2])
    expect(onChange).toHaveBeenCalledWith('server')
  })

  it('hides optional explanations in quiet mode but keeps the three labels', () => {
    render(<SaveChatControl saveMode="none" onChange={vi.fn()} quiet />)
    fireEvent.click(screen.getByRole('button', { name: /Save this chat: Not saved/ }))

    const menu = screen.getByRole('menu', { name: 'Save this chat' })
    expect(within(menu).queryByText('How is this chat saved?')).toBeNull()
    expect(within(menu).queryByText(/Conversations already in your history/)).toBeNull()
    expect(within(menu).getByText('Saved in this browser')).toBeTruthy()
    expect(within(menu).getByText('Saved to server')).toBeTruthy()
  })
})

describe('conversation setup cluster', () => {
  it('orders security level, save mode, then New Chat', () => {
    const { getByTestId } = render(
      <ConversationSetupCluster
        complianceOptions={LEVELS}
        complianceRequired={false}
        securityValue="Internal"
        onSecurityChange={vi.fn()}
        lock={unlocked}
        saveMode="none"
        onSaveModeChange={vi.fn()}
        onNewChat={vi.fn()}
      />
    )
    const row = getByTestId('conversation-setup-row')
    const controls = [...row.children].map(child => child.dataset.testid || child.getAttribute('aria-label') || child.textContent)
    expect(controls[0]).toBe('security-level-control')
    expect(controls[1]).toBe('save-chat-control')
    expect(controls[2]).toBe('New Chat')
  })

  it('shows locked guidance on one line below the bar controls', () => {
    render(
      <ConversationSetupCluster
        complianceOptions={LEVELS}
        complianceRequired={false}
        securityValue="Internal"
        onSecurityChange={vi.fn()}
        lock={lockedInternal}
        saveMode="none"
        onSaveModeChange={vi.fn()}
        onNewChat={vi.fn()}
      />
    )

    const guidance = screen.getByText(/Start a new chat to change security level/).closest('p')
    expect(guidance).toBeTruthy()
    expect(guidance.className).toContain('whitespace-nowrap')
    const select = screen.getByLabelText('Security level')
    expect(select.getAttribute('aria-describedby')).toBe(guidance.id)
    expect(screen.getByText('Locked for this chat')).toBeTruthy()
  })

  it('offers an opt-in help panel for first-time users', () => {
    render(<SetupHelp />)
    fireEvent.click(screen.getByRole('button', { name: 'About conversation setup' }))
    expect(screen.getByRole('dialog', { name: 'About conversation setup' })).toBeTruthy()
    expect(screen.getByText(/It locks after the conversation starts/)).toBeTruthy()
    expect(screen.getByText(/does not hide conversations that are already in your history/)).toBeTruthy()
  })

  it('removes the help affordance entirely in quiet mode', () => {
    const { container } = render(<SetupHelp quiet />)
    expect(container).toBeEmptyDOMElement()
  })
})

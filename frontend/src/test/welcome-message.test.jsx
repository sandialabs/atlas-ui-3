/**
 * Welcome screen instructional text override (issue #1072).
 *
 * VITE_WELCOME_MESSAGE replaces the paragraph under the logo at build time;
 * unset or blank keeps the original text, and the value is plain text.
 */
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import WelcomeScreen from '../components/WelcomeScreen'
import { useChat } from '../contexts/ChatContext'
import {
  DEFAULT_WELCOME_MESSAGE,
  resolveWelcomeMessage,
} from '../utils/welcomeMessage'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

describe('resolveWelcomeMessage', () => {
  it('keeps the exact original default text', () => {
    expect(DEFAULT_WELCOME_MESSAGE).toBe(
      'Select a model and start chatting. To explore available tools, click the Tools and Settings button (a gear with a wrench) or to view files, click the Folder icon in the top right.'
    )
  })

  it.each([undefined, null, '', '   ', '\n\t'])(
    'falls back to the default for %j',
    (value) => {
      expect(resolveWelcomeMessage(value)).toBe(DEFAULT_WELCOME_MESSAGE)
    }
  )

  it('returns a configured message, trimmed', () => {
    expect(resolveWelcomeMessage('  Contact your administrator.  ')).toBe(
      'Contact your administrator.'
    )
  })
})

describe('WelcomeScreen message', () => {
  beforeEach(() => {
    useChat.mockReturnValue({ appName: 'ATLAS' })
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('renders the default message when VITE_WELCOME_MESSAGE is unset', () => {
    vi.stubEnv('VITE_WELCOME_MESSAGE', '')
    render(<WelcomeScreen />)
    expect(screen.getByTestId('welcome-message')).toHaveTextContent(
      DEFAULT_WELCOME_MESSAGE
    )
  })

  it('renders a configured VITE_WELCOME_MESSAGE in place of the default', () => {
    vi.stubEnv(
      'VITE_WELCOME_MESSAGE',
      'Choose a model to begin. Contact your administrator for help.'
    )
    render(<WelcomeScreen />)
    const message = screen.getByTestId('welcome-message')
    expect(message).toHaveTextContent(
      'Choose a model to begin. Contact your administrator for help.'
    )
    expect(message).not.toHaveTextContent('Select a model and start chatting')
    expect(message.tagName).toBe('P')
    expect(message).toHaveClass('text-gray-400', 'text-lg')
  })

  it('renders markup in the configured message as literal text', () => {
    const markup = '<img src=x onerror="alert(1)"><b>Hi</b>'
    vi.stubEnv('VITE_WELCOME_MESSAGE', markup)
    render(<WelcomeScreen />)
    const message = screen.getByTestId('welcome-message')
    expect(message).toHaveTextContent(markup)
    expect(message.querySelector('img')).toBeNull()
    expect(message.querySelector('b')).toBeNull()
  })
})

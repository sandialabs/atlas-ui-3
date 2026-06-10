/**
 * Regression test for issue #448 — don't allow sending a message while the
 * WebSocket is disconnected.
 *
 * When disconnected, ChatArea must:
 *   - disable the send button,
 *   - show a "disconnected" banner, and
 *   - refuse to call sendChatMessage (so the UI never hangs on "Thinking...").
 * When connected again, sending works as normal.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/WSContext')

describe('ChatArea - blocks sending while WebSocket is disconnected (#448)', () => {
  const sendChatMessage = vi.fn()

  const defaultChatContext = {
    messages: [],
    isWelcomeVisible: false,
    isThinking: false,
    sendChatMessage,
    currentModel: 'gpt-4',
    tools: [],
    prompts: [],
    selectedTools: new Set(),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    setToolChoiceRequired: vi.fn(),
    sessionFiles: { files: [], total_files: 0, categories: {} },
    agentModeEnabled: false,
    agentPendingQuestion: null,
    setAgentPendingQuestion: vi.fn(),
    stopAgent: vi.fn(),
    answerAgentQuestion: vi.fn(),
    followUpSuggestions: [],
    setFollowUpSuggestions: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useChat.mockReturnValue(defaultChatContext)
  })

  const renderChat = () =>
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

  const typeMessage = (text) => {
    const textarea = screen.getByPlaceholderText(/Type a message/i)
    fireEvent.change(textarea, { target: { value: text } })
    return textarea
  }

  it('shows a disconnected banner and disables send when not connected', () => {
    useWS.mockReturnValue({ isConnected: false, connectionStatus: 'Disconnected', sendMessage: vi.fn() })
    const { container } = renderChat()

    expect(screen.getByTestId('ws-disconnected-banner')).toBeInTheDocument()

    typeMessage('hello')
    // The submit (send) button must be disabled while disconnected, even with text.
    const sendButton = container.querySelector('button[type="submit"]')
    expect(sendButton).toBeTruthy()
    expect(sendButton).toBeDisabled()
  })

  it('does not call sendChatMessage when Enter is pressed while disconnected', () => {
    useWS.mockReturnValue({ isConnected: false, connectionStatus: 'Disconnected', sendMessage: vi.fn() })
    renderChat()

    const textarea = typeMessage('hello while offline')
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    expect(sendChatMessage).not.toHaveBeenCalled()
  })

  it('hides the banner and allows sending when connected', async () => {
    sendChatMessage.mockReturnValue(true)
    useWS.mockReturnValue({ isConnected: true, connectionStatus: 'Connected', sendMessage: vi.fn() })
    renderChat()

    expect(screen.queryByTestId('ws-disconnected-banner')).not.toBeInTheDocument()

    const textarea = typeMessage('hello online')
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    // handleSubmit awaits @file reference processing, so the send is async.
    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledTimes(1))
    expect(sendChatMessage).toHaveBeenCalledWith('hello online', expect.any(Object))
  })
})

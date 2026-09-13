/**
 * The composer's send/stop controls follow the mode the current turn
 * actually entered (#849 review).
 *
 * Agent mode now defaults to on, so a browser with no tools selected sends
 * turns that run as plain chats. The stop/send choice must therefore key on
 * `isAgentRunning` (set when the server acknowledges the loop via
 * `agent_start`), not on the persisted `agentModeEnabled` toggle: gating on
 * the toggle left Send enabled during a plain turn's generation, and a
 * second submission could start another untracked task against the same
 * session.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: () => ({ isComplianceAccessible: () => true }),
  useOptionalMarketplace: () => null,
}))
vi.mock('../contexts/WSContext')

describe('composer stop/send controls follow the entered turn mode (issue #849 review)', () => {
  const sendChatMessage = vi.fn()
  const stopStreaming = vi.fn()
  const stopAgent = vi.fn()

  const baseContext = {
    messages: [],
    isWelcomeVisible: false,
    isSynthesizing: false,
    isStreaming: false,
    sendChatMessage,
    stopStreaming,
    stopAgent,
    currentModel: 'gpt-4',
    tools: [],
    prompts: [],
    selectedTools: new Set(),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    sessionFiles: { files: [], total_files: 0, categories: {} },
    agentModeAvailable: true,
    agentPendingQuestion: null,
    setAgentPendingQuestion: vi.fn(),
    answerAgentQuestion: vi.fn(),
    followUpSuggestions: [],
    setFollowUpSuggestions: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useWS.mockReturnValue({ isConnected: true, connectionStatus: 'Connected', sendMessage: vi.fn() })
  })

  const renderChat = (overrides = {}) => {
    useChat.mockReturnValue({ ...baseContext, ...overrides })
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )
  }

  it('offers Stop-streaming (not Send) while a plain turn generates, even with agent mode toggled on', () => {
    // Default-on agent toggle, no tools selected: the turn ran as a plain
    // chat, so isAgentRunning is false despite agentModeEnabled being true.
    renderChat({ isThinking: true, isAgentRunning: false, agentModeEnabled: true })

    expect(screen.getByTitle('Stop streaming')).toBeInTheDocument()
    expect(screen.queryByTitle('Send message')).not.toBeInTheDocument()
    // Nothing to steer on a plain turn: no agent stop either.
    expect(screen.queryByTitle('Stop agent')).not.toBeInTheDocument()
  })

  it('keeps Send up while an agent turn runs (steering path) with the agent Stop available', () => {
    renderChat({ isThinking: false, isAgentRunning: true, agentModeEnabled: true })

    expect(screen.getByTitle('Stop agent')).toBeInTheDocument()
    const submit = document.querySelector('button[type="submit"]')
    expect(submit).not.toBeNull()
  })

  it('returns to Send once the plain turn finishes', () => {
    renderChat({ isThinking: false, isAgentRunning: false, agentModeEnabled: true })

    expect(screen.queryByTitle('Stop streaming')).not.toBeInTheDocument()
    expect(document.querySelector('button[type="submit"]')).not.toBeNull()
  })

  it('still blocks a second submission while a plain turn is generating', () => {
    renderChat({ isThinking: true, isAgentRunning: false, agentModeEnabled: true })

    // Enter in the composer must not send a second turn while one runs.
    const textarea = screen.getByPlaceholderText(/Type a message/i)
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })
    expect(sendChatMessage).not.toHaveBeenCalled()
  })
})

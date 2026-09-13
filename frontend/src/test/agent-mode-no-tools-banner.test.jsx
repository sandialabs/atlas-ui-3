/**
 * Agent mode with no tools selected: warn, don't block (#921 follow-up).
 *
 * The composer shows a small persistent warning while agent mode is on and no
 * tools are ticked, but sending is still allowed -- the backend downgrades
 * the turn to a normal chat and says so in the transcript.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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

describe('ChatArea - agent mode warning with no tools selected', () => {
  const sendChatMessage = vi.fn()

  const chatContext = (overrides = {}) => ({
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
    sessionFiles: { files: [], total_files: 0, categories: {} },
    agentModeAvailable: true,
    agentModeEnabled: true,
    agentPendingQuestion: null,
    setAgentPendingQuestion: vi.fn(),
    stopAgent: vi.fn(),
    answerAgentQuestion: vi.fn(),
    followUpSuggestions: [],
    setFollowUpSuggestions: vi.fn(),
    ...overrides,
  })

  beforeEach(() => {
    vi.clearAllMocks()
    useWS.mockReturnValue({ isConnected: true, connectionStatus: 'Connected', sendMessage: vi.fn() })
  })

  const renderChat = () =>
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

  it('shows the small warning when agent mode is on with no tools', () => {
    useChat.mockReturnValue(chatContext())
    renderChat()

    const banner = screen.getByTestId('agent-mode-no-tools-banner')
    expect(banner).toBeInTheDocument()
    expect(banner).toHaveTextContent(/agent mode is on/i)
    expect(banner).toHaveTextContent(/normal chat/i)
  })

  it('stays quiet once a tool is selected', () => {
    useChat.mockReturnValue(chatContext({ selectedTools: new Set(['srv_tool']) }))
    renderChat()

    expect(screen.queryByTestId('agent-mode-no-tools-banner')).not.toBeInTheDocument()
  })

  it('stays quiet when agent mode is off', () => {
    useChat.mockReturnValue(chatContext({ agentModeEnabled: false }))
    renderChat()

    expect(screen.queryByTestId('agent-mode-no-tools-banner')).not.toBeInTheDocument()
  })

  it('stays quiet when agent mode is enabled but not available', () => {
    // The enabled flag is persisted and can outlive a config where the
    // feature is off; the warning must not show for a feature the user
    // cannot actually use.
    useChat.mockReturnValue(chatContext({ agentModeAvailable: false }))
    renderChat()

    expect(screen.queryByTestId('agent-mode-no-tools-banner')).not.toBeInTheDocument()
  })
})
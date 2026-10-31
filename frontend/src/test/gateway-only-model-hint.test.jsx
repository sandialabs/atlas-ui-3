/**
 * Regression test for issue #1134 — on a gateway-only deployment there is no
 * static model to default to, so before the user picks a team model the
 * composer cannot send. Instead of a silently dead Send button, ChatArea
 * says what to do; a mixed or already-selected state stays quiet.
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

describe('ChatArea - gateway-only deployments explain the empty model selection (#1134)', () => {
  const gatewayOnlyContext = {
    messages: [],
    isWelcomeVisible: false,
    isThinking: false,
    sendChatMessage: vi.fn(),
    currentModel: '',
    models: [],
    llmGateways: [{ name: 'enterprise' }],
    tools: [],
    prompts: [],
    selectedTools: new Set(),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
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
    useWS.mockReturnValue({ isConnected: true, connectionStatus: 'Connected', sendMessage: vi.fn() })
  })

  const renderChat = () =>
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

  it('shows the choose-a-team-model hint and disables send before a pick', () => {
    useChat.mockReturnValue(gatewayOnlyContext)
    const { container } = renderChat()

    expect(screen.getByTestId('choose-team-model-hint')).toBeInTheDocument()
    const sendButton = container.querySelector('button[type="submit"]')
    expect(sendButton).toBeTruthy()
    expect(sendButton).toBeDisabled()
  })

  it('stays quiet once a team model is selected', () => {
    useChat.mockReturnValue({
      ...gatewayOnlyContext,
      currentModel: 'enterprise::team-a::gpt-x',
      models: [{ name: 'enterprise::team-a::gpt-x' }],
    })
    renderChat()

    expect(screen.queryByTestId('choose-team-model-hint')).toBeNull()
  })

  it('stays quiet in a mixed deployment without a selection', () => {
    useChat.mockReturnValue({
      ...gatewayOnlyContext,
      models: [{ name: 'gpt-4o' }],
    })
    const { container } = renderChat()

    expect(screen.queryByTestId('choose-team-model-hint')).toBeNull()
    expect(container.querySelector('button[type="submit"]')).toBeTruthy()
  })
})

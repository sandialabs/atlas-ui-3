/**
 * Persistent agent-run status near the composer.
 *
 * A run must stay visible after the user scrolls away from the transcript's
 * newest row, and Stop has to be reachable from the same place. The footer is
 * always on screen, so the status lives there and reads the run state the
 * composer already uses -- no second source of truth.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import AgentRunStatus from '../components/AgentRunStatus'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: () => ({ isComplianceAccessible: () => true }),
  useOptionalMarketplace: () => null,
}))
vi.mock('../contexts/WSContext')

describe('AgentRunStatus', () => {
  it('stays hidden when no run is active', () => {
    render(<AgentRunStatus isAgentRunning={false} currentAgentStep={2} onStop={vi.fn()} />)
    expect(screen.queryByTestId('agent-run-status')).not.toBeInTheDocument()
  })

  it('shows a high-visibility running state with a Stop action', () => {
    const onStop = vi.fn()
    render(<AgentRunStatus isAgentRunning currentAgentStep={0} onStop={onStop} />)

    const status = screen.getByTestId('agent-run-status')
    expect(status).toHaveTextContent('Agent is running')
    expect(status).toHaveTextContent(/using tools as needed/i)
    expect(status).toHaveAttribute('role', 'status')

    fireEvent.click(screen.getByRole('button', { name: /stop/i }))
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it('names the current step once the loop reports one', () => {
    render(<AgentRunStatus isAgentRunning currentAgentStep={3} onStop={vi.fn()} />)
    expect(screen.getByTestId('agent-run-status')).toHaveTextContent(/step 3/i)
  })

  it('explains why an agent is paused for an answer', () => {
    render(
      <AgentRunStatus isAgentRunning currentAgentStep={0} onStop={vi.fn()} waitingForAnswer />
    )
    const status = screen.getByTestId('agent-run-status')
    expect(status).toHaveTextContent(/waiting for your answer/i)
    expect(status).toHaveTextContent(/answer below/i)
  })
})

describe('ChatArea - agent run status is pinned to the composer', () => {
  const chatContext = (overrides = {}) => ({
    messages: [],
    isWelcomeVisible: false,
    isThinking: false,
    sendChatMessage: vi.fn(),
    currentModel: 'gpt-4',
    models: [{ name: 'gpt-4', display_name: 'GPT-4', supports_tools: true }],
    tools: [],
    prompts: [],
    selectedTools: new Set(['srv_tool']),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    sessionFiles: { files: [], total_files: 0, categories: {} },
    agentModeAvailable: true,
    agentModeEnabled: true,
    setAgentModeEnabled: vi.fn(),
    isAgentRunning: false,
    currentAgentStep: 0,
    agentPendingQuestion: null,
    setAgentPendingQuestion: vi.fn(),
    stopAgent: vi.fn(),
    answerAgentQuestion: vi.fn(),
    followUpSuggestions: [],
    setFollowUpSuggestions: vi.fn(),
    settings: {},
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

  it('renders the running status inside the always-visible composer footer', () => {
    useChat.mockReturnValue(chatContext({ isAgentRunning: true, currentAgentStep: 2 }))
    renderChat()

    const status = screen.getByTestId('agent-run-status')
    const footer = screen.getByTestId('composer-footer')
    expect(footer).toContainElement(status)
    expect(status).toHaveTextContent(/agent is running/i)
    expect(status).toHaveTextContent(/step 2/i)
  })

  it('stops the run from the footer status using the shared handler', () => {
    const stopAgent = vi.fn()
    useChat.mockReturnValue(chatContext({ isAgentRunning: true, stopAgent }))
    renderChat()

    const status = screen.getByTestId('agent-run-status')
    fireEvent.click(within(status).getByRole('button', { name: /stop/i }))
    expect(stopAgent).toHaveBeenCalledTimes(1)
  })

  it('does not render a run status when nothing is running', () => {
    useChat.mockReturnValue(chatContext())
    renderChat()
    expect(screen.queryByTestId('agent-run-status')).not.toBeInTheDocument()
  })
})

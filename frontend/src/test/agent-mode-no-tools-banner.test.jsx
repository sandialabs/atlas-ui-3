/**
 * Agent mode with no tools selected: a strong, plain-language explanation.
 *
 * The composer still allows the send (the backend downgrades the turn to a
 * normal chat and says so in the transcript), but the callout now explains
 * what Agent Mode would do, states the tool-capable model requirement, and
 * offers a direct route to the tools picker.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { OPEN_SETTINGS_EVENT } from '../utils/settingsPanelEvents'

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
    models: [{ name: 'gpt-4', display_name: 'GPT-4', supports_tools: true }],
    tools: [],
    prompts: [],
    selectedTools: new Set(),
    selectedPrompts: new Set(),
    selectedDataSources: new Set(),
    ragEnabled: false,
    ragSources: [],
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    sessionFiles: { files: [], total_files: 0, categories: {} },
    agentModeAvailable: true,
    agentModeEnabled: true,
    setAgentModeEnabled: vi.fn(),
    agentPendingQuestion: null,
    setAgentPendingQuestion: vi.fn(),
    stopAgent: vi.fn(),
    answerAgentQuestion: vi.fn(),
    followUpSuggestions: [],
    setFollowUpSuggestions: vi.fn(),
    settings: {},
    features: { tools: true },
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

  it('announces only the title, not the whole callout and its buttons', () => {
    useChat.mockReturnValue(chatContext())
    renderChat()

    const banner = screen.getByTestId('agent-mode-no-tools-banner')
    expect(banner).not.toHaveAttribute('role')
    const live = within(banner).getByRole('status')
    expect(live).toHaveTextContent('Agent Mode is on, but no tools are selected')
    expect(live).not.toContainElement(screen.getByRole('button', { name: /choose tools/i }))
  })

  it('explains multi-step tool use in plain language', () => {
    useChat.mockReturnValue(chatContext())
    renderChat()

    const banner = screen.getByTestId('agent-mode-no-tools-banner')
    expect(banner).toHaveTextContent(/take multiple steps/i)
    expect(banner).toHaveTextContent(/calls the tools you select/i)
    expect(banner).toHaveTextContent(/inspects the results/i)
    expect(banner).toHaveTextContent(/continues until the task is done/i)
  })

  it('states that a tool-capable model is required', () => {
    useChat.mockReturnValue(chatContext())
    renderChat()

    const banner = screen.getByTestId('agent-mode-no-tools-banner')
    expect(banner).toHaveTextContent(/tool-capable model/i)
    expect(banner).toHaveTextContent(/GPT-4 supports tools/i)
  })

  it('calls out a current model that cannot call tools', () => {
    useChat.mockReturnValue(chatContext({
      models: [{ name: 'gpt-4', display_name: 'GPT-4', supports_tools: false }],
    }))
    renderChat()

    const banner = screen.getByTestId('agent-mode-no-tools-banner')
    expect(banner).toHaveTextContent(/does not support tools/i)
    expect(banner).toHaveTextContent(/switch to a tool-capable model/i)
  })

  it('does not claim the model lacks tools while the model list is loading', () => {
    useChat.mockReturnValue(chatContext({ models: [] }))
    renderChat()

    const banner = screen.getByTestId('agent-mode-no-tools-banner')
    expect(banner).toHaveTextContent(/tool-capable model/i)
    expect(banner).not.toHaveTextContent(/does not support tools/i)
    expect(banner).not.toHaveTextContent(/supports tools/i)
  })

  it('does not claim a stale stored model lacks tools', () => {
    useChat.mockReturnValue(chatContext({ currentModel: 'removed-model' }))
    renderChat()

    const banner = screen.getByTestId('agent-mode-no-tools-banner')
    expect(banner).not.toHaveTextContent(/does not support tools/i)
  })

  it('offers a direct action that opens the tools picker', () => {
    useChat.mockReturnValue(chatContext())
    const listener = vi.fn()
    window.addEventListener(OPEN_SETTINGS_EVENT, listener)
    renderChat()

    fireEvent.click(screen.getByRole('button', { name: /choose tools/i }))

    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener.mock.calls[0][0].detail).toEqual({ tab: 'tools' })
    window.removeEventListener(OPEN_SETTINGS_EVENT, listener)
  })

  it('offers a quick way to turn agent mode back off', () => {
    const setAgentModeEnabled = vi.fn()
    useChat.mockReturnValue(chatContext({ setAgentModeEnabled }))
    renderChat()

    fireEvent.click(screen.getByRole('button', { name: /turn off agent mode/i }))

    expect(setAgentModeEnabled).toHaveBeenCalledWith(false)
  })

  it('keeps the state, model requirement, and action in quiet mode', () => {
    useChat.mockReturnValue(chatContext({ settings: { reduceHelpText: true } }))
    renderChat()

    const banner = screen.getByTestId('agent-mode-no-tools-banner')
    expect(banner).toHaveTextContent(/agent mode is on/i)
    expect(banner).toHaveTextContent(/select at least one tool/i)
    expect(banner).toHaveTextContent(/tool-capable model/i)
    expect(screen.getByRole('button', { name: /choose tools/i })).toBeInTheDocument()
    // The long onboarding paragraph is the optional part.
    expect(banner).not.toHaveTextContent(/inspects the results/i)
  })

  it('keeps the callout scrollable on short viewports so the composer stays reachable', () => {
    useChat.mockReturnValue(chatContext())
    renderChat()

    const detail = screen.getByTestId('agent-mode-no-tools-detail')
    expect(detail).toHaveClass('max-h-24')
    expect(detail).toHaveClass('overflow-y-auto')
    expect(detail).toHaveClass('sm:max-h-none')
    // The title and actions sit outside the scrolling region.
    expect(screen.getByTestId('agent-mode-no-tools-banner')).toContainElement(
      screen.getByRole('button', { name: /choose tools/i })
    )
  })

  it('does not offer the tools picker when the deployment disables tools', () => {
    useChat.mockReturnValue(chatContext({ features: { tools: false } }))
    renderChat()

    const banner = screen.getByTestId('agent-mode-no-tools-banner')
    expect(screen.queryByRole('button', { name: /choose tools/i })).not.toBeInTheDocument()
    expect(banner).toHaveTextContent(/agent mode is on, but tools are disabled/i)
    expect(banner).toHaveTextContent(/messages run as normal chat/i)
    // Nothing to select or switch, so no tool or model guidance.
    expect(banner).not.toHaveTextContent(/no tools are selected/i)
    expect(banner).not.toHaveTextContent(/tool-capable model/i)
    expect(screen.getByRole('button', { name: /turn off agent mode/i })).toBeInTheDocument()
  })

  it('stays quiet once a tool is selected', () => {
    useChat.mockReturnValue(chatContext({ selectedTools: new Set(['srv_tool']) }))
    renderChat()

    expect(screen.queryByTestId('agent-mode-no-tools-banner')).not.toBeInTheDocument()
  })

  it('stays quiet when the selected data sources imply the search tool', () => {
    // Issue #1102: selected sources imply the built-in search tool, so the
    // turn does have something for the agent loop to call.
    useChat.mockReturnValue(chatContext({
      tools: [{ server: 'atlas', tools: ['canvas', 'search'], tools_detailed: [] }],
      selectedDataSources: new Set(['srv:doc']),
    }))
    renderChat()

    expect(screen.queryByTestId('agent-mode-no-tools-banner')).not.toBeInTheDocument()
  })

  it('still warns when the selected sources cannot be searched', () => {
    // The deployment does not offer the search tool, so the sources leave the
    // turn with nothing to read them.
    useChat.mockReturnValue(chatContext({
      tools: [{ server: 'atlas', tools: ['canvas'], tools_detailed: [] }],
      selectedDataSources: new Set(['srv:doc']),
    }))
    renderChat()

    expect(screen.getByTestId('agent-mode-no-tools-banner')).toBeInTheDocument()
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

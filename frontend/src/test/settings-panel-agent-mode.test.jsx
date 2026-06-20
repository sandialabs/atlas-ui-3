import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPanel from '../components/SettingsPanel'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn()
}))

vi.mock('../hooks/useGlobusAuth', () => ({
  useGlobusAuth: () => ({
    authStatus: null,
    loading: false,
    error: null,
    fetchAuthStatus: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    isAuthenticated: false,
  })
}))

vi.mock('../components/PromptManager', () => ({
  default: () => <div>Prompt manager</div>
}))

const baseChatContext = {
  settings: {
    autoApproveTools: false,
  },
  updateSettings: vi.fn(),
  features: {
    custom_prompts: false,
    globus_auth: false,
  },
  agentModeAvailable: true,
}

describe('SettingsPanel agent mode settings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  const renderSettingsPanel = (overrides = {}) => {
    useChat.mockReturnValue({
      ...baseChatContext,
      ...overrides,
      features: {
        ...baseChatContext.features,
        ...overrides.features,
      },
    })

    return render(<SettingsPanel isOpen={true} onClose={vi.fn()} />)
  }

  it('shows agent-specific settings when agent mode is available', async () => {
    renderSettingsPanel({ agentModeAvailable: true })

    expect(await screen.findByText('LLM Temperature')).toBeInTheDocument()
    expect(screen.getByText('Max Agent Iterations')).toBeInTheDocument()
  })

  it('hides agent-specific settings when agent mode is unavailable', async () => {
    renderSettingsPanel({ agentModeAvailable: false })

    expect(await screen.findByText('LLM Temperature')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByText('Max Agent Iterations')).not.toBeInTheDocument()
    })
  })

  it('uses the high-contrast approval warning style', async () => {
    renderSettingsPanel({ settings: { autoApproveTools: false } })

    const warning = await screen.findByText(/You will be prompted to approve all tool calls/)
    expect(warning.closest('p')).toHaveClass('approval-warning-text')
    expect(screen.getByText(/Currently:/)).toBeInTheDocument()
  })
})

import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPanel from '../components/SettingsPanel'
import { ThemeProvider } from '../contexts/ThemeContext'
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

    return render(
      <ThemeProvider>
        <SettingsPanel isOpen={true} onClose={vi.fn()} />
      </ThemeProvider>
    )
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

  it('exposes the Compact Tool Messages toggle and updates the setting when clicked', async () => {
    const updateSettings = vi.fn()
    renderSettingsPanel({ settings: { compactMessages: true }, updateSettings })

    const label = await screen.findByText('Compact Tool Messages')
    // The toggle button is the sibling control in the same row as the label.
    const toggle = label.parentElement.querySelector('button')
    toggle.click()

    expect(updateSettings).toHaveBeenCalledWith({ compactMessages: false })
  })

  it('treats a missing compactMessages setting as enabled (default on)', async () => {
    const updateSettings = vi.fn()
    renderSettingsPanel({ settings: {}, updateSettings })

    const label = await screen.findByText('Compact Tool Messages')
    const toggle = label.parentElement.querySelector('button')
    toggle.click()

    // Undefined is treated as "on", so the first click turns it off.
    expect(updateSettings).toHaveBeenCalledWith({ compactMessages: false })
  })

  it('bounds the Max Agent Iterations slider by the admin-configured ceiling (issue #849)', async () => {
    renderSettingsPanel({ agentMaxStepsLimit: 30 })

    expect(await screen.findByText('Max Agent Iterations')).toBeInTheDocument()
    const slider = [...document.querySelectorAll('input[type=range]')].find(s => s.max !== '1')
    expect(slider.max).toBe('30')
    // Scale labels track the bound instead of the old hardcoded 1/25/50.
    expect(screen.getByText('15')).toBeInTheDocument()
    expect(screen.getByText('30')).toBeInTheDocument()
  })

  it('clamps a stored max-iterations value above the ceiling and persists the fix (issue #849)', async () => {
    localStorage.setItem('chatui-settings', JSON.stringify({ maxIterations: 50 }))
    renderSettingsPanel({ agentMaxStepsLimit: 30 })

    expect(await screen.findByText('Max Agent Iterations')).toBeInTheDocument()
    const slider = [...document.querySelectorAll('input[type=range]')].find(s => s.max !== '1')
    expect(slider.value).toBe('30')
    expect(screen.getByText('30 / 30')).toBeInTheDocument()
    expect(screen.queryByText('50 / 30')).not.toBeInTheDocument()
    // The stored preference itself is corrected, so direct consumers of
    // chatui-settings never see an unreachable value.
    expect(JSON.parse(localStorage.getItem('chatui-settings')).maxIterations).toBe(30)
  })

  it('falls back to the server default ceiling when none is configured yet', async () => {
    renderSettingsPanel({})

    expect(await screen.findByText('Max Agent Iterations')).toBeInTheDocument()
    const slider = [...document.querySelectorAll('input[type=range]')].find(s => s.max !== '1')
    expect(slider.max).toBe('10')
  })
})

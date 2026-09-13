import { render, screen, waitFor, fireEvent } from '@testing-library/react'
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
    const updateSettings = vi.fn()
    renderSettingsPanel({
      // The owner's in-memory copy mirrors the stored value (useSettings
      // loads chatui-settings on mount).
      settings: { maxIterations: 50 },
      agentMaxStepsLimit: 30,
      agentCeilingConfirmed: true,
      updateSettings,
    })

    expect(await screen.findByText('Max Agent Iterations')).toBeInTheDocument()
    const slider = [...document.querySelectorAll('input[type=range]')].find(s => s.max !== '1')
    expect(slider.value).toBe('30')
    expect(screen.getByText('30 / 30')).toBeInTheDocument()
    expect(screen.queryByText('50 / 30')).not.toBeInTheDocument()
    // The persisted fix routes through the settings owner, so the context
    // copy and localStorage stay in sync (#849 review).
    await waitFor(() => {
      expect(updateSettings).toHaveBeenCalledWith({ maxIterations: 30 })
    })
  })

  it('does not persist the pre-config fallback ceiling into stored settings (issue #849 review)', async () => {
    // Before a live config response confirms the ceiling, a saved 30 must
    // survive untouched: the fallback of 10 (or a stale cache) is not the
    // deployment's real ceiling, and the server clamps early requests itself.
    localStorage.setItem('chatui-settings', JSON.stringify({ maxIterations: 30 }))
    const updateSettings = vi.fn()
    renderSettingsPanel({ agentMaxStepsLimit: 10, agentCeilingConfirmed: false, updateSettings })

    expect(await screen.findByText('Max Agent Iterations')).toBeInTheDocument()
    expect(updateSettings).not.toHaveBeenCalled()
    expect(JSON.parse(localStorage.getItem('chatui-settings')).maxIterations).toBe(30)
  })

  it('writes the plain default on Reset until the ceiling is confirmed (issue #849 review)', async () => {
    // A stale cache advertises a ceiling of 5 that the deployment does not
    // actually have; the same click a moment later (ceiling 10+) would write
    // 10, so the pre-config reset must not write 5.
    localStorage.setItem('chatui-settings', JSON.stringify({ maxIterations: 30 }))
    renderSettingsPanel({ agentMaxStepsLimit: 5, agentCeilingConfirmed: false })

    await screen.findByText('Max Agent Iterations')
    fireEvent.click(screen.getByRole('button', { name: /Reset to Defaults/i }))

    expect(JSON.parse(localStorage.getItem('chatui-settings')).maxIterations).toBe(10)
  })

  it('clamps the Reset default once the ceiling is confirmed (issue #849 review)', async () => {
    localStorage.setItem('chatui-settings', JSON.stringify({ maxIterations: 30 }))
    renderSettingsPanel({ agentMaxStepsLimit: 5, agentCeilingConfirmed: true })

    await screen.findByText('Max Agent Iterations')
    fireEvent.click(screen.getByRole('button', { name: /Reset to Defaults/i }))

    expect(JSON.parse(localStorage.getItem('chatui-settings')).maxIterations).toBe(5)
  })

  it('renders static copy instead of an inert slider when the ceiling is 1 (issue #849 review)', async () => {
    renderSettingsPanel({ agentMaxStepsLimit: 1, agentCeilingConfirmed: true })

    expect(await screen.findByText('Max Agent Iterations')).toBeInTheDocument()
    expect(screen.getByText(/caps agent runs at a single iteration/i)).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    // No range input with min=max=1 (the temperature slider is min 0), and no
    // colliding "1 1 1" scale row.
    const agentSlider = [...document.querySelectorAll('input[type=range]')].find(s => s.min === '1' && s.max === '1')
    expect(agentSlider).toBeUndefined()
    expect(screen.queryByText(/1\s+1\s+1/)).not.toBeInTheDocument()
  })

  it('drops the midpoint scale label when it would collide with an endpoint (issue #849 review)', async () => {
    renderSettingsPanel({ agentMaxStepsLimit: 2, agentCeilingConfirmed: true })

    await screen.findByText('Max Agent Iterations')
    const section = screen.getByText('Max Agent Iterations').closest('.space-y-3')
    const scaleLabels = [...section.querySelectorAll('div.justify-between.text-xs span')].map(s => s.textContent)
    expect(scaleLabels).toEqual(['1', '2'])
  })

  it('falls back to the server default ceiling when none is configured yet', async () => {
    renderSettingsPanel({})

    expect(await screen.findByText('Max Agent Iterations')).toBeInTheDocument()
    const slider = [...document.querySelectorAll('input[type=range]')].find(s => s.max !== '1')
    expect(slider.max).toBe('10')
  })
})

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

// Default context writer mirrors useSettings' persistence so Reset/Save tests
// can inspect localStorage without reaching into the real hook.
const persistSettings = (next) => {
  const current = JSON.parse(localStorage.getItem('chatui-settings') || '{}')
  const updated = { ...current, ...next }
  localStorage.setItem('chatui-settings', JSON.stringify(updated))
  return updated
}

const baseChatContext = {
  settings: {
    autoApproveTools: false,
  },
  updateSettings: vi.fn(persistSettings),
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

  it('explains that off (the default) asks before every tool call', async () => {
    renderSettingsPanel({ settings: { autoApproveTools: false } })

    const toggle = await screen.findByRole('switch', { name: 'Auto-Approve Tool Calls' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    expect(toggle).toHaveAccessibleDescription(/wait for your confirmation before they run/i)
    expect(toggle).toHaveAccessibleDescription(/Currently off\. You are asked to approve each tool call before it runs\. This is the default\./)
    expect(toggle).toHaveAccessibleDescription(/administrator requires approval for always ask/i)
    // Off is the safe default, so it is not styled as a warning.
    expect(screen.getByText('Currently off.').closest('p')).not.toHaveClass('approval-warning-text')
  })

  it('uses the high-contrast approval warning style when auto-approve is on', async () => {
    renderSettingsPanel({ settings: { autoApproveTools: true } })

    const status = await screen.findByText('Currently on.')
    expect(status.closest('p')).toHaveClass('approval-warning-text')
    expect(status.closest('p')).toHaveTextContent(/conversations you are not currently viewing/)
  })

  it('shows a plain-language Agent Mode explanation by default', async () => {
    renderSettingsPanel()

    fireEvent.click(await screen.findByRole('tab', { name: /General/ }))
    expect(await screen.findByRole('heading', { name: 'Agent Mode' })).toBeInTheDocument()
    expect(screen.getByText(/take multiple steps/i)).toBeInTheDocument()
    expect(screen.getByText(/behaves like normal chat/i)).toBeInTheDocument()
    expect(screen.getByText(/model that supports tool calling/i)).toBeInTheDocument()
  })

  it('hides the Agent Mode explanation when helper text is reduced', async () => {
    renderSettingsPanel({ settings: { reduceHelpText: true } })

    fireEvent.click(await screen.findByRole('tab', { name: /General/ }))
    expect(await screen.findByText('Max Agent Iterations')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Agent Mode' })).not.toBeInTheDocument()
    expect(screen.queryByText(/take multiple steps/i)).not.toBeInTheDocument()
  })

  it('exposes the Compact Tool Messages toggle and updates the setting when clicked', async () => {
    const updateSettings = vi.fn()
    renderSettingsPanel({ settings: { compactMessages: true }, updateSettings })

    const label = await screen.findByText('Compact Tool Messages')
    // The toggle button is the sibling control in the same row as the label.
    const toggle = label.parentElement.querySelector('button')
    fireEvent.click(toggle)

    expect(updateSettings).toHaveBeenCalledWith({ compactMessages: false })
  })

  it('treats a missing compactMessages setting as enabled (default on)', async () => {
    const updateSettings = vi.fn()
    renderSettingsPanel({ settings: {}, updateSettings })

    const label = await screen.findByText('Compact Tool Messages')
    const toggle = label.parentElement.querySelector('button')
    fireEvent.click(toggle)

    // Undefined is treated as "on", so the first click turns it off.
    expect(updateSettings).toHaveBeenCalledWith({ compactMessages: false })
  })

  it('offers a reduce-helper-text switch that persists through the context', async () => {
    const updateSettings = vi.fn()
    renderSettingsPanel({ settings: {}, updateSettings })

    fireEvent.click(await screen.findByRole('tab', { name: /General/ }))
    const toggle = await screen.findByRole('switch', { name: 'Reduce helper text' })
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    fireEvent.click(toggle)

    expect(updateSettings).toHaveBeenCalledWith({ reduceHelpText: true })
  })

  it('keeps the reduce-helper-text preference in the General draft on save', async () => {
    const updateSettings = vi.fn()
    renderSettingsPanel({ settings: {}, updateSettings })

    fireEvent.click(await screen.findByRole('tab', { name: /General/ }))
    fireEvent.click(await screen.findByRole('switch', { name: 'Reduce helper text' }))

    // Touch another draft-backed setting so Save Changes is enabled, then save.
    const [temperature] = await screen.findAllByRole('slider')
    fireEvent.change(temperature, { target: { value: '0.9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }))

    expect(updateSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ reduceHelpText: true, llmTemperature: 0.9 })
    )
  })

  it('does not write a stale context-owned switch back on Save', async () => {
    localStorage.setItem('chatui-settings', JSON.stringify({
      autoApproveTools: true,
      llmTemperature: 0.7,
      maxIterations: 10,
    }))
    const updateSettings = vi.fn()
    renderSettingsPanel({
      settings: { autoApproveTools: false },
      updateSettings,
    })

    fireEvent.click(await screen.findByRole('tab', { name: /General/ }))
    const [temperature] = await screen.findAllByRole('slider')
    fireEvent.change(temperature, { target: { value: '0.9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }))

    expect(updateSettings).toHaveBeenCalledTimes(1)
    const payload = updateSettings.mock.calls[0][0]
    // Auto-approve is owned by ChatContext and was toggled after this draft
    // loaded; the stale draft value must not come back through Save.
    expect(payload).not.toHaveProperty('autoApproveTools')
    expect(payload).toMatchObject({
      llmTemperature: 0.9,
      maxIterations: 10,
      debugMode: false,
      reduceHelpText: false,
    })
  })

  it('Debug Mode only writes its own key, not unsaved General edits', async () => {
    localStorage.setItem('chatui-settings', JSON.stringify({
      llmTemperature: 0.7,
      maxIterations: 10,
      debugMode: false,
    }))
    const updateSettings = vi.fn()
    renderSettingsPanel({ settings: { debugMode: false }, updateSettings })

    fireEvent.click(await screen.findByRole('tab', { name: /General/ }))
    const [temperature] = await screen.findAllByRole('slider')
    fireEvent.change(temperature, { target: { value: '0.9' } })

    const debugLabel = await screen.findByText('Debug Mode')
    fireEvent.click(debugLabel.parentElement.querySelector('button'))

    expect(updateSettings).toHaveBeenCalledWith({ debugMode: true })
    expect(JSON.parse(localStorage.getItem('chatui-settings')).llmTemperature).toBe(0.7)
  })

  it('hides optional explanatory copy when reduce helper text is on', async () => {
    renderSettingsPanel({ settings: { reduceHelpText: true } })

    fireEvent.click(await screen.findByRole('tab', { name: /General/ }))
    expect(await screen.findByText('LLM Temperature')).toBeInTheDocument()
    expect(screen.queryByText(/Controls randomness in AI responses/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Currently using dark mode/)).not.toBeInTheDocument()
  })

  it('Reset restores the context-owned switches as well as General defaults', async () => {
    localStorage.setItem('chatui-settings', JSON.stringify({
      autoApproveTools: true,
      compactMessages: false,
      llmTemperature: 0.9,
      maxIterations: 20,
    }))
    renderSettingsPanel({
      settings: { autoApproveTools: true, compactMessages: false },
    })

    fireEvent.click(await screen.findByRole('tab', { name: /General/ }))
    fireEvent.click(screen.getByRole('button', { name: /Reset to Defaults/i }))

    const stored = JSON.parse(localStorage.getItem('chatui-settings'))
    expect(stored.autoApproveTools).toBe(false)
    expect(stored.compactMessages).toBe(true)
    expect(stored.llmTemperature).toBe(0.7)
    expect(stored.maxIterations).toBe(10)
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

  it('clamps the chip and slider above the ceiling but never touches the stored preference or draft (issue #849 review)', async () => {
    localStorage.setItem('chatui-settings', JSON.stringify({ maxIterations: 50 }))
    const updateSettings = vi.fn()
    renderSettingsPanel({
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
    // The clamp is applied where the value is consumed (slider, chip, send
    // payload). Neither the stored preference nor the in-memory draft is
    // rewritten, so a Save of unrelated settings cannot persist a
    // config-derived value (#849 review); the server clamps the payload
    // authoritatively.
    await waitFor(() => {
      expect(updateSettings).not.toHaveBeenCalled()
    })
    expect(JSON.parse(localStorage.getItem('chatui-settings')).maxIterations).toBe(50)
  })

  it('leaves stored settings untouched before the ceiling is confirmed (issue #849 review)', async () => {
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

/**
 * The `closeGuardRef` / `requestClose` seam between SettingsPanel and the real
 * ToolsPanel (issue #839 review follow-ups).
 *
 * Unlike combined-tools-settings-panel.test.jsx, this renders the *real*
 * ToolsPanel inside the panel, so the unsaved-selection dialog, the deferred
 * "Full Admin Page" navigation, and Escape deferring to a nested modal are all
 * exercised end to end rather than against a mock.
 */
import { render, screen, fireEvent, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import SettingsPanel from '../components/SettingsPanel'
import { ThemeProvider } from '../contexts/ThemeContext'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))
vi.mock('../contexts/MarketplaceContext', () => ({ useMarketplace: vi.fn() }))
vi.mock('../hooks/useGlobusAuth', () => ({
  useGlobusAuth: () => ({
    authStatus: null, loading: false, error: null,
    fetchAuthStatus: vi.fn(), login: vi.fn(), logout: vi.fn(), isAuthenticated: false,
  })
}))
vi.mock('../hooks/useServerAuthStatus', () => ({
  useServerAuthStatus: () => ({
    authStatus: {}, loading: false, error: null,
    fetchAuthStatus: vi.fn(), uploadToken: vi.fn(), removeToken: vi.fn(),
    getServerAuth: vi.fn(() => null),
  })
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('../components/PromptManager', () => ({
  default: ({ onDirtyChange }) => (
    <div>
      prompt manager
      <button onClick={() => onDirtyChange?.(true)}>start draft</button>
    </div>
  )
}))

const testTools = [{
  server: 'test_server',
  description: 'Test server',
  tools: ['fetch', 'search'],
  tools_detailed: [],
  tool_count: 2,
  prompts: [],
  prompt_count: 0,
}]

const renderPanel = (props = {}, chatOverrides = {}) => {
  useChat.mockReturnValue({
    settings: {}, updateSettings: vi.fn(),
    features: { tools: true, custom_prompts: true },
    agentModeAvailable: false,
    isInAdminGroup: false,
    selectedTools: new Set(), selectedPrompts: new Set(),
    addTools: vi.fn(), addPrompts: vi.fn(),
    removeTools: vi.fn(), removePrompts: vi.fn(),
    clearToolsAndPrompts: vi.fn(),
    complianceLevelFilter: 'all',
    tools: testTools, prompts: [],
    ...chatOverrides,
  })
  useMarketplace.mockReturnValue({
    getComplianceFilteredTools: vi.fn(() => testTools),
    getComplianceFilteredPrompts: vi.fn(() => []),
    getFilteredTools: vi.fn(() => testTools),
    getFilteredPrompts: vi.fn(() => []),
  })
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <SettingsPanel isOpen onClose={vi.fn()} initialTab="tools" {...props} />
      </ThemeProvider>
    </MemoryRouter>
  )
}

describe('combined panel close guard, against the real ToolsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    // The admin tab fetches system status on mount.
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }))
  })

  it('raises the unsaved-changes dialog when closing with staged selections', () => {
    const onClose = vi.fn()
    renderPanel({ onClose })

    fireEvent.click(screen.getByRole('button', { name: 'fetch' }))
    fireEvent.click(screen.getByRole('button', { name: /Close tools and settings/ }))

    expect(screen.getByRole('dialog', { name: /Unsaved Changes/ })).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes once the unsaved-changes dialog is answered', () => {
    const onClose = vi.fn()
    renderPanel({ onClose })

    fireEvent.click(screen.getByRole('button', { name: 'fetch' }))
    fireEvent.click(screen.getByRole('button', { name: /Close tools and settings/ }))
    fireEvent.click(screen.getByRole('button', { name: /Discard Changes/ }))

    expect(onClose).toHaveBeenCalled()
  })

  it('defers to a nested modal for Escape', () => {
    const onClose = vi.fn()
    renderPanel({ onClose })

    fireEvent.click(screen.getByRole('button', { name: 'fetch' }))
    fireEvent.click(screen.getByRole('button', { name: /Close tools and settings/ }))
    expect(screen.getByRole('dialog', { name: /Unsaved Changes/ })).toBeInTheDocument()

    // Escape while the prompt is up must not tear the whole panel down.
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Tools and Settings' }), { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not navigate to the full admin page until the guard is answered', () => {
    const onClose = vi.fn()
    renderPanel({ onClose }, { isInAdminGroup: true })

    fireEvent.click(screen.getByRole('button', { name: 'fetch' }))
    fireEvent.click(screen.getByRole('tab', { name: /Admin/ }))
    fireEvent.click(screen.getByRole('button', { name: /Full Admin Page/ }))

    // The guard is up; navigating now would drop the staged selection.
    expect(mockNavigate).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /Discard Changes/ }))
    expect(mockNavigate).toHaveBeenCalledWith('/admin')
  })

  it('drops the deferred navigation when the user backs out of the guard', () => {
    renderPanel({}, { isInAdminGroup: true })

    fireEvent.click(screen.getByRole('button', { name: 'fetch' }))
    fireEvent.click(screen.getByRole('tab', { name: /Admin/ }))
    fireEvent.click(screen.getByRole('button', { name: /Full Admin Page/ }))
    const guard = screen.getByRole('dialog', { name: /Unsaved Changes/ })
    fireEvent.click(within(guard).getByRole('button', { name: /^Cancel$/ }))

    expect(mockNavigate).not.toHaveBeenCalled()

    // And a later, clean close must not resurrect the navigation.
    fireEvent.click(screen.getByRole('button', { name: /Close tools and settings/ }))
    fireEvent.click(screen.getByRole('button', { name: /Discard Changes/ }))
    expect(mockNavigate).not.toHaveBeenCalled()
  })
})

/**
 * An in-progress prompt draft is unsaved work too, and gets the same guard.
 */
describe('prompt draft close guard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('warns before closing over an unfinished prompt', () => {
    const onClose = vi.fn()
    renderPanel({ onClose, initialTab: 'prompts' })

    fireEvent.click(screen.getByRole('button', { name: 'start draft' }))
    fireEvent.click(screen.getByRole('button', { name: /Close tools and settings/ }))

    expect(screen.getByRole('dialog', { name: /Unsaved Prompt/ })).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes once the draft is discarded', () => {
    const onClose = vi.fn()
    renderPanel({ onClose, initialTab: 'prompts' })

    fireEvent.click(screen.getByRole('button', { name: 'start draft' }))
    fireEvent.click(screen.getByRole('button', { name: /Close tools and settings/ }))
    fireEvent.click(screen.getByRole('button', { name: /Discard Changes/ }))

    expect(onClose).toHaveBeenCalled()
  })

  it('asks again after a discard whose close was then aborted', () => {
    const onClose = vi.fn()
    renderPanel({ onClose, initialTab: 'prompts' })

    // Both a prompt draft and a staged tool selection are outstanding.
    fireEvent.click(screen.getByRole('button', { name: 'start draft' }))
    fireEvent.click(screen.getByRole('tab', { name: /Tools & Integrations/ }))
    fireEvent.click(screen.getByRole('button', { name: 'fetch' }))

    fireEvent.click(screen.getByRole('button', { name: /Close tools and settings/ }))
    // Discard the draft, then back out of the tools guard: the close is aborted.
    fireEvent.click(within(screen.getByRole('dialog', { name: /Unsaved Prompt/ }))
      .getByRole('button', { name: /Discard Changes/ }))
    fireEvent.click(within(screen.getByRole('dialog', { name: /Unsaved Changes/ }))
      .getByRole('button', { name: /^Cancel$/ }))
    expect(onClose).not.toHaveBeenCalled()

    // The draft is still there, so the next close must ask about it again.
    fireEvent.click(screen.getByRole('button', { name: /Close tools and settings/ }))
    expect(screen.getByRole('dialog', { name: /Unsaved Prompt/ })).toBeInTheDocument()
  })

  it('keeps the panel open when the user chooses to keep editing', () => {
    const onClose = vi.fn()
    renderPanel({ onClose, initialTab: 'prompts' })

    fireEvent.click(screen.getByRole('button', { name: 'start draft' }))
    fireEvent.click(screen.getByRole('button', { name: /Close tools and settings/ }))
    const guard = screen.getByRole('dialog', { name: /Unsaved Prompt/ })
    fireEvent.click(within(guard).getByRole('button', { name: /^Cancel$/ }))

    expect(onClose).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog', { name: /Unsaved Prompt/ })).not.toBeInTheDocument()
  })
})

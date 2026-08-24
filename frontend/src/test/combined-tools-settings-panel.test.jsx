/**
 * Combined "Tools and Settings" panel (issue #836).
 *
 * The header's gear, wrench, and light/dark buttons collapsed into one panel:
 * tools and integrations became a tab, the theme toggle moved into General,
 * and admins get a quick-controls tab.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPanel from '../components/SettingsPanel'
import PromptSelector from '../components/PromptSelector'
import { ThemeProvider } from '../contexts/ThemeContext'
import { useChat } from '../contexts/ChatContext'
import { OPEN_SETTINGS_EVENT } from '../utils/settingsPanelEvents'

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

vi.mock('../components/ToolsPanel', () => ({
  default: ({ active }) => <div>{active ? 'tools tab body' : 'tools tab hidden'}</div>
}))

vi.mock('../components/admin/AdminQuickPanel', () => ({
  default: () => <div>admin quick controls</div>
}))

vi.mock('../components/PromptManager', () => ({
  default: ({ intent }) => <div>prompt manager intent: {intent ? intent.type : 'none'}</div>
}))

const baseChatContext = {
  settings: { autoApproveTools: false },
  updateSettings: vi.fn(),
  features: { tools: true, custom_prompts: true },
  agentModeAvailable: false,
  isInAdminGroup: false,
}

const renderPanel = (props = {}, contextOverrides = {}) => {
  useChat.mockReturnValue({
    ...baseChatContext,
    ...contextOverrides,
    features: { ...baseChatContext.features, ...contextOverrides.features },
  })
  return render(
    <ThemeProvider>
      <SettingsPanel isOpen onClose={vi.fn()} {...props} />
    </ThemeProvider>
  )
}

describe('SettingsPanel as the combined Tools and Settings panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('is titled "Tools and Settings"', () => {
    renderPanel()
    expect(screen.getByRole('heading', { name: /Tools and Settings/ })).toBeInTheDocument()
  })

  it('shows a Tools & Integrations tab when the tools feature is on', () => {
    renderPanel({ initialTab: 'tools' })
    expect(screen.getByRole('button', { name: /Tools & Integrations/ })).toBeInTheDocument()
    expect(screen.getByText('tools tab body')).toBeInTheDocument()
  })

  it('hides the tools tab when the tools feature is off', () => {
    renderPanel({}, { features: { tools: false } })
    expect(screen.queryByRole('button', { name: /Tools & Integrations/ })).not.toBeInTheDocument()
  })

  it('shows the admin tab only for admins', () => {
    const { unmount } = renderPanel()
    expect(screen.queryByRole('button', { name: /Admin/ })).not.toBeInTheDocument()
    unmount()

    renderPanel({ initialTab: 'admin' }, { isInAdminGroup: true })
    expect(screen.getByText('admin quick controls')).toBeInTheDocument()
  })

  it('moves the light/dark toggle into the General tab', () => {
    renderPanel({ initialTab: 'general' })
    const toggle = screen.getByRole('button', { name: /Switch to light mode/ })
    expect(toggle).toBeInTheDocument()
    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: /Switch to dark mode/ })).toBeInTheDocument()
  })

  it('passes a prompt intent through to the prompt manager', () => {
    renderPanel({ initialTab: 'prompts', promptIntent: { type: 'edit', id: 'p1' } })
    expect(screen.getByText('prompt manager intent: edit')).toBeInTheDocument()
  })
})

describe('PromptSelector quick access to custom prompts', () => {
  const chatContext = {
    prompts: [],
    selectedPrompts: new Set(),
    activePromptKey: null,
    makePromptActive: vi.fn(),
    clearActivePrompt: vi.fn(),
    removePrompts: vi.fn(),
    features: { custom_prompts: true },
    userPrompts: [{ id: 'p1', title: 'Code reviewer', content: 'Be terse.' }],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useChat.mockReturnValue(chatContext)
  })

  const openDropdown = () => {
    render(<PromptSelector />)
    fireEvent.click(screen.getByTitle('Select custom prompts'))
  }

  it('asks to open the prompt editor for a specific prompt', () => {
    const listener = vi.fn()
    window.addEventListener(OPEN_SETTINGS_EVENT, listener)
    openDropdown()

    fireEvent.click(screen.getByLabelText('Edit prompt Code reviewer'))

    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener.mock.calls[0][0].detail).toEqual({
      tab: 'prompts',
      promptIntent: { type: 'edit', id: 'p1' },
    })
    window.removeEventListener(OPEN_SETTINGS_EVENT, listener)
  })

  it('offers a create-new-system-prompt shortcut', () => {
    const listener = vi.fn()
    window.addEventListener(OPEN_SETTINGS_EVENT, listener)
    openDropdown()

    fireEvent.click(screen.getByTitle('Create a new system prompt'))

    expect(listener.mock.calls[0][0].detail).toEqual({
      tab: 'prompts',
      promptIntent: { type: 'create' },
    })
    window.removeEventListener(OPEN_SETTINGS_EVENT, listener)
  })
})

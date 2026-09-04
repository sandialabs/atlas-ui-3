/**
 * Follow-ups to PR #889, which promoted the data source picker out of the
 * Tools & Integrations tab into a Data Sources tab of the Tools and Settings
 * modal.
 *
 * That move puts the picker inside a focus-trapped ARIA tabs pattern whose
 * every other tab is fully keyboard-operable, and it inserts a sixth tab in
 * second position, pushing the later tabs past the right edge of the strip on
 * a narrow window. Both are covered here.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DataSourcesSelector from '../components/DataSourcesSelector'
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
vi.mock('../components/ToolsPanel', () => ({ default: () => <div>tools tab body</div> }))
vi.mock('../components/admin/AdminQuickPanel', () => ({ default: () => <div>admin quick controls</div> }))
vi.mock('../components/PromptManager', () => ({ default: () => <div>prompt manager</div> }))

const SOURCES = [
  { id: 'public-docs', label: 'public-docs', serverName: 'atlas_rag', serverComplianceLevel: 'Public', complianceLevel: 'Public' },
  { id: 'internal-docs', label: 'internal-docs', serverName: 'atlas_rag', serverComplianceLevel: 'Internal', complianceLevel: 'Internal' },
]

const COMPLIANCE_LEVELS = [
  { name: 'Public', allowed_with: ['Public'] },
  { name: 'Internal', allowed_with: ['Internal', 'Public'] },
]

function mockChat(overrides = {}) {
  useChat.mockReturnValue({
    ragSources: SOURCES,
    selectedDataSources: new Set(),
    toggleDataSource: vi.fn(),
    addDataSources: vi.fn(),
    clearDataSources: vi.fn(),
    features: { compliance_levels: false, rag: true, tools: true },
    complianceLevelFilter: null,
    models: [
      { name: 'public-model', compliance_level: 'Public' },
      { name: 'internal-model', compliance_level: 'Internal' },
    ],
    currentModel: 'internal-model',
    settings: { autoApproveTools: false },
    updateSettings: vi.fn(),
    agentModeAvailable: false,
    isInAdminGroup: true,
    ...overrides,
  })
  useMarketplace.mockReturnValue({
    complianceLevels: COMPLIANCE_LEVELS,
    isComplianceAccessible: (userLevel, resourceLevel) => {
      if (!userLevel) return true
      if (!resourceLevel) return false
      const level = COMPLIANCE_LEVELS.find(l => l.name === userLevel)
      return !!level && level.allowed_with.includes(resourceLevel)
    },
  })
}

describe('DataSourcesSelector - keyboard operability', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders each source as a focusable toggle button rather than a bare div', () => {
    mockChat()
    render(<DataSourcesSelector />)

    const row = screen.getByRole('button', { name: /public-docs/ })
    expect(row).toHaveAttribute('type', 'button')
    expect(row).toHaveAttribute('aria-pressed', 'false')
  })

  it('reports the selected state through aria-pressed', () => {
    mockChat({ selectedDataSources: new Set(['atlas_rag:public-docs']) })
    render(<DataSourcesSelector />)

    expect(screen.getByRole('button', { name: /public-docs/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('toggles a source from the keyboard', () => {
    const toggleDataSource = vi.fn()
    mockChat({ toggleDataSource })
    render(<DataSourcesSelector />)

    const row = screen.getByRole('button', { name: /public-docs/ })
    row.focus()
    expect(row).toHaveFocus()
    // A real button fires click for both Enter and Space; jsdom does not
    // synthesise that, so assert the activation path directly.
    fireEvent.click(row)
    expect(toggleDataSource).toHaveBeenCalledWith('atlas_rag:public-docs')
  })

  it('keeps an out-of-boundary source focusable and marked aria-disabled', () => {
    const toggleDataSource = vi.fn()
    mockChat({
      toggleDataSource,
      currentModel: 'public-model',
      features: { compliance_levels: true, rag: true, tools: true },
    })
    render(<DataSourcesSelector />)

    const row = screen.getByRole('button', { name: /internal-docs/ })
    expect(row).toHaveAttribute('aria-disabled', 'true')
    expect(row).not.toBeDisabled()
    fireEvent.click(row)
    expect(toggleDataSource).not.toHaveBeenCalled()
  })

  it('leaves an already-selected out-of-boundary source deselectable', () => {
    const toggleDataSource = vi.fn()
    mockChat({
      toggleDataSource,
      currentModel: 'public-model',
      selectedDataSources: new Set(['atlas_rag:internal-docs']),
      features: { compliance_levels: true, rag: true, tools: true },
    })
    render(<DataSourcesSelector />)

    const row = screen.getByRole('button', { name: /internal-docs/ })
    expect(row).not.toHaveAttribute('aria-disabled')
    fireEvent.click(row)
    expect(toggleDataSource).toHaveBeenCalledWith('atlas_rag:internal-docs')
  })
})

describe('SettingsPanel - the opened tab is scrolled into view', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('scrolls the tab the panel opens on into the strip', () => {
    mockChat()
    const scrolled = []
    const original = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function scrollIntoViewSpy(opts) {
      scrolled.push({ id: this.id, opts })
    }
    try {
      render(
        <ThemeProvider>
          <SettingsPanel isOpen onClose={vi.fn()} initialTab="admin" />
        </ThemeProvider>
      )
      expect(scrolled.some(s => s.id === 'settings-tab-admin')).toBe(true)
      expect(scrolled.find(s => s.id === 'settings-tab-admin').opts)
        .toEqual({ block: 'nearest', inline: 'nearest' })
    } finally {
      Element.prototype.scrollIntoView = original
    }
  })

  it('scrolls the Data Sources tab into view when it is selected', () => {
    mockChat()
    const scrolled = []
    const original = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function scrollIntoViewSpy() { scrolled.push(this.id) }
    try {
      render(
        <ThemeProvider>
          <SettingsPanel isOpen onClose={vi.fn()} />
        </ThemeProvider>
      )
      scrolled.length = 0
      fireEvent.click(screen.getByRole('tab', { name: /Data Sources/ }))
      expect(scrolled).toContain('settings-tab-dataSources')
    } finally {
      Element.prototype.scrollIntoView = original
    }
  })
})

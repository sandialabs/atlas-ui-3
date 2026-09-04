/**
 * Chat-bar controls and the sources/tools consolidation (PR #839 UX review).
 *
 * cmlanca asked for the day-to-day controls to move to where the work happens:
 * tools toggled from the chat bar with descriptions rather than a flat list of
 * names, enabled datasets visible as pills, and data sources shown in the same
 * view as the search tool that consumes them.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ToolSelector from '../components/ToolSelector'
import EnabledDataSourcesIndicator from '../components/EnabledDataSourcesIndicator'
import { useChat } from '../contexts/ChatContext'
import { useOptionalMarketplace } from '../contexts/MarketplaceContext'
import { OPEN_SETTINGS_EVENT } from '../utils/settingsPanelEvents'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))
vi.mock('../contexts/MarketplaceContext', () => ({ useOptionalMarketplace: vi.fn() }))

const tools = [{
  server: 'files',
  tools: ['read_file', 'write_file'],
  tools_detailed: [
    { name: 'read_file', description: 'Read a file from the session workspace' },
    { name: 'write_file', description: 'Write a file into the session workspace' },
  ],
}]

const ragSources = [
  { serverName: 'corp', id: 'west', label: 'West Region Fleet' },
  { serverName: 'corp', id: 'east', label: 'East Region Fleet' },
  { serverName: 'corp', id: 'central', label: 'Central Region Fleet' },
  { serverName: 'corp', id: 'exec', label: 'Executive Fleet' },
]

// The chat-bar menu lists what the Tools and Settings panel lists, so its rows
// come from the marketplace filters rather than the raw tool list.
const mockMarketplace = ({ filtered = tools, complianceFiltered = tools } = {}) => {
  useOptionalMarketplace.mockReturnValue({
    getFilteredTools: () => filtered,
    getComplianceFilteredTools: () => complianceFiltered,
  })
}

describe('ToolSelector in the chat bar', () => {
  let toggleTool

  beforeEach(() => {
    toggleTool = vi.fn()
    useChat.mockReturnValue({
      selectedTools: new Set(['files_read_file']),
      toggleTool,
      features: { tools: true },
    })
    mockMarketplace()
  })

  it('summarises the selection on the closed control', () => {
    render(<ToolSelector />)
    expect(screen.getByText('1 tool')).toBeInTheDocument()
  })

  it('lists each tool with a description and an on/off state', () => {
    render(<ToolSelector />)
    fireEvent.click(screen.getByRole('button', { name: /1 tool/ }))

    expect(screen.getByText('read_file')).toBeInTheDocument()
    expect(screen.getByText('Read a file from the session workspace')).toBeInTheDocument()

    const on = screen.getByRole('button', { name: /read_file/ })
    const off = screen.getByRole('button', { name: /write_file/ })
    expect(on).toHaveAttribute('aria-pressed', 'true')
    expect(off).toHaveAttribute('aria-pressed', 'false')
  })

  it('toggles a tool without leaving the chat bar', () => {
    render(<ToolSelector />)
    fireEvent.click(screen.getByRole('button', { name: /1 tool/ }))
    fireEvent.click(screen.getByRole('button', { name: /write_file/ }))
    expect(toggleTool).toHaveBeenCalledWith('files_write_file')
  })

  it('opens the full panel from the footer link', () => {
    const listener = vi.fn()
    window.addEventListener(OPEN_SETTINGS_EVENT, listener)
    render(<ToolSelector />)
    fireEvent.click(screen.getByRole('button', { name: /1 tool/ }))
    fireEvent.click(screen.getByText(/Open Tools and Settings/))
    expect(listener).toHaveBeenCalled()
    expect(listener.mock.calls[0][0].detail).toEqual({ tab: 'tools' })
    window.removeEventListener(OPEN_SETTINGS_EVENT, listener)
  })

  it('renders nothing when the tools feature is off', () => {
    useChat.mockReturnValue({ selectedTools: new Set(), toggleTool, features: {} })
    const { container } = render(<ToolSelector />)
    expect(container).toBeEmptyDOMElement()
  })

  // Parity with ToolsPanel (PR #839 review). The menu used to read the raw
  // `tools` list off the chat context, so a tool the panel hid -- its server
  // unselected in the marketplace -- was still listed and toggleable here.
  it('hides tools whose server is not selected in the marketplace', () => {
    mockMarketplace({
      filtered: [{ ...tools[0], tools: ['read_file'] }],
      complianceFiltered: [{ ...tools[0], tools: ['read_file'] }],
    })
    render(<ToolSelector />)
    fireEvent.click(screen.getByRole('button', { name: /1 tool/ }))

    expect(screen.getByText('read_file')).toBeInTheDocument()
    expect(screen.queryByText('write_file')).not.toBeInTheDocument()
  })

  // With compliance levels on, the panel switches filters; the chat bar must
  // switch with it rather than keep showing the unrestricted list.
  it('uses the compliance-filtered list when compliance levels are enabled', () => {
    useChat.mockReturnValue({
      selectedTools: new Set(),
      toggleTool,
      features: { tools: true, compliance_levels: true },
      complianceLevelFilter: 'low',
    })
    mockMarketplace({
      filtered: tools,
      complianceFiltered: [{ ...tools[0], tools: ['write_file'] }],
    })
    render(<ToolSelector />)
    fireEvent.click(screen.getByRole('button', { name: /Tools/ }))

    expect(screen.getByText('write_file')).toBeInTheDocument()
    expect(screen.queryByText('read_file')).not.toBeInTheDocument()
  })
})

describe('EnabledDataSourcesIndicator', () => {
  const renderWith = (keys, overrides = {}) => {
    useChat.mockReturnValue({
      ragSources,
      selectedDataSources: new Set(keys),
      toggleDataSource: vi.fn(),
      features: { rag: true },
      ...overrides,
    })
    return render(<EnabledDataSourcesIndicator />)
  }

  it('stays out of the way when nothing is enabled', () => {
    const { container } = renderWith([])
    expect(container).toBeEmptyDOMElement()
  })

  it('names each enabled dataset as a pill', () => {
    renderWith(['corp:west', 'corp:east'])
    expect(screen.getByText('West Region Fleet')).toBeInTheDocument()
    expect(screen.getByText('East Region Fleet')).toBeInTheDocument()
  })

  it('collapses a long list into an expandable summary', () => {
    renderWith(['corp:west', 'corp:east', 'corp:central', 'corp:exec'])
    expect(screen.queryByText('Executive Fleet')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('+1 more'))
    expect(screen.getByText('Executive Fleet')).toBeInTheDocument()
  })

  it('removes a dataset from its pill', () => {
    const toggleDataSource = vi.fn()
    renderWith(['corp:west'], { toggleDataSource })
    fireEvent.click(screen.getByRole('button', { name: 'Remove West Region Fleet' }))
    expect(toggleDataSource).toHaveBeenCalledWith('corp:west')
  })

  it('is hidden when RAG is disabled', () => {
    const { container } = renderWith(['corp:west'], { features: {} })
    expect(container).toBeEmptyDOMElement()
  })

  // Mirrors the Active Tools strip directly above it (#870/#871). Without this
  // the dataset pills wrap into a second and third row on a narrow viewport --
  // the exact bug #870 fixed, reintroduced one row down.
  it('keeps the pills to a single scrolling row rather than wrapping', () => {
    renderWith(['corp:west', 'corp:east'])
    const pillRow = screen.getByText('West Region Fleet').closest('.overflow-x-auto')
    expect(pillRow).not.toBeNull()
    expect(pillRow.className).toContain('flex-nowrap')
    expect(pillRow.className).not.toContain('flex-wrap')
  })

  it('does not wrap at the strip root either', () => {
    renderWith(['corp:west'])
    const strip = screen.getByTestId('active-data-sources')
    expect(strip.className).not.toContain('flex-wrap')
  })

  it('keeps the "+N more" toggle outside the scroll region', () => {
    renderWith(['corp:west', 'corp:east', 'corp:central', 'corp:exec'])
    const more = screen.getByText('+1 more')
    expect(more.closest('.overflow-x-auto')).toBeNull()
  })
})

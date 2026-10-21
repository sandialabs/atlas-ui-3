/**
 * The tool panels show the built-in search tool's automatic state (#1102).
 *
 * Selected data sources imply the search tool: the server offers it for the
 * very same reason, so the panel shows it as on, says why, and refuses to
 * let it be switched off while the sources are selected. Deselecting every
 * source removes the automatic state again, and the tool goes back to being
 * an ordinary toggle.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ToolsPanel from '../components/ToolsPanel'
import ToolSelector from '../components/ToolSelector'
import { useChat } from '../contexts/ChatContext'

// The marketplace filter decides which servers the panels list, so the
// catalogue has to be steerable per test.
const marketplaceState = vi.hoisted(() => ({ tools: [] }))

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({
  useOptionalMarketplace: () => null,
  useMarketplace: () => ({
    getComplianceFilteredTools: () => marketplaceState.tools,
    getComplianceFilteredPrompts: () => [],
    getFilteredTools: () => marketplaceState.tools,
    getFilteredPrompts: () => [],
    isComplianceAccessible: () => true
  })
}))

const ATLAS_SERVER = {
  server: 'atlas',
  description: 'Built-in ATLAS tools',
  tools: ['canvas', 'search'],
  tools_detailed: [
    { name: 'canvas', description: 'Show final content in the panel', inputSchema: {} },
    { name: 'search', description: 'Search the selected data sources', inputSchema: {} }
  ],
  tool_count: 2,
  prompts: [],
  prompt_count: 0
}

const OTHER_SERVER = {
  server: 'other',
  description: 'Another provider',
  tools: ['search'],
  tools_detailed: [{ name: 'search', description: 'Provider search', inputSchema: {} }],
  tool_count: 1,
  prompts: [],
  prompt_count: 0
}

const NOTE = 'Automatically enabled by selected data sources.'
const RAG_NOTE = 'Automatically enabled while RAG is on.'
const EXPLICIT_NOTE = 'Also selected on its own, so it stays on when the sources are deselected.'

const chatContext = (overrides = {}) => ({
  selectedTools: new Set(),
  selectedPrompts: new Set(),
  toggleTool: vi.fn(),
  togglePrompt: vi.fn(),
  addTools: vi.fn(),
  removeTools: vi.fn(),
  addPrompts: vi.fn(),
  removePrompts: vi.fn(),
  clearToolsAndPrompts: vi.fn(),
  complianceLevelFilter: 'all',
  tools: marketplaceState.tools,
  prompts: [],
  features: { tools: true },
  currentModel: 'gpt-4',
  models: [{ name: 'gpt-4', supports_tools: true }],
  effectiveDataSources: [],
  searchToolReason: null,
  ...overrides
})

beforeEach(() => {
  vi.clearAllMocks()
  marketplaceState.tools = [ATLAS_SERVER]
})

describe('ToolsPanel - the search tool is on because of the selected sources', () => {
  const renderPanel = (overrides = {}) => {
    const context = chatContext({ tools: marketplaceState.tools, ...overrides })
    useChat.mockReturnValue(context)
    render(
      <BrowserRouter>
        <ToolsPanel isOpen onClose={vi.fn()} />
      </BrowserRouter>
    )
    return context
  }

  it('shows the search tool as enabled and says why', () => {
    renderPanel({ effectiveDataSources: ['srv:doc'], searchToolReason: 'sources' })

    const chip = screen.getByRole('button', { name: 'search' })
    expect(chip).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText(NOTE)).toBeInTheDocument()
  })

  it('does not let the user switch it off while the sources are selected', () => {
    const context = renderPanel({ effectiveDataSources: ['srv:doc'] })

    const chip = screen.getByRole('button', { name: 'search' })
    expect(chip).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(chip)
    expect(context.toggleTool).not.toHaveBeenCalled()
  })

  it('goes back to an ordinary toggle once every source is deselected', () => {
    const context = renderPanel({ effectiveDataSources: [] })

    const chip = screen.getByRole('button', { name: 'search' })
    expect(chip).toHaveAttribute('aria-pressed', 'false')
    expect(chip).not.toHaveAttribute('aria-disabled')
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument()

    fireEvent.click(chip)
    fireEvent.click(screen.getByRole('button', { name: /Save and Close/i }))
    expect(context.addTools).toHaveBeenCalledWith(['atlas_search'])
  })

  it('leaves the state alone when the deployment does not offer the search tool', () => {
    // The feature flag is off: the backend leaves the search tool out of the
    // catalogue, so there is nothing to enable automatically.
    const withoutSearch = {
      ...ATLAS_SERVER,
      tools: ['canvas'],
      tools_detailed: [ATLAS_SERVER.tools_detailed[0]]
    }
    marketplaceState.tools = [withoutSearch]
    renderPanel({ effectiveDataSources: ['srv:doc'] })

    expect(screen.queryByRole('button', { name: 'search' })).not.toBeInTheDocument()
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument()
  })

  it('leaves the state alone for a model that cannot call tools', () => {
    // The backend strips every tool for such a model, so the search tool is
    // not on the table for the turn -- and the panel must not claim it is.
    renderPanel({
      effectiveDataSources: ['srv:doc'],
      currentModel: 'plain-model',
      models: [{ name: 'plain-model', supports_tools: false }]
    })

    const chip = screen.getByRole('button', { name: 'search' })
    expect(chip).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument()
  })

  it('says so when the tool is also selected in its own right', () => {
    // "Enable All" (or an earlier tick) puts the search tool in the
    // selection, so it stays on after the sources are deselected. The chip
    // has to say that, or the lock reads as "this cannot be turned off".
    renderPanel({
      effectiveDataSources: ['srv:doc'],
      selectedTools: new Set(['atlas_search'])
    })

    expect(screen.getByText(NOTE)).toBeInTheDocument()
    expect(screen.getByText(EXPLICIT_NOTE)).toBeInTheDocument()
  })

  it('names the RAG toggle when the toggle is what put the sources in play', () => {
    // Nothing was hand-picked: the RAG toggle expanded the list, so the
    // note says so rather than claiming the user picked sources.
    renderPanel({
      effectiveDataSources: ['srv:doc'],
      searchToolReason: 'rag'
    })

    expect(screen.getByText(RAG_NOTE)).toBeInTheDocument()
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument()
  })

  it('does not touch another server that has a tool called search', () => {
    marketplaceState.tools = [ATLAS_SERVER, OTHER_SERVER]
    const context = renderPanel({ effectiveDataSources: ['srv:doc'] })

    const chips = screen.getAllByRole('button', { name: 'search' })
    expect(chips).toHaveLength(2)
    const locked = chips.filter(c => c.getAttribute('aria-disabled') === 'true')
    expect(locked).toHaveLength(1)

    fireEvent.click(chips.find(c => c.getAttribute('aria-disabled') !== 'true'))
    fireEvent.click(screen.getByRole('button', { name: /Save and Close/i }))
    expect(context.addTools).toHaveBeenCalledWith(['other_search'])
  })
})

describe('ToolSelector - the search tool is on because of the selected sources', () => {
  const renderSelector = (overrides = {}) => {
    const context = chatContext({ tools: marketplaceState.tools, ...overrides })
    useChat.mockReturnValue(context)
    render(
      <BrowserRouter>
        <ToolSelector />
      </BrowserRouter>
    )
    fireEvent.click(screen.getByRole('button', { name: /tool/i }))
    return context
  }

  it('shows the search tool as on and explains the automatic state', () => {
    renderSelector({ effectiveDataSources: ['srv:doc'] })

    const row = screen.getByRole('button', { name: /search/i })
    expect(row).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText(NOTE)).toBeInTheDocument()
  })

  it('does not toggle the automatic search tool off', () => {
    const context = renderSelector({ effectiveDataSources: ['srv:doc'] })

    const row = screen.getByRole('button', { name: /search/i })
    fireEvent.click(row)
    expect(context.toggleTool).not.toHaveBeenCalled()
  })

  it('is an ordinary toggle again once every source is deselected', () => {
    const context = renderSelector({ effectiveDataSources: [] })

    const row = screen.getByRole('button', { name: /search/i })
    expect(row).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument()

    fireEvent.click(row)
    expect(context.toggleTool).toHaveBeenCalledWith('atlas_search')
  })

  it('counts the implied tool once when it is already ticked explicitly', () => {
    // The selection already holds the search tool, so the implied one must
    // not be counted a second time in the badge.
    renderSelector({
      selectedTools: new Set(['atlas_canvas', 'atlas_search']),
      effectiveDataSources: ['srv:doc']
    })

    expect(screen.getByRole('button', { name: '2 tools' })).toBeInTheDocument()
  })

  it('counts the implied tool when it is not ticked explicitly', () => {
    renderSelector({
      selectedTools: new Set(['atlas_canvas']),
      effectiveDataSources: ['srv:doc']
    })

    expect(screen.getByRole('button', { name: '2 tools' })).toBeInTheDocument()
  })
})

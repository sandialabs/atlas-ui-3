/**
 * Issue #1141: the launch-family built-ins were renamed so they read as
 * delegating work to a subagent (`atlas_launch` -> `atlas_launch_subagent`).
 * The old names are deprecated aliases: stored selections and old transcripts
 * still carry them, so they migrate forward and are presented the same way.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ToolsPanel from '../components/ToolsPanel'
import ToolNameLabel from '../components/ToolNameLabel'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import {
  DEPRECATED_TOOL_ALIASES,
  DISCOVER_LAUNCH_OPTIONS_TOOL,
  DISCOVER_SUBAGENT_OPTIONS_TOOL,
  FOLLOW_UP_SUBAGENT_TOOL,
  GET_SUBAGENT_RESULT_TOOL,
  GET_SUBAGENT_RUNS_TOOL,
  LAUNCH_SUBAGENT_TOOL,
  SUBAGENT_TOOL_GROUP,
  atlasToolGroup,
  isSubagentTool,
  migrateToolName,
  migrateToolNames,
  subagentToolLabel,
} from '../constants/atlasTools'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext')
vi.mock('../hooks/useServerAuthStatus', () => ({
  useServerAuthStatus: () => ({
    authStatus: {},
    loading: false,
    error: null,
    fetchAuthStatus: vi.fn(),
    uploadToken: vi.fn(),
    removeToken: vi.fn(),
    startOAuth: vi.fn(),
    getServerAuth: vi.fn(() => null),
  }),
}))

describe('subagent tool names', () => {
  it('uses the subagent-oriented names', () => {
    expect(LAUNCH_SUBAGENT_TOOL).toBe('atlas_launch_subagent')
    expect(GET_SUBAGENT_RUNS_TOOL).toBe('atlas_get_subagent_runs')
    expect(GET_SUBAGENT_RESULT_TOOL).toBe('atlas_get_subagent_result')
    expect(FOLLOW_UP_SUBAGENT_TOOL).toBe('atlas_follow_up_subagent')
    expect(DISCOVER_SUBAGENT_OPTIONS_TOOL).toBe('atlas_discover_subagent_options')
    expect(DISCOVER_LAUNCH_OPTIONS_TOOL).toBe(DISCOVER_SUBAGENT_OPTIONS_TOOL)
  })

  it('migrates every deprecated name forward', () => {
    expect(DEPRECATED_TOOL_ALIASES).toEqual({
      atlas_discover_launch_options: 'atlas_discover_subagent_options',
      atlas_launch: 'atlas_launch_subagent',
      atlas_get_runs: 'atlas_get_subagent_runs',
      atlas_result: 'atlas_get_subagent_result',
      atlas_follow_up: 'atlas_follow_up_subagent',
    })
    for (const [oldName, newName] of Object.entries(DEPRECATED_TOOL_ALIASES)) {
      expect(migrateToolName(oldName)).toBe(newName)
    }
  })

  it('collapses an old and a new name in one stored selection', () => {
    expect(migrateToolNames(['atlas_launch', LAUNCH_SUBAGENT_TOOL, 'math_add']))
      .toEqual([LAUNCH_SUBAGENT_TOOL, 'math_add'])
  })

  it('titles a subagent tool the same under either name', () => {
    expect(subagentToolLabel(LAUNCH_SUBAGENT_TOOL)).toBe('Launch Subagent')
    expect(subagentToolLabel('atlas_launch')).toBe('Launch Subagent')
    expect(subagentToolLabel(GET_SUBAGENT_RESULT_TOOL)).toBe('Get Subagent Result')
    expect(subagentToolLabel('atlas_result')).toBe('Get Subagent Result')
    expect(subagentToolLabel(GET_SUBAGENT_RUNS_TOOL)).toBe('Get Subagent Runs')
    expect(subagentToolLabel(FOLLOW_UP_SUBAGENT_TOOL)).toBe('Follow Up Subagent')
    expect(subagentToolLabel(DISCOVER_SUBAGENT_OPTIONS_TOOL)).toBe('Discover Subagent Options')
  })

  it('leaves every other tool alone', () => {
    for (const name of ['atlas_canvas', 'atlas_search', 'math_add', 'toString', undefined, null, 42]) {
      expect(subagentToolLabel(name)).toBe(null)
      expect(isSubagentTool(name)).toBe(false)
      expect(atlasToolGroup(name)).toBe(null)
    }
  })

  it('groups the subagent tools together', () => {
    expect(atlasToolGroup(LAUNCH_SUBAGENT_TOOL)).toBe(SUBAGENT_TOOL_GROUP)
    expect(atlasToolGroup('atlas_get_runs')).toBe('Subagents')
  })
})

describe('ToolNameLabel', () => {
  it('shows an ordinary tool by its own name', () => {
    render(<ToolNameLabel toolName="math_add" />)
    expect(screen.getByText('math_add')).toBeInTheDocument()
    expect(screen.queryByTestId('subagent-tool-label')).toBeNull()
  })

  it('identifies a subagent launch, keeping the tool name visible', () => {
    render(<ToolNameLabel toolName="atlas_launch_subagent" />)
    const label = screen.getByTestId('subagent-tool-label')
    expect(label).toHaveTextContent('Launch Subagent')
    expect(label).toHaveTextContent('atlas_launch_subagent')
  })

  it('identifies a call made under a deprecated name the same way', () => {
    render(<ToolNameLabel toolName="atlas_result" />)
    const label = screen.getByTestId('subagent-tool-label')
    expect(label).toHaveTextContent('Get Subagent Result')
    expect(label).toHaveTextContent('atlas_result')
  })
})

describe('ToolsPanel - Subagents group', () => {
  const atlasServer = {
    server: 'atlas',
    description: 'Built-in ATLAS tools',
    tools: [
      'canvas',
      'discover_subagent_options',
      'launch_subagent',
      'get_subagent_runs',
      'get_subagent_result',
      'follow_up_subagent',
    ],
    tools_detailed: [],
    tool_count: 6,
    prompts: [],
    prompt_count: 0,
  }
  let addTools

  beforeEach(() => {
    addTools = vi.fn()
    useChat.mockReturnValue({
      selectedTools: new Set(),
      selectedPrompts: new Set(),
      toggleTool: vi.fn(),
      togglePrompt: vi.fn(),
      addTools,
      addPrompts: vi.fn(),
      removeTools: vi.fn(),
      removePrompts: vi.fn(),
      clearToolsAndPrompts: vi.fn(),
      complianceLevelFilter: 'all',
      tools: [atlasServer],
      prompts: [],
      features: {},
    })
    useMarketplace.mockReturnValue({
      getComplianceFilteredTools: vi.fn(() => []),
      getComplianceFilteredPrompts: vi.fn(() => []),
      getFilteredTools: vi.fn(() => [atlasServer]),
      getFilteredPrompts: vi.fn(() => []),
      isComplianceAccessible: vi.fn(() => true),
    })
  })

  const renderPanel = () => render(
    <BrowserRouter>
      <ToolsPanel isOpen={true} onClose={vi.fn()} />
    </BrowserRouter>
  )

  it('lists the subagent tools under one Subagents heading', () => {
    renderPanel()

    expect(screen.getAllByTestId('tool-group-Subagents')).toHaveLength(1)
    expect(screen.getByTestId('tool-group-Subagents')).toHaveTextContent('Subagents')
    expect(screen.getByRole('button', { name: 'Launch Subagent' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Get Subagent Runs' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Get Subagent Result' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Follow Up Subagent' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Discover Subagent Options' })).toBeInTheDocument()
    // Tools outside the group keep their own name.
    expect(screen.getByRole('button', { name: 'canvas' })).toBeInTheDocument()
  })

  it('finds the server when searching by a displayed title or a raw name', () => {
    renderPanel()
    const search = screen.getByPlaceholderText(/Search installed tools/i)

    fireEvent.change(search, { target: { value: 'launch subagent' } })
    expect(screen.getByRole('button', { name: 'Launch Subagent' })).toBeInTheDocument()

    fireEvent.change(search, { target: { value: 'get_subagent_result' } })
    expect(screen.getByRole('button', { name: 'Get Subagent Result' })).toBeInTheDocument()

    fireEvent.change(search, { target: { value: 'no such tool' } })
    expect(screen.queryByRole('button', { name: 'Launch Subagent' })).toBeNull()
  })

  it('still selects the tool by its real name', () => {
    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: 'Launch Subagent' }))
    fireEvent.click(screen.getByRole('button', { name: /Save and Close/i }))

    expect(addTools).toHaveBeenCalledWith(['atlas_launch_subagent'])
  })
})

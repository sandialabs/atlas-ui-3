/**
 * Tests for ToolsPanel component - tool selection and management
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ToolsPanel from '../components/ToolsPanel'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'

// Mock the contexts
vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext')

describe('ToolsPanel - Tool Selection', () => {
  let mockToggleTool
  let mockAddTools
  let mockRemoveTools
  let mockTogglePrompt
  let mockRemovePrompts
  let mockClearToolsAndPrompts
  let mockSetToolChoiceRequired

  const defaultChatContext = {
    selectedTools: new Set(),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    addTools: vi.fn(),
    removeTools: vi.fn(),
    removePrompts: vi.fn(),
    clearToolsAndPrompts: vi.fn(),
    toolChoiceRequired: false,
    setToolChoiceRequired: vi.fn(),
    complianceLevelFilter: 'all',
    tools: [],
    prompts: [],
    features: {}
  }

  const defaultMarketplaceContext = {
    getComplianceFilteredTools: vi.fn(() => []),
    getComplianceFilteredPrompts: vi.fn(() => []),
    getFilteredTools: vi.fn(() => []),
    getFilteredPrompts: vi.fn(() => [])
  }

  beforeEach(() => {
    mockToggleTool = vi.fn()
    mockAddTools = vi.fn()
    mockRemoveTools = vi.fn()
    mockTogglePrompt = vi.fn()
    mockRemovePrompts = vi.fn()
    mockClearToolsAndPrompts = vi.fn()
    mockSetToolChoiceRequired = vi.fn()

    useChat.mockReturnValue({
      ...defaultChatContext,
      toggleTool: mockToggleTool,
      addTools: mockAddTools,
      addPrompts: vi.fn(),
      removeTools: mockRemoveTools,
      togglePrompt: mockTogglePrompt,
      removePrompts: mockRemovePrompts,
      clearToolsAndPrompts: mockClearToolsAndPrompts,
      setToolChoiceRequired: mockSetToolChoiceRequired
    })

    useMarketplace.mockReturnValue(defaultMarketplaceContext)
  })

  it('should toggle individual tool selection when tool button is clicked', () => {
    const testTools = [{
      server: 'test_server',
      description: 'Test server description',
      tools: ['fetch', 'search'],
      tools_detailed: [],
      tool_count: 2,
      prompts: [],
      prompt_count: 0
    }]

    useChat.mockReturnValue({
      ...defaultChatContext,
      tools: testTools,
      toggleTool: mockToggleTool
    })

    useMarketplace.mockReturnValue({
      ...defaultMarketplaceContext,
      getFilteredTools: vi.fn(() => testTools)
    })

    render(
      <BrowserRouter>
        <ToolsPanel isOpen={true} onClose={vi.fn()} />
      </BrowserRouter>
    )

    // Find and click the 'fetch' tool button
    const fetchButton = screen.getByRole('button', { name: 'fetch' })
    fireEvent.click(fetchButton)

    // Verify toggleTool was called with correct key
    expect(mockToggleTool).toHaveBeenCalledWith('test_server_fetch')
  })

  it('should filter tools based on search input', () => {
    const testTools = [
      {
        server: 'filesystem',
        description: 'File operations',
        tools: ['read_file', 'write_file'],
        tools_detailed: [],
        tool_count: 2,
        prompts: [],
        prompt_count: 0
      },
      {
        server: 'database',
        description: 'Database operations',
        tools: ['query', 'insert'],
        tools_detailed: [],
        tool_count: 2,
        prompts: [],
        prompt_count: 0
      }
    ]

    useChat.mockReturnValue({
      ...defaultChatContext,
      tools: testTools
    })

    useMarketplace.mockReturnValue({
      ...defaultMarketplaceContext,
      getFilteredTools: vi.fn(() => testTools)
    })

    render(
      <BrowserRouter>
        <ToolsPanel isOpen={true} onClose={vi.fn()} />
      </BrowserRouter>
    )

    // Initially both servers should be visible
    expect(screen.getByText('filesystem')).toBeInTheDocument()
    expect(screen.getByText('database')).toBeInTheDocument()

    // Search for 'file'
    const searchInput = screen.getByPlaceholderText('Search installed tools...')
    fireEvent.change(searchInput, { target: { value: 'file' } })

    // Only filesystem should be visible now
    expect(screen.getByText('filesystem')).toBeInTheDocument()
    expect(screen.queryByText('database')).not.toBeInTheDocument()
  })

  it('should clear all tools and prompts when Clear All button is clicked', () => {
    const testTools = [{
      server: 'test_server',
      description: 'Test server',
      tools: ['tool1', 'tool2'],
      tools_detailed: [],
      tool_count: 2,
      prompts: [],
      prompt_count: 0
    }]

    useChat.mockReturnValue({
      ...defaultChatContext,
      tools: testTools,
      selectedTools: new Set(['test_server_tool1']),
      clearToolsAndPrompts: mockClearToolsAndPrompts
    })

    useMarketplace.mockReturnValue({
      ...defaultMarketplaceContext,
      getFilteredTools: vi.fn(() => testTools)
    })

    render(
      <BrowserRouter>
        <ToolsPanel isOpen={true} onClose={vi.fn()} />
      </BrowserRouter>
    )

    // Find and click the Clear All button
    const clearButton = screen.getByRole('button', { name: 'Clear All' })
    fireEvent.click(clearButton)

    // Verify clearToolsAndPrompts was called
    expect(mockClearToolsAndPrompts).toHaveBeenCalled()
  })

  it('should toggle Required Tool Usage setting', () => {
    const testTools = [{
      server: 'test_server',
      description: 'Test server',
      tools: ['tool1'],
      tools_detailed: [],
      tool_count: 1,
      prompts: [],
      prompt_count: 0
    }]

    useChat.mockReturnValue({
      ...defaultChatContext,
      tools: testTools,
      toolChoiceRequired: false,
      setToolChoiceRequired: mockSetToolChoiceRequired
    })

    useMarketplace.mockReturnValue({
      ...defaultMarketplaceContext,
      getFilteredTools: vi.fn(() => testTools)
    })

    render(
      <BrowserRouter>
        <ToolsPanel isOpen={true} onClose={vi.fn()} />
      </BrowserRouter>
    )

    // Find the toggle switch in the Required Tool Usage section
    const toggleButtons = screen.getAllByRole('button')
    const toggleSwitch = toggleButtons.find(button =>
      button.className.includes('relative inline-flex')
    )

    expect(toggleSwitch).toBeDefined()
    fireEvent.click(toggleSwitch)

    // Verify setToolChoiceRequired was called with true
    expect(mockSetToolChoiceRequired).toHaveBeenCalledWith(true)
  })

  it('should enable all tools and prompts when Enable All button is clicked', () => {
    const mockAddTools = vi.fn()
    const mockAddPrompts = vi.fn()

    const testToolsWithPrompts = [{
      server: 'test_server',
      description: 'Test server with tools and prompts',
      tools: ['tool1', 'tool2', 'tool3'],
      tools_detailed: [],
      tool_count: 3,
      prompts: [
        { name: 'prompt1', description: 'First prompt' },
        { name: 'prompt2', description: 'Second prompt' },
        { name: 'prompt3', description: 'Third prompt' }
      ],
      prompt_count: 3
    }]

    useChat.mockReturnValue({
      ...defaultChatContext,
      tools: testToolsWithPrompts,
      selectedTools: new Set(),
      selectedPrompts: new Set(),
      addTools: mockAddTools,
      addPrompts: mockAddPrompts
    })

    useMarketplace.mockReturnValue({
      ...defaultMarketplaceContext,
      getFilteredTools: vi.fn(() => testToolsWithPrompts),
      getFilteredPrompts: vi.fn(() => testToolsWithPrompts)
    })

    render(
      <BrowserRouter>
        <ToolsPanel isOpen={true} onClose={vi.fn()} />
      </BrowserRouter>
    )

    // Find and click the Enable All button
    const enableAllButton = screen.getByRole('button', { name: 'Enable All' })
    fireEvent.click(enableAllButton)

    // Verify all tools were added
    expect(mockAddTools).toHaveBeenCalledWith([
      'test_server_tool1',
      'test_server_tool2',
      'test_server_tool3'
    ])

    // Verify all prompts were added
    expect(mockAddPrompts).toHaveBeenCalledWith([
      'test_server_prompt1',
      'test_server_prompt2',
      'test_server_prompt3'
    ])
  })

  it('should disable all tools and prompts when All On button is clicked', () => {
    const mockRemoveTools = vi.fn()
    const mockRemovePrompts = vi.fn()

    const testToolsWithPrompts = [{
      server: 'test_server',
      description: 'Test server with tools and prompts',
      tools: ['tool1', 'tool2'],
      tools_detailed: [],
      tool_count: 2,
      prompts: [
        { name: 'prompt1', description: 'First prompt' },
        { name: 'prompt2', description: 'Second prompt' }
      ],
      prompt_count: 2
    }]

    // All tools and prompts are already selected
    const selectedTools = new Set(['test_server_tool1', 'test_server_tool2'])
    const selectedPrompts = new Set(['test_server_prompt1', 'test_server_prompt2'])

    useChat.mockReturnValue({
      ...defaultChatContext,
      tools: testToolsWithPrompts,
      selectedTools,
      selectedPrompts,
      removeTools: mockRemoveTools,
      removePrompts: mockRemovePrompts
    })

    useMarketplace.mockReturnValue({
      ...defaultMarketplaceContext,
      getFilteredTools: vi.fn(() => testToolsWithPrompts),
      getFilteredPrompts: vi.fn(() => testToolsWithPrompts)
    })

    render(
      <BrowserRouter>
        <ToolsPanel isOpen={true} onClose={vi.fn()} />
      </BrowserRouter>
    )

    // Find and click the All On button (all are selected)
    const allOnButton = screen.getByRole('button', { name: 'All On' })
    fireEvent.click(allOnButton)

    // Verify all tools were removed
    expect(mockRemoveTools).toHaveBeenCalledWith([
      'test_server_tool1',
      'test_server_tool2'
    ])

    // Verify all prompts were removed
    expect(mockRemovePrompts).toHaveBeenCalledWith([
      'test_server_prompt1',
      'test_server_prompt2'
    ])
  })
})

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
})

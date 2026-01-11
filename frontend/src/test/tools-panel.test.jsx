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

    const mockAddTools = vi.fn()

    useChat.mockReturnValue({
      ...defaultChatContext,
      tools: testTools,
      addTools: mockAddTools
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

    // Click save button to persist the change
    const saveButton = screen.getByRole('button', { name: /Save Changes/i })
    fireEvent.click(saveButton)

    // Verify addTools was called with correct key
    expect(mockAddTools).toHaveBeenCalledWith(['test_server_fetch'])
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
    const mockRemoveTools = vi.fn()
    const mockRemovePrompts = vi.fn()
    const testTools = [{
      server: 'test_server',
      description: 'Test server',
      tools: ['tool1', 'tool2'],
      tools_detailed: [],
      tool_count: 2,
      prompts: [{name: 'prompt1', description: 'Test prompt'}],
      prompt_count: 1
    }]

    useChat.mockReturnValue({
      ...defaultChatContext,
      tools: testTools,
      selectedTools: new Set(['test_server_tool1', 'test_server_tool2']),
      selectedPrompts: new Set(['test_server_prompt1']),
      removeTools: mockRemoveTools,
      removePrompts: mockRemovePrompts
    })

    useMarketplace.mockReturnValue({
      ...defaultMarketplaceContext,
      getFilteredTools: vi.fn(() => testTools),
      getFilteredPrompts: vi.fn(() => [])
    })

    render(
      <BrowserRouter>
        <ToolsPanel isOpen={true} onClose={vi.fn()} />
      </BrowserRouter>
    )

    // Initially save button should be disabled
    const saveButton = screen.getByRole('button', { name: /Save Changes/i })
    expect(saveButton).toBeDisabled()

    // Find and click the Clear All button
    const clearButton = screen.getByRole('button', { name: 'Clear All' })
    fireEvent.click(clearButton)

    // Save button should now be enabled since we made changes
    expect(saveButton).not.toBeDisabled()
    
    // Click save to persist the changes
    fireEvent.click(saveButton)

    // Verify removeTools and removePrompts were called with all selected items
    expect(mockRemoveTools).toHaveBeenCalledWith(['test_server_tool1', 'test_server_tool2'])
    expect(mockRemovePrompts).toHaveBeenCalledWith(['test_server_prompt1'])
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

    // Click save button to persist the change
    const saveButton = screen.getByRole('button', { name: /Save Changes/i })
    fireEvent.click(saveButton)

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

    // Click save button to persist the changes
    const saveButton = screen.getByRole('button', { name: /Save Changes/i })
    fireEvent.click(saveButton)

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

    // Click save button to persist the changes
    const saveButton = screen.getByRole('button', { name: /Save Changes/i })
    fireEvent.click(saveButton)

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

  it('should show save button disabled when no changes are made', () => {
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

    // Find the Save Changes button
    const saveButton = screen.getByRole('button', { name: /Save Changes/i })
    
    // Should be disabled initially
    expect(saveButton).toBeDisabled()
  })

  it('should enable save button when changes are made', () => {
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
      selectedTools: new Set()
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

    // Initially save button should be disabled
    const saveButton = screen.getByRole('button', { name: /Save Changes/i })
    expect(saveButton).toBeDisabled()

    // Click a tool to make a change
    const toolButton = screen.getByRole('button', { name: 'tool1' })
    fireEvent.click(toolButton)

    // Save button should now be enabled
    expect(saveButton).not.toBeDisabled()
  })

  it('should save changes when save button is clicked', () => {
    const mockAddTools = vi.fn()
    const mockOnClose = vi.fn()

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
      selectedTools: new Set(),
      addTools: mockAddTools
    })

    useMarketplace.mockReturnValue({
      ...defaultMarketplaceContext,
      getFilteredTools: vi.fn(() => testTools)
    })

    render(
      <BrowserRouter>
        <ToolsPanel isOpen={true} onClose={mockOnClose} />
      </BrowserRouter>
    )

    // Click a tool to make a change
    const toolButton = screen.getByRole('button', { name: 'tool1' })
    fireEvent.click(toolButton)

    // Click save button
    const saveButton = screen.getByRole('button', { name: /Save Changes/i })
    fireEvent.click(saveButton)

    // Verify addTools was called with the selected tool
    expect(mockAddTools).toHaveBeenCalledWith(['test_server_tool1'])
    
    // Verify onClose was called
    expect(mockOnClose).toHaveBeenCalled()
  })

  it('should cancel changes when cancel button is clicked', () => {
    const mockAddTools = vi.fn()
    const mockOnClose = vi.fn()

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
      selectedTools: new Set()
    })

    useMarketplace.mockReturnValue({
      ...defaultMarketplaceContext,
      getFilteredTools: vi.fn(() => testTools)
    })

    render(
      <BrowserRouter>
        <ToolsPanel isOpen={true} onClose={mockOnClose} />
      </BrowserRouter>
    )

    // Click a tool to make a change
    const toolButton = screen.getByRole('button', { name: 'tool1' })
    fireEvent.click(toolButton)

    // Click cancel button
    const cancelButton = screen.getByRole('button', { name: 'Cancel' })
    fireEvent.click(cancelButton)

    // Verify addTools was NOT called (changes were cancelled)
    expect(mockAddTools).not.toHaveBeenCalled()
    
    // Verify onClose was called
    expect(mockOnClose).toHaveBeenCalled()
  })
})

describe('ToolsPanel - Custom Information Display', () => {
  const defaultChatContext = {
    selectedTools: new Set(),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    addTools: vi.fn(),
    addPrompts: vi.fn(),
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
    useChat.mockReturnValue(defaultChatContext)
    useMarketplace.mockReturnValue(defaultMarketplaceContext)
  })

  it('should display author information when present', () => {
    const testTools = [{
      server: 'calculator',
      description: 'Mathematical calculator',
      short_description: 'Math operations',
      author: 'Chat UI Team',
      help_email: 'support@example.com',
      tools: ['evaluate'],
      tools_detailed: [],
      tool_count: 1,
      prompts: [],
      prompt_count: 0
    }]

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

    // Verify author is displayed
    expect(screen.getByText('Chat UI Team')).toBeInTheDocument()
  })

  it('should display help email as a mailto link when present', () => {
    const testTools = [{
      server: 'calculator',
      description: 'Mathematical calculator',
      short_description: 'Math operations',
      author: 'Chat UI Team',
      help_email: 'support@example.com',
      tools: ['evaluate'],
      tools_detailed: [],
      tool_count: 1,
      prompts: [],
      prompt_count: 0
    }]

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

    // Verify help email is displayed as a link
    const emailLink = screen.getByText('support@example.com')
    expect(emailLink).toBeInTheDocument()
    expect(emailLink.tagName).toBe('A')
    expect(emailLink).toHaveAttribute('href', 'mailto:support@example.com')
  })

  it('should display short description by default', () => {
    const testTools = [{
      server: 'calculator',
      description: 'Evaluate mathematical expressions, perform calculations with basic arithmetic, trigonometry, and logarithms',
      short_description: 'Mathematical calculator',
      author: 'Chat UI Team',
      help_email: 'support@example.com',
      tools: ['evaluate'],
      tools_detailed: [],
      tool_count: 1,
      prompts: [],
      prompt_count: 0
    }]

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

    // Verify short description is displayed
    expect(screen.getByText('Mathematical calculator')).toBeInTheDocument()
    
    // Verify full description is not initially displayed
    expect(screen.queryByText(/Evaluate mathematical expressions, perform calculations/)).not.toBeInTheDocument()
  })

  it('should show expandable description button when description differs from short_description', () => {
    const testTools = [{
      server: 'calculator',
      description: 'Evaluate mathematical expressions, perform calculations with basic arithmetic, trigonometry, and logarithms',
      short_description: 'Mathematical calculator',
      author: 'Chat UI Team',
      help_email: 'support@example.com',
      tools: ['evaluate'],
      tools_detailed: [],
      tool_count: 1,
      prompts: [],
      prompt_count: 0
    }]

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

    // Verify "Show more details..." button is present
    expect(screen.getByRole('button', { name: 'Show more details...' })).toBeInTheDocument()
  })

  it('should expand and collapse full description when toggle button is clicked', () => {
    const testTools = [{
      server: 'calculator',
      description: 'Evaluate mathematical expressions, perform calculations with basic arithmetic, trigonometry, and logarithms',
      short_description: 'Mathematical calculator',
      author: 'Chat UI Team',
      help_email: 'support@example.com',
      tools: ['evaluate'],
      tools_detailed: [],
      tool_count: 1,
      prompts: [],
      prompt_count: 0
    }]

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

    // Click "Show more details..." button
    const showMoreButton = screen.getByRole('button', { name: 'Show more details...' })
    fireEvent.click(showMoreButton)

    // Verify full description is now displayed
    expect(screen.getByText(/Evaluate mathematical expressions, perform calculations/)).toBeInTheDocument()
    
    // Verify button text changed to "Show less"
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show more details...' })).not.toBeInTheDocument()

    // Click "Show less" button
    const showLessButton = screen.getByRole('button', { name: 'Show less' })
    fireEvent.click(showLessButton)

    // Verify full description is hidden again
    expect(screen.queryByText(/Evaluate mathematical expressions, perform calculations/)).not.toBeInTheDocument()
    
    // Verify button text changed back to "Show more details..."
    expect(screen.getByRole('button', { name: 'Show more details...' })).toBeInTheDocument()
  })

  it('should not show expand button when description equals short_description', () => {
    const testTools = [{
      server: 'simple',
      description: 'Simple tool',
      short_description: 'Simple tool',
      author: 'Test Team',
      help_email: 'test@example.com',
      tools: ['tool1'],
      tools_detailed: [],
      tool_count: 1,
      prompts: [],
      prompt_count: 0
    }]

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

    // Verify no "Show more details..." button when descriptions match
    expect(screen.queryByRole('button', { name: 'Show more details...' })).not.toBeInTheDocument()
  })

  it('should handle tools with only description (no short_description)', () => {
    const testTools = [{
      server: 'legacy',
      description: 'Legacy tool description',
      tools: ['tool1'],
      tools_detailed: [],
      tool_count: 1,
      prompts: [],
      prompt_count: 0
    }]

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

    // Verify description is displayed when short_description is missing
    expect(screen.getByText('Legacy tool description')).toBeInTheDocument()
  })

  it('should preserve custom fields when merging tools and prompts', () => {
    const testTools = [{
      server: 'shared_server',
      description: 'Server with tools',
      short_description: 'Shared server',
      author: 'Tool Team',
      help_email: 'tools@example.com',
      tools: ['tool1'],
      tools_detailed: [],
      tool_count: 1,
      prompts: [],
      prompt_count: 0
    }]

    const testPrompts = [{
      server: 'shared_server',
      description: 'Server with prompts',
      short_description: 'Shared server',
      author: 'Tool Team',
      help_email: 'tools@example.com',
      prompts: [{ name: 'prompt1', description: 'Test prompt' }],
      prompt_count: 1
    }]

    useChat.mockReturnValue({
      ...defaultChatContext,
      tools: testTools,
      prompts: testPrompts
    })

    useMarketplace.mockReturnValue({
      ...defaultMarketplaceContext,
      getFilteredTools: vi.fn(() => testTools),
      getFilteredPrompts: vi.fn(() => testPrompts)
    })

    render(
      <BrowserRouter>
        <ToolsPanel isOpen={true} onClose={vi.fn()} />
      </BrowserRouter>
    )

    // Verify custom fields are displayed (should only appear once since server is merged)
    expect(screen.getByText('Tool Team')).toBeInTheDocument()
    expect(screen.getByText('tools@example.com')).toBeInTheDocument()
    expect(screen.getByText('Shared server')).toBeInTheDocument()
  })
})

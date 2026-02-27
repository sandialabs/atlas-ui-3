import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ToolsPanel from '../components/ToolsPanel'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'

// Mock the contexts
vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext')

const mockTools = [
  {
    server: 'test_server',
    description: 'Test server for tools',
    tools: ['tool1', 'tool2'],
    tools_detailed: [
      { name: 'tool1', description: 'Test tool 1' },
      { name: 'tool2', description: 'Test tool 2' }
    ],
    tool_count: 2,
    prompts: [
      { name: 'prompt1', description: 'Test prompt 1' },
      { name: 'prompt2', description: 'Test prompt 2' }
    ],
    prompt_count: 2
  }
]

const TestWrapper = ({ children }) => (
  <BrowserRouter>
    {children}
  </BrowserRouter>
)

describe('ToolsPanel - Unsaved Changes Dialog', () => {
  let mockOnClose
  let mockToggleTool
  let mockAddTools
  let mockRemoveTools

  const defaultChatContext = {
    selectedTools: new Set(),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    addTools: vi.fn(),
    removeTools: vi.fn(),
    addPrompts: vi.fn(),
    removePrompts: vi.fn(),
    clearToolsAndPrompts: vi.fn(),
    toolChoiceRequired: false,
    setToolChoiceRequired: vi.fn(),
    complianceLevelFilter: null,
    tools: mockTools,
    prompts: [],
    features: { tools: true }
  }

  const defaultMarketplaceContext = {
    getComplianceFilteredTools: vi.fn(() => mockTools),
    getComplianceFilteredPrompts: vi.fn(() => []),
    getFilteredTools: vi.fn(() => mockTools),
    getFilteredPrompts: vi.fn(() => [])
  }

  beforeEach(() => {
    mockOnClose = vi.fn()
    mockToggleTool = vi.fn()
    mockAddTools = vi.fn()
    mockRemoveTools = vi.fn()

    // Reset mocks
    vi.clearAllMocks()

    // Mock the contexts
    useChat.mockReturnValue({
      ...defaultChatContext,
      toggleTool: mockToggleTool,
      addTools: mockAddTools,
      removeTools: mockRemoveTools
    })

    useMarketplace.mockReturnValue(defaultMarketplaceContext)
  })

  it('should show unsaved changes dialog when clicking outside with unsaved changes', async () => {
    render(
      <TestWrapper>
        <ToolsPanel isOpen={true} onClose={mockOnClose} />
      </TestWrapper>
    )

    // Make a change to create unsaved changes
    const toolButton = screen.getByText('tool1')
    fireEvent.click(toolButton)

    // Click outside the panel (on the overlay)
    const overlay = screen.getByText('Tools & Integrations').closest('.fixed')
    fireEvent.click(overlay)

    // Should show the unsaved changes dialog
    await waitFor(() => {
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument()
      expect(screen.getByText('You have unsaved changes to your tools and integrations. What would you like to do?')).toBeInTheDocument()
    })

    // Should not have called onClose yet
    expect(mockOnClose).not.toHaveBeenCalled()
  })

  it('should show unsaved changes dialog when clicking X button with unsaved changes', async () => {
    render(
      <TestWrapper>
        <ToolsPanel isOpen={true} onClose={mockOnClose} />
      </TestWrapper>
    )

    // Make a change to create unsaved changes
    const toolButton = screen.getByText('tool1')
    fireEvent.click(toolButton)

    // Click the X button (find it by the X icon)
    const closeButton = screen.getByText('Tools & Integrations').parentElement.querySelector('button')
    fireEvent.click(closeButton)

    // Should show the unsaved changes dialog
    await waitFor(() => {
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument()
    })

    // Should not have called onClose yet
    expect(mockOnClose).not.toHaveBeenCalled()
  })

  it('should close directly when no unsaved changes', async () => {
    render(
      <TestWrapper>
        <ToolsPanel isOpen={true} onClose={mockOnClose} />
      </TestWrapper>
    )

    // Click outside without making changes
    const overlay = screen.getByText('Tools & Integrations').closest('.fixed')
    fireEvent.click(overlay)

    // Should call onClose directly without showing dialog
    expect(mockOnClose).toHaveBeenCalled()
    expect(screen.queryByText('Unsaved Changes')).not.toBeInTheDocument()
  })

  it('should save and close when clicking Save Changes in dialog', async () => {
    render(
      <TestWrapper>
        <ToolsPanel isOpen={true} onClose={mockOnClose} />
      </TestWrapper>
    )

    // Make a change
    const toolButton = screen.getByText('tool1')
    fireEvent.click(toolButton)

    // Try to close
    const overlay = screen.getByText('Tools & Integrations').closest('.fixed')
    fireEvent.click(overlay)

    // Wait for dialog to appear
    await waitFor(() => {
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument()
    })

    // Click Save Changes in the dialog (not the main panel)
    const allSaveButtons = screen.getAllByText('Save Changes')
    // The dialog button should be the second one (index 1)
    const dialogSaveButton = allSaveButtons[1]
    fireEvent.click(dialogSaveButton)

    // Should close the panel
    await waitFor(() => {
      expect(mockOnClose).toHaveBeenCalled()
    })
  })

  it('should discard and close when clicking Discard Changes in dialog', async () => {
    render(
      <TestWrapper>
        <ToolsPanel isOpen={true} onClose={mockOnClose} />
      </TestWrapper>
    )

    // Make a change
    const toolButton = screen.getByText('tool1')
    fireEvent.click(toolButton)

    // Try to close
    const overlay = screen.getByText('Tools & Integrations').closest('.fixed')
    fireEvent.click(overlay)

    // Wait for dialog to appear
    await waitFor(() => {
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument()
    })

    // Click Discard Changes
    const discardButton = screen.getByText('Discard Changes')
    fireEvent.click(discardButton)

    // Should close the panel
    await waitFor(() => {
      expect(mockOnClose).toHaveBeenCalled()
    })
  })

  it('should cancel and stay open when clicking Cancel in dialog', async () => {
    render(
      <TestWrapper>
        <ToolsPanel isOpen={true} onClose={mockOnClose} />
      </TestWrapper>
    )

    // Make a change
    const toolButton = screen.getByText('tool1')
    fireEvent.click(toolButton)

    // Try to close
    const overlay = screen.getByText('Tools & Integrations').closest('.fixed')
    fireEvent.click(overlay)

    // Wait for dialog to appear
    await waitFor(() => {
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument()
    })

    // Click Cancel in the dialog (not the main panel)
    const allCancelButtons = screen.getAllByText('Cancel')
    // The dialog button should be the second one (index 1)
    const dialogCancelButton = allCancelButtons[1]
    fireEvent.click(dialogCancelButton)

    // Should not close the panel
    expect(mockOnClose).not.toHaveBeenCalled()
    
    // Wait a bit to ensure any state changes have time to process
    await new Promise(resolve => setTimeout(resolve, 100))
    
    // Dialog should be hidden
    expect(screen.queryByText('Unsaved Changes')).not.toBeInTheDocument()
  })
})
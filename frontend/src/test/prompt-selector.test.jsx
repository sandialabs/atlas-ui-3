/**
 * PromptSelector Component Tests
 * Tests the custom prompt selection dropdown behavior
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import PromptSelector from '../components/PromptSelector'
import { useChat } from '../contexts/ChatContext'

// Mock the ChatContext
vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn()
}))

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
  ChevronDown: () => <span data-testid="chevron-down">v</span>,
  Sparkles: () => <span data-testid="sparkles">*</span>
}))

describe('PromptSelector', () => {
  const mockTogglePrompt = vi.fn()
  const mockMakePromptActive = vi.fn()
  const mockRemovePrompts = vi.fn()

  const defaultChatContext = {
    prompts: [],
    selectedPrompts: new Set(),
    togglePrompt: mockTogglePrompt,
    makePromptActive: mockMakePromptActive,
    removePrompts: mockRemovePrompts
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useChat.mockReturnValue(defaultChatContext)
  })

  describe('Default Prompt Behavior', () => {
    it('should display "Default Prompt" when no prompts are selected', () => {
      render(<PromptSelector />)
      expect(screen.getByText('Default Prompt')).toBeInTheDocument()
    })

    it('should show Default Prompt option in dropdown', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      expect(screen.getAllByText('Default Prompt').length).toBeGreaterThan(0)
      expect(screen.getByText('Use the standard system prompt without customization')).toBeInTheDocument()
    })

    it('should highlight Default Prompt when no prompts selected', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      const defaultOption = screen.getByText('Use the standard system prompt without customization').closest('button')
      expect(defaultOption).toHaveClass('bg-blue-900/30')
    })

    it('should show checkmark on Default Prompt when active', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      expect(screen.getByText('✓')).toBeInTheDocument()
      expect(screen.getByText('(active)')).toBeInTheDocument()
    })
  })

  describe('Single Prompt Selection', () => {
    const singlePromptContext = {
      ...defaultChatContext,
      prompts: [{
        server: 'test-server',
        prompts: [{
          name: 'test_prompt',
          description: 'A test prompt'
        }]
      }],
      selectedPrompts: new Set(['test-server_test_prompt'])
    }

    beforeEach(() => {
      useChat.mockReturnValue(singlePromptContext)
    })

    it('should display active prompt name in button', () => {
      render(<PromptSelector />)
      expect(screen.getByText('test_prompt')).toBeInTheDocument()
    })

    it('should show selected prompt in dropdown with checkmark', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      const promptText = screen.getAllByText('test_prompt')[1] // Second instance in dropdown
      expect(promptText).toBeInTheDocument()
      expect(screen.getByText('✓')).toBeInTheDocument()
      expect(screen.getByText('(active)')).toBeInTheDocument()
    })

    it('should highlight active prompt with blue background', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      const promptButton = screen.getByText('A test prompt').closest('button')
      expect(promptButton).toHaveClass('bg-blue-900/30')
    })

    it('should show prompt description', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      expect(screen.getByText('A test prompt')).toBeInTheDocument()
    })

    it('should show server name', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      expect(screen.getByText('from test-server')).toBeInTheDocument()
    })

    it('should call removePrompts when clicking Default Prompt', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      const defaultButton = screen.getByText('Use the standard system prompt without customization').closest('button')
      fireEvent.click(defaultButton)

      expect(mockRemovePrompts).toHaveBeenCalledWith(['test-server_test_prompt'])
    })
  })

  describe('Multiple Prompt Selection', () => {
    const multiPromptContext = {
      ...defaultChatContext,
      prompts: [{
        server: 'test-server',
        prompts: [
          { name: 'prompt_one', description: 'First prompt' },
          { name: 'prompt_two', description: 'Second prompt' },
          { name: 'prompt_three', description: 'Third prompt' }
        ]
      }],
      selectedPrompts: new Set([
        'test-server_prompt_one',
        'test-server_prompt_two',
        'test-server_prompt_three'
      ])
    }

    beforeEach(() => {
      useChat.mockReturnValue(multiPromptContext)
    })

    it('should display first prompt as active in button', () => {
      render(<PromptSelector />)
      expect(screen.getByText('prompt_one')).toBeInTheDocument()
    })

    it('should show all selected prompts in dropdown', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      expect(screen.getByText('First prompt')).toBeInTheDocument()
      expect(screen.getByText('Second prompt')).toBeInTheDocument()
      expect(screen.getByText('Third prompt')).toBeInTheDocument()
    })

    it('should only highlight first prompt as active', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      const promptOne = screen.getByText('First prompt').closest('button')
      const promptTwo = screen.getByText('Second prompt').closest('button')
      
      expect(promptOne).toHaveClass('bg-blue-900/30')
      expect(promptTwo).not.toHaveClass('bg-blue-900/30')
    })

    it('should show Clear All button with count', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      expect(screen.getByText('Clear All (3)')).toBeInTheDocument()
    })

    it('should call removePrompts with all prompts when clicking Clear All', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      const clearButton = screen.getByText('Clear All (3)').closest('button')
      fireEvent.click(clearButton)

      expect(mockRemovePrompts).toHaveBeenCalledWith([
        'test-server_prompt_one',
        'test-server_prompt_two',
        'test-server_prompt_three'
      ])
    })

    it('should call makePromptActive when clicking a different prompt', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      const promptTwo = screen.getByText('Second prompt').closest('button')
      fireEvent.click(promptTwo)

      expect(mockMakePromptActive).toHaveBeenCalledWith('test-server_prompt_two')
    })
  })

  describe('Dropdown Interaction', () => {
    it('should open dropdown when button is clicked', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      expect(screen.getByText('Custom Prompts')).toBeInTheDocument()
      expect(screen.getByText('Select prompts to customize AI behavior')).toBeInTheDocument()
    })

    it('should close dropdown when clicking outside', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)
      
      expect(screen.getByText('Custom Prompts')).toBeInTheDocument()

      // Click outside
      fireEvent.mouseDown(document.body)
      
      expect(screen.queryByText('Select prompts to customize AI behavior')).not.toBeInTheDocument()
    })

    it('should toggle dropdown open/close', () => {
      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      
      // Open
      fireEvent.click(button)
      expect(screen.getByText('Custom Prompts')).toBeInTheDocument()

      // Close
      fireEvent.click(button)
      expect(screen.queryByText('Select prompts to customize AI behavior')).not.toBeInTheDocument()
    })
  })

  describe('Edge Cases', () => {
    it('should handle empty prompts array', () => {
      useChat.mockReturnValue({
        ...defaultChatContext,
        prompts: []
      })

      render(<PromptSelector />)
      expect(screen.getByText('Default Prompt')).toBeInTheDocument()
    })

    it('should handle prompts without descriptions', () => {
      useChat.mockReturnValue({
        ...defaultChatContext,
        prompts: [{
          server: 'test',
          prompts: [{ name: 'no_desc_prompt' }]
        }],
        selectedPrompts: new Set(['test_no_desc_prompt'])
      })

      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      // Should find multiple instances (button label and dropdown item)
      const items = screen.getAllByText('no_desc_prompt')
      expect(items.length).toBeGreaterThan(0)
    })

    it('should not show Clear All with only one prompt', () => {
      useChat.mockReturnValue({
        ...defaultChatContext,
        prompts: [{
          server: 'test',
          prompts: [{ name: 'single', description: 'Single prompt' }]
        }],
        selectedPrompts: new Set(['test_single'])
      })

      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      expect(screen.queryByText(/Clear All/)).not.toBeInTheDocument()
    })

    it('should handle missing makePromptActive function', () => {
      useChat.mockReturnValue({
        ...defaultChatContext,
        prompts: [{
          server: 'test',
          prompts: [{ name: 'test', description: 'Test' }]
        }],
        selectedPrompts: new Set(['test_test']),
        makePromptActive: undefined
      })

      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      const promptButton = screen.getByText('Test').closest('button')
      
      // Should not throw error
      expect(() => fireEvent.click(promptButton)).not.toThrow()
    })

    it('should handle missing removePrompts function', () => {
      useChat.mockReturnValue({
        ...defaultChatContext,
        prompts: [{
          server: 'test',
          prompts: [{ name: 'test', description: 'Test' }]
        }],
        selectedPrompts: new Set(['test_test']),
        removePrompts: undefined
      })

      render(<PromptSelector />)
      
      const button = screen.getByRole('button')
      fireEvent.click(button)

      const defaultButton = screen.getByText('Use the standard system prompt without customization').closest('button')
      
      // Should not throw error
      expect(() => fireEvent.click(defaultButton)).not.toThrow()
    })
  })
})

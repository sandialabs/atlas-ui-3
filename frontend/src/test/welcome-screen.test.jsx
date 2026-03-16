/**
 * WelcomeScreen Component Tests
 * Tests the welcome/onboarding screen rendered when no messages exist.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import WelcomeScreen from '../components/WelcomeScreen'
import { useChat } from '../contexts/ChatContext'

// Mock the ChatContext
vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn()
}))

// Mock AnimatedLogo since it's conditionally imported
vi.mock('../components/AnimatedLogo', () => ({
  default: ({ appName }) => <div data-testid="animated-logo">{appName}</div>
}))

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
  Wrench: () => <span data-testid="icon-wrench" />,
  FolderOpen: () => <span data-testid="icon-folder" />,
  Save: () => <span data-testid="icon-save" />,
  Bot: () => <span data-testid="icon-bot" />,
  LayoutPanelLeft: () => <span data-testid="icon-canvas" />,
  MessageSquare: () => <span data-testid="icon-message" />,
}))

const defaultChatContext = {
  appName: 'Atlas',
  features: {
    tools: true,
    files_panel: true,
    rag: true,
  },
  agentModeAvailable: true,
}

describe('WelcomeScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useChat.mockReturnValue(defaultChatContext)
  })

  describe('Introductory text', () => {
    it('should show a getting-started instruction mentioning the app name', () => {
      render(<WelcomeScreen />)
      expect(screen.getByText(/Select a model/i)).toBeInTheDocument()
      expect(screen.getByText(/Atlas/)).toBeInTheDocument()
    })
  })

  describe('Capability cards', () => {
    it('should render the capability cards container', () => {
      render(<WelcomeScreen />)
      expect(screen.getByTestId('capability-cards')).toBeInTheDocument()
    })

    it('should show Model Selection card', () => {
      render(<WelcomeScreen />)
      expect(screen.getByText('Model Selection')).toBeInTheDocument()
    })

    it('should show Tools card when tools feature is enabled', () => {
      render(<WelcomeScreen />)
      expect(screen.getByText('Tools')).toBeInTheDocument()
    })

    it('should show Files card when files_panel feature is enabled', () => {
      render(<WelcomeScreen />)
      expect(screen.getByText('Files')).toBeInTheDocument()
    })

    it('should show Canvas card', () => {
      render(<WelcomeScreen />)
      expect(screen.getByText('Canvas')).toBeInTheDocument()
    })

    it('should show Save Mode card', () => {
      render(<WelcomeScreen />)
      expect(screen.getByText('Save Mode')).toBeInTheDocument()
    })

    it('should show Agent Mode card when agentModeAvailable is true', () => {
      render(<WelcomeScreen />)
      expect(screen.getByText('Agent Mode')).toBeInTheDocument()
    })

    it('should hide Agent Mode card when agentModeAvailable is false', () => {
      useChat.mockReturnValue({ ...defaultChatContext, agentModeAvailable: false })
      render(<WelcomeScreen />)
      expect(screen.queryByText('Agent Mode')).not.toBeInTheDocument()
    })

    it('should hide Tools card when tools feature is disabled', () => {
      useChat.mockReturnValue({
        ...defaultChatContext,
        features: { ...defaultChatContext.features, tools: false }
      })
      render(<WelcomeScreen />)
      expect(screen.queryByText('Tools')).not.toBeInTheDocument()
    })

    it('should hide Files card when files_panel feature is disabled', () => {
      useChat.mockReturnValue({
        ...defaultChatContext,
        features: { ...defaultChatContext.features, files_panel: false }
      })
      render(<WelcomeScreen />)
      expect(screen.queryByText('Files')).not.toBeInTheDocument()
    })
  })

  describe('Suggested prompts', () => {
    it('should not render suggested prompts section when onSuggestPrompt is not provided', () => {
      render(<WelcomeScreen />)
      expect(screen.queryByTestId('suggested-prompts')).not.toBeInTheDocument()
    })

    it('should render suggested prompts when onSuggestPrompt is provided', () => {
      const onSuggestPrompt = vi.fn()
      render(<WelcomeScreen onSuggestPrompt={onSuggestPrompt} />)
      expect(screen.getByTestId('suggested-prompts')).toBeInTheDocument()
    })

    it('should display multiple suggested prompt buttons', () => {
      const onSuggestPrompt = vi.fn()
      render(<WelcomeScreen onSuggestPrompt={onSuggestPrompt} />)
      const buttons = screen.getAllByRole('button')
      expect(buttons.length).toBeGreaterThan(1)
    })

    it('should call onSuggestPrompt with the prompt text when clicked', () => {
      const onSuggestPrompt = vi.fn()
      render(<WelcomeScreen onSuggestPrompt={onSuggestPrompt} />)

      const firstButton = screen.getAllByRole('button')[0]
      fireEvent.click(firstButton)

      expect(onSuggestPrompt).toHaveBeenCalledTimes(1)
      expect(typeof onSuggestPrompt.mock.calls[0][0]).toBe('string')
      expect(onSuggestPrompt.mock.calls[0][0].length).toBeGreaterThan(0)
    })

    it('should pass the exact prompt text to onSuggestPrompt', () => {
      const onSuggestPrompt = vi.fn()
      render(<WelcomeScreen onSuggestPrompt={onSuggestPrompt} />)

      const promptButton = screen.getByText('What can you help me with?')
      fireEvent.click(promptButton)

      expect(onSuggestPrompt).toHaveBeenCalledWith('What can you help me with?')
    })
  })

  describe('Logo rendering', () => {
    it('should render a logo image (static logo)', () => {
      render(<WelcomeScreen />)
      const img = screen.getByAltText('Atlas Logo')
      expect(img).toBeInTheDocument()
    })
  })
})

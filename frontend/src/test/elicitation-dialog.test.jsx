/**
 * Tests for ElicitationDialog component
 * Tests user input collection for MCP tool elicitation
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ElicitationDialog from '../components/ElicitationDialog'
import { useChat } from '../contexts/ChatContext'

// Mock the ChatContext
vi.mock('../contexts/ChatContext')

describe('ElicitationDialog - Basic Rendering', () => {
  let mockSendMessage
  let mockSetPendingElicitation

  beforeEach(() => {
    mockSendMessage = vi.fn()
    mockSetPendingElicitation = vi.fn()

    useChat.mockReturnValue({
      sendMessage: mockSendMessage,
      setPendingElicitation: mockSetPendingElicitation
    })
  })

  it('should render with basic string input', () => {
    const elicitation = {
      elicitation_id: 'test-123',
      tool_call_id: 'call-456',
      tool_name: 'get_user_name',
      message: "What's your name?",
      response_schema: {
        type: 'object',
        properties: {
          value: { type: 'string' }
        }
      }
    }

    render(<ElicitationDialog elicitation={elicitation} />)

    expect(screen.getByText('User Input Required')).toBeInTheDocument()
    expect(screen.getByText('Tool: get_user_name')).toBeInTheDocument()
    expect(screen.getByText("What's your name?")).toBeInTheDocument()
  })

  it('should render with number input', () => {
    const elicitation = {
      elicitation_id: 'test-123',
      tool_call_id: 'call-456',
      tool_name: 'pick_a_number',
      message: 'Pick a number between 1 and 100',
      response_schema: {
        type: 'object',
        properties: {
          value: { type: 'number' }
        }
      }
    }

    render(<ElicitationDialog elicitation={elicitation} />)

    expect(screen.getByText('Pick a number between 1 and 100')).toBeInTheDocument()
    const input = screen.getByRole('spinbutton')
    expect(input).toBeInTheDocument()
    expect(input.type).toBe('number')
  })
})

describe('ElicitationDialog - User Actions', () => {
  let mockSendMessage
  let mockSetPendingElicitation

  beforeEach(() => {
    mockSendMessage = vi.fn()
    mockSetPendingElicitation = vi.fn()

    useChat.mockReturnValue({
      sendMessage: mockSendMessage,
      setPendingElicitation: mockSetPendingElicitation
    })
  })

  it('should send accept response with user input', async () => {
    const elicitation = {
      elicitation_id: 'test-123',
      tool_call_id: 'call-456',
      tool_name: 'get_user_name',
      message: "What's your name?",
      response_schema: {
        type: 'object',
        properties: {
          value: { type: 'string' }
        },
        required: ['value']
      }
    }

    render(<ElicitationDialog elicitation={elicitation} />)

    // Enter text
    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'Alice' } })

    // Click Accept
    const acceptButton = screen.getByRole('button', { name: /accept/i })
    fireEvent.click(acceptButton)

    // Verify message sent
    expect(mockSendMessage).toHaveBeenCalledWith({
      type: 'elicitation_response',
      elicitation_id: 'test-123',
      action: 'accept',
      data: 'Alice'
    })

    // Verify dialog closed
    expect(mockSetPendingElicitation).toHaveBeenCalledWith(null)
  })

  it('should send decline response', () => {
    const elicitation = {
      elicitation_id: 'test-123',
      tool_call_id: 'call-456',
      tool_name: 'get_user_name',
      message: "What's your name?",
      response_schema: {
        type: 'object',
        properties: {
          value: { type: 'string' }
        }
      }
    }

    render(<ElicitationDialog elicitation={elicitation} />)

    // Click Decline
    const declineButton = screen.getByRole('button', { name: /decline/i })
    fireEvent.click(declineButton)

    // Verify message sent
    expect(mockSendMessage).toHaveBeenCalledWith({
      type: 'elicitation_response',
      elicitation_id: 'test-123',
      action: 'decline',
      data: null
    })

    // Verify dialog closed
    expect(mockSetPendingElicitation).toHaveBeenCalledWith(null)
  })

  it('should send cancel response on Cancel button', () => {
    const elicitation = {
      elicitation_id: 'test-123',
      tool_call_id: 'call-456',
      tool_name: 'get_user_name',
      message: "What's your name?",
      response_schema: {
        type: 'object',
        properties: {
          value: { type: 'string' }
        }
      }
    }

    render(<ElicitationDialog elicitation={elicitation} />)

    // Click Cancel (the main action button in footer, not the header X button)
    const cancelButtons = screen.getAllByRole('button', { name: /cancel/i })
    const cancelButton = cancelButtons[1] // The footer cancel button
    fireEvent.click(cancelButton)

    // Verify message sent
    expect(mockSendMessage).toHaveBeenCalledWith({
      type: 'elicitation_response',
      elicitation_id: 'test-123',
      action: 'cancel',
      data: null
    })

    // Verify dialog closed
    expect(mockSetPendingElicitation).toHaveBeenCalledWith(null)
  })
})

describe('ElicitationDialog - Input Validation', () => {
  let mockSendMessage
  let mockSetPendingElicitation

  beforeEach(() => {
    mockSendMessage = vi.fn()
    mockSetPendingElicitation = vi.fn()

    useChat.mockReturnValue({
      sendMessage: mockSendMessage,
      setPendingElicitation: mockSetPendingElicitation
    })
  })

  it('should disable Accept button when required field is empty', () => {
    const elicitation = {
      elicitation_id: 'test-123',
      tool_call_id: 'call-456',
      tool_name: 'get_user_name',
      message: "What's your name?",
      response_schema: {
        type: 'object',
        properties: {
          value: { type: 'string' }
        },
        required: ['value']
      }
    }

    render(<ElicitationDialog elicitation={elicitation} />)

    const acceptButton = screen.getByRole('button', { name: /accept/i })
    expect(acceptButton).toBeDisabled()
  })

  it('should enable Accept button when required field is filled', () => {
    const elicitation = {
      elicitation_id: 'test-123',
      tool_call_id: 'call-456',
      tool_name: 'get_user_name',
      message: "What's your name?",
      response_schema: {
        type: 'object',
        properties: {
          value: { type: 'string' }
        },
        required: ['value']
      }
    }

    render(<ElicitationDialog elicitation={elicitation} />)

    const input = screen.getByRole('textbox')
    const acceptButton = screen.getByRole('button', { name: /accept/i })

    // Initially disabled
    expect(acceptButton).toBeDisabled()

    // Fill input
    fireEvent.change(input, { target: { value: 'Alice' } })

    // Now enabled
    expect(acceptButton).toBeEnabled()
  })
})

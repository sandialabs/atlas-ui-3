/**
 * Tests for ElicitationDialog component
 * Tests user input collection for MCP tool elicitation
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ElicitationDialog from '../components/ElicitationDialog'
import { useChat } from '../contexts/ChatContext'

// Mock the ChatContext
vi.mock('../contexts/ChatContext')

describe('ElicitationDialog - Basic Rendering', () => {
  let mockSendMessage
  let mockSettleElicitation

  beforeEach(() => {
    mockSendMessage = vi.fn()
    mockSettleElicitation = vi.fn()

    useChat.mockReturnValue({
      sendMessage: mockSendMessage,
      settleElicitation: mockSettleElicitation
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
  let mockSettleElicitation

  beforeEach(() => {
    mockSendMessage = vi.fn(() => true)
    mockSettleElicitation = vi.fn()

    useChat.mockReturnValue({
      sendMessage: mockSendMessage,
      settleElicitation: mockSettleElicitation
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
    expect(mockSettleElicitation).toHaveBeenCalledWith('test-123')
  })

  it('keeps the prompt open and says so when the answer could not be sent', () => {
    mockSendMessage.mockReturnValue(false)
    const elicitation = {
      elicitation_id: 'test-123',
      tool_call_id: 'call-456',
      tool_name: 'get_user_name',
      message: "What's your name?",
      response_schema: { type: 'object', properties: { value: { type: 'string' } } }
    }

    render(<ElicitationDialog elicitation={elicitation} />)
    fireEvent.click(screen.getByRole('button', { name: /decline/i }))

    expect(mockSettleElicitation).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/not sent/i)

    // Back online: the same click goes through and closes the prompt.
    mockSendMessage.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: /decline/i }))
    expect(mockSettleElicitation).toHaveBeenCalledWith('test-123')
  })

  it('lets Cancel dismiss the prompt even while disconnected', () => {
    mockSendMessage.mockReturnValue(false)
    const elicitation = {
      elicitation_id: 'test-123',
      tool_call_id: 'call-456',
      tool_name: 'get_user_name',
      message: "What's your name?",
      response_schema: { type: 'object', properties: { value: { type: 'string' } } }
    }

    render(<ElicitationDialog elicitation={elicitation} />)
    fireEvent.click(screen.getAllByRole('button', { name: /^cancel$/i })[0])
    expect(mockSettleElicitation).toHaveBeenCalledWith('test-123')
  })

  it('clears the not-sent notice when a new prompt replaces the failed one', () => {
    mockSendMessage.mockReturnValue(false)
    const make = (id) => ({
      elicitation_id: id,
      tool_call_id: 'call-456',
      tool_name: 'get_user_name',
      message: 'q',
      response_schema: { type: 'object', properties: { value: { type: 'string' } } }
    })
    const { rerender } = render(<ElicitationDialog elicitation={make('a')} />)
    fireEvent.click(screen.getByRole('button', { name: /decline/i }))
    expect(screen.getByRole('alert')).toBeInTheDocument()
    rerender(<ElicitationDialog elicitation={make('b')} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
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
    expect(mockSettleElicitation).toHaveBeenCalledWith('test-123')
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
    expect(mockSettleElicitation).toHaveBeenCalledWith('test-123')
  })
})

describe('ElicitationDialog - Input Validation', () => {
  let mockSendMessage
  let mockSettleElicitation

  beforeEach(() => {
    mockSendMessage = vi.fn()
    mockSettleElicitation = vi.fn()

    useChat.mockReturnValue({
      sendMessage: mockSendMessage,
      settleElicitation: mockSettleElicitation
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

  it('should tab from input to Accept button first', async () => {
    const elicitation = {
      elicitation_id: 'test-123',
      tool_call_id: 'call-456',
      tool_name: 'pick_a_number',
      message: 'Pick a number between 1 and 100',
      response_schema: {
        type: 'object',
        properties: {
          value: { type: 'number' }
        },
        required: ['value']
      }
    }

    const user = userEvent.setup()
    render(<ElicitationDialog elicitation={elicitation} />)

    const input = screen.getByRole('spinbutton')
    const acceptButton = screen.getByRole('button', { name: /accept/i })

    // Fill required field so Accept is tabbable (enabled)
    await user.clear(input)
    await user.type(input, '42')
    expect(acceptButton).toBeEnabled()

    input.focus()
    expect(input).toHaveFocus()

    await user.tab()
    expect(acceptButton).toHaveFocus()
  })
})

describe('ElicitationDialog - Choice Fields', () => {
  let mockSendMessage
  let mockSettleElicitation

  beforeEach(() => {
    mockSendMessage = vi.fn(() => true)
    mockSettleElicitation = vi.fn()
    useChat.mockReturnValue({
      sendMessage: mockSendMessage,
      settleElicitation: mockSettleElicitation
    })
  })

  it('shows titled options and answers with the option value', () => {
    render(<ElicitationDialog elicitation={{
      elicitation_id: 'test-123',
      tool_name: 'choose_toppings',
      message: 'What size pizza?',
      response_schema: {
        type: 'object',
        properties: {
          value: {
            type: 'string',
            title: 'Size',
            oneOf: [{ const: 's', title: 'Small (10 inch)' }, { const: 'l', title: 'Large (14 inch)' }]
          }
        },
        required: ['value']
      }
    }} />)

    expect(screen.getByText('Size')).toBeInTheDocument()
    const accept = screen.getByRole('button', { name: /accept/i })
    expect(accept).toBeDisabled()

    const select = screen.getByRole('combobox')
    fireEvent.change(select, { target: { value: '1' } })
    expect(screen.getByRole('option', { name: 'Large (14 inch)' }).selected).toBe(true)
    fireEvent.click(accept)

    expect(mockSendMessage).toHaveBeenCalledWith({
      type: 'elicitation_response',
      elicitation_id: 'test-123',
      action: 'accept',
      data: 'l'
    })
  })

  it('collects a multi-select as a list in option order', () => {
    render(<ElicitationDialog elicitation={{
      elicitation_id: 'test-123',
      tool_name: 'choose_toppings',
      message: 'Pick any toppings you like:',
      response_schema: {
        type: 'object',
        properties: {
          value: { type: 'array', items: { type: 'string', enum: ['mushrooms', 'peppers', 'olives'] } }
        },
        required: ['value']
      }
    }} />)

    const accept = screen.getByRole('button', { name: /accept/i })
    expect(accept).toBeDisabled()

    fireEvent.click(screen.getByRole('checkbox', { name: 'olives' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'mushrooms' }))
    fireEvent.click(accept)

    expect(mockSendMessage).toHaveBeenCalledWith({
      type: 'elicitation_response',
      elicitation_id: 'test-123',
      action: 'accept',
      data: ['mushrooms', 'olives']
    })
  })

  it('refuses a non-integer answer to an integer field', () => {
    render(<ElicitationDialog elicitation={{
      elicitation_id: 'test-123',
      tool_name: 'pick_a_number',
      message: 'Pick a number',
      response_schema: {
        type: 'object',
        properties: { value: { type: 'integer', minimum: 1, maximum: 100 } },
        required: ['value']
      }
    }} />)

    const input = screen.getByRole('spinbutton')
    const accept = screen.getByRole('button', { name: /accept/i })
    fireEvent.change(input, { target: { value: '2.5' } })
    expect(accept).toBeDisabled()
    fireEvent.change(input, { target: { value: '500' } })
    expect(accept).toBeDisabled()
    fireEvent.change(input, { target: { value: '42' } })
    expect(accept).not.toBeDisabled()
  })

  it('confirms an empty schema without sending placeholder data', () => {
    render(<ElicitationDialog elicitation={{
      elicitation_id: 'test-123',
      tool_name: 'approve',
      message: 'Proceed?',
      response_schema: { type: 'object', properties: {} }
    }} />)

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /accept/i }))

    expect(mockSendMessage).toHaveBeenCalledWith({
      type: 'elicitation_response',
      elicitation_id: 'test-123',
      action: 'accept',
      data: null
    })
  })
})

describe('ElicitationDialog - URL Requests', () => {
  let mockSendMessage
  let mockSettleElicitation
  let openSpy

  const urlRequest = (url) => ({
    elicitation_id: 'test-123',
    tool_name: 'connect_account',
    message: 'Sign in to continue',
    response_schema: {},
    mode: 'url',
    url
  })

  beforeEach(() => {
    mockSendMessage = vi.fn(() => true)
    mockSettleElicitation = vi.fn()
    useChat.mockReturnValue({
      sendMessage: mockSendMessage,
      settleElicitation: mockSettleElicitation
    })
    openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
  })

  it('shows the full address and opens it only after consent is sent', () => {
    render(<ElicitationDialog elicitation={urlRequest('https://auth.example.com/start?state=abc')} />)

    expect(screen.getByText('https://auth.example.com/start?state=abc')).toBeInTheDocument()
    expect(openSpy).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /open link/i }))

    expect(mockSendMessage).toHaveBeenCalledWith({
      type: 'elicitation_response',
      elicitation_id: 'test-123',
      action: 'accept',
      data: null
    })
    expect(openSpy).toHaveBeenCalledWith(
      'https://auth.example.com/start?state=abc', '_blank', 'noopener,noreferrer'
    )
    expect(mockSettleElicitation).toHaveBeenCalledWith('test-123')
  })

  it('does not open the page when the consent could not be sent', () => {
    mockSendMessage.mockReturnValue(false)
    render(<ElicitationDialog elicitation={urlRequest('https://auth.example.com/start')} />)

    fireEvent.click(screen.getByRole('button', { name: /open link/i }))

    expect(openSpy).not.toHaveBeenCalled()
    expect(mockSettleElicitation).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('will not offer a link that is not a web address', () => {
    render(<ElicitationDialog elicitation={urlRequest('javascript:alert(1)')} />)

    expect(screen.queryByText('javascript:alert(1)')).not.toBeInTheDocument()
    const open = screen.getByRole('button', { name: /open link/i })
    expect(open).toBeDisabled()
    fireEvent.click(open)
    expect(openSpy).not.toHaveBeenCalled()
    expect(mockSendMessage).not.toHaveBeenCalled()
  })

  it('declines without opening anything', () => {
    render(<ElicitationDialog elicitation={urlRequest('https://auth.example.com/start')} />)

    fireEvent.click(screen.getByRole('button', { name: /decline/i }))

    expect(mockSendMessage).toHaveBeenCalledWith({
      type: 'elicitation_response',
      elicitation_id: 'test-123',
      action: 'decline',
      data: null
    })
    expect(openSpy).not.toHaveBeenCalled()
  })
})

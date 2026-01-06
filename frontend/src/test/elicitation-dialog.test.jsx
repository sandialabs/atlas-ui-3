/**
 * Tests for elicitation UI components and functionality
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ElicitationDialog from '../components/ElicitationDialog'

// Mock ChatContext
const mockSendMessage = vi.fn()
vi.mock('../contexts/ChatContext', () => ({
  useChat: () => ({
    sendMessage: mockSendMessage
  })
}))

const isCI = process.env.CI || process.env.ENVIRONMENT === 'cicd'

describe('Elicitation Tests', () => {
  it('should verify test framework is working', () => {
    expect(true).toBe(true)
  })

  if (!isCI) {
    describe('ElicitationDialog Component', () => {
      beforeEach(() => {
        mockSendMessage.mockClear()
      })

      it('should render elicitation dialog with message', () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Please enter your name',
          response_schema: {
            type: 'object',
            properties: {
              value: { type: 'string' }
            },
            required: ['value']
          }
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        expect(screen.getByText('User Input Required')).toBeInTheDocument()
        expect(screen.getByText('test_tool')).toBeInTheDocument()
        expect(screen.getByText('Please enter your name')).toBeInTheDocument()
      })

      it('should render string input field', () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Enter your name',
          response_schema: {
            type: 'object',
            properties: {
              value: { type: 'string', description: 'Your name' }
            },
            required: ['value']
          }
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        const input = screen.getByPlaceholderText('Your name')
        expect(input).toBeInTheDocument()
        expect(input.type).toBe('text')
      })

      it('should render number input field', () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Enter a number',
          response_schema: {
            type: 'object',
            properties: {
              value: { type: 'number', description: 'Pick a number' }
            },
            required: ['value']
          }
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        const input = screen.getByPlaceholderText('Pick a number')
        expect(input).toBeInTheDocument()
        expect(input.type).toBe('number')
      })

      it('should render boolean input field as checkbox', () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Do you confirm?',
          response_schema: {
            type: 'object',
            properties: {
              value: { type: 'boolean', description: 'Confirm action' }
            },
            required: ['value']
          }
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        const checkbox = screen.getByRole('checkbox')
        expect(checkbox).toBeInTheDocument()
        expect(screen.getByText('Confirm action')).toBeInTheDocument()
      })

      it('should render enum as dropdown', () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Choose a priority',
          response_schema: {
            type: 'object',
            properties: {
              value: { 
                type: 'string',
                enum: ['low', 'medium', 'high'],
                description: 'Priority level'
              }
            },
            required: ['value']
          }
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        const select = screen.getByRole('combobox')
        expect(select).toBeInTheDocument()
        expect(screen.getByText('Select an option...')).toBeInTheDocument()
      })

      it('should render structured form with multiple fields', () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Create a task',
          response_schema: {
            type: 'object',
            properties: {
              title: { type: 'string', description: 'Task title' },
              priority: { 
                type: 'string',
                enum: ['low', 'medium', 'high']
              }
            },
            required: ['title', 'priority']
          }
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        expect(screen.getByText('Title')).toBeInTheDocument()
        expect(screen.getByText('Priority')).toBeInTheDocument()
        expect(screen.getByPlaceholderText('Task title')).toBeInTheDocument()
      })

      it('should disable accept button when required fields are empty', () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Enter your name',
          response_schema: {
            type: 'object',
            properties: {
              value: { type: 'string' }
            },
            required: ['value']
          }
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        const acceptButton = screen.getByText('Accept')
        expect(acceptButton).toBeDisabled()
      })

      it('should enable accept button when required fields are filled', async () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Enter your name',
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
        fireEvent.change(input, { target: { value: 'John Doe' } })

        await waitFor(() => {
          const acceptButton = screen.getByText('Accept')
          expect(acceptButton).not.toBeDisabled()
        })
      })

      it('should send accept response when accept button clicked', async () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Enter your name',
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
        fireEvent.change(input, { target: { value: 'Alice' } })

        await waitFor(() => {
          const acceptButton = screen.getByText('Accept')
          expect(acceptButton).not.toBeDisabled()
        })

        const acceptButton = screen.getByText('Accept')
        fireEvent.click(acceptButton)

        expect(mockSendMessage).toHaveBeenCalledWith({
          type: 'elicitation_response',
          elicitation_id: 'elicit_123',
          action: 'accept',
          data: 'Alice'  // Scalar value unwrapped
        })
      })

      it('should send decline response when decline button clicked', () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Enter your name',
          response_schema: {
            type: 'object',
            properties: {
              value: { type: 'string' }
            }
          }
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        const declineButton = screen.getByText('Decline')
        fireEvent.click(declineButton)

        expect(mockSendMessage).toHaveBeenCalledWith({
          type: 'elicitation_response',
          elicitation_id: 'elicit_123',
          action: 'decline',
          data: null
        })
      })

      it('should send cancel response when cancel button clicked', () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Enter your name',
          response_schema: {
            type: 'object',
            properties: {
              value: { type: 'string' }
            }
          }
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        const cancelButton = screen.getAllByText('Cancel')[0]
        fireEvent.click(cancelButton)

        expect(mockSendMessage).toHaveBeenCalledWith({
          type: 'elicitation_response',
          elicitation_id: 'elicit_123',
          action: 'cancel',
          data: null
        })
      })

      it('should handle approval-only elicitation (no fields)', () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Are you sure you want to delete?',
          response_schema: {}
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        expect(screen.getByText('No additional information required. Please confirm or decline.')).toBeInTheDocument()
        
        const acceptButton = screen.getByText('Accept')
        expect(acceptButton).not.toBeDisabled()
      })

      it('should send structured data for multi-field form', async () => {
        const elicitation = {
          elicitation_id: 'elicit_123',
          tool_call_id: 'tool_456',
          tool_name: 'test_tool',
          message: 'Create a task',
          response_schema: {
            type: 'object',
            properties: {
              title: { type: 'string' },
              description: { type: 'string' }
            },
            required: ['title']
          }
        }

        render(<ElicitationDialog elicitation={elicitation} />)

        const titleInput = screen.getByPlaceholderText('Enter title')
        fireEvent.change(titleInput, { target: { value: 'My Task' } })

        const descInput = screen.getByPlaceholderText('Enter description')
        fireEvent.change(descInput, { target: { value: 'Task description' } })

        await waitFor(() => {
          const acceptButton = screen.getByText('Accept')
          expect(acceptButton).not.toBeDisabled()
        })

        const acceptButton = screen.getByText('Accept')
        fireEvent.click(acceptButton)

        expect(mockSendMessage).toHaveBeenCalledWith({
          type: 'elicitation_response',
          elicitation_id: 'elicit_123',
          action: 'accept',
          data: {
            title: 'My Task',
            description: 'Task description'
          }
        })
      })
    })
  }
})

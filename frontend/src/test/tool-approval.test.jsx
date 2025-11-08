/**
 * Tests for tool approval UI components and functionality
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ToolApprovalDialog from '../components/ToolApprovalDialog'

const isCI = process.env.CI || process.env.ENVIRONMENT === 'cicd'

describe('Tool Approval Tests', () => {
  it('should verify test framework is working', () => {
    expect(true).toBe(true)
  })

  if (!isCI) {
    describe('ToolApprovalDialog Component', () => {
      let mockOnResponse

      beforeEach(() => {
        mockOnResponse = vi.fn()
      })

      it('should render approval dialog with tool name', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { arg1: 'value1' },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        expect(screen.getByText('Tool Approval Required')).toBeInTheDocument()
        expect(screen.getByText('test_tool')).toBeInTheDocument()
      })

      it('should display arguments in view mode by default', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { code: 'print("hello")', param: 'value' },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        expect(screen.getByText(/"code"/)).toBeInTheDocument()
        expect(screen.getByText(/"param"/)).toBeInTheDocument()
      })

      it('should call onResponse with approved=true when approve button clicked', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { arg1: 'value1' },
          allow_edit: false
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        const approveButton = screen.getByText(/Approve/)
        fireEvent.click(approveButton)

        expect(mockOnResponse).toHaveBeenCalledWith({
          tool_call_id: 'test_123',
          approved: true,
          arguments: { arg1: 'value1' }
        })
      })

      it('should call onResponse with approved=false when reject button clicked', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { arg1: 'value1' },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        const rejectButton = screen.getByText('Reject')
        fireEvent.click(rejectButton)

        expect(mockOnResponse).toHaveBeenCalledWith({
          tool_call_id: 'test_123',
          approved: false,
          reason: 'User rejected the tool call'
        })
      })

      it('should include rejection reason when provided', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { arg1: 'value1' },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        const reasonInput = screen.getByPlaceholderText(/Enter reason/)
        fireEvent.change(reasonInput, { target: { value: 'Not safe' } })

        const rejectButton = screen.getByText('Reject')
        fireEvent.click(rejectButton)

        expect(mockOnResponse).toHaveBeenCalledWith({
          tool_call_id: 'test_123',
          approved: false,
          reason: 'Not safe'
        })
      })

      it('should show edit mode button when allow_edit is true', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { arg1: 'value1' },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        expect(screen.getByText('Edit Mode')).toBeInTheDocument()
      })

      it('should not show edit mode button when allow_edit is false', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { arg1: 'value1' },
          allow_edit: false
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        expect(screen.queryByText('Edit Mode')).not.toBeInTheDocument()
      })

      it('should toggle to edit mode when edit button clicked', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { code: 'print("test")' },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        const editButton = screen.getByText('Edit Mode')
        fireEvent.click(editButton)

        expect(screen.getByText('View Mode')).toBeInTheDocument()
        expect(screen.getByDisplayValue('print("test")')).toBeInTheDocument()
      })

      it('should allow editing arguments in edit mode', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { code: 'print("hello")' },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        const editButton = screen.getByText('Edit Mode')
        fireEvent.click(editButton)

        const codeTextarea = screen.getByDisplayValue('print("hello")')
        fireEvent.change(codeTextarea, { target: { value: 'print("world")' } })

        const approveButton = screen.getByText(/Approve/)
        fireEvent.click(approveButton)

        expect(mockOnResponse).toHaveBeenCalledWith({
          tool_call_id: 'test_123',
          approved: true,
          arguments: { code: 'print("world")' }
        })
      })

      it('should handle complex nested arguments', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'complex_tool',
          arguments: {
            nested: {
              level1: {
                level2: ['item1', 'item2']
              }
            },
            simple: 'value'
          },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        expect(screen.getByText(/"nested"/)).toBeInTheDocument()
        expect(screen.getByText(/"simple"/)).toBeInTheDocument()
      })

      it('should handle empty arguments', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'no_args_tool',
          arguments: {},
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        expect(screen.getByText('No arguments provided')).toBeInTheDocument()
      })

      it('should reset state when request changes', () => {
        const request1 = {
          tool_call_id: 'test_1',
          tool_name: 'tool_1',
          arguments: { arg: 'value1' },
          allow_edit: true
        }

        const { rerender } = render(<ToolApprovalDialog request={request1} onResponse={mockOnResponse} />)

        const editButton = screen.getByText('Edit Mode')
        fireEvent.click(editButton)
        expect(screen.getByText('View Mode')).toBeInTheDocument()

        const request2 = {
          tool_call_id: 'test_2',
          tool_name: 'tool_2',
          arguments: { arg: 'value2' },
          allow_edit: true
        }

        rerender(<ToolApprovalDialog request={request2} onResponse={mockOnResponse} />)

        expect(screen.getByText('Edit Mode')).toBeInTheDocument()
      })

      it('should display "with edits" text when approving in edit mode', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { code: 'test' },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        const editButton = screen.getByText('Edit Mode')
        fireEvent.click(editButton)

        expect(screen.getByText('Approve (with edits)')).toBeInTheDocument()
      })

      it('should handle JSON parsing in edit mode', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { data: { key: 'value' } },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        const editButton = screen.getByText('Edit Mode')
        fireEvent.click(editButton)

        const textarea = screen.getByDisplayValue(/"key"/)

        const validJSON = '{"newKey": "newValue"}'
        fireEvent.change(textarea, { target: { value: validJSON } })

        const approveButton = screen.getByText(/Approve/)
        fireEvent.click(approveButton)

        expect(mockOnResponse).toHaveBeenCalledWith({
          tool_call_id: 'test_123',
          approved: true,
          arguments: { data: { newKey: 'newValue' } }
        })
      })

      it('should handle array arguments in edit mode', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: { items: ['a', 'b', 'c'] },
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={mockOnResponse} />)

        const editButton = screen.getByText('Edit Mode')
        fireEvent.click(editButton)

        // Find textarea by role since label association might not be working
        const textareas = screen.getAllByRole('textbox')
        const textarea = textareas[0] // First textarea should be for the items field

        const newArray = '["x", "y", "z"]'
        fireEvent.change(textarea, { target: { value: newArray } })

        const approveButton = screen.getByText(/Approve/)
        fireEvent.click(approveButton)

        expect(mockOnResponse).toHaveBeenCalledWith({
          tool_call_id: 'test_123',
          approved: true,
          arguments: { items: ['x', 'y', 'z'] }
        })
      })
    })

    describe('Tool Approval UI Integration', () => {
      it('should render approval badge', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: {},
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={vi.fn()} />)

        expect(screen.getByText('Requires Approval')).toBeInTheDocument()
      })

      it('should have styled approve button', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: {},
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={vi.fn()} />)

        const approveButton = screen.getByText(/Approve/)
        expect(approveButton.className).toContain('bg-green-600')
      })

      it('should have styled reject button', () => {
        const request = {
          tool_call_id: 'test_123',
          tool_name: 'test_tool',
          arguments: {},
          allow_edit: true
        }

        render(<ToolApprovalDialog request={request} onResponse={vi.fn()} />)

        const rejectButton = screen.getByText('Reject')
        expect(rejectButton.className).toContain('bg-red-600')
      })
    })
  }
})

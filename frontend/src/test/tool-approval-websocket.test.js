/**
 * Tests for tool approval WebSocket message handling
 */

import { describe, it, expect, vi } from 'vitest'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'

describe('Tool Approval WebSocket Tests', () => {
  it('should verify test framework is working', () => {
    expect(true).toBe(true)
  })

  describe('createWebSocketHandler - Tool Approval Messages', () => {
    let deps
    let handler

    beforeEach(() => {
      deps = {
        addMessage: vi.fn(),
        mapMessages: vi.fn(),
        setIsThinking: vi.fn(),
        setCurrentAgentStep: vi.fn(),
        setCanvasContent: vi.fn(),
        setCanvasFiles: vi.fn(),
        setCurrentCanvasFileIndex: vi.fn(),
        setCustomUIContent: vi.fn(),
        setSessionFiles: vi.fn(),
        getFileType: vi.fn(),
        triggerFileDownload: vi.fn(),
        addAttachment: vi.fn(),
        resolvePendingFileEvent: vi.fn()
      }
      handler = createWebSocketHandler(deps)
    })

    it('should handle tool_approval_request message', () => {
      const message = {
        type: 'tool_approval_request',
        tool_call_id: 'test_123',
        tool_name: 'dangerous_tool',
        arguments: { code: 'rm -rf /' },
        allow_edit: true,
        admin_required: false
      }

      handler(message)

      expect(deps.addMessage).toHaveBeenCalledWith({
        role: 'system',
        content: 'Tool Approval Required: dangerous_tool',
        type: 'tool_approval_request',
        tool_call_id: 'test_123',
        tool_name: 'dangerous_tool',
        arguments: { code: 'rm -rf /' },
        allow_edit: true,
        admin_required: false,
        status: 'pending',
        timestamp: expect.any(String)
      })
    })

    it('should handle tool_approval_request with allow_edit=false', () => {
      const message = {
        type: 'tool_approval_request',
        tool_call_id: 'test_456',
        tool_name: 'restricted_tool',
        arguments: { param: 'value' },
        allow_edit: false,
        admin_required: true
      }

      handler(message)

      expect(deps.addMessage).toHaveBeenCalledWith({
        role: 'system',
        content: 'Tool Approval Required: restricted_tool',
        type: 'tool_approval_request',
        tool_call_id: 'test_456',
        tool_name: 'restricted_tool',
        arguments: { param: 'value' },
        allow_edit: false,
        admin_required: true,
        status: 'pending',
        timestamp: expect.any(String)
      })
    })

    it('should handle tool_approval_request with empty arguments', () => {
      const message = {
        type: 'tool_approval_request',
        tool_call_id: 'test_789',
        tool_name: 'no_args_tool',
        arguments: {},
        allow_edit: true,
        admin_required: false
      }

      handler(message)

      expect(deps.addMessage).toHaveBeenCalledWith({
        role: 'system',
        content: 'Tool Approval Required: no_args_tool',
        type: 'tool_approval_request',
        tool_call_id: 'test_789',
        tool_name: 'no_args_tool',
        arguments: {},
        allow_edit: true,
        admin_required: false,
        status: 'pending',
        timestamp: expect.any(String)
      })
    })

    it('should handle tool_approval_request with complex nested arguments', () => {
      const message = {
        type: 'tool_approval_request',
        tool_call_id: 'test_complex',
        tool_name: 'complex_tool',
        arguments: {
          nested: {
            level1: {
              level2: ['item1', 'item2']
            }
          },
          list: [1, 2, 3],
          string: 'value'
        },
        allow_edit: true,
        admin_required: false
      }

      handler(message)

      expect(deps.addMessage).toHaveBeenCalledWith({
        role: 'system',
        content: 'Tool Approval Required: complex_tool',
        type: 'tool_approval_request',
        tool_call_id: 'test_complex',
        tool_name: 'complex_tool',
        arguments: message.arguments,
        allow_edit: true,
        admin_required: false,
        status: 'pending',
        timestamp: expect.any(String)
      })
    })

    it('should handle tool_start message after approval', () => {
      const message = {
        type: 'tool_start',
        tool_call_id: 'test_123',
        tool_name: 'approved_tool',
        arguments: { param: 'value' },
        server_name: 'mcp_server'
      }

      handler(message)

      expect(deps.addMessage).toHaveBeenCalledWith({
        role: 'system',
        content: '**Tool Call: approved_tool**',
        type: 'tool_call',
        tool_call_id: 'test_123',
        tool_name: 'approved_tool',
        server_name: 'mcp_server',
        arguments: { param: 'value' },
        status: 'calling',
        timestamp: expect.any(String),
        agent_mode: false
      })
    })

    it('should handle tool_complete message', () => {
      const message = {
        type: 'tool_complete',
        tool_call_id: 'test_123',
        tool_name: 'completed_tool',
        success: true,
        result: 'Success output'
      }

      handler(message)

      expect(deps.mapMessages).toHaveBeenCalled()
    })

    it('should handle tool_error message', () => {
      const message = {
        type: 'tool_error',
        tool_call_id: 'test_123',
        tool_name: 'failed_tool',
        error: 'Tool execution failed'
      }

      handler(message)

      expect(deps.mapMessages).toHaveBeenCalled()
    })

    it('should not modify other message types', () => {
      const message = {
        type: 'chat_response',
        message: 'Hello world'
      }

      handler(message)

      expect(deps.addMessage).toHaveBeenCalledWith({
        role: 'assistant',
        content: 'Hello world',
        timestamp: expect.any(String)
      })
    })

    it('should handle multiple approval requests in sequence', () => {
      const message1 = {
        type: 'tool_approval_request',
        tool_call_id: 'test_1',
        tool_name: 'tool_1',
        arguments: { arg: 'value1' },
        allow_edit: true,
        admin_required: false
      }

      const message2 = {
        type: 'tool_approval_request',
        tool_call_id: 'test_2',
        tool_name: 'tool_2',
        arguments: { arg: 'value2' },
        allow_edit: false,
        admin_required: true
      }

      handler(message1)
      handler(message2)

      expect(deps.addMessage).toHaveBeenCalledTimes(2)
      expect(deps.addMessage).toHaveBeenNthCalledWith(1, expect.objectContaining({
        tool_call_id: 'test_1',
        tool_name: 'tool_1'
      }))
      expect(deps.addMessage).toHaveBeenNthCalledWith(2, expect.objectContaining({
        tool_call_id: 'test_2',
        tool_name: 'tool_2'
      }))
    })
  })
})

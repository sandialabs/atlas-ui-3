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
    let messages

    beforeEach(() => {
      messages = []
      deps = {
        addMessage: vi.fn(message => {
          messages = messages.concat(message)
        }),
        mapMessages: vi.fn(mapper => {
          messages = mapper(messages.slice())
        }),
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

      expect(messages).toEqual([{
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
      }])
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

      expect(messages).toEqual([{
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
      }])
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

      expect(messages).toEqual([{
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
      }])
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

      expect(messages).toEqual([{
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
      }])
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

      expect(messages).toEqual([{
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
      }])
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

      expect(messages).toHaveLength(2)
      expect(messages[0]).toEqual(expect.objectContaining({
        tool_call_id: 'test_1',
        tool_name: 'tool_1'
      }))
      expect(messages[1]).toEqual(expect.objectContaining({
        tool_call_id: 'test_2',
        tool_name: 'tool_2'
      }))
    })

    it('does not add a second card when the updater already has the request', () => {
      const updates = []
      deps.mapMessages = vi.fn(mapper => { updates.push(mapper) })
      deps.addMessage = vi.fn()
      handler = createWebSocketHandler(deps)
      handler({
        type: 'tool_approval_request',
        tool_call_id: 'test_123',
        tool_name: 'dangerous_tool',
        arguments: { code: 'rm -rf /' },
      })
      const prior = [{
        type: 'tool_approval_request',
        tool_call_id: 'test_123',
        status: 'pending',
      }]
      expect(deps.addMessage).not.toHaveBeenCalled()
      expect(updates.at(-1)(prior)).toBe(prior)
    })

    it('appends a tool row once inside the updater', () => {
      const updates = []
      deps.mapMessages = vi.fn(mapper => { updates.push(mapper) })
      deps.addMessage = vi.fn()
      handler = createWebSocketHandler(deps)
      handler({
        type: 'tool_start',
        tool_call_id: 'test_123',
        tool_name: 'approved_tool',
        invocation_id: 'run-1:0:test_123',
      })
      expect(deps.addMessage).not.toHaveBeenCalled()
      const next = updates.at(-1)([])
      expect(next).toHaveLength(1)
      expect(updates.at(-1)(next)).toBe(next)
    })

    it('marks atlas_sleep as sleeping and does not add the same invocation twice', () => {
      let messages = []
      deps.mapMessages = vi.fn(mapper => {
        messages = mapper(messages)
      })
      deps.addMessage = vi.fn(message => {
        messages = messages.concat(message)
      })
      handler = createWebSocketHandler(deps)
      const frame = {
        type: 'tool_start',
        tool_call_id: 'call_0',
        invocation_id: 'run-1:0:call_0',
        run_id: 'run-1',
        tool_name: 'atlas_sleep',
        arguments: { seconds: 5 },
      }
      handler(frame)
      handler(frame)
      expect(messages).toHaveLength(1)
      expect(messages[0].status).toBe('sleeping')
      expect(messages[0].invocation_id).toBe('run-1:0:call_0')
    })

    it('does not end the turn when a stale approval card is rejected', () => {
      let messages = [{
        type: 'tool_approval_request',
        tool_call_id: 'call_0',
        invocation_id: 'run-1:0:call_0',
        status: 'pending',
      }]
      deps.mapMessages = vi.fn(mapper => {
        messages = mapper(messages)
      })
      deps.clearAgentRunning = vi.fn()
      handler = createWebSocketHandler(deps)
      handler({
        type: 'error',
        error_type: 'unknown_tool_approval',
        message: 'No matching pending tool approval',
        tool_call_id: 'call_0',
        invocation_id: 'run-1:0:call_0',
      })
      // The card is replaced by a notice, so a vanished card does not read
      // as a successful approval.
      expect(messages.filter(m => m.type === 'tool_approval_request')).toEqual([])
      expect(messages).toHaveLength(1)
      expect(messages[0].role).toBe('system')
      expect(messages[0].content).toMatch(/no longer active.*did not run/)
      expect(deps.addMessage).not.toHaveBeenCalled()
      expect(deps.setIsThinking).not.toHaveBeenCalled()
      expect(deps.clearAgentRunning).not.toHaveBeenCalled()
    })

    it('marks a duplicate approval settled without ending the turn', () => {
      let messages = [{
        type: 'tool_approval_request',
        tool_call_id: 'call_0',
        invocation_id: 'run-1:0:call_0',
        status: 'pending',
      }]
      deps.mapMessages = vi.fn(mapper => {
        messages = mapper(messages)
      })
      handler = createWebSocketHandler(deps)
      handler({
        type: 'tool_approval_settled',
        approved: false,
        already_resolved: true,
        tool_call_id: 'call_0',
        invocation_id: 'run-1:0:call_0',
      })
      expect(messages[0].status).toBe('rejected')
      expect(messages[0].already_resolved).toBe(true)
      expect(deps.setIsThinking).not.toHaveBeenCalled()
      expect(deps.addMessage).not.toHaveBeenCalled()
    })

    it('labels a settled expired approval as expired, not rejected', () => {
      let messages = [{
        type: 'tool_approval_request',
        tool_call_id: 'call_0',
        invocation_id: 'run-1:0:call_0',
        status: 'pending',
      }]
      deps.mapMessages = vi.fn(mapper => {
        messages = mapper(messages)
      })
      handler = createWebSocketHandler(deps)
      handler({
        type: 'tool_approval_settled',
        approved: false,
        already_resolved: true,
        status: 'expired',
        tool_call_id: 'call_0',
        invocation_id: 'run-1:0:call_0',
      })
      expect(messages[0].status).toBe('expired')
    })

    it('closes only the interrupted invocation when call_0 was reused', () => {
      let messages = [
        { type: 'tool_call', tool_call_id: 'call_0', invocation_id: 'run-1:0:call_0', status: 'completed', result: 'done' },
        { type: 'tool_call', tool_call_id: 'call_0', invocation_id: 'run-1:1:call_0', status: 'sleeping' },
      ]
      deps.mapMessages = vi.fn(mapper => {
        messages = mapper(messages)
      })
      handler = createWebSocketHandler(deps)
      handler({
        type: 'tool_interrupted',
        tool_call_id: 'call_0',
        invocation_id: 'run-1:1:call_0',
        run_id: 'run-1',
        step: 1,
        tool_name: 'atlas_sleep',
        status: 'interrupted',
        result: 'Stopped before the tool result was recorded.',
      })
      expect(messages[0].status).toBe('completed')
      expect(messages[0].result).toBe('done')
      expect(messages[1].status).toBe('interrupted')
    })

    it('adds a new turn\'s rejected call_0 beside an earlier turn\'s settled legacy row', () => {
      let messages = [
        { type: 'tool_call', tool_call_id: 'call_0', status: 'completed', result: 'old turn' },
      ]
      deps.mapMessages = vi.fn(mapper => {
        messages = mapper(messages)
      })
      handler = createWebSocketHandler(deps)
      handler({
        type: 'tool_error',
        tool_call_id: 'call_0',
        invocation_id: 'run-2:0:call_0',
        run_id: 'run-2',
        tool_name: 'search',
        status: 'rejected',
        error: 'User rejected the tool call',
      })
      expect(messages).toHaveLength(2)
      expect(messages[0].status).toBe('completed')
      expect(messages[0].result).toBe('old turn')
      expect(messages[1].status).toBe('rejected')
      expect(messages[1].invocation_id).toBe('run-2:0:call_0')
    })

    it('settles only the dialog on an elicitation timeout, not the running tool row', () => {
      let messages = [
        { type: 'tool_call', tool_call_id: 'call_0', invocation_id: 'run-1:0:call_0', status: 'in_progress' },
      ]
      let pending = { elicitation_id: 'e1', tool_call_id: 'call_0' }
      deps.mapMessages = vi.fn(mapper => {
        messages = mapper(messages)
      })
      deps.setPendingElicitation = vi.fn(next => {
        pending = typeof next === 'function' ? next(pending) : next
      })
      handler = createWebSocketHandler(deps)
      handler({
        type: 'tool_error',
        elicitation_id: 'e1',
        tool_call_id: 'call_0',
        invocation_id: 'run-1:0:call_0',
        tool_name: 'ask',
        status: 'expired',
        error: 'Elicitation timed out',
      })
      expect(messages[0].status).toBe('in_progress')
      expect(pending).toBeNull()
    })

    it('renders a new bare call_0 approval after an earlier bare call_0 was approved', () => {
      // Untracked turns carry no invocation id, and providers reuse call_0.
      let messages = [{ type: 'tool_approval_request', tool_call_id: 'call_0', status: 'approved' }]
      deps.mapMessages = vi.fn(mapper => {
        messages = mapper(messages)
      })
      handler = createWebSocketHandler(deps)
      const request = { type: 'tool_approval_request', tool_call_id: 'call_0', tool_name: 'search', arguments: {} }
      handler(request)
      expect(messages.filter(m => m.type === 'tool_approval_request')).toHaveLength(2)
      expect(messages[1].status).toBe('pending')
      // A repeat of the still-pending request does not stack a third card.
      handler(request)
      expect(messages.filter(m => m.type === 'tool_approval_request')).toHaveLength(2)
    })

    it('gives a later bare call_0 its own row and keeps the earlier completed one', () => {
      let messages = [{ type: 'tool_call', tool_call_id: 'call_0', tool_name: 'search', status: 'completed', result: 'turn 1' }]
      deps.mapMessages = vi.fn(mapper => {
        messages = mapper(messages)
      })
      handler = createWebSocketHandler(deps)
      handler({ type: 'tool_start', tool_call_id: 'call_0', tool_name: 'search', arguments: {} })
      expect(messages).toHaveLength(2)
      handler({ type: 'tool_complete', tool_call_id: 'call_0', tool_name: 'search', success: true, result: 'turn 2' })
      expect(messages[0].result).toBe('turn 1')
      expect(messages[1].status).toBe('completed')
      expect(messages[1].result).toBe('turn 2')
    })
  })
})

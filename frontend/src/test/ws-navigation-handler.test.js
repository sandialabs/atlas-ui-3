/**
 * Issue #1223: the chat message handler knows the acknowledgement frames of
 * reset_session / restore_conversation and tells the user when a navigation
 * could not be confirmed.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'

const makeDeps = () => ({
  addMessage: vi.fn(),
  mapMessages: vi.fn(),
  setIsThinking: vi.fn(),
  setIsAgentRunning: vi.fn(),
  setIsSynthesizing: vi.fn(),
  setCurrentAgentStep: vi.fn(),
  setAgentPendingQuestion: vi.fn(),
  streamToken: vi.fn(),
  streamEnd: vi.fn(),
})

describe('navigation frames in the chat handler', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it.each(['session_reset', 'conversation_restored'])('accepts %s without an unknown-type warning', (type) => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const deps = makeDeps()
    createWebSocketHandler(deps)({ type, conversation_id: 'c1' })
    expect(warn).not.toHaveBeenCalled()
    expect(deps.addMessage).not.toHaveBeenCalled()
  })

  it('shows a warning when a navigation was not confirmed, without ending the turn', () => {
    const deps = makeDeps()
    createWebSocketHandler(deps)({
      type: 'navigation_failed',
      frame_type: 'restore_conversation',
      message: 'The server did not confirm the change.',
    })
    expect(deps.addMessage).toHaveBeenCalledTimes(1)
    const [[message]] = deps.addMessage.mock.calls
    expect(message.role).toBe('system')
    expect(message.type).toBe('warning')
    expect(message.content).toContain('did not confirm')
    expect(deps.setIsThinking).not.toHaveBeenCalled()
  })
})

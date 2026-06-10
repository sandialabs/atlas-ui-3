/**
 * Tests for "New Chat" behavior while generating (GH issue: clicking new chat
 * causes outputs to be disrupted when generating).
 *
 * Verifies that clearChat():
 *   1. Confirms with the user before discarding a conversation or in-flight reply.
 *   2. Cancels in-flight generation (stop_streaming + agent_control:stop) before
 *      asking the backend for a new session, so tokens don't keep streaming
 *      into the fresh empty chat.
 *   3. Resets local "thinking"/"synthesizing"/"agent step" state so the
 *      centered welcome logo reappears immediately.
 *
 * This mirrors the logic of ChatContext.clearChat as a pure function so it can
 * be tested without spinning up the full React context tree (same pattern used
 * by rag-activation-gating.test.js).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

/**
 * Pure extraction of ChatContext.clearChat behavior.
 */
function clearChat({
  skipConfirm = false,
  isThinking,
  isSynthesizing,
  isStreaming,
  hasContent,
  agentModeEnabled,
  sendMessage,
  resetLocalState,
  confirmFn = (globalThis.window && globalThis.window.confirm) || (() => true),
} = {}) {
  const isGenerating = isThinking || isSynthesizing || isStreaming
  if (!skipConfirm && (hasContent || isGenerating)) {
    const prompt = isGenerating
      ? 'A response is still being generated. Start a new chat and stop the current response?'
      : 'Start a new chat? This will clear the current conversation from view.'
    if (!confirmFn(prompt)) return false
  }

  if (sendMessage && isGenerating) {
    if (agentModeEnabled) {
      sendMessage({ type: 'agent_control', action: 'stop' })
    }
    sendMessage({ type: 'stop_streaming' })
  }

  resetLocalState()
  if (sendMessage) sendMessage({ type: 'reset_session' })
  return true
}

describe('New Chat while generating', () => {
  let sendMessage
  let resetLocalState

  beforeEach(() => {
    sendMessage = vi.fn()
    resetLocalState = vi.fn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('cancels in-flight streaming before requesting a new session', () => {
    clearChat({
      isThinking: false,
      isSynthesizing: false,
      isStreaming: true,
      hasContent: true,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
      confirmFn: () => true,
    })

    const types = sendMessage.mock.calls.map(c => c[0].type)
    // stop_streaming must be sent BEFORE reset_session so the backend cancels
    // the task that is still emitting tokens.
    const stopIdx = types.indexOf('stop_streaming')
    const resetIdx = types.indexOf('reset_session')
    expect(stopIdx).toBeGreaterThanOrEqual(0)
    expect(resetIdx).toBeGreaterThan(stopIdx)
    expect(resetLocalState).toHaveBeenCalledTimes(1)
  })

  it('also stops the agent loop when agent mode is enabled and generating', () => {
    clearChat({
      isThinking: true,
      isSynthesizing: false,
      isStreaming: false,
      hasContent: true,
      agentModeEnabled: true,
      sendMessage,
      resetLocalState,
      confirmFn: () => true,
    })

    const types = sendMessage.mock.calls.map(c => c[0].type)
    expect(types).toContain('agent_control')
    expect(types).toContain('stop_streaming')
    expect(types).toContain('reset_session')
    const agentCall = sendMessage.mock.calls.find(c => c[0].type === 'agent_control')
    expect(agentCall[0].action).toBe('stop')
  })

  it('prompts for confirmation when chat has content or is generating', () => {
    const confirmFn = vi.fn(() => true)
    clearChat({
      isThinking: true,
      isSynthesizing: false,
      isStreaming: false,
      hasContent: false,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
      confirmFn,
    })
    expect(confirmFn).toHaveBeenCalledTimes(1)
  })

  it('aborts without resetting or sending when user cancels the confirm dialog', () => {
    const result = clearChat({
      isThinking: false,
      isSynthesizing: false,
      isStreaming: true,
      hasContent: true,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
      confirmFn: () => false,
    })

    expect(result).toBe(false)
    expect(sendMessage).not.toHaveBeenCalled()
    expect(resetLocalState).not.toHaveBeenCalled()
  })

  it('returns true after a successful clear so callers can gate side-effects', () => {
    const result = clearChat({
      isThinking: false,
      isSynthesizing: false,
      isStreaming: false,
      hasContent: false,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
      confirmFn: () => true,
    })
    expect(result).toBe(true)
  })

  it('does not prompt when the chat is empty and idle', () => {
    const confirmFn = vi.fn(() => true)
    clearChat({
      isThinking: false,
      isSynthesizing: false,
      isStreaming: false,
      hasContent: false,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
      confirmFn,
    })
    expect(confirmFn).not.toHaveBeenCalled()
    // Still resets and creates a new session.
    expect(resetLocalState).toHaveBeenCalledTimes(1)
    expect(sendMessage).toHaveBeenCalledWith({ type: 'reset_session' })
  })

  it('does not send stop_streaming when nothing is generating', () => {
    clearChat({
      isThinking: false,
      isSynthesizing: false,
      isStreaming: false,
      hasContent: true,
      agentModeEnabled: true,
      sendMessage,
      resetLocalState,
      confirmFn: () => true,
    })
    const types = sendMessage.mock.calls.map(c => c[0].type)
    expect(types).not.toContain('stop_streaming')
    expect(types).not.toContain('agent_control')
    expect(types).toContain('reset_session')
  })
})

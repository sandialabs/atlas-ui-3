/**
 * Tests for "New Chat" behavior while generating (GH issue: clicking new chat
 * causes outputs to be disrupted when generating).
 *
 * Verifies that clearChat():
 *   1. Never raises a blocking dialog: New Chat stops an active turn and
 *      clears immediately, while an idle transcript is recoverable through the
 *      Undo toast (see new-chat-undo.test.jsx).
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
  // A tracked background run keeps going after the view is cleared (issue
  // #884), so it is not the current turn's to stop -- and clearing is then
  // pure navigation.
  hasBackgroundRun = false,
  agentModeEnabled,
  sendMessage,
  resetLocalState,
  offerUndo = () => {},
} = {}) {
  const isGenerating = isThinking || isSynthesizing || isStreaming
  const mustStopCurrentTurn = isGenerating && !hasBackgroundRun
  const canUndo = !skipConfirm && hasContent && !mustStopCurrentTurn

  if (sendMessage && mustStopCurrentTurn) {
    if (agentModeEnabled) {
      sendMessage({ type: 'agent_control', action: 'stop' })
    }
    sendMessage({ type: 'stop_streaming' })
  }

  resetLocalState()
  if (sendMessage) sendMessage({ type: 'reset_session' })
  if (canUndo) offerUndo()
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
    })

    const types = sendMessage.mock.calls.map(c => c[0].type)
    expect(types).toContain('agent_control')
    expect(types).toContain('stop_streaming')
    expect(types).toContain('reset_session')
    const agentCall = sendMessage.mock.calls.find(c => c[0].type === 'agent_control')
    expect(agentCall[0].action).toBe('stop')
  })

  it('stops an untracked reply without prompting', () => {
    clearChat({
      isThinking: true,
      isSynthesizing: false,
      isStreaming: false,
      hasContent: false,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
    })
    expect(resetLocalState).toHaveBeenCalledTimes(1)
    expect(sendMessage).toHaveBeenCalledWith({ type: 'reset_session' })
  })

  it('does NOT prompt when the chat merely has content -- it offers Undo instead', () => {
    const offerUndo = vi.fn()
    const result = clearChat({
      isThinking: false,
      isSynthesizing: false,
      isStreaming: false,
      hasContent: true,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
      offerUndo,
    })
    expect(offerUndo).toHaveBeenCalledTimes(1)
    expect(result).toBe(true)
    expect(resetLocalState).toHaveBeenCalledTimes(1)
  })

  it('does not prompt when generation belongs to a tracked background run', () => {
    clearChat({
      isThinking: false,
      isSynthesizing: false,
      isStreaming: true,
      hasBackgroundRun: true,
      hasContent: true,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
    })
    // The background run is not stopped -- it keeps going in history.
    expect(sendMessage.mock.calls.map(c => c[0].type)).not.toContain('stop_streaming')
  })

  it('does not offer Undo on skipConfirm or on an empty chat', () => {
    const offerUndo = vi.fn()
    clearChat({ hasContent: true, skipConfirm: true, sendMessage, resetLocalState, offerUndo })
    clearChat({ hasContent: false, sendMessage, resetLocalState, offerUndo })
    expect(offerUndo).not.toHaveBeenCalled()
  })

  it('clears an active turn without waiting for confirmation', () => {
    const result = clearChat({
      isThinking: false,
      isSynthesizing: false,
      isStreaming: true,
      hasContent: true,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
    })

    expect(result).toBe(true)
    expect(sendMessage).toHaveBeenCalledWith({ type: 'stop_streaming' })
    expect(sendMessage).toHaveBeenCalledWith({ type: 'reset_session' })
    expect(resetLocalState).toHaveBeenCalledTimes(1)
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
    })
    expect(result).toBe(true)
  })

  it('does not prompt when the chat is empty and idle', () => {
    clearChat({
      isThinking: false,
      isSynthesizing: false,
      isStreaming: false,
      hasContent: false,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
    })
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
    })
    const types = sendMessage.mock.calls.map(c => c[0].type)
    expect(types).not.toContain('stop_streaming')
    expect(types).not.toContain('agent_control')
    expect(types).toContain('reset_session')
  })
})

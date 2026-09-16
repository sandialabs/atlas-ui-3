/**
 * Tests for "New Chat" behavior while generating (GH issue: clicking new chat
 * causes outputs to be disrupted when generating).
 *
 * Verifies that clearChat():
 *   1. Confirms ONLY for the irreversible case -- an untracked reply that is
 *      still being generated and would be cancelled outright. A plain clear of
 *      an existing transcript is recoverable (Undo toast, see
 *      new-chat-undo.test.jsx) and must not raise a blocking dialog: a native
 *      confirm is a hard two-step on a phone and unusable from a car mount.
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
  confirmFn = (globalThis.window && globalThis.window.confirm) || (() => true),
} = {}) {
  const isGenerating = isThinking || isSynthesizing || isStreaming
  const mustStopCurrentTurn = isGenerating && !hasBackgroundRun
  if (!skipConfirm && mustStopCurrentTurn) {
    const prompt = 'A response is still being generated. Start a new chat and stop the current response?'
    if (!confirmFn(prompt)) return false
  }

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

  it('prompts for confirmation when an untracked reply is still generating', () => {
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

  it('does NOT prompt when the chat merely has content -- it offers Undo instead', () => {
    const confirmFn = vi.fn(() => true)
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
      confirmFn,
    })
    expect(confirmFn).not.toHaveBeenCalled()
    expect(offerUndo).toHaveBeenCalledTimes(1)
    expect(result).toBe(true)
    expect(resetLocalState).toHaveBeenCalledTimes(1)
  })

  it('does not prompt when generation belongs to a tracked background run', () => {
    const confirmFn = vi.fn(() => true)
    clearChat({
      isThinking: false,
      isSynthesizing: false,
      isStreaming: true,
      hasBackgroundRun: true,
      hasContent: true,
      agentModeEnabled: false,
      sendMessage,
      resetLocalState,
      confirmFn,
    })
    expect(confirmFn).not.toHaveBeenCalled()
    // The background run is not stopped -- it keeps going in history.
    expect(sendMessage.mock.calls.map(c => c[0].type)).not.toContain('stop_streaming')
  })

  it('does not offer Undo on skipConfirm or on an empty chat', () => {
    const offerUndo = vi.fn()
    clearChat({ hasContent: true, skipConfirm: true, sendMessage, resetLocalState, offerUndo })
    clearChat({ hasContent: false, sendMessage, resetLocalState, offerUndo })
    expect(offerUndo).not.toHaveBeenCalled()
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

/**
 * Tests for auto-scroll behavior during streaming (#441)
 *
 * Validates that:
 * - New assistant messages trigger force-scroll (so user sees the response start)
 * - Streaming token updates do NOT force-scroll (user can read earlier output)
 * - MutationObserver-style updates don't force-scroll during streaming
 * - After streaming ends, normal force-scroll resumes for non-user messages
 * - User scroll position is respected when they scroll up during streaming
 */

import { describe, it, expect } from 'vitest'

/**
 * Extracted scroll decision logic from ChatArea.jsx.
 * These mirror the conditions in the useEffect hooks exactly.
 */

// Mirrors the scroll-on-message-change effect (lines 120-141 in ChatArea.jsx)
function computeMessageChangeScroll(messages, prevMessageCount) {
  const newCount = messages.length
  const lastMsg = messages[messages.length - 1]
  const isNewMessage = newCount !== prevMessageCount
  const isStreamingUpdate = lastMsg && lastMsg._streaming && !isNewMessage
  const force = isNewMessage && lastMsg && (lastMsg.role !== 'user')

  if (isStreamingUpdate) {
    return { force: false, isStreamingUpdate: true }
  }
  return { force, isStreamingUpdate: false }
}

// Mirrors the MutationObserver callback (lines 143-155 in ChatArea.jsx)
function computeMutationScroll(messages) {
  const lastMsg = messages[messages.length - 1]
  const isCurrentlyStreaming = lastMsg && lastMsg._streaming
  const force = !isCurrentlyStreaming && lastMsg && lastMsg.role !== 'user'
  return { force }
}

// Mirrors scrollToBottom logic (lines 108-118 in ChatArea.jsx)
function shouldActuallyScroll(force, userScrolledAway) {
  if (userScrolledAway && !force) return false
  return true
}

describe('Auto-scroll during streaming (#441)', () => {

  describe('Scenario 1: New assistant message appears (streaming starts)', () => {
    it('should force-scroll when a new assistant message is created', () => {
      const messages = [
        { role: 'user', content: 'Hello' },
        { role: 'assistant', content: 'Hi', _streaming: true },
      ]
      const prevCount = 1 // was 1 message, now 2
      const result = computeMessageChangeScroll(messages, prevCount)
      expect(result.force).toBe(true)
      expect(result.isStreamingUpdate).toBe(false)
    })

    it('should force-scroll even if user had scrolled up (new message)', () => {
      const result = shouldActuallyScroll(true, true) // force=true, userScrolledAway=true
      expect(result).toBe(true)
    })
  })

  describe('Scenario 2: Streaming token updates (content growing)', () => {
    it('should NOT force-scroll during streaming token updates', () => {
      const messages = [
        { role: 'user', content: 'Hello' },
        { role: 'assistant', content: 'Hi there, let me explain...', _streaming: true },
      ]
      const prevCount = 2 // same message count, content just changed
      const result = computeMessageChangeScroll(messages, prevCount)
      expect(result.force).toBe(false)
      expect(result.isStreamingUpdate).toBe(true)
    })

    it('should not scroll if user has scrolled up during streaming', () => {
      const result = shouldActuallyScroll(false, true) // force=false, userScrolledAway=true
      expect(result).toBe(false)
    })

    it('should auto-scroll if user is near bottom during streaming', () => {
      const result = shouldActuallyScroll(false, false) // force=false, userScrolledAway=false
      expect(result).toBe(true)
    })
  })

  describe('Scenario 3: MutationObserver during streaming', () => {
    it('should NOT force-scroll during streaming', () => {
      const messages = [
        { role: 'user', content: 'Hello' },
        { role: 'assistant', content: 'Response with code...', _streaming: true },
      ]
      const result = computeMutationScroll(messages)
      expect(result.force).toBe(false)
    })

    it('should force-scroll for completed assistant messages (post-render expansion)', () => {
      const messages = [
        { role: 'user', content: 'Hello' },
        { role: 'assistant', content: 'Completed response', _streaming: false },
      ]
      const result = computeMutationScroll(messages)
      expect(result.force).toBe(true)
    })
  })

  describe('Scenario 4: After streaming ends', () => {
    it('should NOT force on message-change when streaming just ended (same count)', () => {
      const messages = [
        { role: 'user', content: 'Hello' },
        { role: 'assistant', content: 'Full response', _streaming: false },
      ]
      const prevCount = 2 // same count, streaming flag just cleared
      const result = computeMessageChangeScroll(messages, prevCount)
      // Not a new message and not streaming, so no force
      expect(result.force).toBe(false)
      expect(result.isStreamingUpdate).toBe(false)
    })

    it('MutationObserver should force-scroll after streaming ends', () => {
      const messages = [
        { role: 'user', content: 'Hello' },
        { role: 'assistant', content: 'Full response', _streaming: false },
      ]
      const result = computeMutationScroll(messages)
      expect(result.force).toBe(true)
    })
  })

  describe('Scenario 5: User messages (no forced scroll)', () => {
    it('should not force-scroll for new user messages', () => {
      const messages = [
        { role: 'user', content: 'Hello' },
      ]
      const prevCount = 0
      const result = computeMessageChangeScroll(messages, prevCount)
      expect(result.force).toBe(false)
    })

    it('MutationObserver should not force for user messages', () => {
      const messages = [
        { role: 'user', content: 'Hello' },
      ]
      const result = computeMutationScroll(messages)
      expect(result.force).toBe(false)
    })
  })

  describe('Scenario 6: Edge cases', () => {
    it('handles empty messages array', () => {
      const result = computeMessageChangeScroll([], 0)
      expect(result.force).toBe(false)
      expect(result.isStreamingUpdate).toBe(false)
    })

    it('handles undefined _streaming (non-streaming message)', () => {
      const messages = [
        { role: 'assistant', content: 'History message' }, // no _streaming flag
      ]
      const prevCount = 0
      const result = computeMessageChangeScroll(messages, prevCount)
      expect(result.force).toBe(true) // new assistant message, should force
    })

    it('MutationObserver treats undefined _streaming as not streaming', () => {
      const messages = [
        { role: 'assistant', content: 'History message' }, // no _streaming flag
      ]
      const result = computeMutationScroll(messages)
      expect(result.force).toBe(true)
    })
  })

  describe('Scenario 7: Realistic streaming session flow', () => {
    it('simulates a full user→assistant streaming cycle', () => {
      let prevCount = 0

      // Step 1: User sends a message
      let messages = [{ role: 'user', content: 'Explain quantum computing' }]
      let scroll = computeMessageChangeScroll(messages, prevCount)
      expect(scroll.force).toBe(false) // user message, no force
      prevCount = messages.length

      // Step 2: Assistant message appears (streaming starts)
      messages = [
        { role: 'user', content: 'Explain quantum computing' },
        { role: 'assistant', content: 'Quantum', _streaming: true },
      ]
      scroll = computeMessageChangeScroll(messages, prevCount)
      expect(scroll.force).toBe(true) // new assistant message → force scroll to see it
      prevCount = messages.length

      // Step 3: More tokens arrive (user wants to read from top)
      messages = [
        { role: 'user', content: 'Explain quantum computing' },
        { role: 'assistant', content: 'Quantum computing uses qubits which can be in superposition...', _streaming: true },
      ]
      scroll = computeMessageChangeScroll(messages, prevCount)
      expect(scroll.force).toBe(false) // streaming update → NO force
      expect(scroll.isStreamingUpdate).toBe(true)
      // User scrolled up — should NOT scroll
      expect(shouldActuallyScroll(false, true)).toBe(false)

      // Step 4: Even more tokens, user still reading top
      messages = [
        { role: 'user', content: 'Explain quantum computing' },
        { role: 'assistant', content: 'Quantum computing uses qubits which can be in superposition... (long explanation continues here)', _streaming: true },
      ]
      scroll = computeMessageChangeScroll(messages, prevCount)
      expect(scroll.force).toBe(false) // still streaming
      expect(scroll.isStreamingUpdate).toBe(true)

      // Step 5: MutationObserver fires during streaming (e.g., code block highlighted)
      let mutation = computeMutationScroll(messages)
      expect(mutation.force).toBe(false) // during streaming, no force

      // Step 6: Streaming ends
      messages = [
        { role: 'user', content: 'Explain quantum computing' },
        { role: 'assistant', content: 'Full quantum computing explanation...', _streaming: false },
      ]
      scroll = computeMessageChangeScroll(messages, prevCount)
      expect(scroll.force).toBe(false) // same count, not a new message
      expect(scroll.isStreamingUpdate).toBe(false) // not streaming anymore

      // Step 7: Post-streaming DOM mutation (e.g., lazy-loaded image)
      mutation = computeMutationScroll(messages)
      expect(mutation.force).toBe(true) // streaming done, force for content expansion
    })
  })
})

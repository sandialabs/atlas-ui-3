/**
 * Tests for the shared user-message-ordinal helper (issue #142).
 *
 * This is the load-bearing contract behind rewind/edit: ChatArea assigns each
 * rewindable user row an ordinal and ChatContext truncates the local transcript
 * at that same ordinal, which must line up with the backend's
 * ConversationHistory.truncate_at_user_index. The cases below pin the rules that
 * keep all three in agreement -- counting user prompts only, ignoring
 * assistant/tool/system rows, and excluding agent-loop answer rows that never
 * reach backend history.
 */

import { describe, it, expect } from 'vitest'
import {
  isRewindableUserMessage,
  userMessageSliceIndex,
} from '../utils/userMessageOrdinal'

const u = (content) => ({ role: 'user', content })
const a = (content) => ({ role: 'assistant', content })
const tool = (content) => ({ role: 'tool', content })
const sys = (content) => ({ role: 'system', content })
const agentAnswer = (content) => ({ role: 'user', content, _agentInput: true })

describe('isRewindableUserMessage', () => {
  it('accepts a plain user prompt', () => {
    expect(isRewindableUserMessage(u('hi'))).toBe(true)
  })

  it('rejects assistant / tool / system rows', () => {
    expect(isRewindableUserMessage(a('reply'))).toBe(false)
    expect(isRewindableUserMessage(tool('t'))).toBe(false)
    expect(isRewindableUserMessage(sys('s'))).toBe(false)
  })

  it('rejects agent-loop answer rows (not persisted to backend history)', () => {
    expect(isRewindableUserMessage(agentAnswer('my answer'))).toBe(false)
  })

  it('is null-safe', () => {
    expect(isRewindableUserMessage(null)).toBe(false)
    expect(isRewindableUserMessage(undefined)).toBe(false)
  })
})

describe('userMessageSliceIndex', () => {
  it('maps the user ordinal to its absolute transcript position', () => {
    const msgs = [u('u0'), a('a0'), u('u1'), a('a1'), u('u2')]
    expect(userMessageSliceIndex(msgs, 0)).toBe(0)
    expect(userMessageSliceIndex(msgs, 1)).toBe(2)
    expect(userMessageSliceIndex(msgs, 2)).toBe(4)
  })

  it('ignores intervening tool and system rows when counting', () => {
    const msgs = [u('u0'), a('a0'), tool('t0'), sys('s0'), u('u1'), a('a1')]
    // The second user message is at absolute index 4 despite the tool/system rows.
    expect(userMessageSliceIndex(msgs, 1)).toBe(4)
  })

  it('skips agent-loop answer rows so ordinals stay aligned with the backend', () => {
    // u0, (agent answer), u1 -> the agent answer must NOT consume ordinal 1.
    const msgs = [u('u0'), a('a0'), agentAnswer('answer'), a('a1'), u('u1')]
    expect(userMessageSliceIndex(msgs, 0)).toBe(0)
    expect(userMessageSliceIndex(msgs, 1)).toBe(4)
  })

  it('returns -1 for an out-of-range ordinal so callers can no-op', () => {
    const msgs = [u('u0'), a('a0')]
    expect(userMessageSliceIndex(msgs, 5)).toBe(-1)
  })

  it('returns -1 for negative or null ordinals', () => {
    const msgs = [u('u0')]
    expect(userMessageSliceIndex(msgs, -1)).toBe(-1)
    expect(userMessageSliceIndex(msgs, null)).toBe(-1)
    expect(userMessageSliceIndex(msgs, undefined)).toBe(-1)
  })

  it('returns -1 when there are no rewindable user messages', () => {
    expect(userMessageSliceIndex([a('a0'), agentAnswer('x')], 0)).toBe(-1)
  })
})

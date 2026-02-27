/**
 * Tests for sidebar conversation display logic (GH #354)
 *
 * Verifies that the current conversation never disappears from the sidebar
 * during the gap between backend confirming a save (activeConversationId set)
 * and the conversation list API response catching up.
 *
 * Three display states:
 * 1. Pre-save: optimistic "Saving..." entry shown
 * 2. Post-save, pre-refresh: bridge entry with real ID shown (the bug fix)
 * 3. Post-refresh: real conversation from fetched list shown
 */

import { describe, it, expect } from 'vitest'
import { getDisplayConversations } from '../utils/getDisplayConversations'

const makeMessages = (texts) =>
  texts.map((content, i) => ({
    role: i % 2 === 0 ? 'user' : 'assistant',
    content,
  }))

const makeConversation = (id, title, extra = {}) => ({
  id,
  title,
  preview: '',
  updated_at: '2026-01-01T00:00:00Z',
  message_count: 2,
  ...extra,
})

describe('getDisplayConversations', () => {
  // --- State 1: Pre-save (optimistic entry) ---

  it('shows optimistic "Saving..." entry when activeConversationId is null and there are user messages', () => {
    const result = getDisplayConversations({
      conversations: [],
      messages: makeMessages(['Hello', 'Hi there']),
      activeConversationId: null,
      chatHistoryEnabled: true,
      saveMode: 'server',
    })

    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('__current__')
    expect(result[0].title).toBe('Hello')
    expect(result[0].preview).toBe('Saving...')
    expect(result[0]._optimistic).toBe(true)
  })

  it('does not duplicate optimistic entry if title already in fetched list', () => {
    const result = getDisplayConversations({
      conversations: [makeConversation('abc-123', 'Hello')],
      messages: makeMessages(['Hello', 'Hi there']),
      activeConversationId: null,
      chatHistoryEnabled: true,
      saveMode: 'server',
    })

    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('abc-123')
    expect(result[0]._optimistic).toBeUndefined()
  })

  // --- State 2: Post-save, pre-refresh (the bug fix) ---

  it('shows bridge entry when activeConversationId is set but not in fetched list', () => {
    const result = getDisplayConversations({
      conversations: [],
      messages: makeMessages(['Hello', 'Hi there']),
      activeConversationId: 'conv-uuid-123',
      chatHistoryEnabled: true,
      saveMode: 'server',
    })

    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('conv-uuid-123')
    expect(result[0].title).toBe('Hello')
    expect(result[0]._current).toBe(true)
    expect(result[0]._optimistic).toBeUndefined()
  })

  it('bridge entry uses real conversation ID so it can be matched for active styling', () => {
    const result = getDisplayConversations({
      conversations: [makeConversation('other-conv', 'Old chat')],
      messages: makeMessages(['New question', 'Answer']),
      activeConversationId: 'new-conv-id',
      chatHistoryEnabled: true,
      saveMode: 'server',
    })

    expect(result).toHaveLength(2)
    // Bridge entry should be first (unshifted)
    expect(result[0].id).toBe('new-conv-id')
    expect(result[0]._current).toBe(true)
    // Existing conversation still present
    expect(result[1].id).toBe('other-conv')
  })

  // --- State 3: Post-refresh (normal) ---

  it('does not add bridge entry when conversation is already in fetched list', () => {
    const result = getDisplayConversations({
      conversations: [makeConversation('conv-uuid-123', 'Hello')],
      messages: makeMessages(['Hello', 'Hi there']),
      activeConversationId: 'conv-uuid-123',
      chatHistoryEnabled: true,
      saveMode: 'server',
    })

    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('conv-uuid-123')
    expect(result[0]._current).toBeUndefined()
    expect(result[0]._optimistic).toBeUndefined()
  })

  // --- Regression test: the full lifecycle ---

  it('transitions cleanly through all three states without gaps', () => {
    const msgs = makeMessages(['My question', 'The answer'])
    const base = { chatHistoryEnabled: true, saveMode: 'server' }

    // State 1: User just sent a message, backend hasn't saved yet
    const state1 = getDisplayConversations({
      ...base,
      conversations: [],
      messages: msgs,
      activeConversationId: null,
    })
    expect(state1).toHaveLength(1)
    expect(state1[0]._optimistic).toBe(true)
    expect(state1[0].title).toBe('My question')

    // State 2: Backend saved, but fetched list is stale (empty)
    const state2 = getDisplayConversations({
      ...base,
      conversations: [],
      messages: msgs,
      activeConversationId: 'saved-conv-id',
    })
    expect(state2).toHaveLength(1)
    expect(state2[0].id).toBe('saved-conv-id')
    expect(state2[0]._current).toBe(true)
    expect(state2[0].title).toBe('My question')

    // State 3: Fetched list has caught up
    const state3 = getDisplayConversations({
      ...base,
      conversations: [makeConversation('saved-conv-id', 'My question')],
      messages: msgs,
      activeConversationId: 'saved-conv-id',
    })
    expect(state3).toHaveLength(1)
    expect(state3[0].id).toBe('saved-conv-id')
    expect(state3[0]._current).toBeUndefined()
    expect(state3[0]._optimistic).toBeUndefined()
  })

  // --- Guard conditions ---

  it('returns fetched list as-is when chat history is disabled', () => {
    const existing = [makeConversation('a', 'Chat A')]
    const result = getDisplayConversations({
      conversations: existing,
      messages: makeMessages(['Hello']),
      activeConversationId: null,
      chatHistoryEnabled: false,
      saveMode: 'server',
    })

    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('a')
    expect(result[0]._optimistic).toBeUndefined()
  })

  it('returns fetched list as-is when saveMode is none (incognito)', () => {
    const result = getDisplayConversations({
      conversations: [],
      messages: makeMessages(['Secret question']),
      activeConversationId: null,
      chatHistoryEnabled: true,
      saveMode: 'none',
    })

    expect(result).toHaveLength(0)
  })

  it('returns fetched list as-is when there are no user messages', () => {
    const existing = [makeConversation('a', 'Chat A')]
    const result = getDisplayConversations({
      conversations: existing,
      messages: [],
      activeConversationId: null,
      chatHistoryEnabled: true,
      saveMode: 'server',
    })

    expect(result).toHaveLength(1)
    expect(result[0]._optimistic).toBeUndefined()
  })

  it('returns fetched list as-is when messages is null', () => {
    const result = getDisplayConversations({
      conversations: [],
      messages: null,
      activeConversationId: null,
      chatHistoryEnabled: true,
      saveMode: 'server',
    })

    expect(result).toHaveLength(0)
  })

  it('truncates long first messages to 200 characters for the title', () => {
    const longMsg = 'A'.repeat(300)
    const result = getDisplayConversations({
      conversations: [],
      messages: makeMessages([longMsg]),
      activeConversationId: null,
      chatHistoryEnabled: true,
      saveMode: 'server',
    })

    expect(result[0].title).toBe('A'.repeat(200))
  })

  it('uses "New conversation" as fallback title when first user message is empty', () => {
    const result = getDisplayConversations({
      conversations: [],
      messages: [{ role: 'user', content: '' }],
      activeConversationId: null,
      chatHistoryEnabled: true,
      saveMode: 'server',
    })

    expect(result[0].title).toBe('New conversation')
  })

  it('includes correct message_count in optimistic entry', () => {
    const msgs = makeMessages(['Q1', 'A1', 'Q2', 'A2'])
    const result = getDisplayConversations({
      conversations: [],
      messages: msgs,
      activeConversationId: null,
      chatHistoryEnabled: true,
      saveMode: 'server',
    })

    expect(result[0].message_count).toBe(4)
  })
})

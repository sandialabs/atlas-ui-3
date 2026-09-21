/**
 * The refuse-and-fall-back path for a joined run ending (issue #959).
 *
 * The append-only refresh is only safe because a transcript it cannot
 * reconcile still gets the full reload. That safety net is a single branch,
 * and it had no test: inverting it, or dropping the reload call, left every
 * suite green while silently leaving a diverged transcript stale on screen.
 */

import { describe, it, expect, vi } from 'vitest'
import { applyRunEndRefresh } from '../utils/runEndRefresh'

const record = { id: 'conv-1', messages: [{ role: 'user', content: 'hi' }] }

describe('applyRunEndRefresh', () => {
  it('appends and does NOT reload when the refresh reconciles', () => {
    const refreshJoinedConversation = vi.fn(() => true)
    const loadSavedConversation = vi.fn()
    const outcome = applyRunEndRefresh({ fullConv: record, refreshJoinedConversation, loadSavedConversation })

    expect(outcome).toBe('appended')
    expect(refreshJoinedConversation).toHaveBeenCalledWith(record)
    // The whole point: no full reload, so the scroll anchor stands.
    expect(loadSavedConversation).not.toHaveBeenCalled()
  })

  it('falls back to the full reload with the fetched record when the refresh refuses', () => {
    const refreshJoinedConversation = vi.fn(() => false)
    const loadSavedConversation = vi.fn()
    const outcome = applyRunEndRefresh({ fullConv: record, refreshJoinedConversation, loadSavedConversation })

    expect(outcome).toBe('reloaded')
    expect(loadSavedConversation).toHaveBeenCalledTimes(1)
    expect(loadSavedConversation).toHaveBeenCalledWith(record)
  })

  it('does nothing when the fetch failed or returned an error record', () => {
    for (const fullConv of [null, undefined, { error: 'boom' }]) {
      const refreshJoinedConversation = vi.fn(() => false)
      const loadSavedConversation = vi.fn()
      const outcome = applyRunEndRefresh({ fullConv, refreshJoinedConversation, loadSavedConversation })

      expect(outcome).toBe('skipped')
      expect(refreshJoinedConversation).not.toHaveBeenCalled()
      expect(loadSavedConversation).not.toHaveBeenCalled()
    }
  })
})

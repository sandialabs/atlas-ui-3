/**
 * The conversation history is paged (issue #1085): the sidebar shows the
 * newest page and lets the user walk back through the older ones.
 *
 * These tests cover the bookkeeping the walk depends on -- where the next
 * page starts, that a page is appended rather than replacing what is on
 * screen, that a row is never shown twice, and that a failed page leaves the
 * list alone and can be retried.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useConversationHistory } from '../hooks/useConversationHistory'

const conv = (id) => ({ id, title: id, updated_at: '2026-01-01T00:00:00Z' })

const page = (ids, total) => ({
  conversations: ids.map(conv),
  total: total ?? ids.length,
  has_more: total !== undefined && total > ids.length,
})

const jsonResponse = (body, ok = true) => ({
  ok,
  status: ok ? 200 : 500,
  json: async () => body,
})

describe('useConversationHistory paging', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('asks for the newest page first and reports that older ones exist', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(page(['a', 'b'], 5)))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useConversationHistory())
    await act(async () => { await result.current.fetchConversations() })

    expect(fetchMock.mock.calls[0][0]).toBe('/api/conversations?limit=50&offset=0')
    expect(result.current.conversations.map(c => c.id)).toEqual(['a', 'b'])
    expect(result.current.hasMore).toBe(true)
  })

  it('appends the next page without replacing or duplicating what is on screen', async () => {
    const first = page(['a', 'b'], 4)
    const second = page(['b', 'c'], 4)
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(first))
      .mockResolvedValueOnce(jsonResponse(second))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useConversationHistory())
    await act(async () => { await result.current.fetchConversations() })
    await act(async () => { await result.current.loadMore() })

    // The next page starts after the rows already held.
    expect(fetchMock.mock.calls[1][0]).toBe('/api/conversations?limit=50&offset=2')
    // "b" came back a second time and is not shown twice.
    expect(result.current.conversations.map(c => c.id)).toEqual(['a', 'b', 'c'])
    expect(result.current.hasMore).toBe(true)
  })

  it('keeps the next page in the same tag as the listing', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(page(['a'], 2)))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useConversationHistory())
    await act(async () => { await result.current.filterByTag('important') })
    await act(async () => { await result.current.loadMore() })

    expect(fetchMock.mock.calls[0][0]).toContain('tag=important')
    expect(fetchMock.mock.calls[1][0]).toContain('tag=important')
  })

  it('reports the end of the history and stops offering a page', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(page(['a'], 3)))
      .mockResolvedValueOnce(jsonResponse(page(['b', 'c'], 3)))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useConversationHistory())
    await act(async () => { await result.current.fetchConversations() })
    await act(async () => { await result.current.loadMore() })

    expect(result.current.conversations.map(c => c.id)).toEqual(['a', 'b', 'c'])
    expect(result.current.hasMore).toBe(false)
  })

  it('keeps the list and the error apart when a page fails, and retries', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(page(['a'], 2)))
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce(jsonResponse(page(['b'], 2)))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useConversationHistory())
    await act(async () => { await result.current.fetchConversations() })
    await act(async () => { await result.current.loadMore() })

    expect(result.current.conversations.map(c => c.id)).toEqual(['a'])
    expect(result.current.loadMoreError).toBeTruthy()
    expect(result.current.loadingMore).toBe(false)

    await act(async () => { await result.current.loadMore() })
    expect(result.current.loadMoreError).toBeNull()
    expect(result.current.conversations.map(c => c.id)).toEqual(['a', 'b'])
  })

  it('keeps the older rows a refresh would otherwise throw away', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(page(['a'], 3)))
      .mockResolvedValueOnce(jsonResponse(page(['b'], 3)))
      .mockResolvedValueOnce(jsonResponse(page(['a', 'b'], 3)))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useConversationHistory())
    await act(async () => { await result.current.fetchConversations() })
    await act(async () => { await result.current.loadMore() })
    await act(async () => { await result.current.refreshConversations() })

    // The older row survives the refresh, and the newest page stays in front.
    expect(result.current.conversations.map(c => c.id)).toEqual(['a', 'b'])
  })

  it('follows the history of the server when a conversation is deleted', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(page(['a', 'b'], 3)))
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ deleted: true }) })
      .mockResolvedValueOnce(jsonResponse(page(['c'], 2)))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useConversationHistory())
    await act(async () => { await result.current.fetchConversations() })
    await act(async () => { await result.current.deleteConversation('a') })
    await act(async () => { await result.current.loadMore() })

    // The deleted row shortens the server's list, so the next page starts
    // one row earlier -- otherwise the row after it would be skipped.
    expect(fetchMock.mock.calls[2][0]).toBe('/api/conversations?limit=50&offset=1')
    expect(result.current.conversations.map(c => c.id)).toEqual(['b', 'c'])
  })

  it('does not page a search result set', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(page(['a'], 3)))
      .mockResolvedValueOnce(jsonResponse(page(['hit'], 1)))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useConversationHistory())
    await act(async () => { await result.current.fetchConversations() })
    await act(async () => { await result.current.searchConversations('hit') })

    expect(result.current.conversations.map(c => c.id)).toEqual(['hit'])
    expect(result.current.hasMore).toBe(false)
  })
})

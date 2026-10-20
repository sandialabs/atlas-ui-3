import { useState, useCallback, useRef } from 'react'
import {
  CONVERSATION_PAGE_SIZE,
  appendUnique,
  mergeFreshFirst,
} from '../utils/conversationPagination'

/**
 * Hook for conversation history REST API operations.
 * Provides methods for listing, searching, deleting, and tagging conversations.
 */
export function useConversationHistory() {
  const [conversations, setConversations] = useState([])
  const [tags, setTags] = useState([])
  const [loading, setLoading] = useState(false)
  // The next page of older conversations is on its way (issue #1085).
  const [loadingMore, setLoadingMore] = useState(false)
  // Why the last "load more" failed, so the sidebar can offer a retry.
  const [loadMoreError, setLoadMoreError] = useState(null)
  // Whether older conversations remain beyond the ones the list holds.
  const [hasMore, setHasMore] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [activeTag, setActiveTag] = useState(null)
  const abortRef = useRef(null)
  // The rows the list currently holds. The sidebar renders them from state,
  // but the paging bookkeeping needs the value at request time rather than
  // the one captured when a callback was created.
  const conversationsRef = useRef([])
  // The position in the server's order the next page starts at. It counts
  // the rows the server has handed over, which is not always the number of
  // rows on screen: a page can repeat a row when the order shifts between
  // two requests, and the server's position still moved on.
  const offsetRef = useRef(0)
  // How many conversations the current listing holds in total, so the client
  // can tell "there is an older page" from "that is all of them".
  const totalRef = useRef(0)
  // Which listing the requests belong to. A reset (a new tag, a cleared
  // search) starts a new one, and a page that was still on its way for the
  // previous one is dropped instead of being appended to the wrong list.
  const generationRef = useRef(0)
  // Guards against a second "load more" while one is in flight.
  const loadingMoreRef = useRef(false)

  // Store a new list and re-derive the paging state from it. The list is a
  // prefix of the server's order, so the next page starts after the rows it
  // holds. A total of `null` means the count is not known; a full page then
  // still suggests there is more behind it.
  const applyList = useCallback((next, total, hasMore) => {
    const rows = Array.isArray(next) ? next : []
    conversationsRef.current = rows
    offsetRef.current = rows.length
    if (typeof total === 'number') totalRef.current = total
    setConversations(rows)
    if (typeof hasMore === 'boolean') setHasMore(hasMore)
    else if (typeof total === 'number') setHasMore(total > rows.length)
    else if (total === null) setHasMore(rows.length >= CONVERSATION_PAGE_SIZE)
    else setHasMore(totalRef.current > rows.length)
  }, [])

  // Change the rows on screen without moving the paging state: a rename or a
  // new tag does not change how much of the history is left.
  const replaceRows = useCallback((rows) => {
    conversationsRef.current = rows
    setConversations(rows)
  }, [])

  // Take conversations out of the list. The server's history is that much
  // shorter now, so the next page starts that much earlier and the count of
  // what is left drops with it.
  const removeRows = useCallback((ids) => {
    const removed = new Set(ids)
    const rows = conversationsRef.current.filter(c => !removed.has(c.id))
    const count = conversationsRef.current.length - rows.length
    conversationsRef.current = rows
    offsetRef.current = Math.max(0, offsetRef.current - count)
    totalRef.current = Math.max(0, totalRef.current - count)
    setConversations(rows)
  }, [])

  const fetchConversations = useCallback(async (options = {}) => {
    const { limit = CONVERSATION_PAGE_SIZE, offset = 0, tag = null } = options
    // A new listing: anything still on its way for the previous one no
    // longer belongs here.
    const generation = ++generationRef.current
    setLoading(true)
    setLoadMoreError(null)
    try {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
      if (tag) params.set('tag', tag)
      const res = await fetch(`/api/conversations?${params}`)
      if (!res.ok) return
      const data = await res.json()
      if (generationRef.current !== generation) return
      applyList(data.conversations || [], data.total, data.has_more)
    } catch (e) {
      console.error('Failed to fetch conversations:', e)
    } finally {
      setLoading(false)
    }
  }, [applyList])

  // Re-read the newest page and keep the older rows the user already
  // loaded, so a refresh (a message saved, a background run finished) does
  // not send the user back to the top of the history.
  const refreshConversations = useCallback(async (options = {}) => {
    const { limit = CONVERSATION_PAGE_SIZE, tag = null } = options
    const generation = generationRef.current
    try {
      const params = new URLSearchParams({ limit: String(limit), offset: '0' })
      if (tag) params.set('tag', tag)
      const res = await fetch(`/api/conversations?${params}`)
      if (!res.ok) return
      const data = await res.json()
      if (generationRef.current !== generation) return
      const fresh = data.conversations || []
      // The page's own "more remain" flag describes the window it covers,
      // not the merged one, so the count decides here.
      applyList(
        mergeFreshFirst(fresh, conversationsRef.current, data.total),
        data.total
      )
    } catch (e) {
      console.error('Failed to refresh conversations:', e)
    }
  }, [applyList])

  // Fetch the next page of older conversations and append it. The rows the
  // list already holds stay exactly where they are, so neither the scroll
  // position nor the open conversation changes (issue #1085).
  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current) return
    loadingMoreRef.current = true
    const generation = generationRef.current
    setLoadingMore(true)
    setLoadMoreError(null)
    try {
      const params = new URLSearchParams({
        limit: String(CONVERSATION_PAGE_SIZE),
        offset: String(offsetRef.current),
      })
      if (activeTag) params.set('tag', activeTag)
      const res = await fetch(`/api/conversations?${params}`)
      if (!res.ok) throw new Error(`Failed to load older conversations (${res.status})`)
      const data = await res.json()
      if (generationRef.current !== generation) return
      const page = data.conversations || []
      const merged = appendUnique(conversationsRef.current, page)
      conversationsRef.current = merged
      // The server's position moved on by the whole page, even when a row
      // of it was already on screen: the next page starts after the page
      // the server just sent.
      offsetRef.current += page.length
      if (typeof data.total === 'number') totalRef.current = data.total
      setConversations(merged)
      // The server knows whether the history continues past this page.
      if (typeof data.has_more === 'boolean') setHasMore(data.has_more)
      else setHasMore(totalRef.current > offsetRef.current)
    } catch (e) {
      console.error('Failed to load more conversations:', e)
      setLoadMoreError('Could not load older conversations. Try again.')
    } finally {
      loadingMoreRef.current = false
      setLoadingMore(false)
    }
  }, [activeTag])

  const searchConversations = useCallback(async (query) => {
    if (!query || !query.trim()) {
      setSearchQuery('')
      // Respect active tag filter when clearing search
      return fetchConversations(activeTag ? { tag: activeTag } : {})
    }
    setSearchQuery(query)
    setLoading(true)
    // A search is its own listing: a page still on its way for the previous
    // one must not be appended to the results.
    const generation = ++generationRef.current
    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const params = new URLSearchParams({ q: query.trim(), limit: '20' })
      const res = await fetch(`/api/conversations/search?${params}`, { signal: controller.signal })
      if (!res.ok) return
      const data = await res.json()
      if (generationRef.current !== generation) return
      // Search results are their own list: there is nothing older to page
      // through, so the paging state is reset with them.
      applyList(data.conversations || [], (data.conversations || []).length)
    } catch (e) {
      if (e.name !== 'AbortError') console.error('Search failed:', e)
    } finally {
      setLoading(false)
    }
  }, [fetchConversations, activeTag, applyList])

  // `complianceLevel` is the active level ('' for none). When given, the server
  // refuses a conversation recorded under another classification (issue #1042)
  // with 409 and no content; that comes back as `{ classificationRefused }`.
  const loadConversation = useCallback(async (conversationId, { complianceLevel } = {}) => {
    try {
      const query = complianceLevel === undefined
        ? ''
        : `?${new URLSearchParams({ compliance_level: complianceLevel ?? '' })}`
      const res = await fetch(`/api/conversations/${conversationId}${query}`)
      if (res.status === 409) {
        const body = await res.json().catch(() => ({}))
        return { classificationRefused: true, message: body.detail || 'This conversation cannot be opened under the selected compliance level.' }
      }
      if (!res.ok) return null
      return await res.json()
    } catch (e) {
      console.error('Failed to load conversation:', e)
      return null
    }
  }, [])

  const deleteConversation = useCallback(async (conversationId) => {
    try {
      const res = await fetch(`/api/conversations/${conversationId}`, { method: 'DELETE' })
      if (!res.ok) return false
      removeRows([conversationId])
      return true
    } catch (e) {
      console.error('Failed to delete conversation:', e)
      return false
    }
  }, [removeRows])

  const deleteMultiple = useCallback(async (ids) => {
    try {
      const res = await fetch('/api/conversations/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      })
      if (!res.ok) return 0
      const data = await res.json()
      removeRows(ids)
      return data.deleted_count || 0
    } catch (e) {
      console.error('Failed to delete conversations:', e)
      return 0
    }
  }, [removeRows])

  const deleteAll = useCallback(async () => {
    try {
      const res = await fetch('/api/conversations', { method: 'DELETE' })
      if (!res.ok) return 0
      const data = await res.json()
      applyList([], 0, false)
      return data.deleted_count || 0
    } catch (e) {
      console.error('Failed to delete all conversations:', e)
      return 0
    }
  }, [applyList])

  const downloadAll = useCallback(async () => {
    try {
      const res = await fetch('/api/conversations/export')
      if (!res.ok) return false
      const data = await res.json()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
      a.download = `conversations-export-${ts}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      return true
    } catch (e) {
      console.error('Failed to download all conversations:', e)
      return false
    }
  }, [])

  const addTag = useCallback(async (conversationId, tagName) => {
    try {
      const res = await fetch(`/api/conversations/${conversationId}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: tagName }),
      })
      if (!res.ok) return null
      const data = await res.json()
      // Update local state to reflect new tag (deduplicate)
      replaceRows(conversationsRef.current.map(c =>
        c.id === conversationId
          ? { ...c, tags: [...new Set([...(c.tags || []), tagName])] }
          : c
      ))
      return data.tag_id
    } catch (e) {
      console.error('Failed to add tag:', e)
      return null
    }
  }, [replaceRows])

  const removeTag = useCallback(async (conversationId, tagId) => {
    try {
      const res = await fetch(`/api/conversations/${conversationId}/tags/${tagId}`, { method: 'DELETE' })
      return res.ok
    } catch (e) {
      console.error('Failed to remove tag:', e)
      return false
    }
  }, [])

  const updateTitle = useCallback(async (conversationId, title) => {
    try {
      const res = await fetch(`/api/conversations/${conversationId}/title`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      })
      if (!res.ok) return false
      replaceRows(conversationsRef.current.map(c =>
        c.id === conversationId ? { ...c, title } : c
      ))
      return true
    } catch (e) {
      console.error('Failed to update title:', e)
      return false
    }
  }, [replaceRows])

  const fetchTags = useCallback(async () => {
    try {
      const res = await fetch('/api/conversations/tags/list')
      if (!res.ok) return
      const data = await res.json()
      setTags(data.tags || [])
    } catch (e) {
      console.error('Failed to fetch tags:', e)
    }
  }, [])

  const filterByTag = useCallback((tagName) => {
    setActiveTag(tagName)
    // A different tag is a different list, so the paging state starts over.
    if (tagName) {
      fetchConversations({ tag: tagName })
    } else {
      fetchConversations()
    }
  }, [fetchConversations])

  return {
    conversations,
    tags,
    loading,
    loadingMore,
    loadMoreError,
    hasMore,
    searchQuery,
    activeTag,
    fetchConversations,
    refreshConversations,
    loadMore,
    searchConversations,
    loadConversation,
    deleteConversation,
    deleteMultiple,
    deleteAll,
    downloadAll,
    addTag,
    removeTag,
    updateTitle,
    fetchTags,
    filterByTag,
  }
}

/**
 * Hook for browser-local conversation history (IndexedDB).
 *
 * Drop-in replacement for useConversationHistory -- exposes the same
 * return shape so the Sidebar can swap between local and server storage
 * without changing its rendering logic.
 *
 * Scoped to `user`, the signed-in account (issue #1050): another account's
 * locally saved conversations on this browser are never listed, opened,
 * exported or deleted. Until the user is known nothing is.
 *
 * The listing is paged like the server one (issue #1085): the newest page
 * is shown first and the older ones are fetched when the user asks for them.
 */

import { useState, useCallback, useEffect, useRef } from 'react'
import * as db from '../utils/localConversationDB'
import { isKnownUser } from '../utils/userStorage'
import {
  CONVERSATION_PAGE_SIZE,
  appendUnique,
  mergeFreshFirst,
} from '../utils/conversationPagination'

export function useLocalConversationHistory(user) {
  const owner = isKnownUser(user) ? user : null
  const [conversations, setConversations] = useState([])
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState(null)
  const [hasMore, setHasMore] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  // Tags are not supported for local storage (server-only feature)
  const tags = []
  const activeTag = null

  // The owner each in-flight read was issued for. When the signed-in user
  // changes, a read still in flight belongs to the previous account and must
  // not repopulate the list (issue #1050).
  const ownerRef = useRef(owner)
  ownerRef.current = owner

  // The rows the list holds, and where the next page starts. Kept beside
  // the state so a request always uses the current values (issue #1085).
  const conversationsRef = useRef([])
  const offsetRef = useRef(0)
  const totalRef = useRef(0)
  // Which listing the requests belong to. A reset (a cleared search, a new
  // account) starts a new one, and a page that was still on its way for the
  // previous one is dropped instead of being appended to the wrong list.
  const generationRef = useRef(0)
  const loadingMoreRef = useRef(false)

  // A different account signed in: drop the previous one's list.
  useEffect(() => {
    generationRef.current += 1
    setConversations([])
    conversationsRef.current = []
    offsetRef.current = 0
    totalRef.current = 0
    setHasMore(false)
  }, [owner])

  // Store a new list and re-derive the paging state from it. A total of
  // `null` means the count is not known; a full page then still suggests
  // there is more behind it.
  const applyList = useCallback((next, total) => {
    const rows = Array.isArray(next) ? next : []
    conversationsRef.current = rows
    offsetRef.current = rows.length
    if (typeof total === 'number') {
      totalRef.current = total
      setHasMore(total > rows.length)
    } else if (total === null) {
      setHasMore(rows.length >= CONVERSATION_PAGE_SIZE)
    } else {
      setHasMore(totalRef.current > rows.length)
    }
    setConversations(rows)
  }, [])

  // How many conversations the account has stored. A storage backend that
  // cannot answer leaves the total unknown, and the list then simply has no
  // older page to offer.
  const countOwned = useCallback(async (requestedOwner) => {
    try {
      return await db.countConversations(requestedOwner)
    } catch {
      return null
    }
  }, [])

  const fetchConversations = useCallback(async () => {
    const requestedOwner = owner
    // A new listing: a page still on its way for the previous one no longer
    // belongs here.
    const generation = ++generationRef.current
    setLoading(true)
    setLoadMoreError(null)
    try {
      const list = await db.listConversations(requestedOwner, CONVERSATION_PAGE_SIZE, 0)
      if (ownerRef.current !== requestedOwner) return
      if (generationRef.current !== generation) return
      applyList(list, await countOwned(requestedOwner))
    } catch (e) {
      console.error('Failed to fetch local conversations:', e)
    } finally {
      if (ownerRef.current === requestedOwner) setLoading(false)
    }
  }, [owner, applyList, countOwned])

  // Re-read the newest page and keep the older rows already loaded.
  const refreshConversations = useCallback(async () => {
    const requestedOwner = owner
    const generation = generationRef.current
    try {
      const list = await db.listConversations(requestedOwner, CONVERSATION_PAGE_SIZE, 0)
      if (ownerRef.current !== requestedOwner) return
      if (generationRef.current !== generation) return
      applyList(mergeFreshFirst(list, conversationsRef.current), await countOwned(requestedOwner))
    } catch (e) {
      console.error('Failed to refresh local conversations:', e)
    }
  }, [owner, applyList, countOwned])

  // Fetch the next page of older conversations and append it.
  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current) return
    const requestedOwner = owner
    const generation = generationRef.current
    loadingMoreRef.current = true
    setLoadingMore(true)
    setLoadMoreError(null)
    try {
      const page = await db.listConversations(
        requestedOwner,
        CONVERSATION_PAGE_SIZE,
        offsetRef.current
      )
      if (ownerRef.current !== requestedOwner) return
      if (generationRef.current !== generation) return
      applyList(
        appendUnique(conversationsRef.current, page),
        await countOwned(requestedOwner)
      )
    } catch (e) {
      console.error('Failed to load more local conversations:', e)
      setLoadMoreError('Could not load older conversations. Try again.')
    } finally {
      loadingMoreRef.current = false
      setLoadingMore(false)
    }
  }, [owner, applyList, countOwned])

  const searchConversations = useCallback(async (query) => {
    if (!query || !query.trim()) {
      setSearchQuery('')
      return fetchConversations()
    }
    const requestedOwner = owner
    // A search is its own listing: a page still on its way for the previous
    // one must not be appended to the results.
    const generation = ++generationRef.current
    setSearchQuery(query)
    setLoading(true)
    try {
      const results = await db.searchConversations(requestedOwner, query.trim(), 20)
      if (ownerRef.current !== requestedOwner) return
      if (generationRef.current !== generation) return
      // Search results are their own list: there is nothing older to page
      // through, so the paging state is reset with them.
      applyList(results, results.length)
    } catch (e) {
      console.error('Local search failed:', e)
    } finally {
      if (ownerRef.current === requestedOwner) setLoading(false)
    }
  }, [owner, fetchConversations, applyList])

  const loadConversation = useCallback(async (conversationId) => {
    const requestedOwner = owner
    try {
      const conv = await db.getConversation(conversationId, requestedOwner)
      if (ownerRef.current !== requestedOwner) return null
      return conv
    } catch (e) {
      console.error('Failed to load local conversation:', e)
      return null
    }
  }, [owner])

  const deleteConversation = useCallback(async (conversationId) => {
    const requestedOwner = owner
    try {
      const deleted = await db.deleteConversation(conversationId, requestedOwner)
      if (deleted && ownerRef.current === requestedOwner) {
        applyList(
          conversationsRef.current.filter((c) => c.id !== conversationId),
          Math.max(0, totalRef.current - 1)
        )
      }
      return deleted
    } catch (e) {
      console.error('Failed to delete local conversation:', e)
      return false
    }
  }, [owner, applyList])

  const deleteAll = useCallback(async () => {
    const requestedOwner = owner
    try {
      const count = await db.deleteAllConversations(requestedOwner)
      if (ownerRef.current === requestedOwner) {
        applyList([], 0)
      }
      return count
    } catch (e) {
      console.error('Failed to delete all local conversations:', e)
      return 0
    }
  }, [owner, applyList])

  const downloadAll = useCallback(async () => {
    const requestedOwner = owner
    try {
      const all = await db.exportAllConversations(requestedOwner)
      const exportData = {
        export_date: new Date().toISOString(),
        source: 'browser-local',
        conversation_count: all.length,
        conversations: all,
      }
      const blob = new Blob([JSON.stringify(exportData, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
      a.download = `local-conversations-export-${ts}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      return true
    } catch (e) {
      console.error('Failed to export local conversations:', e)
      return false
    }
  }, [owner])

  // Stubs for tag operations (not supported for browser-local storage)
  const fetchTags = useCallback(async () => {}, [])
  const filterByTag = useCallback(() => {}, [])
  const addTag = useCallback(async () => null, [])
  const removeTag = useCallback(async () => false, [])
  const updateTitle = useCallback(async (conversationId, title) => {
    const requestedOwner = owner
    try {
      const conv = await db.getConversation(conversationId, requestedOwner)
      if (!conv || ownerRef.current !== requestedOwner) return false
      conv.title = title
      await db.saveConversation(conv, requestedOwner)
      if (ownerRef.current !== requestedOwner) return false
      applyList(
        conversationsRef.current.map((c) => (c.id === conversationId ? { ...c, title } : c))
      )
      return true
    } catch (e) {
      console.error('Failed to update local title:', e)
      return false
    }
  }, [owner, applyList])

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
    deleteMultiple: async () => 0,
    deleteAll,
    downloadAll,
    addTag,
    removeTag,
    updateTitle,
    fetchTags,
    filterByTag,
  }
}

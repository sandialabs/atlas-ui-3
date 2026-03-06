/**
 * Hook for browser-local conversation history (IndexedDB).
 *
 * Drop-in replacement for useConversationHistory -- exposes the same
 * return shape so the Sidebar can swap between local and server storage
 * without changing its rendering logic.
 */

import { useState, useCallback } from 'react'
import * as db from '../utils/localConversationDB'

export function useLocalConversationHistory() {
  const [conversations, setConversations] = useState([])
  const [loading, setLoading] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  // Tags are not supported for local storage (server-only feature)
  const tags = []
  const activeTag = null

  const fetchConversations = useCallback(async () => {
    setLoading(true)
    try {
      const list = await db.listConversations(50)
      setConversations(list)
    } catch (e) {
      console.error('Failed to fetch local conversations:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  const searchConversations = useCallback(async (query) => {
    if (!query || !query.trim()) {
      setSearchQuery('')
      return fetchConversations()
    }
    setSearchQuery(query)
    setLoading(true)
    try {
      const results = await db.searchConversations(query.trim(), 20)
      setConversations(results)
    } catch (e) {
      console.error('Local search failed:', e)
    } finally {
      setLoading(false)
    }
  }, [fetchConversations])

  const loadConversation = useCallback(async (conversationId) => {
    try {
      return await db.getConversation(conversationId)
    } catch (e) {
      console.error('Failed to load local conversation:', e)
      return null
    }
  }, [])

  const deleteConversation = useCallback(async (conversationId) => {
    try {
      await db.deleteConversation(conversationId)
      setConversations((prev) => prev.filter((c) => c.id !== conversationId))
      return true
    } catch (e) {
      console.error('Failed to delete local conversation:', e)
      return false
    }
  }, [])

  const deleteAll = useCallback(async () => {
    try {
      const count = await db.deleteAllConversations()
      setConversations([])
      return count
    } catch (e) {
      console.error('Failed to delete all local conversations:', e)
      return 0
    }
  }, [])

  const downloadAll = useCallback(async () => {
    try {
      const all = await db.exportAllConversations()
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
  }, [])

  // Stubs for tag operations (not supported for browser-local storage)
  const fetchTags = useCallback(async () => {}, [])
  const filterByTag = useCallback(() => {}, [])
  const addTag = useCallback(async () => null, [])
  const removeTag = useCallback(async () => false, [])
  const updateTitle = useCallback(async (conversationId, title) => {
    try {
      const conv = await db.getConversation(conversationId)
      if (!conv) return false
      conv.title = title
      await db.saveConversation(conv)
      setConversations((prev) =>
        prev.map((c) => (c.id === conversationId ? { ...c, title } : c))
      )
      return true
    } catch (e) {
      console.error('Failed to update local title:', e)
      return false
    }
  }, [])

  return {
    conversations,
    tags,
    loading,
    searchQuery,
    activeTag,
    fetchConversations,
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

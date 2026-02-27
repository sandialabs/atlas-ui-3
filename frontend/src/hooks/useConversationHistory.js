import { useState, useCallback, useRef } from 'react'

/**
 * Hook for conversation history REST API operations.
 * Provides methods for listing, searching, deleting, and tagging conversations.
 */
export function useConversationHistory() {
  const [conversations, setConversations] = useState([])
  const [tags, setTags] = useState([])
  const [loading, setLoading] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [activeTag, setActiveTag] = useState(null)
  const abortRef = useRef(null)

  const fetchConversations = useCallback(async (options = {}) => {
    const { limit = 50, offset = 0, tag = null } = options
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
      if (tag) params.set('tag', tag)
      const res = await fetch(`/api/conversations?${params}`)
      if (!res.ok) return
      const data = await res.json()
      setConversations(data.conversations || [])
    } catch (e) {
      console.error('Failed to fetch conversations:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  const searchConversations = useCallback(async (query) => {
    if (!query || !query.trim()) {
      setSearchQuery('')
      // Respect active tag filter when clearing search
      return fetchConversations(activeTag ? { tag: activeTag } : {})
    }
    setSearchQuery(query)
    setLoading(true)
    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const params = new URLSearchParams({ q: query.trim(), limit: '20' })
      const res = await fetch(`/api/conversations/search?${params}`, { signal: controller.signal })
      if (!res.ok) return
      const data = await res.json()
      setConversations(data.conversations || [])
    } catch (e) {
      if (e.name !== 'AbortError') console.error('Search failed:', e)
    } finally {
      setLoading(false)
    }
  }, [fetchConversations, activeTag])

  const loadConversation = useCallback(async (conversationId) => {
    try {
      const res = await fetch(`/api/conversations/${conversationId}`)
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
      setConversations(prev => prev.filter(c => c.id !== conversationId))
      return true
    } catch (e) {
      console.error('Failed to delete conversation:', e)
      return false
    }
  }, [])

  const deleteMultiple = useCallback(async (ids) => {
    try {
      const res = await fetch('/api/conversations/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      })
      if (!res.ok) return 0
      const data = await res.json()
      setConversations(prev => prev.filter(c => !ids.includes(c.id)))
      return data.deleted_count || 0
    } catch (e) {
      console.error('Failed to delete conversations:', e)
      return 0
    }
  }, [])

  const deleteAll = useCallback(async () => {
    try {
      const res = await fetch('/api/conversations', { method: 'DELETE' })
      if (!res.ok) return 0
      const data = await res.json()
      setConversations([])
      return data.deleted_count || 0
    } catch (e) {
      console.error('Failed to delete all conversations:', e)
      return 0
    }
  }, [])

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
      setConversations(prev => prev.map(c =>
        c.id === conversationId
          ? { ...c, tags: [...new Set([...(c.tags || []), tagName])] }
          : c
      ))
      return data.tag_id
    } catch (e) {
      console.error('Failed to add tag:', e)
      return null
    }
  }, [])

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
      setConversations(prev => prev.map(c =>
        c.id === conversationId ? { ...c, title } : c
      ))
      return true
    } catch (e) {
      console.error('Failed to update title:', e)
      return false
    }
  }, [])

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
    searchQuery,
    activeTag,
    fetchConversations,
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

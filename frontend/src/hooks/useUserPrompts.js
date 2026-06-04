import { useState, useCallback, useEffect } from 'react'

/**
 * Manages the per-user custom prompt library (issue #153).
 *
 * Prompts are persisted server-side (DuckDB/Postgres) via /api/user-prompts and
 * can be selected as the active prompt for a chat, replacing the default system
 * prompt. CRUD here keeps the local list in sync without re-fetching.
 */
export function useUserPrompts() {
  const [prompts, setPrompts] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchPrompts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/user-prompts')
      if (!res.ok) throw new Error(`Failed to load prompts (${res.status})`)
      const data = await res.json()
      setPrompts(Array.isArray(data.prompts) ? data.prompts : [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const createPrompt = useCallback(async (title, content) => {
    setError(null)
    try {
      const res = await fetch('/api/user-prompts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, content }),
      })
      if (!res.ok) throw new Error(`Failed to create prompt (${res.status})`)
      const data = await res.json()
      setPrompts(prev => [data.prompt, ...prev])
      return data.prompt
    } catch (e) {
      setError(e.message)
      return null
    }
  }, [])

  const updatePrompt = useCallback(async (id, title, content) => {
    setError(null)
    try {
      const res = await fetch(`/api/user-prompts/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, content }),
      })
      if (!res.ok) throw new Error(`Failed to update prompt (${res.status})`)
      const data = await res.json()
      setPrompts(prev => prev.map(p => (p.id === id ? data.prompt : p)))
      return data.prompt
    } catch (e) {
      setError(e.message)
      return null
    }
  }, [])

  const deletePrompt = useCallback(async (id) => {
    setError(null)
    try {
      const res = await fetch(`/api/user-prompts/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`Failed to delete prompt (${res.status})`)
      setPrompts(prev => prev.filter(p => p.id !== id))
      return true
    } catch (e) {
      setError(e.message)
      return false
    }
  }, [])

  useEffect(() => {
    fetchPrompts()
  }, [fetchPrompts])

  return {
    prompts,
    loading,
    error,
    fetchPrompts,
    createPrompt,
    updatePrompt,
    deletePrompt,
  }
}

export default useUserPrompts

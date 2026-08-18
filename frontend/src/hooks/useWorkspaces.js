import { useState, useCallback, useEffect } from 'react'

/**
 * Whether a persisted active-workspace id should be dropped.
 *
 * The pointer survives refreshes in localStorage, but both facts it is checked
 * against arrive asynchronously, and treating either as known too early wipes
 * the pointer on every page load:
 *
 * - `configReady` -- feature flags default to off until the config payload
 *   lands, so an early check reads the feature as disabled.
 * - `loaded` -- the workspace list is empty until the fetch resolves, so
 *   "not in the list" is meaningless before then.
 */
export function isStaleWorkspacePointer({
  activeWorkspaceId,
  configReady,
  enabled,
  loaded,
  workspaces,
}) {
  if (!activeWorkspaceId) return false
  if (!configReady) return false
  if (!enabled) return true
  if (!loaded) return false
  return !(workspaces || []).some(w => w.id === activeWorkspaceId)
}

/**
 * Manages the per-user workspace library.
 *
 * A workspace is a named bundle of chat-context selections (active prompt, RAG
 * data sources, MCP tools) persisted server-side via /api/workspaces, so a user
 * can switch between contexts like work/home/project-A in one click. Applying a
 * workspace is purely client-side -- see useSelections.applyWorkspace.
 *
 * CRUD here keeps the local list in sync without re-fetching, mirroring
 * useUserPrompts.
 */
export function useWorkspaces(enabled = true) {
  const [workspaces, setWorkspaces] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  // False until a list has actually come back. An empty `workspaces` before the
  // first fetch is indistinguishable from "this user has none", so consumers
  // need this to avoid acting on a list that has not loaded yet.
  const [loaded, setLoaded] = useState(false)

  const fetchWorkspaces = useCallback(async () => {
    if (!enabled) {
      setWorkspaces([])
      setLoading(false)
      setError(null)
      setLoaded(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/workspaces')
      if (!res.ok) throw new Error(`Failed to load workspaces (${res.status})`)
      const data = await res.json()
      setWorkspaces(Array.isArray(data.workspaces) ? data.workspaces : [])
      // Only a successful response counts as loaded: a failed fetch must not
      // let consumers treat the empty list as authoritative.
      setLoaded(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [enabled])

  const createWorkspace = useCallback(async (name, config, description = null) => {
    if (!enabled) return null
    setError(null)
    try {
      const res = await fetch('/api/workspaces', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, config, description }),
      })
      if (!res.ok) throw new Error(`Failed to create workspace (${res.status})`)
      const data = await res.json()
      setWorkspaces(prev => [data.workspace, ...prev])
      return data.workspace
    } catch (e) {
      setError(e.message)
      return null
    }
  }, [enabled])

  const updateWorkspace = useCallback(async (id, updates) => {
    if (!enabled) return null
    setError(null)
    try {
      const res = await fetch(`/api/workspaces/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      })
      if (!res.ok) throw new Error(`Failed to update workspace (${res.status})`)
      const data = await res.json()
      setWorkspaces(prev => prev.map(w => (w.id === id ? data.workspace : w)))
      return data.workspace
    } catch (e) {
      setError(e.message)
      return null
    }
  }, [enabled])

  const deleteWorkspace = useCallback(async (id) => {
    if (!enabled) return false
    setError(null)
    try {
      const res = await fetch(`/api/workspaces/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`Failed to delete workspace (${res.status})`)
      setWorkspaces(prev => prev.filter(w => w.id !== id))
      return true
    } catch (e) {
      setError(e.message)
      return false
    }
  }, [enabled])

  useEffect(() => {
    fetchWorkspaces()
  }, [fetchWorkspaces])

  return {
    workspaces,
    loading,
    loaded,
    error,
    fetchWorkspaces,
    createWorkspace,
    updateWorkspace,
    deleteWorkspace,
  }
}

export default useWorkspaces

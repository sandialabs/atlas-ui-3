import { useState, useCallback, useEffect } from 'react'

/**
 * Team-shared prompt library (issue #1169), backed by /api/prompts.
 *
 * `entries` is every entry the signed-in user may see (scope "all"); the chat
 * prompt selector reads it, and the send path uses it to refuse a selection
 * that is no longer available instead of letting it silently fall back. The
 * server re-checks access on every request and chat turn -- this list is a
 * convenience for the UI, never the security boundary.
 */

async function request(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: options.body ? { 'Content-Type': 'application/json', ...(options.headers || {}) } : options.headers,
  })
  let data = null
  try {
    data = await res.json()
  } catch {
    data = null
  }
  if (!res.ok) {
    const detail = data && typeof data.detail === 'string' ? data.detail : null
    const error = new Error(detail || `Request failed (${res.status})`)
    error.status = res.status
    throw error
  }
  return data
}

const enc = encodeURIComponent

export function usePromptLibrary(enabled = true) {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState(null)
  // A draft being tried in chat before publishing; memory only (see useSelections).
  const [testDraft, setTestDraft] = useState(null)

  const refresh = useCallback(async () => {
    if (!enabled) {
      setEntries([])
      setLoaded(false)
      setError(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await request('/api/prompts?scope=all')
      setEntries(Array.isArray(data?.prompts) ? data.prompts : [])
      setLoaded(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [enabled])

  useEffect(() => {
    refresh()
  }, [refresh])

  const listPrompts = useCallback(async (scope = 'all', query = '') => {
    const params = new URLSearchParams({ scope })
    if (query && query.trim()) params.set('q', query.trim())
    const data = await request(`/api/prompts?${params.toString()}`)
    return Array.isArray(data?.prompts) ? data.prompts : []
  }, [])

  const getPrompt = useCallback(async id => (await request(`/api/prompts/${enc(id)}`)).prompt, [])

  const createPrompt = useCallback(async ({ title, content, description }) => {
    const data = await request('/api/prompts', {
      method: 'POST',
      body: JSON.stringify({ title, content, description: description || null }),
    })
    await refresh()
    return data.prompt
  }, [refresh])

  const updatePrompt = useCallback(async (id, fields) => {
    const data = await request(`/api/prompts/${enc(id)}`, { method: 'PUT', body: JSON.stringify(fields) })
    await refresh()
    return data.prompt
  }, [refresh])

  const publishPrompt = useCallback(async (id, changeDescription) => {
    const data = await request(`/api/prompts/${enc(id)}/publish`, {
      method: 'POST',
      body: JSON.stringify({ change_description: changeDescription || null }),
    })
    await refresh()
    return data.version
  }, [refresh])

  const listVersions = useCallback(async id => (await request(`/api/prompts/${enc(id)}/versions`)).versions || [], [])

  const getSharing = useCallback(async id => (await request(`/api/prompts/${enc(id)}/sharing`)).sharing, [])

  const setSharing = useCallback(async (id, sharing) => {
    const data = await request(`/api/prompts/${enc(id)}/sharing`, { method: 'PUT', body: JSON.stringify(sharing) })
    await refresh()
    return data.sharing
  }, [refresh])

  const listShareableGroups = useCallback(async () => (await request('/api/prompts/groups')).groups || [], [])

  const forkPrompt = useCallback(async (id, versionId = null) => {
    const data = await request(`/api/prompts/${enc(id)}/fork`, {
      method: 'POST',
      body: JSON.stringify({ version_id: versionId }),
    })
    await refresh()
    return data.prompt
  }, [refresh])

  const archivePrompt = useCallback(async id => {
    await request(`/api/prompts/${enc(id)}`, { method: 'DELETE' })
    await refresh()
    return true
  }, [refresh])

  const listAudit = useCallback(async id => (await request(`/api/prompts/${enc(id)}/audit`)).events || [], [])

  return {
    entries,
    loading,
    loaded,
    error,
    refresh,
    listPrompts,
    getPrompt,
    createPrompt,
    updatePrompt,
    publishPrompt,
    listVersions,
    getSharing,
    setSharing,
    listShareableGroups,
    forkPrompt,
    archivePrompt,
    listAudit,
    testDraft,
    setTestDraft,
  }
}

export default usePromptLibrary

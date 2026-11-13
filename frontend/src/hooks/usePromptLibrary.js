import { useState, useCallback, useEffect, useMemo } from 'react'

/**
 * Team-shared prompt library (issue #1169), backed by /api/prompts.
 *
 * `entries` is the first page (up to MAX_PAGE) of what the signed-in user may
 * see (scope "all"); `complete` says whether that page is everything. The chat
 * prompt selector reads it, and the send path uses it to refuse a selection
 * that is no longer available instead of letting it silently fall back -- but
 * only when the list is complete, since an entry past the first page is not
 * missing. The server re-checks access on every request and chat turn -- this
 * list is a convenience for the UI, never the security boundary.
 *
 * Draft saves and publishing name the draft revision being edited, and
 * sharing changes name the sharing revision they were computed from; a 409
 * means someone else changed it first (`error.status === 409`).
 */

const MAX_PAGE = 200
export const PAGE_SIZE = 50

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

/**
 * Whether a listing response is everything the user may see. The server sets
 * `incomplete` when its authorization checks were cut short by their safety
 * bounds (issue #1169): then entries can be missing that the user may still
 * use, so the client must never conclude from their absence that access was
 * revoked. Exported for tests.
 */
export function listingIsComplete(data, list) {
  if (data?.incomplete === true) return false
  if (typeof data?.total !== 'number') return false
  return data.total <= list.length
}

export function usePromptLibrary(enabled = true) {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [complete, setComplete] = useState(false)
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
      const data = await request(`/api/prompts?scope=all&limit=${MAX_PAGE}`)
      const list = Array.isArray(data?.prompts) ? data.prompts : []
      setEntries(list)
      setComplete(listingIsComplete(data, list))
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

  // One page: { prompts, total }.
  const listPrompts = useCallback(async (scope = 'all', query = '', offset = 0) => {
    const params = new URLSearchParams({ scope, limit: String(PAGE_SIZE), offset: String(offset) })
    if (query && query.trim()) params.set('q', query.trim())
    const data = await request(`/api/prompts?${params.toString()}`)
    return {
      prompts: Array.isArray(data?.prompts) ? data.prompts : [],
      total: typeof data?.total === 'number' ? data.total : 0,
      // The server could not check every group (too many, or too slow):
      // some team entries may be missing from this listing.
      incomplete: data?.incomplete === true,
    }
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

  const updatePrompt = useCallback(async (id, fields, expectedDraftRevision) => {
    const data = await request(`/api/prompts/${enc(id)}`, {
      method: 'PUT',
      body: JSON.stringify({ ...fields, expected_draft_revision: expectedDraftRevision }),
    })
    await refresh()
    return data.prompt
  }, [refresh])

  const publishPrompt = useCallback(async (id, changeDescription, expectedDraftRevision) => {
    const data = await request(`/api/prompts/${enc(id)}/publish`, {
      method: 'POST',
      body: JSON.stringify({
        change_description: changeDescription || null,
        expected_draft_revision: expectedDraftRevision,
      }),
    })
    await refresh()
    return data.version
  }, [refresh])

  // One page of history, newest first: { versions, total }. Entries carry no
  // prompt text; getVersion returns one version's content.
  const listVersions = useCallback(async (id, offset = 0) => {
    const data = await request(`/api/prompts/${enc(id)}/versions?limit=${PAGE_SIZE}&offset=${offset}`)
    return {
      versions: Array.isArray(data?.versions) ? data.versions : [],
      total: typeof data?.total === 'number' ? data.total : 0,
    }
  }, [])

  const getVersion = useCallback(async (id, versionId) =>
    (await request(`/api/prompts/${enc(id)}/versions/${enc(versionId)}`)).version, [])

  const getSharing = useCallback(async id => (await request(`/api/prompts/${enc(id)}/sharing`)).sharing, [])

  // `sharing` is { groups, users, expected_revision }.
  const setSharing = useCallback(async (id, sharing) => {
    const data = await request(`/api/prompts/${enc(id)}/sharing`, { method: 'PUT', body: JSON.stringify(sharing) })
    await refresh()
    return data.sharing
  }, [refresh])

  // { groups, incomplete }: `incomplete` when the server's bounded membership
  // checks were cut short (only groups confirmed as yours are listed).
  const listShareableGroups = useCallback(async () => {
    const data = await request('/api/prompts/groups')
    return { groups: Array.isArray(data?.groups) ? data.groups : [], incomplete: data?.incomplete === true }
  }, [])

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

  // One stable object per state change (not per render), so consumers
  // that depend on it do not refetch on every parent render.
  return useMemo(() => ({
    entries,
    loading,
    loaded,
    complete,
    error,
    refresh,
    listPrompts,
    getPrompt,
    createPrompt,
    updatePrompt,
    publishPrompt,
    listVersions,
    getVersion,
    getSharing,
    setSharing,
    listShareableGroups,
    forkPrompt,
    archivePrompt,
    listAudit,
    testDraft,
    setTestDraft,
  }), [
    entries, loading, loaded, complete, error, refresh, listPrompts, getPrompt, createPrompt, updatePrompt, publishPrompt, listVersions, getVersion, getSharing, setSharing, listShareableGroups, forkPrompt, archivePrompt, listAudit, testDraft, setTestDraft,
  ])
}

export default usePromptLibrary

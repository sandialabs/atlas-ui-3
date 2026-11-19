import { useEffect, useState } from 'react'
import { X, GitFork } from 'lucide-react'

/**
 * Version history for a team-library entry (issue #1169). Every published
 * version is immutable; choosing one pins chat to exactly that text, and
 * "Follow latest" picks up each new publication in new conversations (a
 * conversation already in progress keeps the version it started with).
 */
const PromptLibraryVersions = ({ prompt, library, onUseVersion, onFork, onClose, activeVersionId }) => {
  const [versions, setVersions] = useState([])
  const [total, setTotal] = useState(0)
  const [expanded, setExpanded] = useState(null)
  // Version text, fetched when a version is first expanded: the history
  // listing is paged and carries no prompt text (issue #1169).
  const [contents, setContents] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // The stable request functions, not the library object, so unrelated
  // renders do not refetch the history.
  const { listVersions, getVersion } = library
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listVersions(prompt.id, 0)
      .then(page => {
        if (cancelled) return
        setVersions(page.versions)
        setTotal(page.total)
      })
      .catch(e => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [prompt.id, listVersions])

  const loadMore = async () => {
    setLoading(true)
    setError(null)
    try {
      const page = await listVersions(prompt.id, versions.length)
      setVersions(prev => {
        const seen = new Set(prev.map(v => v.id))
        return [...prev, ...page.versions.filter(v => !seen.has(v.id))]
      })
      setTotal(page.total)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const toggle = async id => {
    if (expanded === id) {
      setExpanded(null)
      return
    }
    setExpanded(id)
    if (contents[id] !== undefined) return
    try {
      const version = await getVersion(prompt.id, id)
      setContents(prev => ({ ...prev, [id]: version.content }))
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="bg-gray-700 rounded-lg p-4 space-y-3 border border-gray-600">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-100">Version history</h4>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => onUseVersion(null)}
            disabled={!prompt.latest_version}
            className="px-2 py-1 rounded bg-gray-600 hover:bg-gray-500 text-gray-100 text-xs disabled:opacity-50"
          >
            Follow latest
          </button>
          <button type="button" aria-label="Close version history" onClick={onClose} className="p-1 rounded bg-gray-600 hover:bg-gray-500 text-gray-200">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
      {loading && <div className="text-sm text-gray-400">Loading versions...</div>}
      {error && <div role="alert" className="text-sm text-red-300">{error}</div>}
      {!loading && !error && versions.length === 0 && (
        <div className="text-sm text-gray-400">Nothing published yet.</div>
      )}
      <ol className="space-y-2">
        {versions.map(v => (
          <li key={v.id} className="bg-gray-800 rounded p-2">
            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => toggle(v.id)}
                aria-expanded={expanded === v.id}
                className="flex-1 min-w-0 text-left"
              >
                <div className="text-sm text-gray-100">
                  v{v.version_number}
                  {activeVersionId === v.id && <span className="ml-2 text-xs text-blue-400">(selected)</span>}
                </div>
                <div className="text-xs text-gray-400 truncate">
                  {v.change_description || 'No change description'} -- {v.author_email}
                  {v.created_at ? `, ${new Date(v.created_at).toLocaleString()}` : ''}
                </div>
              </button>
              <button
                type="button"
                onClick={() => onUseVersion(v.id)}
                className="px-2 py-1 rounded bg-blue-700 hover:bg-blue-600 text-white text-xs"
              >
                Use this version
              </button>
              <button
                type="button"
                aria-label={`Fork version ${v.version_number}`}
                title="Fork this version into a private copy"
                onClick={() => onFork(v.id)}
                className="p-1.5 rounded bg-gray-600 hover:bg-gray-500 text-gray-200"
              >
                <GitFork className="w-4 h-4" />
              </button>
            </div>
            {expanded === v.id && (
              <pre className="mt-2 max-h-60 overflow-auto text-xs text-gray-200 whitespace-pre-wrap bg-gray-900 rounded p-2">
                {contents[v.id] ?? v.content ?? 'Loading...'}
              </pre>
            )}
          </li>
        ))}
      </ol>
      {versions.length < total && (
        <div className="flex items-center justify-center gap-3">
          <span className="text-xs text-gray-400">Showing {versions.length} of {total}</span>
          <button type="button" onClick={loadMore} disabled={loading}
            className="px-3 py-1.5 rounded bg-gray-600 hover:bg-gray-500 text-gray-100 text-xs disabled:opacity-50">
            Load more versions
          </button>
        </div>
      )}
    </div>
  )
}

export default PromptLibraryVersions

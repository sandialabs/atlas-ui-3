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
  const [expanded, setExpanded] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    library.listVersions(prompt.id)
      .then(v => !cancelled && setVersions(v))
      .catch(e => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [prompt.id, library])

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
                onClick={() => setExpanded(expanded === v.id ? null : v.id)}
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
                {v.content}
              </pre>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

export default PromptLibraryVersions

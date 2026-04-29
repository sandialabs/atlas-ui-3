import { useEffect, useState, useCallback } from 'react'
import { Folder, ArrowUp, X, Home, Check } from 'lucide-react'

// Single-purpose folder picker. Server-paginated list of subdirectories
// at a given path; click to descend, ArrowUp to go to parent, Enter to
// select the current path. No file picker — the launch form only ever
// wants a directory.
export default function FolderBrowser({ open, initialPath, onCancel, onSelect }) {
  const [path, setPath] = useState(initialPath || '')
  const [entries, setEntries] = useState([])
  const [parent, setParent] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (p) => {
    setLoading(true)
    setError(null)
    try {
      const url = '/api/agent-portal/browse' + (p ? `?path=${encodeURIComponent(p)}` : '')
      const res = await fetch(url, { credentials: 'include' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(body.detail || `${res.status} ${res.statusText}`)
        return
      }
      const data = await res.json()
      setPath(data.path || '')
      setParent(data.parent || null)
      setEntries(Array.isArray(data.entries) ? data.entries : [])
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) load(initialPath || null)
  }, [open, initialPath, load])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[9998] bg-black/60 backdrop-blur-sm flex items-start justify-center p-4 overflow-y-auto"
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-label="Browse folders"
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-2xl my-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <h2 className="text-base font-semibold text-gray-100 flex items-center gap-2">
            <Folder className="w-4 h-4 text-blue-400" /> Browse folders
          </h2>
          <button
            type="button"
            onClick={onCancel}
            className="p-1 text-gray-400 hover:text-gray-200"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-3 space-y-2">
          <div className="flex gap-2 items-center">
            <button
              type="button"
              onClick={() => parent && load(parent)}
              disabled={!parent}
              className="px-2 py-1 rounded bg-gray-800 border border-gray-700 hover:bg-gray-700 disabled:opacity-40 text-xs flex items-center gap-1"
              title="Parent folder"
            >
              <ArrowUp className="w-3 h-3" /> Up
            </button>
            <button
              type="button"
              onClick={() => load('~')}
              className="px-2 py-1 rounded bg-gray-800 border border-gray-700 hover:bg-gray-700 text-xs flex items-center gap-1"
              title="Home"
            >
              <Home className="w-3 h-3" /> Home
            </button>
            <input
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); load(path) } }}
              className="flex-1 min-w-0 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="/path/to/folder"
            />
          </div>
          {error && <div className="text-xs text-red-300 px-1">{error}</div>}
          <div className="border border-gray-700 rounded bg-gray-950 max-h-80 overflow-y-auto">
            {loading ? (
              <div className="p-3 text-xs text-gray-500">Loading…</div>
            ) : entries.length === 0 ? (
              <div className="p-3 text-xs text-gray-500">No subfolders here.</div>
            ) : (
              entries.map((e) => (
                <button
                  key={e.name}
                  type="button"
                  onClick={() => load(path + (path.endsWith('/') ? '' : '/') + e.name)}
                  className="w-full text-left px-3 py-1.5 text-xs hover:bg-gray-800 flex items-center gap-2 border-b border-gray-800 last:border-0"
                >
                  <Folder className="w-3.5 h-3.5 text-blue-400 flex-shrink-0" />
                  <span className="font-mono truncate">{e.name}</span>
                </button>
              ))
            )}
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-gray-700 bg-gray-900/40 rounded-b-xl">
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-sm"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onSelect(path)}
            disabled={!path}
            className="px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-400 text-white text-sm flex items-center gap-1"
          >
            <Check className="w-4 h-4" /> Use this folder
          </button>
        </div>
      </div>
    </div>
  )
}

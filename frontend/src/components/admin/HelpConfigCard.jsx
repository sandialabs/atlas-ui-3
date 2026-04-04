import React, { useState, useEffect, useCallback } from 'react'
import { FileText, RefreshCw } from 'lucide-react'

const HelpConfigCard = ({ openModal, addNotification }) => {
  const [filePath, setFilePath] = useState('')
  const [lastModified, setLastModified] = useState(null)
  const [size, setSize] = useState(0)
  const [loading, setLoading] = useState(true)
  const [reloading, setReloading] = useState(false)

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch('/admin/help-config')
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()
      setFilePath(data.file_path || '')
      setLastModified(data.last_modified || null)
      setSize((data.content || '').length)
      return data
    } catch (err) {
      console.error('Error fetching help status:', err)
      throw err
    }
  }, [])

  useEffect(() => {
    fetchStatus().finally(() => setLoading(false))
  }, [fetchStatus])

  const reloadFromDisk = async () => {
    setReloading(true)
    try {
      const data = await fetchStatus()
      const bytes = (data.content || '').length
      const mtime = data.last_modified
        ? new Date(data.last_modified * 1000).toLocaleString()
        : 'unknown'
      addNotification(
        `Help reloaded from disk: ${bytes} bytes, modified ${mtime}`,
        'success'
      )
    } catch (err) {
      addNotification('Error reloading help from disk: ' + err.message, 'error')
    } finally {
      setReloading(false)
    }
  }

  const manageHelp = async () => {
    try {
      const response = await fetch('/admin/help-config')
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()
      openModal('Edit Help Content', {
        type: 'textarea',
        value: data.content,
        description: 'Edit help documentation in Markdown format. Changes are written to disk and re-read on every help page load.'
      }, 'help-config')
    } catch (err) {
      addNotification('Error loading help content: ' + err.message, 'error')
    }
  }

  const mtimeLabel = lastModified
    ? new Date(lastModified * 1000).toLocaleString()
    : '—'

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        <FileText className="w-6 h-6 text-yellow-400" />
        <h2 className="text-lg font-semibold">Help Content</h2>
      </div>
      <p className="text-gray-400 mb-4">Edit the Markdown rendered on the Help page. Content is re-read from disk on every request.</p>

      <div className="px-3 py-1 rounded text-sm font-medium mb-4 text-green-400 bg-green-900/20">
        Ready
      </div>

      {filePath && (
        <div className="mb-3 px-3 py-2 bg-gray-900/50 rounded text-xs text-gray-400 font-mono break-all">
          File: {filePath}
        </div>
      )}
      <div className="mb-4 px-3 py-2 bg-gray-900/50 rounded text-xs text-gray-400 flex justify-between gap-2">
        <span>Modified: {mtimeLabel}</span>
        <span>{size} bytes</span>
      </div>

      <div className="space-y-2">
        <button
          onClick={manageHelp}
          disabled={loading}
          className={`w-full px-4 py-2 rounded-lg transition-colors ${
            loading
              ? 'bg-gray-600 cursor-not-allowed opacity-50'
              : 'bg-yellow-600 hover:bg-yellow-700 cursor-pointer'
          }`}
        >
          Edit Help Content
        </button>
        <button
          onClick={reloadFromDisk}
          disabled={loading || reloading}
          className={`w-full px-4 py-2 rounded-lg transition-colors flex items-center justify-center gap-2 ${
            loading || reloading
              ? 'bg-gray-600 cursor-not-allowed opacity-50'
              : 'bg-gray-600 hover:bg-gray-500 cursor-pointer'
          }`}
        >
          <RefreshCw className={`w-4 h-4 ${reloading ? 'animate-spin' : ''}`} />
          {reloading ? 'Reloading…' : 'Reload from disk'}
        </button>
      </div>
    </div>
  )
}

export default HelpConfigCard

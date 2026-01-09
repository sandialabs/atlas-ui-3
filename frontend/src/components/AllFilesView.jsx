import { useState, useEffect } from 'react'
import {
  File,
  Image,
  Database,
  FileText,
  Code,
  Download,
  Trash2,
  ArrowUpToLine,
  Search,
  SortAsc,
  SortDesc,
  Loader
} from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

const AllFilesView = () => {
  const { token, user: userEmail, ensureSession, addSystemEvent, addPendingFileEvent, attachments } = useChat()
  const { sendMessage } = useWS()
  const [allFiles, setAllFiles] = useState([])
  const [filteredFiles, setFilteredFiles] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [notification, setNotification] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [sortBy, setSortBy] = useState('last_modified')
  const [sortOrder, setSortOrder] = useState('desc')
  const [typeFilter, setTypeFilter] = useState('all')

  useEffect(() => {
    fetchAllFiles()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    applyFiltersAndSort()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allFiles, searchQuery, sortBy, sortOrder, typeFilter])

  const fetchAllFiles = async () => {
    try {
      setLoading(true)
      const response = await fetch('/api/files?limit=1000', {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      })

      if (!response.ok) {
        throw new Error(`Failed to fetch files: ${response.statusText}`)
      }

      const files = await response.json()
      setAllFiles(files)
    } catch (err) {
      setError(err.message)
      console.error('Error fetching all files:', err)
    } finally {
      setLoading(false)
    }
  }

  const applyFiltersAndSort = () => {
    let filtered = [...allFiles]

    // Apply search filter
    if (searchQuery) {
      filtered = filtered.filter(file =>
        file.filename.toLowerCase().includes(searchQuery.toLowerCase())
      )
    }

    // Apply type filter
    if (typeFilter !== 'all') {
      filtered = filtered.filter(file => file.tags?.source === typeFilter)
    }

    // Apply sorting
    filtered.sort((a, b) => {
      let aVal, bVal

      switch (sortBy) {
        case 'name':
          aVal = a.filename.toLowerCase()
          bVal = b.filename.toLowerCase()
          break
        case 'size':
          aVal = a.size
          bVal = b.size
          break
        case 'last_modified':
          aVal = new Date(a.last_modified)
          bVal = new Date(b.last_modified)
          break
        default:
          return 0
      }

      if (aVal < bVal) return sortOrder === 'asc' ? -1 : 1
      if (aVal > bVal) return sortOrder === 'asc' ? 1 : -1
      return 0
    })

    setFilteredFiles(filtered)
  }

  const getFileIcon = (file) => {
    const extension = file.filename.split('.').pop()?.toLowerCase()
    switch (extension) {
      case 'js':
      case 'jsx':
      case 'ts':
      case 'tsx':
      case 'py':
      case 'java':
      case 'cpp':
      case 'c':
      case 'go':
      case 'rs':
        return <Code className="w-4 h-4 text-blue-400" />
      case 'jpg':
      case 'jpeg':
      case 'png':
      case 'gif':
      case 'svg':
      case 'webp':
        return <Image className="w-4 h-4 text-green-400" />
      case 'json':
      case 'csv':
      case 'xlsx':
      case 'xls':
        return <Database className="w-4 h-4 text-yellow-400" />
      case 'pdf':
      case 'doc':
      case 'docx':
      case 'txt':
      case 'md':
        return <FileText className="w-4 h-4 text-red-400" />
      default:
        return <File className="w-4 h-4 text-gray-400" />
    }
  }

  const formatFileSize = (bytes) => {
    if (bytes === 0) return '0 B'
    const k = 1024
    const sizes = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }

  const formatDate = (dateString) => {
    const date = new Date(dateString)
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  const showNotification = (message, type = 'success', duration = 3000) => {
    setNotification({ message, type })
    setTimeout(() => setNotification(null), duration)
  }

  const handleDownloadFile = async (file) => {
    try {
      const response = await fetch(`/api/files/download/${encodeURIComponent(file.key)}`, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      })

      if (!response.ok) {
        throw new Error('Download failed')
      }

      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = file.filename
      a.click()
      window.URL.revokeObjectURL(url)
    } catch (err) {
      console.error('Error downloading file:', err)
      showNotification('Failed to download file', 'error')
    }
  }

  const handleDeleteFile = async (file) => {
    const confirmed = window.confirm(`Are you sure you want to delete "${file.filename}"? This action cannot be undone.`)
    if (!confirmed) {
      return
    }

    try {
      const response = await fetch(`/api/files/${encodeURIComponent(file.key)}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      })

      if (!response.ok) {
        throw new Error('Delete failed')
      }

      // Refresh the file list
      fetchAllFiles()
      showNotification('File deleted successfully', 'success')
    } catch (err) {
      console.error('Error deleting file:', err)
      showNotification('Failed to delete file', 'error')
    }
  }

  const handleAddToSession = async (file) => {
    try {
      // Check if file is already attached
      if (attachments.has(file.key)) {
        addSystemEvent('file-attached', `'${file.filename}' is already in this session.`)
        return
      }

      // Ensure session exists
      await ensureSession()

      // Add "attaching" system event and track it as pending
      const eventId = addSystemEvent('file-attaching', `Adding '${file.filename}' to this session...`, {
        fileId: file.key,
        fileName: file.filename,
        source: 'library'
      })

      // Track this as a pending file event
      addPendingFileEvent(file.key, eventId)

      // Send attach_file message (WebSocket handler will resolve the pending event)
      sendMessage({
        type: 'attach_file',
        s3_key: file.key,
        user: userEmail
      })
    } catch (error) {
      console.error('Error adding file to session:', error)
      addSystemEvent('file-attach-error', `Failed to add '${file.filename}' to session: ${error.message}`)
    }
  }

  const toggleSort = (field) => {
    if (sortBy === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(field)
      setSortOrder('desc')
    }
  }

  if (loading) {
    return (
      <div className="text-center py-12">
        <Loader className="w-8 h-8 animate-spin mx-auto mb-4 text-blue-400" />
        <p className="text-gray-400">Loading files...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <div className="text-red-400 mb-4">Error loading files</div>
        <p className="text-gray-500">{error}</p>
      </div>
    )
  }

  return (
    <div>
      {/* Notification */}
      {notification && (
        <div className={`mb-4 p-3 rounded-lg ${
          notification.type === 'error'
            ? 'bg-red-600 text-white'
            : 'bg-green-600 text-white'
        }`}>
          {notification.message}
        </div>
      )}

      {/* Section Header */}
      <div className="mb-4">
        <h3 className="text-lg font-semibold text-white">
          All Files ({filteredFiles.length})
        </h3>
        <p className="text-sm text-gray-400 mt-1">
          All files across all your sessions
        </p>
      </div>

      {/* Search and Filters */}
      <div className="mb-6 space-y-3">
        <div className="flex items-center gap-4">
          {/* Search */}
          <div className="flex-1 relative">
            <Search className="w-4 h-4 absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Search files..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* Type Filter */}
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:outline-none focus:border-blue-500"
          >
            <option value="all">All Types</option>
            <option value="user">Uploaded</option>
            <option value="tool">Generated</option>
          </select>
        </div>

        {/* Sort Options */}
        <div className="flex items-center gap-2 text-sm">
          <span className="text-gray-400">Sort by:</span>
          {[
            { key: 'last_modified', label: 'Date' },
            { key: 'name', label: 'Name' },
            { key: 'size', label: 'Size' }
          ].map(({ key, label }) => (
            <button
              key={key}
              onClick={() => toggleSort(key)}
              className={`px-3 py-1 rounded-lg border transition-colors ${
                sortBy === key
                  ? 'border-blue-500 text-blue-400'
                  : 'border-gray-600 text-gray-400 hover:border-gray-500'
              }`}
            >
              {label}
              {sortBy === key && (
                sortOrder === 'asc' ? <SortAsc className="w-3 h-3 inline ml-1" /> : <SortDesc className="w-3 h-3 inline ml-1" />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Files List */}
      {filteredFiles.length === 0 ? (
        <div className="text-gray-400 text-center py-12 px-6">
          <File className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <div className="text-lg mb-4">
            {searchQuery || typeFilter !== 'all' ? 'No files match your filters' : 'No files found'}
          </div>
          <p className="text-gray-500">
            {searchQuery || typeFilter !== 'all'
              ? 'Try adjusting your search or filter criteria'
              : 'Files from all sessions will appear here'
            }
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredFiles.map((file, index) => (
            <div
              key={`${file.key}-${index}`}
              className="bg-gray-700 rounded-lg overflow-hidden"
            >
              <div className="p-4 flex items-center gap-4">
                {/* File Icon */}
                <div className="bg-gray-600 rounded-lg p-3 flex-shrink-0">
                  {getFileIcon(file)}
                </div>

                {/* File Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between mb-1">
                    <h3 className="text-white font-semibold text-base truncate font-mono">
                      {file.filename}
                    </h3>
                    <div className="flex items-center gap-2 text-xs">
                      <span className="px-2 py-1 bg-gray-600 text-gray-300 rounded">
                        {file.tags?.source === 'user' ? 'Uploaded' : 'Generated'}
                      </span>
                      <span className="text-gray-400">
                        {formatDate(file.last_modified)}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 text-sm text-gray-400">
                    <span>{formatFileSize(file.size)}</span>
                    <span>•</span>
                    <span className="uppercase">{file.filename.split('.').pop()}</span>
                  </div>
                </div>

                {/* Action Buttons */}
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button
                    onClick={() => handleAddToSession(file)}
                    className="p-2 rounded-lg bg-purple-600 hover:bg-purple-700 text-white transition-colors"
                    title="Add to session"
                  >
                    <ArrowUpToLine className="w-4 h-4" />
                  </button>

                  <button
                    onClick={() => handleDownloadFile(file)}
                    className="p-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
                    title="Download file"
                  >
                    <Download className="w-4 h-4" />
                  </button>

                  <button
                    onClick={() => handleDeleteFile(file)}
                    className="p-2 rounded-lg bg-red-600 hover:bg-red-700 text-white transition-colors"
                    title="Delete file"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default AllFilesView

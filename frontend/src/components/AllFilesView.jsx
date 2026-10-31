import { useState, useEffect, useRef } from 'react'
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
  Loader,
  Folder,
  FolderOpen,
  ChevronLeft,
  ChevronRight
} from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import encodeFileKeyPath from '../utils/encodeFileKeyPath'
import {
  PAGE_SIZE,
  SOURCE_FOLDERS,
  TYPE_FOLDERS,
  filesQuery,
  filterAndSortFiles,
  folderById,
} from '../utils/fileLibraryModel'

const AllFilesView = () => {
  const { token, user: userEmail, ensureSession, addSystemEvent, addPendingFileEvent, attachments } = useChat()
  const { sendMessage } = useWS()
  const [allFiles, setAllFiles] = useState([])
  const [filteredFiles, setFilteredFiles] = useState([])
  const [loading, setLoading] = useState(true)
  const [paging, setPaging] = useState(false)
  const [error, setError] = useState(null)
  const [notification, setNotification] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [sortBy, setSortBy] = useState('last_modified')
  const [sortOrder, setSortOrder] = useState('desc')
  const [folderId, setFolderId] = useState('all')
  const [deletingAll, setDeletingAll] = useState(false)
  const [pageOffset, setPageOffset] = useState(0)
  const [reportedTotal, setReportedTotal] = useState(null)
  const [libraryComplete, setLibraryComplete] = useState(false)

  const folderIdRef = useRef('all')
  const searchRef = useRef('')
  const completeRef = useRef(false)
  const loadedOnce = useRef(false)
  const filterTimer = useRef(null)
  const filesRef = useRef([])

  folderIdRef.current = folderId
  searchRef.current = searchQuery
  filesRef.current = allFiles

  const folder = folderById(folderId)

  const showNotification = (message, type = 'success', duration = 3000) => {
    setNotification({ message, type })
    setTimeout(() => setNotification(null), duration)
  }

  const fetchAllFiles = async ({ offset = 0, folderId: nextFolder = 'all', q = '' } = {}) => {
    const requested = folderById(nextFolder)
    const unfiltered = offset === 0 && requested.id === 'all' && !(q || '').trim()
    const firstLoad = filesRef.current.length === 0 && offset === 0
    try {
      if (firstLoad) setLoading(true)
      else setPaging(true)
      setError(null)
      const response = await fetch(filesQuery({
        offset,
        source: requested.source,
        category: requested.category,
        q,
      }), {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      })

      if (!response.ok) {
        throw new Error(`Failed to fetch files: ${response.statusText}`)
      }

      const payload = await response.json()
      const files = Array.isArray(payload) ? payload : []
      const totalHeader = response.headers?.get?.('X-Total-Count')
      const headerTotal = totalHeader == null || totalHeader === '' ? null : Number(totalHeader)
      const total = Number.isFinite(headerTotal) ? headerTotal : null

      let complete = false
      if (unfiltered) {
        complete = total == null ? files.length < PAGE_SIZE : files.length === total
      }
      completeRef.current = complete
      setLibraryComplete(complete)
      setReportedTotal(total)
      setPageOffset(offset)
      setAllFiles(files)
    } catch (err) {
      if (filesRef.current.length === 0) {
        setError(err.message)
      } else {
        showNotification('Failed to load files', 'error')
      }
      console.error('Error fetching all files:', err)
    } finally {
      setLoading(false)
      setPaging(false)
    }
  }

  useEffect(() => {
    if (loadedOnce.current) return undefined
    loadedOnce.current = true
    fetchAllFiles({ offset: 0, folderId: 'all', q: '' })
    return () => {
      if (filterTimer.current) clearTimeout(filterTimer.current)
    }
  // The library is loaded once per time the panel opens this view.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    setFilteredFiles(filterAndSortFiles(allFiles, {
      searchQuery,
      source: folder.source,
      category: folder.category,
      sortBy,
      sortOrder,
    }))
  }, [allFiles, searchQuery, folder, sortBy, sortOrder])

  const scheduleServerLoad = (next) => {
    if (completeRef.current) return
    if (filterTimer.current) clearTimeout(filterTimer.current)
    filterTimer.current = setTimeout(() => {
      fetchAllFiles(next)
    }, 250)
  }

  const selectFolder = (id) => {
    setFolderId(id)
    setPageOffset(0)
    if (!completeRef.current) {
      fetchAllFiles({ offset: 0, folderId: id, q: searchRef.current })
    }
  }

  const onSearchChange = (value) => {
    setSearchQuery(value)
    scheduleServerLoad({ offset: 0, folderId: folderIdRef.current, q: value })
  }

  const getFileIcon = (file) => {
    const extension = (file.filename || '').split('.').pop()?.toLowerCase()
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
    if (!dateString) return 'Unknown'
    const date = new Date(dateString)
    if (Number.isNaN(date.getTime())) return 'Unknown'
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  const handleDownloadFile = async (file) => {
    try {
      const response = await fetch(`/api/files/download/${encodeFileKeyPath(file.key)}`, {
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
      const response = await fetch(`/api/files/${encodeFileKeyPath(file.key)}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      })

      if (!response.ok) {
        throw new Error('Delete failed')
      }

      fetchAllFiles({ offset: pageOffset, folderId, q: searchQuery })
      showNotification('File deleted successfully', 'success')
    } catch (err) {
      console.error('Error deleting file:', err)
      showNotification('Failed to delete file', 'error')
    }
  }

  const handleDeleteAll = async () => {
    if (deletingAll || filteredFiles.length === 0) {
      return
    }

    const filtersActive = searchQuery !== '' || folderId !== 'all'
    const partialPage = !libraryComplete && reportedTotal != null && reportedTotal > filteredFiles.length
    const confirmed = window.confirm(
      filtersActive
        ? `Are you sure you want to delete the ${filteredFiles.length} file(s) matching your current filters? This action cannot be undone.`
        : partialPage
          ? `Are you sure you want to delete the ${filteredFiles.length} file(s) on this page? This action cannot be undone.`
          : `Are you sure you want to delete all ${filteredFiles.length} file(s)? This action cannot be undone.`
    )
    if (!confirmed) {
      return
    }

    const filesToDelete = [...filteredFiles]
    let failures = 0

    setDeletingAll(true)
    try {
      for (const file of filesToDelete) {
        try {
          const response = await fetch(`/api/files/${encodeFileKeyPath(file.key)}`, {
            method: 'DELETE',
            headers: {
              'Authorization': `Bearer ${token}`
            }
          })

          if (!response.ok) {
            throw new Error('Delete failed')
          }
        } catch (err) {
          failures += 1
          console.error(`Error deleting file ${file.filename}:`, err)
        }
      }

      await fetchAllFiles({ offset: 0, folderId, q: searchQuery })

      if (failures === 0) {
        showNotification(`Deleted ${filesToDelete.length} file(s) successfully`, 'success')
      } else {
        showNotification(`Failed to delete ${failures} of ${filesToDelete.length} file(s)`, 'error')
      }
    } finally {
      setDeletingAll(false)
    }
  }

  const handleAddToSession = async (file) => {
    try {
      if (attachments.has(file.key)) {
        addSystemEvent('file-attached', `'${file.filename}' is already in this session.`)
        return
      }

      await ensureSession()

      const eventId = addSystemEvent('file-attaching', `Adding '${file.filename}' to this session...`, {
        fileId: file.key,
        fileName: file.filename,
        source: 'library'
      })

      addPendingFileEvent(file.key, eventId)

      sendMessage({
        type: 'attach_file',
        s3_key: file.key,
        user: userEmail
      })
    } catch (err) {
      console.error('Error adding file to session:', err)
      addSystemEvent('file-attach-error', `Failed to add '${file.filename}' to session: ${err.message}`)
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

  const folderCount = (candidate) => {
    if (!libraryComplete) return null
    return filterAndSortFiles(allFiles, {
      source: candidate.source,
      category: candidate.category,
    }).length
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

  const filtersActive = searchQuery !== '' || folderId !== 'all'
  const partialPage = !libraryComplete && reportedTotal != null && reportedTotal > filteredFiles.length
  const deleteLabel = filtersActive ? 'Delete Filtered' : partialPage ? 'Delete Page' : 'Delete All'
  const displayCount = libraryComplete ? filteredFiles.length : (reportedTotal ?? filteredFiles.length)
  const showPager = reportedTotal != null
    ? pageOffset > 0 || reportedTotal > allFiles.length
    : allFiles.length >= PAGE_SIZE
  const rangeLabel = showPager && allFiles.length > 0
    ? `Showing ${pageOffset + 1}–${pageOffset + allFiles.length}${reportedTotal != null ? ` of ${reportedTotal}` : ''}`
    : null
  const atEnd = reportedTotal != null && pageOffset + allFiles.length >= reportedTotal

  const renderFolderButton = (candidate) => {
    const active = candidate.id === folderId
    const count = folderCount(candidate)
    return (
      <button
        key={candidate.id}
        type="button"
        aria-label={candidate.label}
        aria-current={active ? 'page' : undefined}
        onClick={() => selectFolder(candidate.id)}
        className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-sm text-left transition-colors ${
          active ? 'bg-gray-700 text-gray-50' : 'text-gray-300 hover:bg-gray-700/70'
        }`}
      >
        {active
          ? <FolderOpen className="w-4 h-4 text-blue-400 flex-shrink-0" />
          : <Folder className="w-4 h-4 text-gray-400 flex-shrink-0" />}
        <span className="truncate">{candidate.label}</span>
        {count != null && (
          <span className="ml-auto text-xs text-gray-400" aria-hidden="true">{count}</span>
        )}
      </button>
    )
  }

  return (
    <div>
      {notification && (
        <div className={`mb-4 p-3 rounded-lg ${
          notification.type === 'error'
            ? 'bg-red-600 text-white'
            : 'bg-green-600 text-white'
        }`}>
          {notification.message}
        </div>
      )}

      <div className="flex flex-col sm:flex-row gap-4 min-h-[20rem]">
        <nav aria-label="Folders" className="sm:w-40 flex-shrink-0">
          <div className="flex sm:flex-col gap-1 overflow-x-auto sm:overflow-visible">
            {SOURCE_FOLDERS.map(renderFolderButton)}
          </div>
          <div className="hidden sm:block mt-4 mb-1 px-2 text-xs uppercase tracking-wide text-gray-500">
            By type
          </div>
          <div className="flex sm:flex-col gap-1 overflow-x-auto sm:overflow-visible mt-2 sm:mt-0">
            {TYPE_FOLDERS.map(renderFolderButton)}
          </div>
        </nav>

        <div className="flex-1 min-w-0">
          <nav aria-label="Breadcrumb" className="mb-3 flex items-center gap-1 text-sm text-gray-400">
            {folderId === 'all' ? (
              <span className="text-gray-200">File Library</span>
            ) : (
              <button
                type="button"
                onClick={() => selectFolder('all')}
                className="text-blue-400 hover:text-blue-300"
              >
                File Library
              </button>
            )}
            {folderId !== 'all' && (
              <>
                <ChevronRight className="w-3 h-3" />
                <span className="text-gray-200">{folder.label}</span>
              </>
            )}
          </nav>

          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <h3 className="text-lg font-semibold text-gray-50">
                {folder.label} ({displayCount})
              </h3>
              <p className="text-sm text-gray-400 mt-1">
                {rangeLabel || 'Browse folders, or search across the files in view'}
              </p>
            </div>
            {filteredFiles.length > 0 && (
              <button
                type="button"
                onClick={handleDeleteAll}
                disabled={deletingAll}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white text-sm transition-colors flex-shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                title={
                  filtersActive
                    ? 'Delete the files matching the current filters'
                    : partialPage
                      ? 'Delete the files on this page'
                      : 'Delete all files'
                }
              >
                {deletingAll ? (
                  <Loader className="w-4 h-4 animate-spin" />
                ) : (
                  <Trash2 className="w-4 h-4" />
                )}
                {deletingAll ? 'Deleting…' : `${deleteLabel} (${filteredFiles.length})`}
              </button>
            )}
          </div>

          <div className="mb-4 space-y-3">
            <div className="flex items-center gap-4">
              <div className="flex-1 relative">
                <Search className="w-4 h-4 absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search files..."
                  value={searchQuery}
                  onChange={(e) => onSearchChange(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-50 placeholder-gray-400 focus:outline-none focus:border-blue-500"
                />
              </div>

              <select
                value={folder.source}
                onChange={(e) => {
                  const value = e.target.value
                  selectFolder(value === 'user' ? 'uploads' : value === 'tool' ? 'generated' : 'all')
                }}
                aria-label="Source"
                className="px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-50 focus:outline-none focus:border-blue-500"
              >
                <option value="all">All Types</option>
                <option value="user">Uploaded</option>
                <option value="tool">Generated</option>
              </select>
            </div>

            <div className="flex items-center gap-2 text-sm">
              <span className="text-gray-400">Sort by:</span>
              {[
                { key: 'last_modified', label: 'Date' },
                { key: 'name', label: 'Name' },
                { key: 'size', label: 'Size' }
              ].map(({ key, label }) => (
                <button
                  type="button"
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

          {filteredFiles.length === 0 ? (
            <div className="text-gray-400 text-center py-12 px-6">
              <File className="w-12 h-12 mx-auto mb-4 opacity-50" />
              <div className="text-lg mb-4">
                {filtersActive ? 'No files match your filters' : 'No files found'}
              </div>
              <p className="text-gray-500">
                {filtersActive
                  ? 'Try another folder, or adjust your search'
                  : 'Files from all sessions will appear here'
                }
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {filteredFiles.map((file) => (
                <div
                  key={file.key}
                  className="bg-gray-700 rounded-lg overflow-hidden"
                >
                  <div className="p-3 flex items-center gap-3">
                    <div className="bg-gray-600 rounded-lg p-2.5 flex-shrink-0">
                      {getFileIcon(file)}
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-1 gap-1 min-w-0">
                        <h3 className="text-gray-50 font-semibold text-base truncate font-mono min-w-0">
                          {file.filename}
                        </h3>
                        <div className="flex flex-wrap items-center gap-2 text-xs sm:flex-shrink-0">
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

                    <div className="flex items-center gap-2 flex-shrink-0">
                      <button
                        type="button"
                        onClick={() => handleAddToSession(file)}
                        className="p-2 rounded-lg bg-purple-600 hover:bg-purple-700 text-white transition-colors"
                        title="Add to session"
                      >
                        <ArrowUpToLine className="w-4 h-4" />
                      </button>

                      <button
                        type="button"
                        onClick={() => handleDownloadFile(file)}
                        className="p-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
                        title="Download file"
                      >
                        <Download className="w-4 h-4" />
                      </button>

                      <button
                        type="button"
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

          {showPager && (
            <div className="mt-4 flex items-center justify-end gap-2 text-sm">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => fetchAllFiles({
                    offset: Math.max(0, pageOffset - PAGE_SIZE),
                    folderId,
                    q: searchQuery,
                  })}
                  disabled={paging || pageOffset === 0}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-600 text-gray-200 disabled:opacity-40"
                >
                  <ChevronLeft className="w-4 h-4" />
                  Previous
                </button>
                <button
                  type="button"
                  onClick={() => fetchAllFiles({
                    offset: pageOffset + PAGE_SIZE,
                    folderId,
                    q: searchQuery,
                  })}
                  disabled={paging || atEnd}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-600 text-gray-200 disabled:opacity-40"
                >
                  Next
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default AllFilesView

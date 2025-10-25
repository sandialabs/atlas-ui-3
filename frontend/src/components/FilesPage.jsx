import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Download, Trash2, Upload, Tag, File, Image, Code, Database, FileText, Search, Filter } from 'lucide-react'

const FilesPage = () => {
  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedCategory, setSelectedCategory] = useState('all')
  const [selectedSource, setSelectedSource] = useState('all')
  const [stats, setStats] = useState({})

  // Mock API calls - replace with actual S3 API calls
  const fetchFiles = async () => {
    try {
      setLoading(true)
      // TODO: Replace with actual S3 API call
      const response = await fetch('/api/files', {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('userEmail') || 'user@example.com'}`
        }
      })
      
      if (response.ok) {
        const data = await response.json()
        setFiles(data)
      } else {
        throw new Error('Failed to fetch files')
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const fetchStats = async () => {
    try {
      const userEmail = localStorage.getItem('userEmail') || 'user@example.com'
      const response = await fetch(`/api/users/${userEmail}/files/stats`, {
        headers: {
          'Authorization': `Bearer ${userEmail}`
        }
      })
      
      if (response.ok) {
        const data = await response.json()
        setStats(data)
      }
    } catch (err) {
      console.error('Failed to fetch stats:', err)
    }
  }

  const deleteFile = async (fileKey, filename) => {
    try {
      const response = await fetch(`/api/files/${fileKey}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('userEmail') || 'user@example.com'}`
        }
      })
      
      if (response.ok) {
        setFiles(files.filter(f => f.s3_key !== fileKey))
        await fetchStats() // Refresh stats
      } else {
        throw new Error('Failed to delete file')
      }
    } catch (err) {
      alert(`Error deleting file: ${err.message}`)
    }
  }

  const downloadFile = async (fileKey, filename) => {
    try {
      const response = await fetch(`/api/files/${fileKey}`, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('userEmail') || 'user@example.com'}`
        }
      })
      
      if (response.ok) {
        const data = await response.json()
        
        // Convert base64 to blob and download
        const byteCharacters = atob(data.content_base64)
        const byteNumbers = new Array(byteCharacters.length)
        for (let i = 0; i < byteCharacters.length; i++) {
          byteNumbers[i] = byteCharacters.charCodeAt(i)
        }
        const byteArray = new Uint8Array(byteNumbers)
        const blob = new Blob([byteArray], { type: data.content_type })
        
        const url = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = filename
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        window.URL.revokeObjectURL(url)
      } else {
        throw new Error('Failed to download file')
      }
    } catch (err) {
      alert(`Error downloading file: ${err.message}`)
    }
  }

  const copyFileReference = (filename) => {
    // Copy @file reference to clipboard
    const reference = `@file ${filename}`
    navigator.clipboard.writeText(reference).then(() => {
      alert(`Copied "@file ${filename}" to clipboard! Paste this in your chat message to reference the file.`)
    }).catch(() => {
      // Fallback for older browsers
      const textArea = document.createElement('textarea')
      textArea.value = reference
      document.body.appendChild(textArea)
      textArea.select()
      document.execCommand('copy')
      document.body.removeChild(textArea)
      alert(`Copied "@file ${filename}" to clipboard! Paste this in your chat message to reference the file.`)
    })
  }

  useEffect(() => {
    fetchFiles()
    fetchStats()
  }, [])

  const getFileIcon = (type) => {
    switch (type) {
      case 'image': return <Image className="w-4 h-4" />
      case 'code': return <Code className="w-4 h-4" />
      case 'data': return <Database className="w-4 h-4" />
      case 'document': return <FileText className="w-4 h-4" />
      default: return <File className="w-4 h-4" />
    }
  }

  const getSourceBadge = (source, sourceType) => {
    if (source === 'tool') {
      return (
        <span className="px-2 py-1 text-xs rounded-full bg-blue-100 text-blue-800">
          Generated{sourceType ? ` by ${sourceType}` : ''}
        </span>
      )
    }
    return (
      <span className="px-2 py-1 text-xs rounded-full bg-green-100 text-green-800">
        Uploaded
      </span>
    )
  }

  const formatFileSize = (bytes) => {
    if (bytes === 0) return '0 B'
    const k = 1024
    const sizes = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
  }

  const formatDate = (dateString) => {
    if (!dateString) return 'Unknown'
    return new Date(dateString).toLocaleDateString() + ' ' + new Date(dateString).toLocaleTimeString()
  }

  // Filter files
  const filteredFiles = files.filter(file => {
    const matchesSearch = file.filename.toLowerCase().includes(searchQuery.toLowerCase())
    const matchesCategory = selectedCategory === 'all' || file.type === selectedCategory
    const matchesSource = selectedSource === 'all' || file.source === selectedSource
    
    return matchesSearch && matchesCategory && matchesSource
  })

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-lg">Loading files...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-lg text-red-600">Error: {error}</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center py-6">
            <div className="flex items-center space-x-4">
              <Link to="/" className="flex items-center space-x-2 text-gray-600 hover:text-gray-900">
                <ArrowLeft className="w-5 h-5" />
                <span>Back to Chat</span>
              </Link>
              <h1 className="text-2xl font-bold text-gray-900">File Manager</h1>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Statistics */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-sm font-medium text-gray-500">Total Files</div>
            <div className="text-2xl font-bold text-gray-900">{stats.total_files || 0}</div>
          </div>
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-sm font-medium text-gray-500">Total Size</div>
            <div className="text-2xl font-bold text-gray-900">{formatFileSize(stats.total_size || 0)}</div>
          </div>
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-sm font-medium text-gray-500">Uploaded</div>
            <div className="text-2xl font-bold text-gray-900">{stats.upload_count || 0}</div>
          </div>
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-sm font-medium text-gray-500">Generated</div>
            <div className="text-2xl font-bold text-gray-900">{stats.generated_count || 0}</div>
          </div>
        </div>

        {/* Filters */}
        <div className="bg-white rounded-lg shadow mb-6 p-6">
          <div className="flex flex-col sm:flex-row gap-4">
            <div className="flex-1">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4" />
                <input
                  type="text"
                  placeholder="Search files..."
                  className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
              </div>
            </div>
            <select
              className="px-3 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              value={selectedCategory}
              onChange={(e) => setSelectedCategory(e.target.value)}
            >
              <option value="all">All Types</option>
              <option value="image">Images</option>
              <option value="code">Code</option>
              <option value="data">Data</option>
              <option value="document">Documents</option>
              <option value="other">Other</option>
            </select>
            <select
              className="px-3 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              value={selectedSource}
              onChange={(e) => setSelectedSource(e.target.value)}
            >
              <option value="all">All Sources</option>
              <option value="user">Uploaded</option>
              <option value="tool">Generated</option>
            </select>
          </div>
        </div>

        {/* Files List */}
        <div className="bg-white rounded-lg shadow">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-medium text-gray-900">
              Files ({filteredFiles.length})
            </h2>
          </div>
          
          <div className="divide-y divide-gray-200">
            {filteredFiles.length === 0 ? (
              <div className="px-6 py-12 text-center">
                <File className="mx-auto h-12 w-12 text-gray-400" />
                <h3 className="mt-4 text-sm font-medium text-gray-900">No files found</h3>
                <p className="mt-2 text-sm text-gray-500">
                  {searchQuery || selectedCategory !== 'all' || selectedSource !== 'all'
                    ? 'Try adjusting your filters'
                    : 'Upload some files to get started'}
                </p>
              </div>
            ) : (
              filteredFiles.map((file) => (
                <div key={file.s3_key} className="px-6 py-4 hover:bg-gray-50">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-3 flex-1 min-w-0">
                      <div className="flex-shrink-0">
                        {getFileIcon(file.type)}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">
                          {file.filename}
                        </p>
                        <div className="flex items-center space-x-2 mt-1">
                          {getSourceBadge(file.source, file.source_tool)}
                          <span className="text-xs text-gray-500">
                            {formatFileSize(file.size)} • {formatDate(file.last_modified)}
                          </span>
                        </div>
                      </div>
                    </div>
                    
                    <div className="flex items-center space-x-2">
                      <button
                        onClick={() => copyFileReference(file.filename)}
                        className="p-2 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded-md transition-colors"
                        title="Copy @file reference"
                      >
                        <Tag className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => downloadFile(file.s3_key, file.filename)}
                        className="p-2 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded-md transition-colors"
                        title="Download file"
                      >
                        <Download className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => {
                          if (confirm(`Are you sure you want to delete "${file.filename}"?`)) {
                            deleteFile(file.s3_key, file.filename)
                          }
                        }}
                        className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-md transition-colors"
                        title="Delete file"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default FilesPage
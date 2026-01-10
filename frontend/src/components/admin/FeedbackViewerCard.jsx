import React, { useState, useEffect } from 'react'
import { Heart, Download, RefreshCw, ThumbsUp, ThumbsDown, Minus } from 'lucide-react'

const FeedbackViewerCard = ({ openModal, addNotification }) => {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [downloadFormat, setDownloadFormat] = useState('csv')

  const loadStats = async () => {
    setLoading(true)
    try {
      const response = await fetch('/api/feedback/stats')
      if (response.ok) {
        const data = await response.json()
        setStats(data)
      } else {
        addNotification('Failed to load feedback statistics: ' + response.statusText, 'error')
      }
    } catch (err) {
      console.error('Error loading feedback stats:', err)
      addNotification('Error loading feedback statistics: ' + err.message, 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadStats()
  }, [])

  const viewFeedback = async () => {
    try {
      const response = await fetch('/api/feedback?limit=100')
      const data = await response.json()
      
      openModal('User Feedback', {
        type: 'feedback',
        feedback: data.feedback,
        statistics: data.statistics,
        pagination: data.pagination,
        readonly: true
      })
    } catch (err) {
      addNotification('Error loading feedback: ' + err.message, 'error')
    }
  }

  const downloadFeedback = () => {
    try {
      const link = document.createElement('a')
      link.href = `/api/feedback/download?format=${downloadFormat}`
      link.download = `feedback_export.${downloadFormat}`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      
      addNotification(`Feedback download started (${downloadFormat.toUpperCase()})`, 'success')
    } catch (err) {
      addNotification('Error downloading feedback: ' + err.message, 'error')
    }
  }

  const getStatusColor = (status) => {
    switch (status) {
      case 'healthy': return 'text-green-400 bg-green-900/20'
      case 'warning': return 'text-yellow-400 bg-yellow-900/20'
      case 'error': return 'text-red-400 bg-red-900/20'
      default: return 'text-gray-400 bg-gray-800'
    }
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        <Heart className="w-6 h-6 text-pink-400" />
        <h2 className="text-lg font-semibold">User Feedback</h2>
        <button
          onClick={loadStats}
          disabled={loading}
          className="ml-auto p-1 hover:bg-gray-700 rounded transition-colors"
          title="Refresh stats"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>
      
      <p className="text-gray-400 mb-4">View and download user feedback collected from the chat interface.</p>
      
      {stats && (
        <div className="mb-4 p-3 bg-gray-700/50 rounded-lg">
          <div className="grid grid-cols-3 gap-2 text-center text-sm">
            <div className="flex flex-col items-center">
              <div className="flex items-center gap-1 text-green-400">
                <ThumbsUp className="w-3 h-3" />
                <span className="font-medium">{stats.rating_distribution?.positive || 0}</span>
              </div>
              <span className="text-xs text-gray-400">Positive</span>
            </div>
            <div className="flex flex-col items-center">
              <div className="flex items-center gap-1 text-yellow-400">
                <Minus className="w-3 h-3" />
                <span className="font-medium">{stats.rating_distribution?.neutral || 0}</span>
              </div>
              <span className="text-xs text-gray-400">Neutral</span>
            </div>
            <div className="flex flex-col items-center">
              <div className="flex items-center gap-1 text-red-400">
                <ThumbsDown className="w-3 h-3" />
                <span className="font-medium">{stats.rating_distribution?.negative || 0}</span>
              </div>
              <span className="text-xs text-gray-400">Negative</span>
            </div>
          </div>
          <div className="mt-2 pt-2 border-t border-gray-600 text-center">
            <span className="text-sm text-gray-300">
              Total: <span className="font-medium text-blue-400">{stats.total_feedback || 0}</span>
            </span>
            {stats.recent_feedback > 0 && (
              <span className="text-sm text-gray-400 ml-2">
                ({stats.recent_feedback} in last 24h)
              </span>
            )}
          </div>
        </div>
      )}
      
      <div className={`px-3 py-1 rounded text-sm font-medium mb-4 ${getStatusColor('healthy')}`}>
        Ready
      </div>
      
      <div className="space-y-2">
        <button 
          onClick={viewFeedback}
          className="w-full px-4 py-2 bg-pink-600 hover:bg-pink-700 rounded-lg transition-colors"
        >
          View Feedback
        </button>
        
        <div className="flex gap-2">
          <select
            value={downloadFormat}
            onChange={(e) => setDownloadFormat(e.target.value)}
            className="px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-sm"
          >
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </select>
          <button 
            onClick={downloadFeedback}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-gray-600 hover:bg-gray-700 rounded-lg transition-colors"
          >
            <Download className="w-4 h-4" />
            Download
          </button>
        </div>
      </div>
    </div>
  )
}

export default FeedbackViewerCard

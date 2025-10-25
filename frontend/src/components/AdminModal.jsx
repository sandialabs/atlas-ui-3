import React, { useState, useEffect } from 'react'
import { Download, Save, X, Check, ThumbsUp, ThumbsDown, Minus } from 'lucide-react'

const AdminModal = ({ data, onClose, onSave, onDownload, addNotification }) => {
  const [content, setContent] = useState(data.content?.value || '')
  const [saving, setSaving] = useState(false)
  const logContainerRef = React.useRef(null)

  // Auto-scroll to bottom when logs are loaded
  useEffect(() => {
    if (data.content?.type === 'logs' && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
  }, [data.content])

  const handleSave = async () => {
    setSaving(true)
    await onSave(content)
    setSaving(false)
  }

  const handleSaveAndClose = async () => {
    await handleSave()
    onClose()
  }

  const renderContent = () => {
    switch (data.content?.type) {
      case 'textarea':
        return (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-2">
                {data.title}
              </label>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                className="w-full h-64 px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg font-mono text-sm resize-vertical"
                placeholder="Enter configuration..."
              />
            </div>
            {data.content.description && (
              <p className="text-sm text-gray-400">{data.content.description}</p>
            )}
          </div>
        )

      case 'logs':
        return (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <label className="block text-sm font-medium">
                Recent Log Entries (last {data.content.lines} lines)
              </label>
              <button
                onClick={onDownload}
                className="flex items-center gap-2 px-3 py-1 bg-cyan-600 hover:bg-cyan-700 rounded text-sm transition-colors"
              >
                <Download className="w-4 h-4" />
                Download Full Log
              </button>
            </div>
            <div ref={logContainerRef} className="bg-black text-green-400 p-4 rounded-lg font-mono text-sm overflow-y-auto whitespace-pre-wrap" style={{height: 'calc(100vh - 200px)'}}>
              {data.content.content}
            </div>
            <p className="text-sm text-gray-400">
              Showing {data.content.lines} of {data.content.totalLines} total lines.
            </p>
          </div>
        )

      case 'health':
        return (
          <div className="space-y-4">
            <div className="p-4 bg-gray-700 rounded-lg">
              <p className="text-sm font-medium">
                Overall Status: <span className={`ml-2 px-2 py-1 rounded text-xs ${
                  data.content.overallStatus === 'healthy' ? 'bg-green-900 text-green-400' :
                  data.content.overallStatus === 'warning' ? 'bg-yellow-900 text-yellow-400' :
                  'bg-red-900 text-red-400'
                }`}>
                  {data.content.overallStatus}
                </span>
              </p>
            </div>
            <div className="space-y-3">
              {data.content.components?.map((component, index) => (
                <div key={index} className="p-3 border border-gray-600 rounded-lg">
                  <div className="flex items-center justify-between">
                    <strong>{component.component}</strong>
                    <span className={`px-2 py-1 rounded text-xs ${
                      component.status === 'healthy' ? 'bg-green-900 text-green-400' :
                      component.status === 'warning' ? 'bg-yellow-900 text-yellow-400' :
                      'bg-red-900 text-red-400'
                    }`}>
                      {component.status}
                    </span>
                  </div>
                  {component.details && (
                    <pre className="mt-2 text-xs text-gray-400 overflow-x-auto">
                      {JSON.stringify(component.details, null, 2)}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          </div>
        )

      case 'mcp-health': {
        const summary = data.content.healthSummary
        return (
          <div className="space-y-4">
            <div className="p-4 bg-gray-700 rounded-lg space-y-2">
              <p><strong>Overall Status:</strong> <span className={`ml-2 px-2 py-1 rounded text-xs ${
                summary.overall_status === 'healthy' ? 'bg-green-900 text-green-400' : 'bg-red-900 text-red-400'
              }`}>{summary.overall_status}</span></p>
              <p><strong>Servers:</strong> {summary.healthy_count}/{summary.total_count} healthy</p>
              <p><strong>Last Check:</strong> {summary.last_check ? new Date(summary.last_check * 1000).toLocaleString() : 'Never'}</p>
              <p><strong>Check Interval:</strong> {summary.check_interval} seconds</p>
            </div>
            {summary.servers && (
              <div className="space-y-3">
                {Object.entries(summary.servers).map(([serverName, serverInfo]) => (
                  <div key={serverName} className="p-3 border border-gray-600 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <strong>{serverName}</strong>
                      <span className={`px-2 py-1 rounded text-xs ${
                        serverInfo.status === 'healthy' ? 'bg-green-900 text-green-400' : 'bg-red-900 text-red-400'
                      }`}>
                        {serverInfo.status}
                      </span>
                    </div>
                    <div className="text-sm text-gray-400 space-y-1">
                      <p>Last Check: {new Date(serverInfo.last_check * 1000).toLocaleString()}</p>
                      {serverInfo.response_time_ms && <p>Response Time: {serverInfo.response_time_ms.toFixed(1)}ms</p>}
                      {serverInfo.error_message && <p>Error: {serverInfo.error_message}</p>}
                      <p>Running: {serverInfo.is_running ? 'Yes' : 'No'}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      }

      case 'enhanced-logs': {
        const entries = data.content.entries || []
        const metadata = data.content.metadata || {}
        
        const formatTimestamp = (timestamp) => {
          try {
            const date = new Date(timestamp)
            return date.toLocaleTimeString('en-US', { 
              hour12: false, 
              hour: '2-digit', 
              minute: '2-digit', 
              second: '2-digit',
              fractionalSecondDigits: 3 
            })
          } catch {
            return timestamp
          }
        }

        const getLevelColor = (level) => {
          switch (level) {
            case 'DEBUG': return 'bg-gray-600 text-gray-300'
            case 'INFO': return 'bg-green-600 text-green-100'
            case 'WARN': return 'bg-yellow-600 text-yellow-100'
            case 'ERROR': return 'bg-red-600 text-red-100'
            case 'CRITICAL': return 'bg-red-800 text-red-100'
            default: return 'bg-gray-500 text-gray-100'
          }
        }

        const copyToClipboard = (text) => {
          navigator.clipboard.writeText(text).then(() => {
            addNotification('Copied to clipboard', 'success') // Use the prop
          }).catch(() => {
            addNotification('Failed to copy', 'error') // Use the prop
          })
        }

        return (
          <div className="space-y-4">
            {/* Metadata */}
            <div className="p-4 bg-gray-700 rounded-lg">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <div className="text-gray-400">Total Entries</div>
                  <div className="font-medium">{metadata.total_entries || 0}</div>
                </div>
                <div>
                  <div className="text-gray-400">Unique Modules</div>
                  <div className="font-medium">{metadata.unique_modules?.length || 0}</div>
                </div>
                <div>
                  <div className="text-gray-400">Log Levels</div>
                  <div className="font-medium">{metadata.unique_levels?.length || 0}</div>
                </div>
                <div>
                  <div className="text-gray-400">Requested Lines</div>
                  <div className="font-medium">{metadata.requested_lines || 0}</div>
                </div>
              </div>
            </div>

            {/* Log Entries */}
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {entries.length === 0 ? (
                <div className="text-center py-8 text-gray-400">
                  {/* <Eye className="w-12 h-12 mx-auto mb-4 opacity-50" /> */}
                  <p>No log entries found</p>
                </div>
              ) : (
                entries.map((entry, index) => (
                  <div key={index} className="p-3 border border-gray-600 rounded-lg hover:bg-gray-750 transition-colors">
                    <div className="flex items-start gap-3">
                      <div className="flex-shrink-0">
                        <span className={`inline-block px-2 py-1 rounded text-xs font-medium ${getLevelColor(entry.level)}`}>
                          {entry.level}
                        </span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-sm font-mono text-gray-400">
                            {formatTimestamp(entry.timestamp)}
                          </span>
                          <span className="text-sm font-medium text-blue-400">
                            {entry.module}
                          </span>
                          {entry.function && (
                            <span className="text-sm font-mono text-gray-500">
                              {entry.function}()
                            </span>
                          )}
                        </div>
                        <div className="text-sm text-gray-200 break-words">
                          {entry.parse_error ? (
                            <span className="text-red-400">Parse Error: {entry.raw_line}</span>
                          ) : (
                            entry.message
                          )}
                        </div>
                        {entry.trace_id && (
                          <div className="mt-2 flex items-center gap-2">
                            <span className="text-xs text-gray-500">Trace:</span>
                            <button
                              onClick={() => copyToClipboard(entry.trace_id)}
                              className="text-xs font-mono bg-gray-600 hover:bg-gray-500 px-2 py-1 rounded transition-colors"
                              title="Click to copy full trace ID"
                            >
                              {entry.trace_id.substring(0, 8)}...
                            </button>
                          </div>
                        )}
                        {Object.keys(entry.extras || {}).length > 0 && (
                          <div className="mt-2">
                            <details className="text-xs">
                              <summary className="text-gray-500 cursor-pointer hover:text-gray-400">
                                Extra Fields ({Object.keys(entry.extras).length})
                              </summary>
                              <div className="mt-1 bg-gray-800 p-2 rounded text-gray-400 font-mono">
                                {Object.entries(entry.extras).map(([key, value]) => (
                                  <div key={key}>
                                    <span className="text-blue-400">{key.replace('extra_', '')}:</span> {JSON.stringify(value)}
                                  </div>
                                ))}
                              </div>
                            </details>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )
      }

      case 'feedback': {
        const feedback = data.content.feedback || []
        const stats = data.content.statistics || {}
        
        const getRatingIcon = (rating) => {
          switch (rating) {
            case 1: return <ThumbsUp className="w-4 h-4 text-green-400" />
            case 0: return <Minus className="w-4 h-4 text-yellow-400" />
            case -1: return <ThumbsDown className="w-4 h-4 text-red-400" />
            default: return null
          }
        }

        const getRatingLabel = (rating) => {
          switch (rating) {
            case 1: return 'Positive'
            case 0: return 'Neutral'
            case -1: return 'Negative'
            default: return 'Unknown'
          }
        }

        return (
          <div className="space-y-4">
            {/* Statistics Summary */}
            <div className="p-4 bg-gray-700 rounded-lg">
              <h4 className="font-medium mb-3">Feedback Statistics</h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <div className="text-center">
                    <div className="flex items-center justify-center gap-1 text-green-400 mb-1">
                      <ThumbsUp className="w-4 h-4" />
                      <span className="font-medium">{stats.positive || 0}</span>
                    </div>
                    <div className="text-gray-400">Positive</div>
                  </div>
                  <div className="text-center">
                    <div className="flex items-center justify-center gap-1 text-yellow-400 mb-1">
                      <Minus className="w-4 h-4" />
                      <span className="font-medium">{stats.neutral || 0}</span>
                    </div>
                    <div className="text-gray-400">Neutral</div>
                  </div>
                  <div className="text-center">
                    <div className="flex items-center justify-center gap-1 text-red-400 mb-1">
                      <ThumbsDown className="w-4 h-4" />
                      <span className="font-medium">{stats.negative || 0}</span>
                    </div>
                    <div className="text-gray-400">Negative</div>
                  </div>
                  <div className="text-center">
                    <div className="text-lg font-medium text-blue-400 mb-1">{stats.total || 0}</div>
                    <div className="text-gray-400">Total</div>
                  </div>
                </div>
                {stats.average !== undefined && (
                  <div className="mt-3 text-center">
                    <span className="text-sm text-gray-400">Average Rating: </span>
                    <span className="font-medium">{stats.average.toFixed(2)}</span>
                  </div>
                )}
              </div>

              {/* Feedback List */}
              <div className="space-y-3 max-h-96 overflow-y-auto">
                {feedback.length === 0 ? (
                  <div className="text-center py-8 text-gray-400">
                    {/* <Heart className="w-12 h-12 mx-auto mb-4 opacity-50" /> */}
                    <p>No feedback submitted yet</p>
                  </div>
                ) : (
                  feedback.map((item, index) => (
                    <div key={index} className="p-3 border border-gray-600 rounded-lg">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          {getRatingIcon(item.rating)}
                          <span className="font-medium">{getRatingLabel(item.rating)}</span>
                          <span className="text-sm text-gray-400">by {item.user}</span>
                        </div>
                        <span className="text-xs text-gray-400">
                          {new Date(item.timestamp).toLocaleString()}
                        </span>
                      </div>
                      {item.comment && (
                        <div className="text-sm text-gray-300 bg-gray-800 p-2 rounded mt-2">
                          "{item.comment}"
                        </div>
                      )}
                      <div className="text-xs text-gray-500 mt-2">
                        ID: {item.id}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )
      }

      default:
        return <p>Unknown content type</p>
    }
  }

  return (
    <div className={`fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center overflow-y-auto ${
      data.content?.type === 'logs' ? 'p-0' : 'p-4'
    }`}>
      <div className={`bg-gray-800 rounded-lg w-full overflow-y-auto my-8 ${
        data.content?.type === 'logs' 
          ? 'max-w-none max-h-none h-full m-0 rounded-none' 
          : 'max-w-4xl max-h-[90vh]'
      }`}>
        <div className="p-6">
          <h2 className="text-xl font-bold mb-4">{data.title}</h2>
          
          {renderContent()}

          <div className="flex justify-end gap-3 mt-6">
            {!data.content?.readonly && (
              <>
                <button
                  onClick={onClose}
                  className="px-4 py-2 bg-gray-600 hover:bg-gray-700 rounded-lg transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors disabled:opacity-50"
                >
                  <Save className="w-4 h-4" />
                  {saving ? 'Saving...' : 'Save'}
                </button>
                <button
                  onClick={handleSaveAndClose}
                  disabled={saving}
                  className="flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 rounded-lg transition-colors disabled:opacity-50"
                >
                  <Check className="w-4 h-4" />
                  Done
                </button>
              </>
            )}
            {data.content?.readonly && (
              <button
                onClick={onClose}
                className="flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 rounded-lg transition-colors"
              >
                <Check className="w-4 h-4" />
                Done
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default AdminModal
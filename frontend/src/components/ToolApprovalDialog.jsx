import { useState, useEffect } from 'react'

export default function ToolApprovalDialog({ request, onResponse }) {
  const [editedArgs, setEditedArgs] = useState(request.arguments)
  const [isEditing, setIsEditing] = useState(false)
  const [reason, setReason] = useState('')

  useEffect(() => {
    setEditedArgs(request.arguments)
    setIsEditing(false)
    setReason('')
  }, [request])

  const handleApprove = () => {
    onResponse({
      tool_call_id: request.tool_call_id,
      approved: true,
      arguments: isEditing ? editedArgs : request.arguments,
    })
  }

  const handleReject = () => {
    onResponse({
      tool_call_id: request.tool_call_id,
      approved: false,
      reason: reason || 'User rejected the tool call',
    })
  }

  const handleArgumentChange = (key, value) => {
    setEditedArgs(prev => ({
      ...prev,
      [key]: value
    }))
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-800 rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-bold text-gray-100">Tool Approval Required</h2>
            <div className="px-3 py-1 bg-yellow-600 text-white text-sm rounded">
              Requires Approval
            </div>
          </div>

          <div className="mb-6">
            <div className="text-gray-300 mb-2">
              <span className="font-medium">Tool:</span> <span className="text-blue-400">{request.tool_name}</span>
            </div>
            <p className="text-gray-400 text-sm">
              This tool requires your approval before execution. Please review the arguments and choose to approve or reject.
            </p>
          </div>

          <div className="mb-6">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-lg font-semibold text-gray-100">Arguments</h3>
              {request.allow_edit && (
                <button
                  onClick={() => setIsEditing(!isEditing)}
                  className="px-3 py-1 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
                >
                  {isEditing ? 'View Mode' : 'Edit Mode'}
                </button>
              )}
            </div>

            {!isEditing ? (
              <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
                <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(request.arguments, null, 2)}
                </pre>
              </div>
            ) : (
              <div className="space-y-3">
                {Object.entries(editedArgs).map(([key, value]) => (
                  <div key={key} className="bg-gray-900 border border-gray-700 rounded-lg p-3">
                    <label className="block text-sm font-medium text-gray-300 mb-1">
                      {key}
                    </label>
                    <textarea
                      value={typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                      onChange={(e) => {
                        const newValue = e.target.value
                        // Try to parse as JSON if it's a complete JSON structure
                        if ((newValue.trim().startsWith('{') && newValue.trim().endsWith('}')) ||
                            (newValue.trim().startsWith('[') && newValue.trim().endsWith(']'))) {
                          try {
                            const parsed = JSON.parse(newValue)
                            handleArgumentChange(key, parsed)
                            return
                          } catch {
                            // Not valid JSON yet, use string value
                          }
                        }
                        // Use string value for non-JSON or incomplete JSON
                        handleArgumentChange(key, newValue)
                      }}
                      className="w-full bg-gray-800 text-gray-200 border border-gray-600 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                      rows={typeof value === 'object' ? Math.min(10, JSON.stringify(value, null, 2).split('\n').length) : 3}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>

          {Object.keys(editedArgs).length === 0 ? (
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 mb-6">
              <p className="text-gray-400 text-sm">No arguments provided</p>
            </div>
          ) : null}

          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Rejection Reason (optional)
            </label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Enter reason for rejecting this tool call..."
              className="w-full bg-gray-900 text-gray-200 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div className="flex gap-3">
            <button
              onClick={handleReject}
              className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg font-medium transition-colors"
            >
              Reject
            </button>
            <button
              onClick={handleApprove}
              className="flex-1 px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg font-medium transition-colors"
            >
              Approve {isEditing ? '(with edits)' : ''}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

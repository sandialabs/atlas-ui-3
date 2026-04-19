import { useState, useEffect } from 'react'
import { useChat } from '../contexts/ChatContext'

// Inline tool-approval prompt rendered as a chat message. Extracted from
// Message.jsx. Behavior is unchanged from the inline version.
const ToolApprovalMessage = ({ message }) => {
  const { sendApprovalResponse, settings, updateSettings } = useChat()
  const [isEditing, setIsEditing] = useState(false)
  const [editedArgs, setEditedArgs] = useState(message.arguments)
  const [reason, setReason] = useState('')
  const [isExpanded, setIsExpanded] = useState(true)

  useEffect(() => {
    if (settings?.autoApproveTools && !message.admin_required && message.status === 'pending') {
      const timer = setTimeout(() => {
        sendApprovalResponse({
          type: 'tool_approval_response',
          tool_call_id: message.tool_call_id,
          approved: true,
          arguments: message.arguments,
        })
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [settings?.autoApproveTools, message.admin_required, message.status, message.tool_call_id, message.arguments, sendApprovalResponse])

  const handleApprove = () => {
    sendApprovalResponse({
      type: 'tool_approval_response',
      tool_call_id: message.tool_call_id,
      approved: true,
      arguments: isEditing ? editedArgs : message.arguments,
    })
  }

  const handleReject = () => {
    sendApprovalResponse({
      type: 'tool_approval_response',
      tool_call_id: message.tool_call_id,
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

  if (message.status === 'approved' || message.status === 'rejected') {
    return (
      <div className="text-gray-200">
        <div className="flex items-center gap-2 mb-3">
          <span className={`px-2 py-1 rounded text-xs font-medium ${
            message.status === 'approved' ? 'bg-green-600' : 'bg-red-600'
          }`}>
            {message.status === 'approved' ? 'APPROVED' : 'REJECTED'}
          </span>
          <span className="font-medium">{message.tool_name}</span>
        </div>
        {message.status === 'rejected' && message.rejection_reason && (
          <div className="text-sm text-gray-400">Reason: {message.rejection_reason}</div>
        )}
      </div>
    )
  }

  return (
    <div className="text-gray-200">
      <div className="flex items-center gap-2 mb-3">
        <span className={`px-2 py-1 rounded text-xs font-medium ${
          settings?.autoApproveTools && !message.admin_required ? 'bg-blue-600' : 'bg-yellow-600'
        }`}>
          {settings?.autoApproveTools && !message.admin_required ? 'AUTO-APPROVED' : 'APPROVAL REQUIRED'}
        </span>
        <span className="font-medium">{message.tool_name}</span>
        {!message.admin_required && (
          <button
            type="button"
            onClick={() => {
              try {
                updateSettings?.({ autoApproveTools: !settings?.autoApproveTools })
              } catch (e) {
                console.error('Failed to toggle auto-approve from inline control', e)
              }
            }}
            className={`ml-2 px-2 py-0.5 rounded text-xs font-medium border transition-colors cursor-pointer ${
              settings?.autoApproveTools
                ? 'bg-blue-600 text-white border-blue-500 hover:bg-blue-700'
                : 'bg-gray-700 text-gray-100 border-gray-600 hover:bg-gray-600'
            }`}
            title="Click to toggle auto-approve for non-admin tool calls. Admin-required calls will still prompt."
          >
            {settings?.autoApproveTools ? 'Auto-approve ON' : 'Auto-approve OFF'}
          </button>
        )}
      </div>

      {/* Arguments Section */}
      <div className="mb-4">
        <div className="border-l-4 border-yellow-500 pl-4">
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="w-full text-left text-sm font-semibold text-yellow-400 mb-2 flex items-center gap-2 hover:text-yellow-300 transition-colors"
          >
            <span className={`transform transition-transform duration-200 ${isExpanded ? 'rotate-90' : 'rotate-0'}`}>
              ▶
            </span>
            Tool Arguments {!isExpanded ? `(${Object.keys(message.arguments).length} params)` : ''}
          </button>

          {isExpanded && (
            <>
              <div className="mb-2 flex items-center gap-2">
                <button
                  onClick={() => setIsEditing(!isEditing)}
                  className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
                >
                  {isEditing ? 'View Mode' : 'Edit Arguments'}
                </button>
              </div>

              {!isEditing ? (
                <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 max-h-96 overflow-y-auto">
                  <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                    {JSON.stringify(message.arguments, null, 2)}
                  </pre>
                </div>
              ) : (
                <div className="space-y-3 max-h-[60vh] overflow-y-auto">
                  {Object.entries(editedArgs).map(([key, value]) => (
                    <div key={key} className="bg-gray-900 border border-gray-700 rounded-lg p-3">
                      <label className="block text-sm font-medium text-gray-300 mb-1">
                        {key}
                      </label>
                      <textarea
                        value={typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                        onChange={(e) => {
                          const newValue = e.target.value
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
                          handleArgumentChange(key, newValue)
                        }}
                        className="w-full bg-gray-800 text-gray-200 border border-gray-600 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                        rows={Math.max(3, Math.min(20, (typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)).split('\n').length))}
                      />
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Action Buttons and Rejection Reason - Compact Layout */}
      {!(settings?.autoApproveTools && !message.admin_required) && (
        <div className="flex gap-2 items-center">
          <button
            onClick={handleApprove}
            className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors whitespace-nowrap"
          >
            Approve {isEditing ? '(with edits)' : ''}
          </button>
          <button
            onClick={handleReject}
            className="px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-200 rounded border border-gray-600 transition-colors whitespace-nowrap"
          >
            Reject
          </button>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Rejection reason (optional)..."
            className="flex-1 bg-gray-900 text-gray-200 border border-gray-700 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      )}
    </div>
  )
}

export default ToolApprovalMessage

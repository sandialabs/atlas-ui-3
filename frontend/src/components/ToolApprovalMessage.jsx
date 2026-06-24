import { useState, useEffect } from 'react'
import { useChat } from '../contexts/ChatContext'

// Inline tool-approval prompt rendered as a chat message. Extracted from
// Message.jsx. Behavior is unchanged from the inline version.
const ToolApprovalMessage = ({ message }) => {
  const { sendApprovalResponse, settings, updateSettings } = useChat()
  const [isEditing, setIsEditing] = useState(false)
  const [editedArgs, setEditedArgs] = useState(message.arguments)
  const [reason, setReason] = useState('')
  // The arguments panel collapses to a single header line; the choice is
  // persisted to localStorage so it sticks across messages and reloads (F5).
  // No saved preference: keep it expanded when the user must act on the call,
  // but start auto-approved calls collapsed so the args box doesn't dwarf the
  // tool-call output below it.
  const [isExpanded, setIsExpanded] = useState(() => {
    const saved = localStorage.getItem('toolApprovalArgsCollapsed')
    if (saved !== null) return !JSON.parse(saved)
    return !(settings?.autoApproveTools && !message.admin_required)
  })

  useEffect(() => {
    localStorage.setItem('toolApprovalArgsCollapsed', JSON.stringify(!isExpanded))
  }, [isExpanded])

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
      <div className="text-gray-200 flex items-center gap-2 flex-wrap">
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
          message.status === 'approved' ? 'bg-green-600' : 'bg-red-600'
        }`}>
          {message.status === 'approved' ? 'APPROVED' : 'REJECTED'}
        </span>
        <span className="font-medium text-sm">{message.tool_name}</span>
        {message.status === 'rejected' && message.rejection_reason && (
          <span className="text-sm text-gray-400">— {message.rejection_reason}</span>
        )}
      </div>
    )
  }

  const argCount = Object.keys(message.arguments).length

  return (
    <div className="text-gray-200">
      {/* Single-line summary: collapse toggle + status + tool name + auto-approve */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-2 text-left hover:text-white transition-colors cursor-pointer"
          aria-expanded={isExpanded}
        >
          <span className={`text-gray-500 text-xs transform transition-transform duration-200 ${isExpanded ? 'rotate-90' : 'rotate-0'}`}>
            ▶
          </span>
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
            settings?.autoApproveTools && !message.admin_required ? 'bg-blue-600' : 'bg-yellow-600'
          }`}>
            {settings?.autoApproveTools && !message.admin_required ? 'AUTO-APPROVED' : 'APPROVAL REQUIRED'}
          </span>
          <span className="font-medium text-sm">{message.tool_name}</span>
          {!isExpanded && argCount > 0 && (
            <span className="text-gray-500 text-xs">· {argCount} param{argCount !== 1 ? 's' : ''}</span>
          )}
        </button>
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
            className={`px-2 py-0.5 rounded text-[10px] font-medium border transition-colors cursor-pointer ${
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

      {/* Expanded arguments (view / edit) */}
      {isExpanded && (
        <div className="mt-2 ml-5 border-l-2 border-yellow-500 pl-3">
          <div className="flex items-center justify-between mb-1">
            <div className="text-xs font-semibold text-yellow-400">Input Arguments</div>
            <button
              onClick={() => setIsEditing(!isEditing)}
              className="px-2 py-0.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
            >
              {isEditing ? 'View' : 'Edit'}
            </button>
          </div>

          {!isEditing ? (
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 max-h-64 overflow-y-auto">
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
        </div>
      )}

      {/* Action Buttons and Rejection Reason - Compact Layout */}
      {!(settings?.autoApproveTools && !message.admin_required) && (
        <div className="flex gap-2 items-center mt-2 ml-5">
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

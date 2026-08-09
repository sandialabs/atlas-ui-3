import { useState, useEffect } from 'react'
import { useChat } from '../contexts/ChatContext'
import { resolveAutoApproved } from '../utils/toolApproval'

// Inline tool-approval prompt rendered as a chat message. Extracted from
// Message.jsx. `compact` (default true) renders the dense single-line row added
// in #673; when the user turns compact messages off it falls back to the
// classic full-bubble approval layout.
//
// Auto-approved calls render nothing (#762): the row said "we approved a thing
// we are about to report anyway", and the tool_call row that follows names the
// same tool a moment later. The component still mounts — it owns the effect
// that sends the auto-approval — it just has no visible output. The
// APPROVAL REQUIRED path is unchanged.
const ToolApprovalMessage = ({ message, compact = true }) => {
  const { sendApprovalResponse, settings, updateToolResult } = useChat()
  // The websocket handler defaults this to {}, but guard anyway so a malformed
  // payload can't crash the row on Object.keys/Object.entries.
  const args = message.arguments || {}
  const [isEditing, setIsEditing] = useState(false)
  const [editedArgs, setEditedArgs] = useState(args)
  const [reason, setReason] = useState('')
  // Once the user approves or rejects, the decision is final — there's no
  // "undo" on the server side, and the backend doesn't echo a status change
  // back. We record the choice on the message in the global store (keyed by the
  // stable tool_call_id) so it survives this row remounting — the message list
  // keys by array index, so an earlier message appearing/collapsing would
  // otherwise reset local state and resurrect the Approve/Reject buttons. A
  // local mirror gives an instant update before the dispatch propagates.
  const [decision, setDecision] = useState(null)
  const resolvedStatus = decision || message.status
  const resolvedReason = message.rejection_reason || (decision === 'rejected' ? reason : '')
  const autoApproved = resolveAutoApproved({ ...message, status: resolvedStatus }, settings)
  // The backend reuses this tool_call_id for the execution lifecycle: once the
  // call is approved and runs, tool_start/tool_complete overwrite this message's
  // status to calling/in_progress/completed/failed. So anything that isn't
  // 'pending' means the call already went through — it's resolved, and the
  // Approve/Reject controls must stay gone (even if this row remounts and loses
  // the local `decision` mirror). Only an explicit 'rejected' is a denial.
  const isPending = resolvedStatus === 'pending'
  const isRejected = resolvedStatus === 'rejected'
  // Calls that need human review always open expanded — a reviewer shouldn't
  // have to expand to see what they're approving. Since #762 the only rows this
  // component renders are review-required ones (auto-approved calls render
  // nothing), so there is no persisted collapse preference to honour: this is
  // per-message local state that starts expanded every time.
  const [reviewCollapsed, setReviewCollapsed] = useState(false)
  const isExpanded = !reviewCollapsed
  const toggleCollapsed = () => setReviewCollapsed(c => !c)

  useEffect(() => {
    if (settings?.autoApproveTools && !message.admin_required && message.status === 'pending') {
      const timer = setTimeout(() => {
        // Only record the decision once the server has actually been told.
        // `sendApprovalResponse` returns false when the WebSocket isn't open;
        // persisting `auto_approved: true` before the send completes would
        // leave the row stuck hidden if the socket dropped during the 100ms
        // delay — the approval never reached the backend, but rendering keys
        // off the persisted flag and so the row never comes back to retry.
        // The live `autoApproveTools` setting still hides the row while the
        // socket is down, so there is no visible flicker on the success path.
        const sent = sendApprovalResponse({
          type: 'tool_approval_response',
          tool_call_id: message.tool_call_id,
          approved: true,
          arguments: message.arguments,
        })
        if (sent) {
          updateToolResult?.(message.tool_call_id, { auto_approved: true })
        }
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [settings?.autoApproveTools, message.admin_required, message.status, message.tool_call_id, message.arguments, sendApprovalResponse, updateToolResult])

  const handleApprove = () => {
    if (resolvedStatus !== 'pending') return
    setDecision('approved')
    updateToolResult?.(message.tool_call_id, { status: 'approved', auto_approved: false })
    sendApprovalResponse({
      type: 'tool_approval_response',
      tool_call_id: message.tool_call_id,
      approved: true,
      arguments: isEditing ? editedArgs : message.arguments,
    })
  }

  const handleReject = () => {
    if (resolvedStatus !== 'pending') return
    const rejectionReason = reason || 'User rejected the tool call'
    setDecision('rejected')
    updateToolResult?.(message.tool_call_id, { status: 'rejected', rejection_reason: rejectionReason, auto_approved: false })
    sendApprovalResponse({
      type: 'tool_approval_response',
      tool_call_id: message.tool_call_id,
      approved: false,
      reason: rejectionReason,
    })
  }

  const handleArgumentChange = (key, value) => {
    setEditedArgs(prev => ({
      ...prev,
      [key]: value
    }))
  }

  // Shared editor used in both layouts when "Edit" is active.
  const argsEditor = (
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
  )

  // Auto-approved calls contribute no transcript row at all (#762). The hooks
  // above still run — including the effect that sends the approval — so this
  // must come after them, not as an early bail-out. The auto-approve toggle
  // that used to live on this row now sits on the Active Tools strip above the
  // composer, where one indicator replaces one per call.
  if (autoApproved) return null

  // ---- Classic (non-compact) layout: full bubble, pre-#673 styling ----
  if (!compact) {
    if (!isPending) {
      return (
        <div className="text-gray-200">
          <div className="flex items-center gap-2 mb-2">
            <span className={`px-2 py-1 rounded text-xs font-medium ${
              isRejected ? 'bg-red-600' : 'bg-green-600'
            }`}>
              {isRejected ? 'REJECTED' : 'APPROVED'}
            </span>
            <span className="font-medium">{message.tool_name}</span>
          </div>
          {isRejected && resolvedReason && (
            <div className="text-sm text-gray-400">Reason: {resolvedReason}</div>
          )}
        </div>
      )
    }

    return (
      <div className="text-gray-200">
        <div className="flex items-center gap-2 mb-3">
          <span className="px-2 py-1 rounded text-xs font-medium bg-yellow-600">
            APPROVAL REQUIRED
          </span>
          <span className="font-medium">{message.tool_name}</span>
        </div>

        {/* Arguments Section */}
        <div className="mb-4">
          <div className="border-l-4 border-yellow-500 pl-4">
            <button
              onClick={toggleCollapsed}
              className="w-full text-left text-sm font-semibold text-yellow-400 mb-2 flex items-center gap-2 hover:text-yellow-300 transition-colors"
              aria-expanded={isExpanded}
            >
              <span className={`transform transition-transform duration-200 ${isExpanded ? 'rotate-90' : 'rotate-0'}`}>
                ▶
              </span>
              Tool Arguments {!isExpanded ? `(${Object.keys(args).length} params)` : ''}
            </button>

            {isExpanded && (
              <>
                {message.allow_edit !== false && (
                  <div className="mb-2 flex items-center gap-2">
                    <button
                      onClick={() => setIsEditing(!isEditing)}
                      className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
                    >
                      {isEditing ? 'View Mode' : 'Edit Arguments'}
                    </button>
                  </div>
                )}

                {!isEditing ? (
                  <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 max-h-96 overflow-y-auto">
                    <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(args, null, 2)}
                    </pre>
                  </div>
                ) : argsEditor}
              </>
            )}
          </div>
        </div>

        {/* Action Buttons and Rejection Reason.
            The row wraps and the buttons opt out of shrinking: the transcript
            containment rule clears min-width on every button, so in a nowrap
            row the fixed-width actions would absorb all the shrinkage and
            render as "Ap"/"Re" while the text input held its intrinsic
            minimum. The input keeps a legible floor and drops to its own line
            instead of becoming a sliver (#747). */}
        <div className="flex flex-wrap gap-2 items-center">
          <button
            onClick={handleApprove}
            className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors whitespace-nowrap shrink-0"
          >
            Approve {isEditing ? '(with edits)' : ''}
          </button>
          <button
            onClick={handleReject}
            className="px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-200 rounded border border-gray-600 transition-colors whitespace-nowrap shrink-0"
          >
            Reject
          </button>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Rejection reason (optional)..."
            className="flex-1 min-w-[12rem] bg-gray-900 text-gray-200 border border-gray-700 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>
    )
  }

  // ---- Compact (default) layout ----
  if (!isPending) {
    return (
      <div className="text-gray-200 flex items-center gap-2 flex-wrap">
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
          isRejected ? 'bg-red-600' : 'bg-green-600'
        }`}>
          {isRejected ? 'REJECTED' : 'APPROVED'}
        </span>
        <span className="font-medium text-sm">{message.tool_name}</span>
        {isRejected && resolvedReason && (
          <span className="text-sm text-gray-400">— {resolvedReason}</span>
        )}
      </div>
    )
  }

  const argCount = Object.keys(args).length

  return (
    <div className="text-gray-200">
      {/* Single-line summary: collapse toggle + status + tool name */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={toggleCollapsed}
          className="flex items-center gap-2 text-left hover:text-white transition-colors cursor-pointer"
          aria-expanded={isExpanded}
        >
          <span className={`text-gray-500 text-xs transform transition-transform duration-200 ${isExpanded ? 'rotate-90' : 'rotate-0'}`}>
            ▶
          </span>
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-yellow-600">
            APPROVAL REQUIRED
          </span>
          <span className="font-medium text-sm">{message.tool_name}</span>
          {!isExpanded && argCount > 0 && (
            <span className="text-gray-500 text-xs">· {argCount} param{argCount !== 1 ? 's' : ''}</span>
          )}
        </button>
      </div>

      {/* Expanded arguments (view / edit) */}
      {isExpanded && (
        <div className="mt-2 ml-5 border-l-2 border-yellow-500 pl-3">
          <div className="flex items-center justify-between mb-1">
            <div className="text-xs font-semibold text-yellow-400">Input Arguments</div>
            {/* Hide the edit affordance when the server disallows edits — any
                edits would be ignored server-side, so showing it is misleading. */}
            {message.allow_edit !== false && (
              <button
                onClick={() => setIsEditing(!isEditing)}
                className="px-2 py-0.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
              >
                {isEditing ? 'View' : 'Edit'}
              </button>
            )}
          </div>

          {!isEditing ? (
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 max-h-64 overflow-y-auto">
              <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                {JSON.stringify(args, null, 2)}
              </pre>
            </div>
          ) : argsEditor}
        </div>
      )}

      {/* Action Buttons and Rejection Reason - Compact Layout.
          Wraps and pins the button widths for the same reason as the classic
          layout above (#747). */}
      <div className="flex flex-wrap gap-2 items-center mt-2 ml-5">
        <button
          onClick={handleApprove}
          className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors whitespace-nowrap shrink-0"
        >
          Approve {isEditing ? '(with edits)' : ''}
        </button>
        <button
          onClick={handleReject}
          className="px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-200 rounded border border-gray-600 transition-colors whitespace-nowrap shrink-0"
        >
          Reject
        </button>
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Rejection reason (optional)..."
          className="flex-1 min-w-[12rem] bg-gray-900 text-gray-200 border border-gray-700 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
    </div>
  )
}

export default ToolApprovalMessage

import { useState, useEffect, useRef } from 'react'
import { useChat } from '../contexts/ChatContext'
import { useToast } from './ui/toastContext'
import { approvalReason, resolveAutoApproved } from '../utils/toolApproval'
import { FOCUS_RING } from '../utils/focusRing'

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
function approvalIdentity(message) {
  const extra = {}
  if (message.run_id) extra.run_id = message.run_id
  if (message.conversation_id) extra.conversation_id = message.conversation_id
  if (message.invocation_id) extra.invocation_id = message.invocation_id
  if (message.step != null) extra.step = message.step
  return extra
}

function resultPatch(message, patch) {
  return message.invocation_id ? { ...patch, invocation_id: message.invocation_id } : patch
}

const ToolApprovalMessage = ({ message, compact = true }) => {
  const { sendApprovalResponse, settings, updateToolResult } = useChat()
  const toast = useToast()
  // Keep a stable handle for the effect: the no-provider useToast fallback
  // returns a fresh object every call, and putting `toast` in the effect deps
  // would re-arm the auto-approval timer on every render.
  const toastRef = useRef(toast)
  toastRef.current = toast
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
  // Guards the auto-approval effect against re-sending when `sendApprovalResponse`
  // changes identity (it is `sendMessage` from WSContext and is not memoized).
  // After a successful send, status stays `pending` until tool_start overwrites
  // it, so deps can re-fire; this ref + the `auto_approved` gate stop a second
  // `tool_approval_response` for the same call.
  const autoApprovalSentRef = useRef(false)
  // A server-settled outcome (tool_approval_settled) is the decision that won,
  // so it overrides a local click that lost, e.g. an approval after the
  // request expired.
  const resolvedStatus = message.already_resolved && message.status !== 'pending'
    ? message.status
    : (decision || message.status)
  const resolvedReason = message.rejection_reason || (decision === 'rejected' ? reason : '')
  const autoApproved = resolveAutoApproved({ ...message, status: resolvedStatus }, settings)
  // The backend reuses this tool_call_id for the execution lifecycle: once the
  // call is approved and runs, tool_start/tool_complete overwrite this message's
  // status to calling/in_progress/completed/failed. So anything that isn't
  // 'pending' means the call already went through — it's resolved, and the
  // Approve/Reject controls must stay gone (even if this row remounts and loses
  // the local `decision` mirror). Only an explicit 'rejected' is a denial.
  const isPending = resolvedStatus === 'pending'
  const isExpired = resolvedStatus === 'expired'
  const isRejected = resolvedStatus === 'rejected' || isExpired
  const settledLabel = isExpired ? 'EXPIRED' : (isRejected ? 'REJECTED' : 'APPROVED')
  // Calls that need human review always open expanded — a reviewer shouldn't
  // have to expand to see what they're approving. Since #762 the only rows this
  // component renders are review-required ones (auto-approved calls render
  // nothing), so there is no persisted collapse preference to honour: this is
  // per-message local state that starts expanded every time.
  const [reviewCollapsed, setReviewCollapsed] = useState(false)
  const isExpanded = !reviewCollapsed
  const toggleCollapsed = () => setReviewCollapsed(c => !c)

  useEffect(() => {
    // A boolean `auto_approved` means the decision was already recorded
    // (true = sent successfully; false = send failed or manual path took over).
    // Do not auto-send again in either case.
    if (typeof message.auto_approved === 'boolean') return
    if (autoApprovalSentRef.current) return
    if (settings?.autoApproveTools && !message.admin_required && message.status === 'pending') {
      const timer = setTimeout(() => {
        if (autoApprovalSentRef.current) return
        // Only record a successful auto-approve once the server has actually
        // been told. `sendApprovalResponse` returns false when the WebSocket
        // isn't open; persisting `auto_approved: true` before the send would
        // leave the row stuck hidden with no approval in flight.
        const sent = sendApprovalResponse({
          type: 'tool_approval_response',
          tool_call_id: message.tool_call_id,
          approved: true,
          arguments: message.arguments,
          ...(message.run_id ? { run_id: message.run_id } : {}),
          ...(message.conversation_id ? { conversation_id: message.conversation_id } : {}),
          ...(message.invocation_id ? { invocation_id: message.invocation_id } : {}),
          ...(message.step != null ? { step: message.step } : {}),
        })
        if (sent) {
          autoApprovalSentRef.current = true
          updateToolResult?.(message.tool_call_id, message.invocation_id
            ? { auto_approved: true, invocation_id: message.invocation_id }
            : { auto_approved: true })
        } else {
          // Force the review row visible so the user can approve manually.
          // `resolveAutoApproved` treats a boolean false as "not auto", which
          // also un-hides the Message wrapper that keys off the same helper.
          updateToolResult?.(message.tool_call_id, message.invocation_id
            ? { auto_approved: false, invocation_id: message.invocation_id }
            : { auto_approved: false })
          toastRef.current.error(
            'Auto-approve failed (not connected). Approve manually, or reconnect and try again.'
          )
        }
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [settings?.autoApproveTools, message.admin_required, message.status, message.auto_approved, message.tool_call_id, message.arguments, message.run_id, message.conversation_id, message.invocation_id, message.step, sendApprovalResponse, updateToolResult])

  const handleApprove = () => {
    if (resolvedStatus !== 'pending') return
    // Persist terminal state only after a successful send — same rule as the
    // auto path. A dropped socket must leave the controls available to retry.
    const sent = sendApprovalResponse({
      type: 'tool_approval_response',
      tool_call_id: message.tool_call_id,
      approved: true,
      arguments: isEditing ? editedArgs : message.arguments,
      ...approvalIdentity(message),
    })
    if (!sent) {
      toastRef.current.error('Could not send approval (not connected). Try again when reconnected.')
      return
    }
    setDecision('approved')
    updateToolResult?.(message.tool_call_id, resultPatch(message, { status: 'approved', auto_approved: false }))
  }

  const handleReject = () => {
    if (resolvedStatus !== 'pending') return
    const rejectionReason = reason || 'User rejected the tool call'
    const sent = sendApprovalResponse({
      type: 'tool_approval_response',
      tool_call_id: message.tool_call_id,
      approved: false,
      reason: rejectionReason,
      ...approvalIdentity(message),
    })
    if (!sent) {
      toastRef.current.error('Could not send rejection (not connected). Try again when reconnected.')
      return
    }
    setDecision('rejected')
    updateToolResult?.(message.tool_call_id, resultPatch(message, { status: 'rejected', rejection_reason: rejectionReason, auto_approved: false }))
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
            aria-label={`Argument ${key}`}
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
              {settledLabel}
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
        <p className="text-xs text-gray-400 -mt-2 mb-3">{approvalReason(message, settings)}</p>

        {/* Arguments Section */}
        <div className="mb-4">
          <div className="border-l-4 border-yellow-500 pl-4">
            <button
              type="button"
              onClick={toggleCollapsed}
              className={`w-full text-left text-sm font-semibold text-yellow-400 mb-2 flex items-center gap-2 hover:text-yellow-300 transition-colors rounded ${FOCUS_RING}`}
              aria-expanded={isExpanded}
            >
              <span aria-hidden="true" className={`transform transition-transform duration-200 ${isExpanded ? 'rotate-90' : 'rotate-0'}`}>
                ▶
              </span>
              Tool Arguments {!isExpanded ? `(${Object.keys(args).length} params)` : ''}
            </button>

            {isExpanded && (
              <>
                {message.allow_edit !== false && (
                  <div className="mb-2 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setIsEditing(!isEditing)}
                      className={`px-3 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors ${FOCUS_RING}`}
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
            type="button"
            onClick={handleApprove}
            className={`px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors whitespace-nowrap shrink-0 ${FOCUS_RING}`}
          >
            Approve {isEditing ? '(with edits)' : ''}
          </button>
          <button
            type="button"
            onClick={handleReject}
            className={`px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-200 rounded border border-gray-600 transition-colors whitespace-nowrap shrink-0 ${FOCUS_RING}`}
          >
            Reject
          </button>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Rejection reason (optional)..."
            aria-label="Rejection reason (optional)"
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
          {settledLabel}
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
          className={`flex items-center gap-2 text-left hover:text-white transition-colors cursor-pointer rounded ${FOCUS_RING}`}
          aria-expanded={isExpanded}
        >
          <span aria-hidden="true" className={`text-gray-500 text-xs transform transition-transform duration-200 ${isExpanded ? 'rotate-90' : 'rotate-0'}`}>
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

      {/* Why this call is waiting on the user: the setting is off, or an
          admin gate overrides it. Without this the row never said the user
          could stop being asked. */}
      <p className="mt-1 ml-5 text-xs text-gray-400">{approvalReason(message, settings)}</p>

      {/* Expanded arguments (view / edit) */}
      {isExpanded && (
        <div className="mt-2 ml-5 border-l-2 border-yellow-500 pl-3">
          <div className="flex items-center justify-between mb-1">
            <div className="text-xs font-semibold text-yellow-400">Input Arguments</div>
            {/* Hide the edit affordance when the server disallows edits — any
                edits would be ignored server-side, so showing it is misleading. */}
            {message.allow_edit !== false && (
              <button
                type="button"
                onClick={() => setIsEditing(!isEditing)}
                className={`px-2 py-0.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors ${FOCUS_RING}`}
                title={isEditing ? 'Show the arguments read-only' : 'Change the arguments before approving'}
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
          type="button"
          onClick={handleApprove}
          className={`px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors whitespace-nowrap shrink-0 ${FOCUS_RING}`}
        >
          Approve {isEditing ? '(with edits)' : ''}
        </button>
        <button
          type="button"
          onClick={handleReject}
          className={`px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-200 rounded border border-gray-600 transition-colors whitespace-nowrap shrink-0 ${FOCUS_RING}`}
        >
          Reject
        </button>
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Rejection reason (optional)..."
          aria-label="Rejection reason (optional)"
          className="flex-1 min-w-[12rem] bg-gray-900 text-gray-200 border border-gray-700 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
    </div>
  )
}

export default ToolApprovalMessage

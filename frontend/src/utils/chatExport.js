// Helpers for the chat-download / export flow.
//
// For MCP-server prompts the frontend does not have the actual prompt body
// (that lives on the backend MCP server), so we surface name / description /
// server. For user-authored prompts (issue #153) the body lives client-side,
// so we also include the first few lines as a preview in the export.

import { USER_PROMPT_PREFIX } from '../hooks/chat/useSelections'
import { filterArgumentsForDisplay, processToolResult } from './toolResultUtils'

// Number of leading lines of the prompt body to include in the export preview.
const PROMPT_PREVIEW_LINES = 5
// Hard cap on preview length so a single long line cannot bloat the export.
const PROMPT_PREVIEW_MAX_CHARS = 400

export function buildPromptPreview(content) {
  if (typeof content !== 'string' || !content) return ''
  const lines = content.split(/\r?\n/).slice(0, PROMPT_PREVIEW_LINES)
  let preview = lines.join('\n').trim()
  if (preview.length > PROMPT_PREVIEW_MAX_CHARS) {
    preview = preview.slice(0, PROMPT_PREVIEW_MAX_CHARS).trimEnd() + '…'
  } else if (content.length > preview.length) {
    preview = preview + '\n…'
  }
  return preview
}

// Tool-call message fields that the renderer reads off the top-level message
// object (Message.jsx). They live as loose props on the in-memory message but
// must be tucked into `metadata` to survive a save/reload round-trip, because
// the restore path (loadSavedConversation) spreads `msg.metadata` back onto the
// message. Without this, reloaded conversations lost all tool input/output
// (issue #684).
const TOOL_CALL_PERSISTED_FIELDS = [
  'tool_call_id',
  'tool_name',
  'server_name',
  'arguments',
  'result',
  'status',
]

// Serialize a live message into the shape persisted to history (local IndexedDB
// or the server schema): role / content / timestamp / message_type plus a
// `metadata` blob carrying any tool-call detail. Keeping tool I/O here means a
// reloaded conversation re-renders the tool calls exactly as the live one did.
export function buildPersistedMessage(m) {
  const persisted = {
    role: m.role,
    content: m.content || '',
    timestamp: m.timestamp,
    message_type: m.type || 'chat',
  }
  if (m.type === 'tool_call') {
    const metadata = {}
    for (const field of TOOL_CALL_PERSISTED_FIELDS) {
      if (m[field] !== undefined) metadata[field] = m[field]
    }
    persisted.metadata = metadata
  }
  return persisted
}

// Render a tool-call message as a plain-text block for the .txt export. Mirrors
// what the UI shows when a tool row is expanded: the tool name, its input
// arguments, and its output result -- with large base64/file payloads elided by
// the same display filters used in-app (issue #684). Returns null for messages
// that are not tool calls so callers can fall back to default formatting.
export function formatToolCallForText(m) {
  if (!m || m.type !== 'tool_call') return null
  const serverPart = m.server_name ? ` (${m.server_name})` : ''
  const lines = [`TOOL CALL: ${m.tool_name || 'unknown'}${serverPart}`]
  if (m.status) lines.push(`Status: ${m.status}`)

  const argCount = m.arguments && typeof m.arguments === 'object'
    ? Object.keys(m.arguments).length
    : 0
  if (argCount > 0) {
    lines.push('Input Arguments:')
    lines.push(JSON.stringify(filterArgumentsForDisplay(m.arguments), null, 2))
  }

  if (m.result != null && m.result !== '') {
    const processed = processToolResult(m.result)
    lines.push('Output Result:')
    lines.push(typeof processed === 'string' ? processed : JSON.stringify(processed, null, 2))
  }
  return lines.join('\n')
}

export function buildPromptInfoByKey(promptsConfig, userPrompts) {
  const out = {}
  ;(promptsConfig || []).forEach(server => {
    (server.prompts || []).forEach(p => {
      const key = `${server.server}_${p.name}`
      out[key] = {
        key,
        name: p.name,
        description: p.description || '',
        server: server.server,
        preview: '',
      }
    })
  })
  ;(userPrompts || []).forEach(p => {
    if (!p || p.id == null) return
    const key = `${USER_PROMPT_PREFIX}${p.id}`
    out[key] = {
      key,
      name: p.title || `user-prompt-${p.id}`,
      description: 'User-authored custom prompt',
      server: 'user library',
      preview: buildPromptPreview(p.content),
    }
  })
  return out
}

export function resolvePromptInfo(key, promptInfoByKey) {
  if (!key) return null
  return promptInfoByKey[key] || { key, name: key, description: '', server: '', preview: '' }
}

// Walk through messages and inject synthetic system entries at points where the
// active custom prompt changed (based on the per-user-message _activePromptKey
// snapshot). Strips _activePromptKey from the exported messages.
export function buildExportConversation(messages, promptInfoByKey) {
  const out = []
  let prev = null
  let sawAny = false
  for (const m of messages) {
    if (m && m.role === 'user' && Object.prototype.hasOwnProperty.call(m, '_activePromptKey')) {
      const cur = m._activePromptKey || null
      if (!sawAny || cur !== prev) {
        if (cur) {
          const info = resolvePromptInfo(cur, promptInfoByKey)
          const serverLabel = info.server ? ` (from ${info.server})` : ''
          const descPart = info.description ? ` — ${info.description}` : ''
          const previewPart = info.preview ? `\nPrompt preview:\n${info.preview}` : ''
          out.push({
            role: 'system',
            content: `Custom prompt activated: ${info.name}${serverLabel}${descPart}${previewPart}`,
            timestamp: m.timestamp,
            _promptChange: true,
            promptKey: info.key,
            promptName: info.name,
            promptServer: info.server,
            promptDescription: info.description,
            promptPreview: info.preview || '',
          })
        } else if (sawAny && prev) {
          out.push({
            role: 'system',
            content: 'Custom prompt cleared (using default prompt)',
            timestamp: m.timestamp,
            _promptChange: true,
            promptKey: null,
          })
        }
        prev = cur
        sawAny = true
      }
    }
    const { _activePromptKey, ...rest } = m || {}
    out.push(rest)
  }
  return out
}

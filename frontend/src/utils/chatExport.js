// Helpers for the chat-download / export flow.
//
// For MCP-server prompts the frontend does not have the actual prompt body
// (that lives on the backend MCP server), so we surface name / description /
// server. For user-authored prompts (issue #153) the body lives client-side,
// so we also include the first few lines as a preview in the export.

import { USER_PROMPT_PREFIX } from '../hooks/chat/useSelections'

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

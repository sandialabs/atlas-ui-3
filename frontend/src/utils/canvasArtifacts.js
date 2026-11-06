// Canvas documents saved with a conversation (issue #1157).
//
// The canvas tool is not a transcript row. Its text is stored as a
// display-only canvas_artifact so a reload can put the same document back
// in the panel, and so a repository-less session can still hand that text
// to the server. Other display-only rows stay out of the restore payload.

import { DISPLAY_ONLY_MESSAGE_TYPES } from './chatExport'

export const CANVAS_ARTIFACT_TYPE = 'canvas_artifact'

export function isCanvasArtifactMessage(message) {
  if (!message || typeof message !== 'object') return false
  const type = message.message_type || message.type
  return type === CANVAS_ARTIFACT_TYPE
}

// Exact text the panel should show. A string is kept character for character.
export function canvasArtifactBody(message) {
  if (!isCanvasArtifactMessage(message)) return ''
  const raw = message.canvas_content ?? message.metadata?.canvas_content
  if (typeof raw === 'string') return raw
  if (raw == null) return ''
  try {
    return JSON.stringify(raw)
  } catch {
    return String(raw)
  }
}

// The document on screen is the newest canvas write that actually has text.
export function latestCanvasArtifactContent(messages) {
  if (!Array.isArray(messages)) return ''
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const body = canvasArtifactBody(messages[i])
    if (body) return body
  }
  return ''
}

// Restore payload: keep canvas rows (with the text), drop every other
// display-only row, and send ordinary turns as role plus content.
export function messagesForRestore(messages) {
  if (!Array.isArray(messages)) return []
  const out = []
  for (const msg of messages) {
    if (!msg || typeof msg !== 'object') continue
    if (isCanvasArtifactMessage(msg)) {
      const metadata = msg.metadata && typeof msg.metadata === 'object' ? { ...msg.metadata } : {}
      if (!metadata.message_type) metadata.message_type = CANVAS_ARTIFACT_TYPE
      if (metadata.canvas_content == null && typeof msg.canvas_content === 'string') {
        metadata.canvas_content = msg.canvas_content
      }
      out.push({
        role: msg.role || 'system',
        content: msg.content || 'Canvas artifact',
        message_type: CANVAS_ARTIFACT_TYPE,
        timestamp: msg.timestamp,
        metadata,
      })
      continue
    }
    const type = msg.message_type || msg.type || 'chat'
    if (DISPLAY_ONLY_MESSAGE_TYPES.includes(type)) continue
    out.push({ role: msg.role, content: msg.content || '' })
  }
  return out
}

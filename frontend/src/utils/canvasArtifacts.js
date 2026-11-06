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
  // A failed write never reached the panel as the document.
  const status = message.status ?? message.metadata?.status
  if (status === 'failed') return ''
  const raw = message.canvas_content ?? message.metadata?.canvas_content
  if (typeof raw === 'string') return raw
  if (raw == null) return ''
  try {
    return JSON.stringify(raw)
  } catch {
    return String(raw)
  }
}

function canvasSeq(message) {
  const raw = message.canvas_seq ?? message.metadata?.canvas_seq
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null
}

// The document on screen is the newest canvas write that actually has text.
// A live local save stamps canvas_seq in display order, which can differ from
// array order. Stored server rows have no sequence; the last one is newest.
export function latestCanvasArtifactContent(messages) {
  if (!Array.isArray(messages)) return ''
  let best = ''
  let bestSeq = null
  for (let i = 0; i < messages.length; i += 1) {
    const body = canvasArtifactBody(messages[i])
    if (!body) continue
    const seq = canvasSeq(messages[i])
    if (seq == null) {
      if (bestSeq == null) best = body
      continue
    }
    if (bestSeq == null || seq >= bestSeq) {
      bestSeq = seq
      best = body
    }
  }
  return best
}

// Restore payload: keep canvas rows (with the text), drop every other
// display-only row, and send ordinary turns as role plus content.
export function messagesForRestore(messages) {
  if (!Array.isArray(messages)) return []
  const out = []
  for (const msg of messages) {
    if (!msg || typeof msg !== 'object') continue
    if (isCanvasArtifactMessage(msg)) {
      if (!canvasArtifactBody(msg)) continue
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

// Canvas documents this tab has already put on screen, per conversation.
// The panel text alone cannot answer "is the stored document news?": a file
// or tool view clears it, and a reconnect or joined poll would then put an
// older document back over the newer view. A stored document is applied only
// when this tab has not shown it yet (one saved while the socket was down,
// or one a joined tab never received as a frame).
const MAX_SEEN_CANVAS = 20

// `texts` are transient panel values, capped. `applied` are stored documents
// this tab put on screen from a restore; they are few (one per open,
// reconnect or joined refill) and are never evicted, so a long run of panel
// previews cannot make an already-applied document look new again.
export function createSeenCanvas(id = null, text = '') {
  const record = { id: id ?? null, texts: [], applied: [] }
  if (text) record.applied.push(text)
  return record
}

// A new chat gets its id only after the first save, so texts shown while the
// id was still null belong to the conversation that id names.
function adoptConversation(record, id) {
  if (record.id === (id ?? null)) return true
  if (record.id == null && id != null) {
    record.id = id
    return true
  }
  return false
}

export function canvasSeen(record, id, text) {
  if (!record || !text) return false
  if (record.id !== (id ?? null) && !(record.id == null && id != null)) return false
  return record.applied.includes(text) || record.texts.includes(text)
}

export function noteCanvasSeen(record, id, text, { applied = false } = {}) {
  if (!record || !text) return
  if (!adoptConversation(record, id)) {
    record.id = id ?? null
    record.texts = []
    record.applied = []
  }
  if (applied) {
    if (!record.applied.includes(text)) record.applied.push(text)
    return
  }
  const at = record.texts.indexOf(text)
  if (at !== -1) record.texts.splice(at, 1)
  record.texts.push(text)
  if (record.texts.length > MAX_SEEN_CANVAS) record.texts.shift()
}

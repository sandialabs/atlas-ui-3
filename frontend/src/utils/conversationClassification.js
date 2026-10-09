// Conversation-level data classification (issue #1042).
//
// Mirror of atlas/domain/conversation_classification.py for the UI: a saved
// conversation may only be continued under the classification it was created
// under. The server is the authority -- it refuses the restore, the REST fetch
// and every chat turn on its own -- so this only lets the sidebar mark rows and
// explain a refusal before a round-trip.

export const CLASSIFICATION_ERROR_TYPE = 'conversation_classification'

// The state of a conversation's record, from a listing row or a full record.
// Server rows carry `data_classification_state`; local (IndexedDB) records carry
// the record in `metadata.data_classification` like the server metadata does.
// A row that carries neither (one the sidebar synthesizes for an unsaved or
// still-running conversation) is 'unknown': the UI cannot judge it and leaves
// the decision to the server.
export function classificationOf(conv) {
  if (!conv) return { state: 'unknown', level: null }
  if (typeof conv.data_classification_state === 'string') {
    return { state: conv.data_classification_state, level: conv.data_classification ?? null }
  }
  if (!('metadata' in conv)) return { state: 'unknown', level: null }
  const meta = conv.metadata
  if (!meta || typeof meta !== 'object' || !('data_classification' in meta)) {
    return { state: 'legacy', level: null }
  }
  const value = meta.data_classification
  if (value === null) return { state: 'unclassified', level: null }
  if (typeof value === 'string' && value.trim()) return { state: 'classified', level: value.trim() }
  return { state: 'invalid', level: null }
}

// Canonical level name: aliases resolve through the deployment's definitions
// (`levels` from /api/compliance-levels), as the server's comparison does.
function canonicalLevel(name, levels) {
  if (!name) return null
  const match = (levels || []).find(l => l?.name === name || (l?.aliases || []).includes(name))
  return match ? match.name : name
}

// Why the conversation cannot be opened under the active level, or null.
export function classificationRefusal(conv, { complianceEnabled, activeLevel, levels = [] }) {
  const { state, level } = classificationOf(conv)
  const active = complianceEnabled ? canonicalLevel(activeLevel || null, levels) : null
  const activeLabel = active || 'no compliance level'
  switch (state) {
    case 'unknown':
      return null
    case 'legacy':
      return complianceEnabled
        ? 'This conversation was saved before compliance levels were recorded for conversations, so it cannot be continued while compliance levels are enforced. It stays in your history and in conversation exports; start a new conversation to keep working.'
        : null
    case 'unclassified':
      return active
        ? `This conversation was saved with no compliance level and cannot be continued under ${activeLabel}. Start a new conversation at ${activeLabel}.`
        : null
    case 'classified':
      if (!complianceEnabled) {
        return `This conversation was saved under ${level}, and compliance levels are not enabled, so it cannot be continued.`
      }
      return canonicalLevel(level, levels) === active
        ? null
        : `This conversation was saved under ${level} and cannot be continued under ${activeLabel}. Switch the compliance level to ${level} to continue it, or start a new conversation.`
    default:
      return "This conversation's recorded compliance level could not be read, so it cannot be continued. Start a new conversation."
  }
}

// Short label for a sidebar row; null when there is nothing worth showing.
export function classificationLabel(conv) {
  const { state, level } = classificationOf(conv)
  if (state === 'classified') return level
  if (state === 'legacy') return 'Unrecorded level'
  if (state === 'invalid') return 'Unreadable level'
  return null
}

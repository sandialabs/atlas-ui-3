// Conversation-level data classification (issue #1042).
//
// Mirror of atlas/domain/conversation_classification.py for the UI: a saved
// conversation may only be continued under the classification it was created
// under. The server is the authority -- it refuses the restore, the REST fetch
// and every chat turn on its own -- so this only lets the sidebar mark rows and
// explain a refusal before a round-trip. Its messages name the header control
// as the UI labels it ("security level"); the server's say "compliance level".
//
// Recorded levels render as their configured friendly label where the
// deployment defines one (issue #1117): display_name ?? name, aliases
// resolved. Every comparison below stays on canonical names -- only the
// rendered strings change -- so a label can never widen what the server
// would refuse.

import { complianceDisplayName } from './complianceDisplay'
import { findComplianceLevel } from './complianceAccess'

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
// The lookup itself is the shared `findComplianceLevel`.
function canonicalLevel(name, levels) {
  if (!name) return null
  return findComplianceLevel(levels, name)?.name ?? name
}

// Friendly label for a level name or alias: display_name ?? canonical name,
// falling back to the raw name the deployment no longer defines. Rendering
// only; matching keeps using canonicalLevel above.
function displayLevel(name, levels) {
  if (!name) return null
  const canonical = canonicalLevel(name, levels) || name
  return complianceDisplayName(levels, canonical) || canonical
}

// Why the conversation cannot be opened under the active level, or null.
export function classificationRefusal(conv, { complianceEnabled, activeLevel, levels = [] }) {
  const { state, level } = classificationOf(conv)
  const active = complianceEnabled ? canonicalLevel(activeLevel || null, levels) : null
  const activeLabel = (active && displayLevel(active, levels)) || 'no security level'
  switch (state) {
    case 'unknown':
      return null
    case 'legacy':
      return complianceEnabled
        ? 'This conversation was saved before security levels were recorded for conversations, so it cannot be continued while security levels are enforced. It stays in your history and in conversation exports; start a new conversation to keep working.'
        : null
    case 'unclassified':
      return active
        ? `This conversation was saved with no security level and cannot be continued under ${activeLabel}. Start a new conversation at ${activeLabel}.`
        : null
    case 'classified':
      if (!complianceEnabled) {
        return `This conversation was saved under ${displayLevel(level, levels)}, and security levels are not enabled, so it cannot be continued.`
      }
      return canonicalLevel(level, levels) === active
        ? null
        : `This conversation was saved under ${displayLevel(level, levels)} and cannot be continued under ${activeLabel}. Switch the security level to ${displayLevel(level, levels)} to continue it, or start a new conversation.`
    default:
      return "This conversation's recorded security level could not be read, so it cannot be continued. Start a new conversation."
  }
}

// Short label for a sidebar row; null when there is nothing worth showing.
// The recorded canonical level renders as its friendly label (issue #1117);
// a level the deployment no longer defines keeps its recorded name.
export function classificationLabel(conv, levels = []) {
  const { state, level } = classificationOf(conv)
  if (state === 'classified') return displayLevel(level, levels)
  if (state === 'legacy') return 'Unrecorded level'
  if (state === 'invalid') return 'Unreadable level'
  return null
}

// Whether the header's classification selector must be locked for the open
// conversation (issue #1044). Once a conversation has begun -- a new chat after
// the server accepts its first turn, or a saved one opened from history -- its
// classification is fixed and the selector may not change it; only starting a
// new chat unlocks selection. `activeConversationClassification` is the open
// conversation's record ({ state, level }, from ChatContext) or null with no
// conversation, and `conversationAccepted` is whether the server has accepted a
// turn (a first turn the server refused leaves the selector open to correct the
// level).
//
// The server stays the authority (issue #1043, conversation_classification.py):
// this only stops the UI from offering a transition the server would refuse.
// Returns the level to display when locked, a placeholder label for a record
// with no usable level, and the guidance shown to the user.
export function classificationLock({
  complianceEnabled,
  activeConversationClassification,
  conversationAccepted = true,
  levels = [],
}) {
  const state = activeConversationClassification?.state
  if (!complianceEnabled || !state || !conversationAccepted) {
    return { locked: false, level: null, placeholder: null, guidance: null }
  }
  if (state === 'classified' && activeConversationClassification.level) {
    // A record may hold an alias; resolve it to the canonical value the
    // selector's options carry, so the locked option matches (and renders the
    // friendly label, issue #1117). An undefined level keeps its recorded
    // name, which the header shows as a synthetic option.
    const recorded = activeConversationClassification.level
    const level = canonicalLevel(recorded, levels) || recorded
    return {
      locked: true,
      level,
      placeholder: level,
      guidance: 'Start a new chat to change security level.',
    }
  }
  const placeholder = state === 'legacy' ? 'Unrecorded level'
    : state === 'invalid' ? 'Unreadable level'
    : state === 'unclassified' ? 'Unclassified'
    : 'Locked'
  return {
    locked: true,
    level: null,
    placeholder,
    guidance: 'Start a new chat to change security level.',
  }
}

/**
 * Shared user-message-ordinal logic for the rewind / edit-a-prompt flow (#142).
 *
 * Rewind addresses a prompt by its 0-based position among *user* messages that
 * also exist in the backend ConversationHistory. The frontend renders extra rows
 * the backend never stores -- assistant/tool/system rows, and (in agent mode)
 * the answers to agent follow-up questions, which are sent as `agent_user_input`
 * and consumed inside the transient agent loop but never appended to
 * `session.history` (see ChatOrchestrator / AgentModeRunner). Counting only
 * "rewindable" user rows keeps the frontend ordinal in lockstep with
 * `ConversationHistory.truncate_at_user_index` on the backend.
 *
 * This lives in one place and is used by both ChatArea (assigning ordinals and
 * wiring the edit affordance) and ChatContext (truncating the local transcript)
 * so the two can never drift apart -- a mismatch would silently drop the wrong
 * prompt, which no single-layer test would catch.
 */

// A transcript row counts toward the rewind ordinal only if it is a user prompt
// the backend actually persisted. Agent-loop answers (_agentInput) render as
// user rows but have no ConversationHistory counterpart, so they are skipped.
export function isRewindableUserMessage(message) {
  return !!message && message.role === 'user' && !message._agentInput
}

// Index into `messages` of the Nth rewindable user message (the slice point for
// a rewind to that ordinal). Returns -1 when `userIndex` does not address an
// existing rewindable user message, so callers can no-op safely.
export function userMessageSliceIndex(messages, userIndex) {
  if (userIndex == null || userIndex < 0) return -1
  let seen = 0
  for (let i = 0; i < messages.length; i++) {
    if (isRewindableUserMessage(messages[i])) {
      if (seen === userIndex) return i
      seen += 1
    }
  }
  return -1
}

// Pair each message with its rewind ordinal (or null for non-rewindable rows),
// preserving transcript order. The render path (ChatArea) consumes this so the
// ordinal it assigns is produced by the same implementation the truncation path
// uses -- there is no second hand-coded counting loop to drift out of sync.
export function withUserOrdinals(messages) {
  let seen = -1
  return messages.map(message => ({
    message,
    userIndex: isRewindableUserMessage(message) ? ++seen : null,
  }))
}

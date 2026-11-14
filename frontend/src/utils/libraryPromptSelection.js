import {
  isLibraryDraftKey,
  isLibraryPromptKey,
  parseLibraryPromptKey,
  LIBRARY_DRAFT_PREFIX,
} from '../hooks/chat/useSelections'

/**
 * What the active prompt key contributes to a chat frame for the team prompt
 * library (issue #1169).
 *
 * Returns one of:
 *   { kind: 'none' }                         -- not a library selection
 *   { kind: 'library', ref }                 -- send `library_prompt: ref`
 *   { kind: 'draft', content }               -- an editor testing a draft inline
 *   { kind: 'blocked', message }             -- refuse the send, tell the user
 *
 * A selection the loaded library no longer lists (unshared, archived, access
 * revoked) is blocked rather than dropped: dropping it would silently run the
 * default prompt, which is exactly the stale-selection failure the issue
 * forbids. When the list has not loaded, failed to, or is only a first page
 * (`complete` false), the reference goes out and the server -- which
 * re-checks access on every turn -- decides.
 */
export function resolveLibrarySelection(activeKey, library) {
  if (isLibraryDraftKey(activeKey)) {
    const promptId = activeKey.slice(LIBRARY_DRAFT_PREFIX.length)
    const draft = library?.testDraft
    if (draft && draft.promptId === promptId && draft.content && draft.content.trim()) {
      return { kind: 'draft', content: draft.content }
    }
    return {
      kind: 'blocked',
      message: 'The draft you were testing is no longer open. Choose a prompt before sending.',
    }
  }
  if (!isLibraryPromptKey(activeKey)) return { kind: 'none' }
  const parsed = parseLibraryPromptKey(activeKey)
  if (!parsed) {
    return { kind: 'blocked', message: 'The selected team prompt is not valid. Choose another prompt.' }
  }
  if (library?.loaded && !library.error && library.complete !== false) {
    const entry = (library.entries || []).find(e => e.id === parsed.promptId)
    if (!entry || !entry.latest_version) {
      return {
        kind: 'blocked',
        message: 'The selected team prompt is no longer available to you. Choose another prompt to continue.',
      }
    }
  }
  return { kind: 'library', ref: { prompt_id: parsed.promptId, version_id: parsed.versionId } }
}

/** Label for the prompt picker button. */
export function libraryPromptLabel(activeKey, entries = []) {
  if (isLibraryDraftKey(activeKey)) return 'Testing draft'
  const parsed = parseLibraryPromptKey(activeKey)
  if (!parsed) return null
  const entry = entries.find(e => e.id === parsed.promptId)
  if (!entry) return 'Unavailable team prompt'
  if (!parsed.versionId) return entry.title
  const latest = entry.latest_version
  return latest && latest.id === parsed.versionId
    ? `${entry.title} (v${latest.version_number})`
    : `${entry.title} (earlier version)`
}

/** Display name for a pinned conversation version, e.g. "v3". */
export function versionLabel(version) {
  return version && version.version_number ? `v${version.version_number}` : 'unpublished'
}

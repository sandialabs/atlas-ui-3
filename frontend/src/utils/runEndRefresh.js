// What to do with the record fetched when a joined conversation's run ends
// (issue #959).
//
// Extracted from Sidebar's effect so the fallback has a test against shipped
// code. The whole safety net is one `if`: inverting it, or dropping the
// `loadSavedConversation` call, silently removes the full reload that a
// diverged transcript depends on, and a render-level test of the surrounding
// effect never exercised it.

// Apply the refresh, falling back to the full reload when it refuses.
// Returns what happened, for the caller and for tests:
//   'skipped'  - nothing usable came back from the fetch
//   'appended' - the refresh reconciled and appended the missing tail
//   'reloaded' - the refresh refused; the full reload took the store's copy
export function applyRunEndRefresh({ fullConv, refreshJoinedConversation, loadSavedConversation }) {
  if (!fullConv || fullConv.error) return 'skipped'
  if (refreshJoinedConversation(fullConv)) return 'appended'
  loadSavedConversation(fullConv)
  return 'reloaded'
}

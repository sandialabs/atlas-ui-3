// The alignment rule the joined-run refresh applies (issue #959).
//
// Extracted from ChatContext so there is exactly one statement of it that
// both test suites can exercise. It had been restated twice -- once in the
// vitest suite and once as a Python mirror in the PR-validation scenario --
// and both copies drifted from the real rule, leaving the end-to-end check
// certifying behaviour the client does not have. The shared case table in
// `test/fixtures/transcript-alignment-cases.json` now drives this function
// and the Python mirror from the same data.

// The only row types the backend ever writes to a tracked run's transcript.
// An allowlist, deliberately: as a denylist of view-only types, every type
// minted elsewhere that nobody remembered to add silently degraded the
// refresh into the full reload it exists to avoid.
export const STORED_ROW_TYPES = new Set(['chat', 'tool_call', 'agent_intermediate'])

// Agent narration is one row wearing two type names: the loop persists the
// pre-tool narration as 'agent_intermediate' while the streamed row in the
// view is a plain assistant row -- same text, same place, so the pair matches.
export const PROSE_ROW_TYPES = new Set(['chat', 'agent_intermediate'])

// Whether a stored row and a live view row describe the same transcript row.
// Tool rows match on tool_call_id, which survives the save/reload round-trip,
// because the persisted shape (role 'tool', elided arguments) deliberately
// differs from the live one (role 'system', raw arguments). Everything else
// matches on role and content.
export function sameTranscriptRow(a, b) {
  const typeA = a.type || 'chat'
  const typeB = b.type || 'chat'
  if (typeA !== typeB && !(PROSE_ROW_TYPES.has(typeA) && PROSE_ROW_TYPES.has(typeB))) return false
  if (typeA === 'tool_call' && a.tool_call_id && b.tool_call_id) {
    return a.tool_call_id === b.tool_call_id
  }
  return a.role === b.role && (a.content || '') === (b.content || '')
}

// Rows that exist only in the live view and have no stored counterpart.
// A row with no `type` at all and role 'system' is view chrome too: the
// socket's `error` frame adds one without a type, which would otherwise fall
// through as a plain 'chat' row. Stored rows always carry a type -- they are
// built with `msg.message_type || 'chat'` -- so this cannot swallow a row the
// store actually has.
export function isLiveOnlyRow(m) {
  return !STORED_ROW_TYPES.has(m.type || 'chat') || m._agentInput === true || (!m.type && m.role === 'system')
}

// Walk the view against the stored transcript. Returns the index into
// `stored` where the view runs out -- the point the tail is appended from --
// or null when the two have diverged and the refresh must refuse.
//
// `view` is expected to have been filtered already (live-only rows and any
// open streaming bubble removed); `alignTranscript` does not filter, so the
// caller and the case table agree on exactly what is compared.
export function alignTranscript(view, stored) {
  let viewIdx = 0
  let storedIdx = 0
  while (storedIdx < stored.length && viewIdx < view.length) {
    if (!sameTranscriptRow(view[viewIdx], stored[storedIdx])) return null
    viewIdx += 1
    storedIdx += 1
  }
  // The store ran out first while the view still holds persistable rows: the
  // conversation was rewound or rewritten elsewhere and a shorter store
  // cannot be reconciled against it. Refuse; the caller falls back to the
  // full reload, as it did before this refresh existed.
  if (viewIdx < view.length) return null
  return storedIdx
}

/**
 * Paging helpers for the conversation history list (issue #1085).
 *
 * The sidebar shows the newest page of the history and lets the user walk
 * back through the older ones. These helpers keep that walk honest: a page
 * is appended rather than replacing what is already on screen, and a row the
 * client already holds is never shown twice.
 */

/** How many conversations one page of the history holds. */
export const CONVERSATION_PAGE_SIZE = 50

/**
 * Append `incoming` to `existing`, skipping rows the client already holds.
 *
 * A later page can repeat a row when the server's order shifts between two
 * requests (a conversation saved elsewhere moves to the top of the list), and
 * the same conversation must not appear twice.
 */
export function appendUnique(existing, incoming) {
  if (!incoming?.length) return existing
  const seen = new Set(existing.map(c => c.id))
  const fresh = incoming.filter(c => !seen.has(c.id))
  return fresh.length ? [...existing, ...fresh] : existing
}

/**
 * Put `fresh` (the newest page, re-read from the server) in front of the
 * rows the client already holds, without duplicating any of them.
 *
 * A plain replace would throw the older rows away and send the user back to
 * the top of the history every time the list is refreshed.
 *
 * `total` is how many conversations the listing holds now. Rows past it are
 * ones the server no longer has -- a conversation deleted in another tab, or
 * the whole list deleted there -- so they are dropped instead of being kept
 * for ever.
 */
export function mergeFreshFirst(fresh, existing, total) {
  const head = Array.isArray(fresh) ? fresh : []
  const tail = Array.isArray(existing) ? existing : []
  if (!head.length && !tail.length) return []
  const seen = new Set(head.map(c => c.id))
  const merged = [...head, ...tail.filter(c => !seen.has(c.id))]
  if (typeof total === 'number' && merged.length > total) {
    return merged.slice(0, total)
  }
  return merged
}

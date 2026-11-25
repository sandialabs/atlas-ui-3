/**
 * Pending MCP elicitation requests, oldest first.
 *
 * A server may ask several questions in one round trip (MCP 2026-07-28 lets
 * an InputRequiredResult carry more than one request), and tools running in
 * parallel may each ask one. Only one dialog is shown at a time, so the rest
 * wait here instead of replacing the one on screen: a replaced request would
 * never be answered and its tool would sit until the elicitation timed out.
 */

/** Add a request, ignoring one that is already queued (a replayed event). */
export function enqueueElicitation(queue, request) {
  const list = Array.isArray(queue) ? queue : []
  if (!request || !request.elicitation_id) return list
  if (list.some(item => item.elicitation_id === request.elicitation_id)) return list
  return [...list, request]
}

/** Remove a request that was answered, expired, or is unknown to the server. */
export function settleElicitation(queue, elicitationId) {
  const list = Array.isArray(queue) ? queue : []
  if (!elicitationId) return list
  const next = list.filter(item => item.elicitation_id !== elicitationId)
  return next.length === list.length ? list : next
}

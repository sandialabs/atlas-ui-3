// Client request ids for chat frames (issue #1128).
//
// Every chat frame carries a fresh id. When the frame lands as a steer on a run
// another server executes and that server does not answer in time, the client
// repeats the frame once with the SAME id: the run's owner applies a request
// id at most once, so the repeat cannot queue the message twice.

// How long a frame stays repeatable. A steer the owner did not answer is
// reported within the command timeout (5 s); the margin covers the round trip.
// Past it the first attempt's fate is unknown, so repeating would be a guess.
export const STEER_RETRY_WINDOW_MS = 15000

// Errors that mean "the owner did not answer", not "the owner refused".
const RETRYABLE_ERROR_TYPES = new Set(['run_control_unavailable'])

export function newRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

// Keeps recent frames by request id until their deadline. A Map iterates in
// insertion order, so the oldest goes first.
export function rememberSteerFrame(frames, requestId, frame, now = Date.now()) {
  // Expire by deadline: a Map iterates oldest first, so stop at the first live one.
  for (const [id, entry] of frames) {
    if (entry.expiresAt > now) break
    frames.delete(id)
  }
  frames.set(requestId, { frame, expiresAt: now + STEER_RETRY_WINDOW_MS })
}

// The frame to repeat for an error frame, or null. Taking it removes it, so a
// second failure of the repeat reports the error instead of looping.
export function takeSteerRetry(frames, errorFrame, now = Date.now()) {
  if (!errorFrame || errorFrame.steering !== true || !errorFrame.request_id) return null
  if (!RETRYABLE_ERROR_TYPES.has(errorFrame.error_type)) return null
  const entry = frames.get(errorFrame.request_id)
  if (!entry) return null
  frames.delete(errorFrame.request_id)
  if (entry.expiresAt <= now) return null
  const frame = entry.frame
  // steer_only: if the run finished meanwhile (the first attempt may have been
  // applied, only its reply was lost), the server answers with a notice instead
  // of starting a new turn that would repeat the message.
  // run_id: the run the first attempt was routed to. The server applies the
  // retry there or reports it finished; it is never steered into a successor
  // run of the conversation.
  const retry = { ...frame, steer_only: true }
  if (errorFrame.run_id) retry.run_id = errorFrame.run_id
  return retry
}

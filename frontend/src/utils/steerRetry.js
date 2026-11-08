// Client request ids for chat frames (issue #1128).
//
// Every chat frame carries a fresh id. When the frame lands as a steer on a run
// another server executes and that server does not answer in time, the client
// repeats the frame once with the SAME id: the run's owner applies a request
// id at most once, so the repeat cannot queue the message twice.

const MAX_REMEMBERED_FRAMES = 8

// Errors that mean "the owner did not answer", not "the owner refused".
const RETRYABLE_ERROR_TYPES = new Set(['run_control_unavailable', 'command_timeout'])

export function newRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

// Keeps the last few frames by request id. A Map iterates in insertion order,
// so the oldest goes first.
export function rememberSteerFrame(frames, requestId, frame) {
  frames.set(requestId, frame)
  while (frames.size > MAX_REMEMBERED_FRAMES) {
    frames.delete(frames.keys().next().value)
  }
}

// The frame to repeat for an error frame, or null. Taking it removes it, so a
// second failure of the repeat reports the error instead of looping.
export function takeSteerRetry(frames, errorFrame) {
  if (!errorFrame || errorFrame.steering !== true || !errorFrame.request_id) return null
  if (!RETRYABLE_ERROR_TYPES.has(errorFrame.error_type)) return null
  const frame = frames.get(errorFrame.request_id)
  if (!frame) return null
  frames.delete(errorFrame.request_id)
  // steer_only: if the run finished meanwhile (the first attempt may have been
  // applied, only its reply was lost), the server answers with a notice instead
  // of starting a new turn that would repeat the message.
  return { ...frame, steer_only: true }
}

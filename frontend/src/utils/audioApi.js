// Calls to the Atlas audio endpoints (issue #1251). The browser only ever
// talks to Atlas; Atlas relays to the audio server the operator configured.

const FALLBACKS = {
  401: 'Your session has expired. Reload the page and sign in again.',
  404: 'This audio feature is not available. It may have been turned off by an administrator.',
  413: 'That is over the size limit for this audio feature.',
  429: 'Too many requests. Wait a moment and try again.',
  504: 'The speech service did not answer in time. Try again.',
}

export class AudioRequestError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'AudioRequestError'
    this.status = status
  }
}

const failure = async (response) => {
  let detail = ''
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') detail = body.detail
  } catch {
    // Not JSON (a proxy error page, say): fall back to the status.
  }
  // A 404 body is the generic "Feature not enabled"; the fallback says more.
  const message = (response.status !== 404 && detail) ||
    FALLBACKS[response.status] ||
    'The speech service request failed. Try again.'
  return new AudioRequestError(message, response.status)
}

const send = async (url, init) => {
  let response
  try {
    response = await fetch(url, { credentials: 'same-origin', ...init })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new AudioRequestError('Could not reach the server. Check your connection and try again.', 0)
  }
  if (!response.ok) throw await failure(response)
  return response
}

const EXTENSIONS = { 'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'audio/mpeg': 'mp3', 'audio/wav': 'wav' }

/** Transcribe one recording; resolves to the text. */
export const transcribeAudio = async (blob, { durationSeconds, complianceLevel, signal } = {}) => {
  const baseType = (blob.type || '').split(';')[0]
  const form = new FormData()
  form.append('file', blob, `recording.${EXTENSIONS[baseType] || 'webm'}`)
  if (Number.isFinite(durationSeconds)) form.append('duration_seconds', durationSeconds.toFixed(1))
  if (complianceLevel) form.append('compliance_level', complianceLevel)
  const response = await send('/api/audio/transcriptions', { method: 'POST', body: form, signal })
  const body = await response.json()
  return typeof body?.text === 'string' ? body.text : ''
}

/** Synthesize speech for `text`; resolves to an audio Blob. */
export const synthesizeSpeech = async (text, { complianceLevel, signal } = {}) => {
  const payload = { input: text }
  if (complianceLevel) payload.compliance_level = complianceLevel
  const response = await send('/api/audio/speech', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  return response.blob()
}

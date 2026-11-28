import { useCallback, useEffect, useRef, useState } from 'react'
import { transcribeAudio } from '../utils/audioApi'
import { RECORDING_MIME_TYPES, recordableMimeTypes } from '../utils/audioControls'

// Re-exported for existing imports; the list lives with the visibility rule.
export { RECORDING_MIME_TYPES }

/** The first recorder type this browser can produce that the service takes. */
export const pickRecordingMimeType = (acceptedMediaTypes) => {
  const Recorder = typeof window !== 'undefined' ? window.MediaRecorder : undefined
  if (!Recorder || typeof Recorder.isTypeSupported !== 'function') return null
  return recordableMimeTypes(acceptedMediaTypes).find(type => Recorder.isTypeSupported(type)) || null
}

/** What to tell the user when the microphone could not be opened. */
export const describeMicrophoneError = (error) => {
  switch (error?.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'Microphone access is blocked. Allow the microphone for this site in your browser\'s site settings, then try again.'
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'No microphone was found. Connect one and try again.'
    case 'NotReadableError':
    case 'AbortError':
      return 'The microphone could not be started. Another application may be using it.'
    default:
      return 'The microphone could not be started.'
  }
}

/**
 * Record from the microphone and transcribe the clip (issue #1251).
 *
 * States: `idle` -> `requesting` (waiting for the permission prompt) ->
 * `recording` -> `transcribing` -> `idle`. `stop()` ends the recording and
 * sends it; `cancel()` abandons whichever step is in progress and sends or
 * inserts nothing. The transcript is handed to `onTranscript`; it is never
 * submitted from here.
 *
 * The audio exists only in this tab's memory until it has been sent, and the
 * microphone is released as soon as recording ends, whatever the reason.
 */
export function useDictation({ maxDurationSeconds = 300, maxAudioBytes, acceptedMediaTypes, complianceLevel, onTranscript, onError, onNotice }) {
  const [status, setStatus] = useState('idle')
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  // One attempt's mutable state. A new object per attempt, so a late callback
  // from an abandoned attempt can see that it is no longer the current one.
  const attemptRef = useRef(null)
  const callbacksRef = useRef({})
  callbacksRef.current = { onTranscript, onError, onNotice, complianceLevel, maxAudioBytes, acceptedMediaTypes }

  const release = useCallback((attempt) => {
    if (!attempt) return
    clearTimeout(attempt.limitTimer)
    clearInterval(attempt.ticker)
    attempt.stream?.getTracks().forEach(track => track.stop())
    attempt.stream = null
  }, [])

  const finish = useCallback((attempt, { error, notice } = {}) => {
    if (attemptRef.current !== attempt) return
    release(attempt)
    attemptRef.current = null
    setStatus('idle')
    setElapsedSeconds(0)
    if (error) callbacksRef.current.onError?.(error)
    if (notice) callbacksRef.current.onNotice?.(notice)
  }, [release])

  const transcribe = useCallback(async (attempt) => {
    const { maxAudioBytes: limit, complianceLevel: level } = callbacksRef.current
    const blob = new Blob(attempt.chunks, { type: attempt.recorder.mimeType || attempt.mimeType })
    attempt.chunks = []
    if (blob.size === 0) {
      finish(attempt, { error: 'Nothing was recorded. Check that the microphone is not muted and try again.' })
      return
    }
    if (Number.isFinite(limit) && blob.size > limit) {
      finish(attempt, { error: 'The recording is too large to transcribe. Record a shorter clip.' })
      return
    }
    setStatus('transcribing')
    attempt.abort = new AbortController()
    try {
      const text = await transcribeAudio(blob, {
        durationSeconds: attempt.durationSeconds,
        complianceLevel: level,
        signal: attempt.abort.signal,
      })
      if (attemptRef.current !== attempt) return
      const trimmed = text.trim()
      if (trimmed) callbacksRef.current.onTranscript?.(trimmed)
      finish(attempt, trimmed ? {} : { notice: 'No speech was detected in the recording.' })
    } catch (error) {
      // An abort is a cancel: back to idle, quietly. (After cancel() this
      // attempt is no longer current and finish() does nothing.)
      finish(attempt, error?.name === 'AbortError' ? {} : { error: error?.message || 'Transcription failed. Try again.' })
    }
  }, [finish])

  const stop = useCallback(() => {
    const attempt = attemptRef.current
    if (!attempt?.recorder || attempt.recorder.state === 'inactive') return
    attempt.recorder.stop()
  }, [])

  const cancel = useCallback(() => {
    const attempt = attemptRef.current
    if (!attempt) return
    attempt.cancelled = true
    attempt.abort?.abort()
    try {
      if (attempt.recorder && attempt.recorder.state !== 'inactive') attempt.recorder.stop()
    } catch {
      // Already stopped.
    }
    finish(attempt)
  }, [finish])

  const start = useCallback(async () => {
    if (attemptRef.current) return
    const report = message => callbacksRef.current.onError?.(message)
    if (!navigator.mediaDevices?.getUserMedia || typeof window.MediaRecorder === 'undefined') {
      report(window.isSecureContext === false
        ? 'Dictation needs a secure (HTTPS) connection. Open this site over HTTPS to use the microphone.'
        : 'This browser does not support audio recording.')
      return
    }
    const mimeType = pickRecordingMimeType(callbacksRef.current.acceptedMediaTypes)
    if (!mimeType) {
      report('This browser cannot record audio in a format the transcription service accepts. Try a current version of Chrome, Edge, Firefox or Safari.')
      return
    }

    const attempt = { chunks: [], mimeType, cancelled: false }
    attemptRef.current = attempt
    setStatus('requesting')
    try {
      attempt.stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (error) {
      finish(attempt, { error: describeMicrophoneError(error) })
      return
    }
    if (attemptRef.current !== attempt) {
      // Cancelled (or unmounted) while the permission prompt was open.
      release(attempt)
      return
    }

    let recorder
    try {
      recorder = new window.MediaRecorder(attempt.stream, { mimeType })
    } catch {
      finish(attempt, { error: 'This browser could not start recording in a supported format.' })
      return
    }
    attempt.recorder = recorder
    recorder.ondataavailable = (event) => {
      if (event.data?.size > 0) attempt.chunks.push(event.data)
    }
    recorder.onerror = () => finish(attempt, { error: 'Recording stopped unexpectedly. Try again.' })
    recorder.onstop = () => {
      attempt.durationSeconds = (Date.now() - attempt.startedAt) / 1000
      release(attempt)
      if (attempt.cancelled || attemptRef.current !== attempt) return
      transcribe(attempt)
    }
    attempt.startedAt = Date.now()
    recorder.start()
    setStatus('recording')
    setElapsedSeconds(0)
    // A one-second UI clock, not a polling loop.
    attempt.ticker = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - attempt.startedAt) / 1000))
    }, 1000)
    // No usable limit (a config response without one) means no auto-stop,
    // not a zero-length recording: setTimeout treats NaN as 0. The server's
    // size limit still applies.
    if (Number.isFinite(maxDurationSeconds) && maxDurationSeconds > 0) {
      attempt.limitTimer = setTimeout(() => {
        if (attemptRef.current !== attempt || recorder.state === 'inactive') return
        callbacksRef.current.onNotice?.(`Recording stopped at the ${maxDurationSeconds}-second limit.`)
        recorder.stop()
      }, maxDurationSeconds * 1000)
    }
  }, [finish, release, transcribe, maxDurationSeconds])

  // Leaving the page section must not leave the microphone open.
  useEffect(() => () => {
    const attempt = attemptRef.current
    if (!attempt) return
    attempt.cancelled = true
    attemptRef.current = null
    attempt.abort?.abort()
    try {
      if (attempt.recorder && attempt.recorder.state !== 'inactive') attempt.recorder.stop()
    } catch {
      // Already stopped.
    }
    release(attempt)
  }, [release])

  return { status, elapsedSeconds, start, stop, cancel }
}

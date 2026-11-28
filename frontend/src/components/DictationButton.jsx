import { Mic, Square, X, Loader } from 'lucide-react'
import { useDictation } from '../hooks/useDictation'
import { useToast } from './ui/toastContext'

const formatClock = (seconds) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

// Sized and coloured like the composer's other square buttons (attach, stop)
// so the row reads as one set of controls at phone and desktop widths.
const BUTTON = 'w-12 h-12 sm:w-auto sm:h-auto sm:px-4 sm:py-3 min-w-[48px] min-h-[48px] rounded-lg flex items-center justify-center gap-2 transition-colors flex-shrink-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400'

/**
 * The composer's microphone button (issue #1251): record, stop or cancel,
 * transcribe, and hand the text to `onTranscript`. The caller inserts it into
 * the draft; nothing here sends a message.
 */
const DictationButton = ({ onTranscript, maxDurationSeconds, maxAudioBytes, acceptedMediaTypes, complianceLevel }) => {
  const toast = useToast()
  const { status, elapsedSeconds, start, stop, cancel } = useDictation({
    maxDurationSeconds,
    maxAudioBytes,
    acceptedMediaTypes,
    complianceLevel,
    onTranscript,
    onError: message => toast.error(message),
    onNotice: message => toast.info(message),
  })

  const statusText = status === 'recording'
    ? 'Recording'
    : status === 'transcribing'
      ? 'Transcribing'
      : status === 'requesting'
        ? 'Waiting for microphone permission'
        : ''

  return (
    <>
      {/* Mounted empty and filled on change so a screen reader announces it. */}
      <span className="sr-only" role="status" aria-live="polite" data-testid="dictation-status">{statusText}</span>
      {status === 'idle' && (
        <button
          type="button"
          onClick={start}
          className={`${BUTTON} bg-gray-700 hover:bg-gray-600 text-gray-300`}
          title="Dictate"
          aria-label="Dictate with the microphone"
          data-testid="dictation-start"
        >
          <Mic className="w-5 h-5" aria-hidden="true" />
        </button>
      )}
      {status === 'requesting' && (
        <button
          type="button"
          onClick={cancel}
          className={`${BUTTON} bg-gray-700 hover:bg-gray-600 text-gray-300`}
          title="Waiting for microphone permission. Click to cancel."
          aria-label="Cancel dictation"
          data-testid="dictation-cancel"
        >
          <Loader className="w-5 h-5 animate-spin" aria-hidden="true" />
        </button>
      )}
      {status === 'recording' && (
        <>
          <button
            type="button"
            onClick={stop}
            className={`${BUTTON} bg-red-700 hover:bg-red-600 text-white`}
            title="Stop and transcribe"
            aria-label="Stop recording and transcribe"
            data-testid="dictation-stop"
          >
            <Square className="w-4 h-4" aria-hidden="true" />
            <span className="hidden sm:inline text-sm tabular-nums" aria-hidden="true">{formatClock(elapsedSeconds)}</span>
          </button>
          <button
            type="button"
            onClick={cancel}
            className={`${BUTTON} bg-gray-700 hover:bg-gray-600 text-gray-300`}
            title="Cancel recording"
            aria-label="Cancel recording"
            data-testid="dictation-cancel"
          >
            <X className="w-5 h-5" aria-hidden="true" />
          </button>
        </>
      )}
      {status === 'transcribing' && (
        <button
          type="button"
          onClick={cancel}
          className={`${BUTTON} bg-gray-700 hover:bg-gray-600 text-gray-300`}
          title="Transcribing. Click to cancel."
          aria-label="Cancel transcription"
          data-testid="dictation-cancel"
        >
          <Loader className="w-5 h-5 animate-spin" aria-hidden="true" />
        </button>
      )}
    </>
  )
}

export default DictationButton

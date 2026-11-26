import { useCallback, useEffect, useRef, useState } from 'react'
import { Volume2, Pause, Play, Square, Loader } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { useAudioControls } from '../hooks/useAudioControls'
import { synthesizeSpeech } from '../utils/audioApi'
import { fitSpeechText, toSpeechText } from '../utils/speechText'
import { useToast } from './ui/toastContext'

const PLAYBACK_RATES = [0.75, 1, 1.25, 1.5, 2]

// Matches the message header's Copy button. The idle button appears on hover
// or focus like its neighbours; the controls of a message that is being read
// stay visible so playback can always be stopped.
const BUTTON = 'bg-gray-700 hover:bg-gray-600 border border-gray-600 text-gray-200 p-1.5 rounded text-xs transition-all duration-200 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500 ml-1 sm:ml-2'
const HOVER_ONLY = 'opacity-0 group-hover:opacity-100 focus:opacity-100 focus-visible:opacity-100'

// One message is read at a time: starting another stops the one in progress.
let stopActivePlayer = null

const normalizeRate = (value) => (PLAYBACK_RATES.includes(Number(value)) ? Number(value) : 1)

/**
 * Read-aloud control for an assistant message (issue #1251): generate the
 * audio, then play, pause, stop and change speed. Speed is applied by the
 * browser's player, so changing it never re-requests the audio.
 *
 * The audio is held as an object URL for as long as this message is being
 * played and is released on stop, on the end of playback and on unmount. It
 * is not cached: reading the same message again asks the server again.
 */
const ReadAloudButton = ({ content }) => {
  const { settings, updateSettings } = useChat()
  const { tts, complianceLevel } = useAudioControls()
  const toast = useToast()
  const [status, setStatus] = useState('idle') // idle | loading | playing | paused
  const playerRef = useRef(null) // { audio, url, abort }
  const rate = normalizeRate(settings?.ttsPlaybackRate)
  const rateRef = useRef(rate)
  rateRef.current = rate

  const teardown = useCallback(() => {
    const player = playerRef.current
    playerRef.current = null
    if (!player) return
    player.abort?.abort()
    if (player.audio) {
      player.audio.onended = null
      player.audio.onerror = null
      player.audio.pause()
      player.audio.removeAttribute('src')
    }
    if (player.url) URL.revokeObjectURL(player.url)
    if (stopActivePlayer === player.stop) stopActivePlayer = null
  }, [])

  const stop = useCallback(() => {
    teardown()
    setStatus('idle')
  }, [teardown])

  useEffect(() => teardown, [teardown])

  // Hidden mid-playback (the preference was switched off, or the security
  // level changed to one the service is not approved for): stop.
  const visible = tts.visible
  useEffect(() => {
    if (!visible) stop()
  }, [visible, stop])

  const start = async () => {
    const speech = fitSpeechText(toSpeechText(content), tts.max_input_chars)
    if (!speech.text) {
      toast.info('There is no text to read aloud in this message.')
      return
    }
    stopActivePlayer?.()
    // The element is created here, inside the click, and given its source
    // when the audio arrives: some browsers only let an element play if it
    // was set up during a user gesture.
    const audio = new Audio()
    const player = { abort: new AbortController(), stop, audio }
    playerRef.current = player
    stopActivePlayer = stop
    setStatus('loading')
    let loaded = false
    try {
      const blob = await synthesizeSpeech(speech.text, { complianceLevel, signal: player.abort.signal })
      if (playerRef.current !== player) return
      player.url = URL.createObjectURL(blob)
      audio.onended = () => { if (playerRef.current === player) stop() }
      audio.onerror = () => {
        if (playerRef.current !== player) return
        stop()
        toast.error('The browser could not play the generated audio.')
      }
      audio.src = player.url
      // Both: a browser resets playbackRate to the default when it loads.
      audio.defaultPlaybackRate = rateRef.current
      audio.playbackRate = rateRef.current
      loaded = true
      if (speech.truncated) {
        toast.info(`This message is longer than the read-aloud limit. Reading the first ${speech.text.length.toLocaleString()} characters.`)
      }
      await audio.play()
      if (playerRef.current !== player) return
      setStatus('playing')
    } catch (error) {
      if (error?.name === 'AbortError' || playerRef.current !== player) return
      if (loaded && error?.name === 'NotAllowedError') {
        // The browser wants a fresh click to start sound (the wait for the
        // audio outlived the first one). Keep what was generated and offer
        // Play, instead of failing and generating it all over again.
        setStatus('paused')
        toast.info('The audio is ready. Press play to start it.')
        return
      }
      stop()
      toast.error(error?.message || 'Read-aloud failed. Try again.')
    }
  }

  const togglePause = () => {
    const audio = playerRef.current?.audio
    if (!audio) return
    if (audio.paused) {
      audio.play().then(() => setStatus('playing')).catch(() => {
        toast.error('The browser blocked audio playback for this site.')
      })
    } else {
      audio.pause()
      setStatus('paused')
    }
  }

  const changeRate = (event) => {
    const next = normalizeRate(event.target.value)
    const audio = playerRef.current?.audio
    if (audio) {
      audio.defaultPlaybackRate = next
      audio.playbackRate = next
    }
    updateSettings?.({ ttsPlaybackRate: next })
  }

  if (!visible) return null

  if (status === 'idle') {
    return (
      <button
        onClick={start}
        className={`read-aloud-button ${HOVER_ONLY} ${BUTTON}`}
        title="Read aloud"
        aria-label="Read message aloud"
        type="button"
      >
        <Volume2 className="w-3 h-3" aria-hidden="true" />
      </button>
    )
  }

  if (status === 'loading') {
    return (
      <button
        onClick={stop}
        className={`read-aloud-button ${BUTTON}`}
        title="Generating audio. Click to cancel."
        aria-label="Cancel read aloud"
        type="button"
      >
        <Loader className="w-3 h-3 animate-spin" aria-hidden="true" />
        <span className="sr-only" role="status">Generating audio</span>
      </button>
    )
  }

  const paused = status === 'paused'
  return (
    <span className="read-aloud-controls inline-flex items-center" role="group" aria-label="Read aloud controls">
      <button
        onClick={togglePause}
        className={BUTTON}
        title={paused ? 'Resume' : 'Pause'}
        aria-label={paused ? 'Resume reading' : 'Pause reading'}
        type="button"
      >
        {paused ? <Play className="w-3 h-3" aria-hidden="true" /> : <Pause className="w-3 h-3" aria-hidden="true" />}
      </button>
      <button onClick={stop} className={BUTTON} title="Stop" aria-label="Stop reading" type="button">
        <Square className="w-3 h-3" aria-hidden="true" />
      </button>
      <select
        value={String(rate)}
        onChange={changeRate}
        className="bg-gray-700 border border-gray-600 text-gray-200 rounded text-xs py-1 px-1 ml-1 sm:ml-2 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500"
        title="Playback speed"
        aria-label="Playback speed"
      >
        {PLAYBACK_RATES.map(value => (
          <option key={value} value={String(value)}>{value}x</option>
        ))}
      </select>
    </span>
  )
}

export default ReadAloudButton

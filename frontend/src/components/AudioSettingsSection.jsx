import { useChat } from '../contexts/ChatContext'
import { FOCUS_RING } from '../utils/focusRing'
import {
  AUDIO_PREFERENCE_KEYS,
  audioButtonPreference,
  audioCapabilityOffered,
  hasExplicitAudioPreference,
} from '../utils/audioControls'

const ROWS = [
  {
    capability: 'stt',
    id: 'show-dictation-button',
    label: 'Show microphone button',
    help: 'Adds a microphone to the message box. Your speech is transcribed into the draft so you can edit it; nothing is sent until you press Send.',
  },
  {
    capability: 'tts',
    id: 'show-read-aloud-button',
    label: 'Show read-aloud button',
    help: 'Adds a speaker button to assistant replies, with pause, stop and playback speed.',
  },
]

/**
 * The show/hide switches for dictation and read-aloud (issue #1251).
 *
 * A capability the administrator has not made available to this user (or,
 * for dictation, whose service takes no format a browser records) has no
 * row at all: a preference cannot turn it on, so there is nothing to offer.
 * Until the user flips a switch it follows the administrator's default; a
 * flip is saved immediately, like the other context-owned switches here.
 */
const AudioSettingsSection = ({ reduceHelpText = false }) => {
  const { audio, settings, updateSettings } = useChat()
  const rows = ROWS.filter(row => audioCapabilityOffered(audio?.[row.capability]))
  if (rows.length === 0) return null

  return (
    <div className="bg-gray-700 rounded-lg p-4 space-y-4" data-testid="audio-settings">
      <div className="text-gray-50 font-medium">Voice</div>
      {rows.map(row => {
        const key = AUDIO_PREFERENCE_KEYS[row.capability]
        const preference = settings?.[key]
        const checked = audioButtonPreference(audio[row.capability], preference)
        return (
          <div key={row.id}>
            <div className="flex items-center justify-between mb-1">
              <label className="text-gray-200" htmlFor={row.id}>{row.label}</label>
              <button
                type="button"
                id={row.id}
                role="switch"
                aria-checked={checked}
                aria-label={row.label}
                onClick={() => updateSettings({ [key]: !checked })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${FOCUS_RING} ${
                  checked ? 'bg-green-600' : 'bg-gray-600'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    checked ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
            {!reduceHelpText && (
              <p className="text-sm text-gray-400">
                {row.help}
                {!hasExplicitAudioPreference(preference) && ' Currently following your administrator\'s default.'}
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

export default AudioSettingsSection

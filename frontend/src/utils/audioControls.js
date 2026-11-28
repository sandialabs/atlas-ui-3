// Whether the dictation and read-aloud buttons are drawn (issue #1251).
//
// Three inputs, in order of authority:
//   1. The server says whether the capability is available to this user
//      (`audio.<capability>.enabled`: switched on, fully configured, and the
//      user is in an allowed group). Nothing below can turn an unavailable
//      capability on. For dictation the service must also accept a format a
//      browser can record (`audio.stt.accepted_media_types`).
//   2. With compliance levels on, the audio endpoint must be approved for
//      the conversation's level -- the same rule models and tools follow.
//      The server enforces it on every request; hiding the button here just
//      avoids offering something that would be refused.
//   3. The user's own show/hide choice, and when they have made none, the
//      administrator's default.

import { isComplianceAccessible, complianceLevelsReady } from './complianceAccess'

// Keys in the `chatui-settings` preferences. `null` (or absent) means the
// user has not chosen, so the administrator's default applies.
export const AUDIO_PREFERENCE_KEYS = { stt: 'showSttButton', tts: 'showTtsButton' }

// Containers the browser can record, in the order to try them. MediaRecorder
// support differs by browser (WebM/Opus in Chromium and Firefox, MP4/AAC in
// Safari), so the first one the browser can produce wins.
export const RECORDING_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
]

const baseMediaType = type => type.split(';')[0].trim().toLowerCase()

/**
 * The recorder types the transcription service takes. `accepted` is the
 * server's `stt.accepted_media_types` (canonical types such as `audio/webm`);
 * a server that does not send it accepts every recorder type.
 */
export const recordableMimeTypes = accepted =>
  Array.isArray(accepted)
    ? RECORDING_MIME_TYPES.filter(type => accepted.includes(baseMediaType(type)))
    : RECORDING_MIME_TYPES

/**
 * Whether the server offers the capability to this user at all. Dictation
 * also needs a service that takes something a browser can record: one that
 * accepts only WAV or MP3 uploads is usable from API clients, not from here.
 */
export const audioCapabilityOffered = capability =>
  Boolean(capability?.enabled) &&
  (capability.accepted_media_types === undefined || recordableMimeTypes(capability.accepted_media_types).length > 0)

export const DEFAULT_AUDIO_CONFIG = {
  stt: { enabled: false, show_button_by_default: true },
  tts: { enabled: false, show_button_by_default: true },
}

export const hasExplicitAudioPreference = preference => preference === true || preference === false

/** The switch position: the user's choice, else the administrator's default. */
export const audioButtonPreference = (capability, preference) =>
  hasExplicitAudioPreference(preference) ? preference : capability?.show_button_by_default !== false

/**
 * `available`: the capability can be used in this conversation.
 * `visible`: the button is drawn (available, and not hidden by preference).
 */
export const resolveAudioControl = (capability, preference, { levels = [], activeLevel = null } = {}) => {
  if (!audioCapabilityOffered(capability)) return { available: false, visible: false }
  let permitted = true
  // `null` means compliance levels are off; an array is the endpoint's
  // declaration (possibly empty, which approves it for no classified level).
  if (activeLevel && Array.isArray(capability.allowed_data_classifications)) {
    // Until the level definitions load every check would deny; the server
    // still decides, so do not flash the button away on page load.
    permitted = !complianceLevelsReady(levels, activeLevel) ||
      isComplianceAccessible(levels, activeLevel, capability.allowed_data_classifications)
  }
  return { available: permitted, visible: permitted && audioButtonPreference(capability, preference) }
}

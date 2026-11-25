// Whether the dictation and read-aloud buttons are drawn (issue #1251).
//
// Three inputs, in order of authority:
//   1. The server says whether the capability is available to this user
//      (`audio.<capability>.enabled`: switched on, fully configured, and the
//      user is in an allowed group). Nothing below can turn an unavailable
//      capability on.
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
  if (!capability?.enabled) return { available: false, visible: false }
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

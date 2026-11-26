import { useMemo } from 'react'
import { useChat } from '../contexts/ChatContext'
import { AUDIO_PREFERENCE_KEYS, DEFAULT_AUDIO_CONFIG, resolveAudioControl } from '../utils/audioControls'

/**
 * Whether the dictation and read-aloud buttons are drawn for this user in
 * this conversation, plus the limits and the level to send with a request.
 * The rule itself is in utils/audioControls.js.
 */
export function useAudioControls() {
  const { audio, settings, complianceLevels, activeComplianceFilter } = useChat()
  const sttPreference = settings?.[AUDIO_PREFERENCE_KEYS.stt]
  const ttsPreference = settings?.[AUDIO_PREFERENCE_KEYS.tts]
  return useMemo(() => {
    const config = audio || DEFAULT_AUDIO_CONFIG
    const context = { levels: complianceLevels, activeLevel: activeComplianceFilter }
    return {
      stt: { ...config.stt, ...resolveAudioControl(config.stt, sttPreference, context) },
      tts: { ...config.tts, ...resolveAudioControl(config.tts, ttsPreference, context) },
      complianceLevel: activeComplianceFilter || null,
    }
  }, [audio, sttPreference, ttsPreference, complianceLevels, activeComplianceFilter])
}

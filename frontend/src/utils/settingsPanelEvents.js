/**
 * Cross-component request to open the combined Tools and Settings panel
 * (issue #836).
 *
 * The panel lives in App, well above the components that want to open it (the
 * prompt selector under the chat box, for one), so a window event keeps that
 * one-off from being threaded through every layer as props.
 *
 * detail: { tab?: 'tools' | 'prompts' | 'general' | 'userInfo' | 'admin',
 *           promptIntent?: { type: 'create' } | { type: 'edit', id } }
 */
export const OPEN_SETTINGS_EVENT = 'atlas:open-settings'

export function openSettingsPanel(detail = {}) {
  window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail }))
}

export default openSettingsPanel

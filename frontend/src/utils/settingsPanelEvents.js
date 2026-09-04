/**
 * Cross-component request to open the combined Tools and Settings panel
 * (issue #836).
 *
 * The panel lives in App, well above the components that want to open it (the
 * prompt selector under the chat box, for one), so a window event keeps that
 * one-off from being threaded through every layer as props.
 *
 * detail: { tab?: 'tools' | 'dataSources' | 'prompts' | 'general' | 'userInfo' | 'admin',
 *           promptIntent?: { type: 'create' } | { type: 'edit', id } }
 */
export const OPEN_SETTINGS_EVENT = 'atlas:open-settings'

export const SETTINGS_TAB_IDS = ['tools', 'dataSources', 'prompts', 'general', 'userInfo', 'admin']

/**
 * Narrow an event's `detail` to the shape above. Any window listener can be
 * fired by anything on the page, so an unrecognised tab id or a malformed
 * prompt intent is dropped rather than spread into panel state, where it would
 * select a tab that does not exist or hand PromptManager a bad id (PR #839
 * review).
 */
export function parseOpenSettingsDetail(detail) {
  const { tab, promptIntent } = detail || {}
  const safeTab = SETTINGS_TAB_IDS.includes(tab) ? tab : null

  let safeIntent = null
  if (promptIntent && typeof promptIntent === 'object') {
    if (promptIntent.type === 'create') {
      safeIntent = { type: 'create' }
    } else if (promptIntent.type === 'edit' && (typeof promptIntent.id === 'string' || typeof promptIntent.id === 'number')) {
      safeIntent = { type: 'edit', id: promptIntent.id }
    }
  }
  return { tab: safeTab, promptIntent: safeIntent }
}

export function openSettingsPanel(detail = {}) {
  window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail }))
}

export default openSettingsPanel

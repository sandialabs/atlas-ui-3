/**
 * Save mode constants shared between ChatContext and the conversation-setup
 * controls.
 *
 * Three modes:
 *   'none'   - Not saved: nothing is persisted after the chat is left
 *   'local'  - Saved in this browser: stored in IndexedDB on this device
 *   'server' - Saved to server: stored in the backend conversation database
 *
 * The header presents these as three explicit choices, not as a cycling
 * toggle; `nextSaveMode` is kept for any other caller that still needs the
 * historical cycle order.
 */

export const SAVE_MODES = ['none', 'local', 'server']

export const nextSaveMode = (mode) =>
  SAVE_MODES[(SAVE_MODES.indexOf(mode) + 1) % SAVE_MODES.length]

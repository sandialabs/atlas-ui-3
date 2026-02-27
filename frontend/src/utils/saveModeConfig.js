/**
 * Save mode constants shared between ChatContext and Header.
 *
 * Three modes:
 *   'none'   - Incognito: nothing saved anywhere
 *   'local'  - Saved Locally: stored in the browser (IndexedDB)
 *   'server' - Saved to Server: stored in the backend database
 */

export const SAVE_MODES = ['none', 'local', 'server']

export const nextSaveMode = (mode) =>
  SAVE_MODES[(SAVE_MODES.indexOf(mode) + 1) % SAVE_MODES.length]

import { getRegisteredTheme } from './themeRegistry'

// This is local preference state, not downloadable themes or code.
export const FAVORITES_STORAGE_KEY = 'atlas-theme-favorites'

export function readThemeFavorites() {
  try {
    const stored = JSON.parse(localStorage.getItem(FAVORITES_STORAGE_KEY) || '[]')
    if (!Array.isArray(stored)) return []
    // The allowlist keeps old or tampered settings from injecting theme IDs.
    return [...new Set(stored.filter(id =>
      typeof id === 'string' && getRegisteredTheme(id) !== null))]
  } catch {
    return []
  }
}

export function writeThemeFavorites(ids) {
  try {
    localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(ids))
  } catch {
    // In private browsing or storage-denied contexts favorites still work
    // for the current session without breaking Settings.
  }
}

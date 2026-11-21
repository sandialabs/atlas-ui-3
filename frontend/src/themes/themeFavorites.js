import { isKnownThemeId } from './themeRegistry'

// This is local preference state, not downloadable themes or code.
export const FAVORITES_STORAGE_KEY = 'atlas-theme-favorites'

export function readThemeFavorites() {
  try {
    const stored = JSON.parse(localStorage.getItem(FAVORITES_STORAGE_KEY) || '[]')
    if (!Array.isArray(stored)) return []
    // The complete known-ID allowlist survives builds that exclude retro themes.
    // Unknown or tampered IDs never become favorite theme identifiers.
    return [...new Set(stored.filter(id =>
      typeof id === 'string' && isKnownThemeId(id)))]
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

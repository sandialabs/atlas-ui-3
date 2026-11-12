/**
 * Developer-owned theme catalog (issue #1119).
 *
 * This list is assembled at build time.  A saved value never loads remote CSS
 * or executable content, and only registered IDs can become an active theme.
 * Light/dark specify the existing base stylesheet so older selectors continue
 * to work while we migrate to fully semantic component tokens.
 *
 * `@atlas/retro-themes` is a build-time alias (vite.config.js): with
 * VITE_FEATURE_RETRO_THEMES=false it resolves to an empty catalog, so only
 * Light and Dark are registered and no retro CSS is bundled.
 */
import { RETRO_CATALOG, RETRO_THEMES_BUILT } from '@atlas/retro-themes'
import { RETRO_THEME_MODES } from './retroThemeModes.generated'

export const BASE_THEMES = Object.freeze([
  Object.freeze({
    id: 'dark', name: 'Dark', era: 'Atlas',
    description: 'Original dark interface', mode: 'dark',
    preview: Object.freeze({ background: '#111827', surface: '#374151', foreground: '#f9fafb', accent: '#60a5fa' }),
  }),
  Object.freeze({
    id: 'light', name: 'Light', era: 'Atlas',
    description: 'Original light interface', mode: 'light',
    preview: Object.freeze({ background: '#f9fafb', surface: '#e5e7eb', foreground: '#111827', accent: '#2563eb' }),
  }),
])

export const THEME_REGISTRY = Object.freeze([...BASE_THEMES, ...RETRO_CATALOG])

/** True when this bundle ships the retro catalog (build-time flag). */
export const RETRO_THEMES_IN_BUILD = RETRO_THEMES_BUILT && RETRO_CATALOG.length > 0

export const DEFAULT_THEME_ID = 'dark'
const themeById = new Map(THEME_REGISTRY.map(theme => [theme.id, theme]))

export function getRegisteredTheme(id) {
  return themeById.get(id) ?? null
}

export function resolveThemeId(id) {
  return themeById.has(id) ? id : DEFAULT_THEME_ID
}

/** True for the Atlas Light/Dark themes, which every build and config allows. */
export function isBaseThemeId(id) {
  return id === 'dark' || id === 'light'
}

function isRetroThemeId(id) {
  return typeof id === 'string' && Object.prototype.hasOwnProperty.call(RETRO_THEME_MODES, id)
}

/**
 * The theme actually applied for a saved ID.
 *
 * With retro themes allowed this is resolveThemeId(). Otherwise (catalog
 * compiled out, or FEATURE_RETRO_THEMES_ENABLED off) a retro ID falls back to
 * its own light/dark base mode, so a Windows 95 user lands on Light rather
 * than the default Dark; unknown values still fall back to the default.
 */
export function effectiveThemeId(id, retroAllowed = true) {
  if (isBaseThemeId(id)) return id
  if (retroAllowed && themeById.has(id)) return id
  if (isRetroThemeId(id)) return RETRO_THEME_MODES[id]
  return DEFAULT_THEME_ID
}

/** Whether `id` is worth persisting: a base theme or any known retro ID. */
export function isKnownThemeId(id) {
  return isBaseThemeId(id) || isRetroThemeId(id)
}

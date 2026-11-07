/**
 * Developer-owned theme catalog (issue #1119).
 *
 * This list is assembled at build time.  A saved value never loads remote CSS
 * or executable content, and only registered IDs can become an active theme.
 * Light/dark specify the existing base stylesheet so older selectors continue
 * to work while we migrate to fully semantic component tokens.
 */
import { RETRO_CATALOG } from './retroCatalog.generated'

export const THEME_REGISTRY = Object.freeze([
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
  ...RETRO_CATALOG,
])

export const DEFAULT_THEME_ID = 'dark'
const themeById = new Map(THEME_REGISTRY.map(theme => [theme.id, theme]))

export function getRegisteredTheme(id) {
  return themeById.get(id) ?? null
}

export function resolveThemeId(id) {
  return themeById.has(id) ? id : DEFAULT_THEME_ID
}

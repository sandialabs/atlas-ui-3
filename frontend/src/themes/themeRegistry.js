/**
 * Developer-owned theme catalog (issue #1119).
 *
 * This list is assembled at build time.  A saved value never loads remote CSS
 * or executable content, and only registered IDs can become an active theme.
 * Light/dark specify the existing base stylesheet so older selectors continue
 * to work while we migrate to fully semantic component tokens.
 */
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
  Object.freeze({
    id: 'crt-phosphor', name: 'CRT Phosphor', era: 'Retro Design System · 09',
    description: 'Green phosphor terminal', mode: 'dark',
    reference: '09-crt-phosphor',
    preview: Object.freeze({ background: '#001a00', surface: '#003300', foreground: '#aaffaa', accent: '#33ff33' }),
  }),
  Object.freeze({
    id: 'bbs-ansi', name: 'BBS ANSI Art', era: 'Retro Design System · 30',
    description: '16-color ANSI bulletin board', mode: 'dark',
    reference: '30-bbs-ansi',
    preview: Object.freeze({ background: '#000000', surface: '#0000aa', foreground: '#ffffff', accent: '#55ffff' }),
  }),
  Object.freeze({
    id: 'win95', name: 'Windows 95', era: 'Retro Design System · 02',
    description: 'Silver bevels and navy title bars', mode: 'light',
    reference: '02-windows-95',
    preview: Object.freeze({ background: '#c0c0c0', surface: '#dfdfdf', foreground: '#000000', accent: '#000080' }),
  }),
  Object.freeze({
    id: 'mac7', name: 'Mac System 7', era: 'Retro Design System · 01',
    description: 'Chicago typography and monochrome borders', mode: 'light',
    reference: '01-mac-system-7',
    preview: Object.freeze({ background: '#a8a8a8', surface: '#dddddd', foreground: '#000000', accent: '#000000' }),
  }),
])

export const DEFAULT_THEME_ID = 'dark'
const themeById = new Map(THEME_REGISTRY.map(theme => [theme.id, theme]))

export function getRegisteredTheme(id) {
  return themeById.get(id) ?? null
}

export function resolveThemeId(id) {
  return themeById.has(id) ? id : DEFAULT_THEME_ID
}

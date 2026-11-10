/**
 * Shared by vite.config.js and vitest.config.js: whether this build ships the
 * Retro Design System catalog, and which module `@atlas/retro-themes` maps to.
 *
 * VITE_FEATURE_RETRO_THEMES is a build-time flag that defaults to on: only the
 * literal string "false" compiles the catalog out. The run-time switch is the
 * backend FEATURE_RETRO_THEMES_ENABLED setting (default off).
 */
import process from 'node:process'
import { fileURLToPath } from 'node:url'

export function retroThemesBuildEnabled(env = process.env) {
  return String(env.VITE_FEATURE_RETRO_THEMES ?? 'true').trim().toLowerCase() !== 'false'
}

export function retroThemesModulePath(enabled) {
  const file = enabled ? './retroThemes.enabled.js' : './retroThemes.disabled.js'
  return fileURLToPath(new URL(file, import.meta.url))
}

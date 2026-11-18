/**
 * Stand-in for retroThemes.enabled.js in builds with
 * VITE_FEATURE_RETRO_THEMES=false: no retro stylesheets, no catalog entries.
 * Only the Atlas Light and Dark themes exist in such a build.
 */
export const RETRO_CATALOG = Object.freeze([])
export const RETRO_THEMES_BUILT = false

/**
 * Build-time entry for the Retro Design System catalog (issue #1119).
 *
 * vite.config.js resolves `@atlas/retro-themes` to this module unless the
 * build sets VITE_FEATURE_RETRO_THEMES=false, in which case it resolves to
 * retroThemes.disabled.js and none of these stylesheets or catalog entries
 * enter the bundle. Keep every retro-only import here.
 */
import '../vendor/retro-design-system/tokens.css'
import '../retroCatalog.generated.css'
import '../retroChrome.generated.css'
import '../retroChrome.css'

export { RETRO_CATALOG } from '../retroCatalog.generated'
export const RETRO_THEMES_BUILT = true

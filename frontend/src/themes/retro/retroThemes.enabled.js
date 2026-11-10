/**
 * Build-time entry for the Retro Design System catalog (issue #1119).
 *
 * vite.config.js resolves `@atlas/retro-themes` to this module unless the
 * build sets VITE_FEATURE_RETRO_THEMES=false, in which case it resolves to
 * retroThemes.disabled.js and no catalog entries enter the bundle.
 *
 * The retro stylesheets (vendor tokens.css, retroCatalog.generated.css,
 * retroChrome.generated.css) are deliberately not imported here: the
 * retroCssPlugin splits them into one /assets/retro/<id>.css per theme and
 * retroThemeCss.js loads only the active theme's file. The catalog's
 * `preview` colors drive the gallery swatches without loading any of them.
 */
export { RETRO_CATALOG } from '../retroCatalog.generated'
export const RETRO_THEMES_BUILT = true

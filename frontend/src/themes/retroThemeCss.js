/**
 * On-demand loader for per-theme retro CSS (issue #1119).
 *
 * Retro themes are not in the main bundle. Each one ships as
 * /assets/retro/<id>.css (vite.config.js -> retro/retroCssPlugin.js); this
 * module injects that stylesheet the first time a theme is applied, caches
 * the result per ID, and reports failures so ThemeContext can fall back to
 * the theme's light/dark base. public/theme-bootstrap.js injects the saved
 * theme's sheet before first paint; the loader adopts that <link>.
 *
 * Lazy loading is active only when the build stamped a CSS version
 * (VITE_RETRO_CSS_VERSION). Builds with VITE_FEATURE_RETRO_THEMES=false have
 * an empty version and no retro themes, so nothing is ever requested.
 * Without a version (Vitest, the contrast fixture) every theme counts as
 * loaded, because those harnesses provide the CSS themselves.
 */
import { RETRO_THEMES_IN_BUILD, getRegisteredTheme } from './themeRegistry'

export const RETRO_CSS_ATTRIBUTE = 'data-atlas-theme-css'
const RETRO_CSS_BASE = '/assets/retro/'
// A sheet that neither loads nor errors (stalled proxy) must not hold the
// previous theme forever.
export const RETRO_CSS_TIMEOUT_MS = 15000

let version = import.meta.env?.VITE_RETRO_CSS_VERSION || ''
// id -> { status: 'pending' | 'loaded' | 'failed', promise }
const cache = new Map()

/** Test hook: override the build version and clear the cache. */
export function __resetRetroThemeCss(nextVersion = import.meta.env?.VITE_RETRO_CSS_VERSION || '') {
  version = nextVersion
  cache.clear()
}

export function retroThemeCssEnabled() {
  return RETRO_THEMES_IN_BUILD && Boolean(version)
}

/** True for registered themes whose CSS comes from /assets/retro/. */
export function needsRetroThemeCss(id) {
  return retroThemeCssEnabled() && Boolean(getRegisteredTheme(id)?.reference)
}

export function retroThemeCssUrl(id) {
  return `${RETRO_CSS_BASE}${encodeURIComponent(id)}.css?v=${encodeURIComponent(version)}`
}

/** 'loaded' | 'failed' | 'pending' (not requested yet, or in flight). */
export function retroThemeCssStatus(id) {
  if (!needsRetroThemeCss(id)) return 'loaded'
  return cache.get(id)?.status ?? 'pending'
}

function findLink(id) {
  for (const link of document.querySelectorAll(`link[${RETRO_CSS_ATTRIBUTE}]`)) {
    if (link.getAttribute(RETRO_CSS_ATTRIBUTE) === id) return link
  }
  return null
}

function sheetHasRules(link) {
  try {
    return link.sheet.cssRules.length > 0
  } catch {
    return true // Unreadable (cross-origin) but present.
  }
}

// Anything styling the page after the retro sheet would win ties it must win.
function laterStylesheet(link) {
  let node = link.nextElementSibling
  for (; node; node = node.nextElementSibling) {
    if (node.matches(`link[rel="stylesheet"]:not([${RETRO_CSS_ATTRIBUTE}]), style`)) return true
  }
  return false
}

function createLink(id) {
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = retroThemeCssUrl(id)
  link.setAttribute(RETRO_CSS_ATTRIBUTE, id)
  document.head.appendChild(link)
  return link
}

function waitFor(link, adopted) {
  return new Promise(resolve => {
    // The bootstrap's parser-inserted <link> blocks module scripts, so by
    // now it has settled: Chrome leaves an empty sheet behind a 404, and the
    // split files are never empty, so no rules means it failed.
    if (adopted && link.sheet) return resolve(sheetHasRules(link))
    let timer
    const done = ok => {
      clearTimeout(timer)
      link.removeEventListener('load', onLoad)
      link.removeEventListener('error', onError)
      resolve(ok)
    }
    const onLoad = () => done(true)
    const onError = () => done(false)
    link.addEventListener('load', onLoad)
    link.addEventListener('error', onError)
    timer = setTimeout(() => done(false), RETRO_CSS_TIMEOUT_MS)
  })
}

/**
 * Load a theme's stylesheet once. Resolves true when it is applied, false
 * when it failed (the <link> is removed; the failure is cached until
 * forgetFailedRetroThemeCss).
 */
export function loadRetroThemeCss(id) {
  if (!needsRetroThemeCss(id)) return Promise.resolve(true)
  const cached = cache.get(id)
  if (cached) return cached.promise
  const bootstrapped = findLink(id)
  let link = bootstrapped
  let adopted = Boolean(link)
  // Production keeps the bootstrap's sheet last (retroCssPlugin moves the
  // bundle CSS above it). The dev server injects app styles after it, so
  // load a second copy at the end and drop the first once it is in.
  if (adopted && laterStylesheet(link)) adopted = false
  if (!adopted) link = createLink(id)
  const entry = { status: 'pending' }
  entry.promise = waitFor(link, adopted).then(ok => {
    entry.status = ok ? 'loaded' : 'failed'
    if (!ok) link.remove()
    if (bootstrapped && bootstrapped !== link) bootstrapped.remove()
    return ok
  })
  cache.set(id, entry)
  return entry.promise
}

/** Let an explicit re-selection retry a theme whose CSS failed to load. */
export function forgetFailedRetroThemeCss(id) {
  if (cache.get(id)?.status === 'failed') cache.delete(id)
}

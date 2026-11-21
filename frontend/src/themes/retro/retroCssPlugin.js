/**
 * Vite plugin: per-theme lazy retro CSS (issue #1119).
 *
 * With VITE_FEATURE_RETRO_THEMES on, the build emits one minified stylesheet
 * per retro theme at /assets/retro/<id>.css (see retroCssSplit.js) instead of
 * bundling all of them into index-*.css, and the dev server serves the same
 * URLs straight from the generated sources. With the flag off it does
 * nothing, so the build contains no retro CSS at all.
 *
 * The cache-busting version (a hash of the generated sources) reaches the
 * page as VITE_RETRO_CSS_VERSION: index.html stamps it on the bootstrap
 * script tag (data-retro-css) and retroThemeCss.js reads it from
 * import.meta.env. An empty version means "no lazy retro CSS".
 */
import crypto from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { transformWithEsbuild } from 'vite'
import { RETRO_CSS_DIR, RETRO_CSS_SOURCES, splitRetroCss } from './retroCssSplit.js'

const FRONTEND = fileURLToPath(new URL('../../../', import.meta.url))

export function readRetroCssSources(root = FRONTEND) {
  return RETRO_CSS_SOURCES.map(path => readFileSync(new URL(path, `file://${root}`), 'utf8'))
}

export function retroCssVersion(sheets) {
  return crypto.createHash('sha256').update(sheets.join('\n/*--*/\n')).digest('hex').slice(0, 12)
}

const BOOTSTRAP_TAG = '<script src="/theme-bootstrap.js"'
const BUNDLE_CSS = /[ \t]*<link rel="stylesheet"[^>]*href="[^"]*\/assets\/index-[^"]*\.css"[^>]*>\r?\n?/g

/**
 * Put the bundle's stylesheet ahead of the first-paint bootstrap. The
 * bootstrap writes the saved theme's /assets/retro/<id>.css where it runs, and
 * Vite appends the bundle CSS at the end of <head>; left there it would come
 * after the retro sheet and win equal-specificity ties that the retro theme
 * is meant to win (it did when retro CSS was bundled after the base CSS).
 */
export function moveBundleCssBeforeBootstrap(html) {
  const at = html.indexOf(BOOTSTRAP_TAG)
  if (at === -1) return html
  const links = html.match(BUNDLE_CSS) || []
  if (!links.length) return html
  const rest = html.replace(BUNDLE_CSS, '')
  const insert = rest.indexOf(BOOTSTRAP_TAG)
  const indent = rest.slice(rest.lastIndexOf('\n', insert) + 1, insert)
  return rest.slice(0, insert) + links.map(link => link.trim() + '\n' + indent).join('') + rest.slice(insert)
}

export function retroCssPlugin(enabled) {
  return {
    name: 'atlas-retro-theme-css',
    transformIndexHtml: {
      order: 'post',
      handler: html => (enabled ? moveBundleCssBeforeBootstrap(html) : html),
    },
    configureServer(server) {
      if (!enabled) return
      server.middlewares.use((req, res, next) => {
        const match = /^\/assets\/retro\/([a-z0-9-]+)\.css(?:\?.*)?$/.exec(req.url || '')
        if (!match) return next()
        // Re-split on every request so regenerated sources apply on reload.
        const css = splitRetroCss(readRetroCssSources())[match[1]]
        if (!css) return next()
        res.setHeader('Content-Type', 'text/css; charset=utf-8')
        res.setHeader('Cache-Control', 'no-cache')
        res.end(css)
      })
    },
    async generateBundle() {
      if (!enabled) return
      const split = splitRetroCss(readRetroCssSources())
      for (const [id, css] of Object.entries(split)) {
        const { code } = await transformWithEsbuild(css, `${id}.css`, { loader: 'css', minify: true })
        // esbuild drops the license comment when minifying; keep attribution.
        const header = css.slice(0, css.indexOf('*/') + 2).replace(/^\/\*/, '/*!')
        this.emitFile({ type: 'asset', fileName: `${RETRO_CSS_DIR}/${id}.css`, source: `${header}\n${code}` })
      }
    },
  }
}

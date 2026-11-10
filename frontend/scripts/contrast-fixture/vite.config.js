import process from 'node:process'
/**
 * Builds the browser contrast fixture (fixture.jsx) into one IIFE script for
 * frontend/scripts/check-retro-browser.py. No CSS is emitted: the checker
 * measures the production stylesheets from `npm run build` (index-*.css plus
 * the per-theme assets/retro/<id>.css).
 *
 * Backend-bound contexts and hooks resolve to inert stubs in ./stubs.
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { retroThemesModulePath } from '../../src/themes/retro/retroThemesBuildFlag.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const stub = name => path.join(here, 'stubs', name)
const STUBS = [
  [/[\\/]src[\\/]contexts[\\/]ChatContext(\.jsx)?$/, stub('ChatContext.jsx')],
  [/[\\/]src[\\/]contexts[\\/]WSContext(\.jsx)?$/, stub('WSContext.jsx')],
  [/[\\/]src[\\/]contexts[\\/]MarketplaceContext(\.jsx)?$/, stub('MarketplaceContext.jsx')],
  [/[\\/]src[\\/]hooks[\\/]useGlobusAuth(\.js)?$/, stub('useGlobusAuth.js')],
]

function fixtureStubs() {
  return {
    name: 'atlas-contrast-fixture-stubs',
    enforce: 'pre',
    resolveId(source, importer) {
      if (!importer || !source.startsWith('.')) return null
      const target = path.resolve(path.dirname(importer), source)
      for (const [pattern, replacement] of STUBS) {
        if (pattern.test(target)) return replacement
      }
      return null
    },
    // Component CSS (e.g. KaTeX) is not part of the measurement.
    load(id) {
      if (id.endsWith('.css')) return ''
      return null
    },
  }
}

export default defineConfig({
  root: here,
  logLevel: 'warn',
  plugins: [fixtureStubs(), react()],
  resolve: { alias: { '@atlas/retro-themes': retroThemesModulePath(true) } },
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
    __APP_VERSION__: JSON.stringify('fixture'),
    __GIT_HASH__: JSON.stringify('fixture'),
    __BUILD_TIME__: JSON.stringify('fixture'),
    // No lazy retro CSS: the checker inlines each theme's split stylesheet
    // and enables only the measured one, so the loader treats every theme
    // as loaded and setTheme() applies synchronously.
    'import.meta.env.VITE_RETRO_CSS_VERSION': JSON.stringify(''),
  },
  build: {
    outDir: process.env.ATLAS_FIXTURE_OUT_DIR || path.join(here, 'dist'),
    emptyOutDir: true,
    minify: false,
    cssCodeSplit: false,
    lib: { entry: path.join(here, 'fixture.jsx'), formats: ['iife'], name: 'AtlasContrastFixture', fileName: () => 'fixture.js' },
  },
})

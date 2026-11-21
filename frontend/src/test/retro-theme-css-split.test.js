// @vitest-environment node
/**
 * Per-theme lazy retro CSS (issue #1119): the build-time split of the
 * generated sheets and the Vite plugin that emits /assets/retro/<id>.css.
 * Node environment: the plugin minifies with esbuild, which rejects jsdom.
 */
import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import { RETRO_CSS_SOURCES, ruleThemeId, splitRetroCss, topLevelRules } from '../themes/retro/retroCssSplit'
import { moveBundleCssBeforeBootstrap, retroCssPlugin } from '../themes/retro/retroCssPlugin'
import { THEME_REGISTRY } from '../themes/themeRegistry'

const retroIds = THEME_REGISTRY.filter(theme => theme.reference).map(theme => theme.id)

describe('per-theme split of the generated retro CSS', () => {
  const sheets = RETRO_CSS_SOURCES.map(path => readFileSync(path, 'utf8'))
  const split = splitRetroCss(sheets)

  it('emits exactly one stylesheet per registered retro theme', () => {
    expect(Object.keys(split).sort()).toEqual([...retroIds].sort())
  })

  it('every rule in a theme file is scoped to that theme only', () => {
    for (const [id, css] of Object.entries(split)) {
      const scopes = new Set([...css.matchAll(/\[data-atlas-theme=["']?([a-z0-9-]+)["']?\]/g)].map(m => m[1]))
      expect([...scopes]).toEqual([id])
      expect(css).toContain('NovusGFX, MIT')
    }
  })

  it('keeps every source rule (tokens, then catalog, then chrome)', () => {
    const count = css => (css.replace(/\/\*[\s\S]*?\*\//g, '').match(/\{/g) || []).length
    const total = Object.values(split).reduce((sum, css) => sum + count(css), 0)
    expect(total).toBe(sheets.reduce((sum, css) => sum + count(css), 0))
    // Per theme: its tokens rules first, then catalog adapters, then chrome.
    const xp = split['windows-xp-luna']
    const firstOwnRule = css => topLevelRules(css).find(([selector]) => ruleThemeId(selector) === 'windows-xp-luna')
    const offsets = sheets.map(css => xp.indexOf(firstOwnRule(css).join(' ')))
    expect(offsets.every(offset => offset > 0)).toBe(true)
    expect([...offsets].sort((a, b) => a - b)).toEqual(offsets)
  })

  it('rejects rules that would leak across themes once split', () => {
    expect(() => splitRetroCss(['body { color: red }'])).toThrow(/exactly one theme/)
    expect(() => splitRetroCss(['[data-atlas-theme="a"] p, [data-atlas-theme="b"] p { color: red }']))
      .toThrow(/exactly one theme/)
    expect(() => splitRetroCss(['[data-atlas-theme="a"] p, p { color: red }'])).toThrow(/Unscoped/)
    expect(() => splitRetroCss(['@media print { [data-atlas-theme="a"] p { color: red } }'])).toThrow(/at-rule/)
    expect(() => splitRetroCss(['[data-atlas-theme="a"] p { color: red'])).toThrow(/Unbalanced/)
  })
})

describe('retroCssPlugin', () => {
  const emitted = async enabled => {
    const files = []
    await retroCssPlugin(enabled).generateBundle.call({ emitFile: file => files.push(file) })
    return files
  }

  it('emits minified /assets/retro/<id>.css files, with attribution, when the flag is on', async () => {
    const files = await emitted(true)
    expect(files.map(file => file.fileName).sort()).toEqual(retroIds.map(id => `assets/retro/${id}.css`).sort())
    const xp = files.find(file => file.fileName === 'assets/retro/windows-xp-luna.css').source
    expect(xp.startsWith('/*! Atlas retro theme CSS')).toBe(true)
    expect(xp).toContain('NovusGFX, MIT')
    expect(xp.length).toBeLessThan(splitRetroCss(RETRO_CSS_SOURCES.map(p => readFileSync(p, 'utf8')))['windows-xp-luna'].length)
  }, 30000)

  it('emits nothing when VITE_FEATURE_RETRO_THEMES=false', async () => {
    expect(await emitted(false)).toEqual([])
    const server = { middlewares: { use: vi.fn() } }
    retroCssPlugin(false).configureServer(server)
    expect(server.middlewares.use).not.toHaveBeenCalled()
  })

  it('serves the split files from the dev server', () => {
    let middleware
    retroCssPlugin(true).configureServer({ middlewares: { use: fn => { middleware = fn } } })
    const res = { headers: {}, setHeader(key, value) { this.headers[key] = value }, end: vi.fn() }
    const next = vi.fn()
    middleware({ url: '/assets/retro/mac7.css?v=abc' }, res, next)
    expect(next).not.toHaveBeenCalled()
    expect(res.headers['Content-Type']).toMatch(/text\/css/)
    expect(res.end.mock.calls[0][0]).toContain('[data-atlas-theme="mac7"]')
    middleware({ url: '/assets/retro/not-a-theme.css' }, res, next)
    middleware({ url: '/assets/index.css' }, res, next)
    expect(next).toHaveBeenCalledTimes(2)
  })
})

describe('first-paint cascade order', () => {
  it('moves the bundle CSS above the bootstrap so the retro sheet it writes comes last', () => {
    const html = [
      '<head>',
      '    <script src="/theme-bootstrap.js" data-retro-themes="true" data-retro-css="v1"></script>',
      '    <script type="module" crossorigin src="/assets/index-abc.js"></script>',
      '    <link rel="stylesheet" crossorigin href="/assets/index-def.css">',
      '  </head>',
    ].join('\n')
    const moved = moveBundleCssBeforeBootstrap(html)
    expect(moved.indexOf('index-def.css')).toBeLessThan(moved.indexOf('theme-bootstrap.js'))
    expect(moved.match(/index-def\.css/g)).toHaveLength(1)
    expect(moved).toContain('    <link rel="stylesheet" crossorigin href="/assets/index-def.css">\n    <script src="/theme-bootstrap.js"')
    expect(moveBundleCssBeforeBootstrap('<head></head>')).toBe('<head></head>')
  })

  it('the production build applies it', async () => {
    const plugin = retroCssPlugin(true)
    const html = '<script src="/theme-bootstrap.js"></script>\n<link rel="stylesheet" href="/assets/index-x.css">\n'
    expect(plugin.transformIndexHtml.order).toBe('post')
    expect(plugin.transformIndexHtml.handler(html).startsWith('<link')).toBe(true)
    expect(retroCssPlugin(false).transformIndexHtml.handler(html)).toBe(html)
  })
})

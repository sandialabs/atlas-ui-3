import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { describe, expect, it } from 'vitest'
import { THEME_REGISTRY } from '../themes/themeRegistry'

const bootstrap = readFileSync('public/theme-bootstrap.js', 'utf8')

/**
 * Run the first-paint script the way index.html does. `retroBuild` is the
 * script tag's data-retro-themes (VITE_FEATURE_RETRO_THEMES); `cachedConfig`
 * is the /api/config cache useChatConfig keeps in localStorage.
 */
function firstPaint(saved, {
  blocked = false, retroBuild = 'true', cachedConfig = null, runtime = null, cssVersion = null,
  readyState = 'loading', written = [], appended = [],
} = {}) {
  const attributes = {}
  const storage = {
    'atlas-theme': saved,
    'chatui-config-cache': cachedConfig === null ? null : JSON.stringify(cachedConfig),
  }
  runInNewContext(bootstrap, {
    localStorage: {
      getItem(key) {
        if (blocked) throw new Error('blocked')
        return storage[key] ?? null
      },
    },
    document: {
      currentScript: {
        getAttribute: name => ({
          'data-retro-themes': retroBuild, 'data-retro-runtime': runtime, 'data-retro-css': cssVersion,
        })[name] ?? null,
      },
      documentElement: { setAttribute(key, value) { attributes[key] = value } },
      readyState,
      write: html => written.push(html),
      createElement: () => {
        const link = { attributes: {}, setAttribute(key, value) { this.attributes[key] = value } }
        return link
      },
      head: { appendChild: link => appended.push(link) },
    },
  })
  return attributes
}

const retroThemes = THEME_REGISTRY.filter(theme => theme.reference)

describe('synchronous first-paint theme restoration', () => {
  it.each(THEME_REGISTRY)('restores $id and its $mode base before React', ({ id, mode }) => {
    expect(firstPaint(id)).toEqual({ 'data-theme': mode, 'data-atlas-theme': id })
  })
  it.each([null, '', 'removed-theme', '__proto__', 'constructor', 'https://bad.invalid'])('defaults safely for %s', saved => {
    expect(firstPaint(saved)).toEqual({ 'data-theme': 'dark', 'data-atlas-theme': 'dark' })
  })
  it('survives blocked storage and matches the intentional dark React default', () => {
    expect(firstPaint(null, { blocked: true })['data-theme']).toBe('dark')
  })
  it('is a blocking head script, not a deferred module, carrying the build flag', () => {
    const html = readFileSync('index.html', 'utf8')
    const tag = '<script src="/theme-bootstrap.js" data-retro-themes="%VITE_FEATURE_RETRO_THEMES%" ' +
      'data-retro-css="%VITE_RETRO_CSS_VERSION%"></script>'
    expect(html).toContain(tag)
    expect(html.indexOf(tag)).toBeLessThan(html.indexOf('</head>'))
    expect(html.indexOf(tag)).toBeLessThan(html.indexOf('type="module"'))
  })
})

describe('first paint with retro themes turned off (issue #1119)', () => {
  it.each(retroThemes)('build flag off: $id falls back to its $mode base mode', ({ id, mode }) => {
    expect(firstPaint(id, { retroBuild: 'false' })).toEqual({ 'data-theme': mode, 'data-atlas-theme': mode })
  })
  it.each(retroThemes.slice(0, 6))('run-time flag off in the cached config: $id falls back to $mode', ({ id, mode }) => {
    expect(firstPaint(id, { cachedConfig: { features: { retro_themes: false } } }))
      .toEqual({ 'data-theme': mode, 'data-atlas-theme': mode })
  })
  it('a missing or unsubstituted build attribute is treated as off', () => {
    expect(firstPaint('win95', { retroBuild: null })).toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'light' })
    expect(firstPaint('win95', { retroBuild: '%VITE_FEATURE_RETRO_THEMES%' }))
      .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'light' })
  })
  it('keeps the saved retro theme when the cached config enables it or predates the flag', () => {
    expect(firstPaint('beos', { cachedConfig: { features: { retro_themes: true } } }))
      .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'beos' })
    expect(firstPaint('beos', { cachedConfig: { features: {} } }))
      .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'beos' })
  })
  it('Light and Dark are unaffected by either flag', () => {
    for (const id of ['dark', 'light']) {
      expect(firstPaint(id, { retroBuild: 'false', cachedConfig: { features: { retro_themes: false } } }))
        .toEqual({ 'data-theme': id, 'data-atlas-theme': id })
    }
  })
  // The backend stamps the live FEATURE_RETRO_THEMES_ENABLED on the tag
  // (atlas/core/spa_index.py), so a just-flipped flag never flashes.
  it('the stamped run-time flag beats a stale cached config', () => {
    expect(firstPaint('windows-xp-luna', { runtime: 'false', cachedConfig: { features: { retro_themes: true } } }))
      .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'light' })
    expect(firstPaint('dos-cga', { runtime: 'false' }))
      .toEqual({ 'data-theme': 'dark', 'data-atlas-theme': 'dark' })
    expect(firstPaint('windows-xp-luna', { runtime: 'true', cachedConfig: { features: { retro_themes: false } } }))
      .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'windows-xp-luna' })
  })
  it('a stamped run-time flag cannot enable themes the build left out', () => {
    expect(firstPaint('win95', { retroBuild: 'false', runtime: 'true' }))
      .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'light' })
  })
  it('a corrupt config cache does not break first paint', () => {
    const attributes = {}
    runInNewContext(bootstrap, {
      localStorage: { getItem: key => (key === 'chatui-config-cache' ? '{not json' : 'win95') },
      document: {
        currentScript: { getAttribute: () => 'true' },
        documentElement: { setAttribute(key, value) { attributes[key] = value } },
      },
    })
    expect(attributes['data-theme']).toBe('light')
  })
})

describe('first-paint per-theme retro CSS (issue #1119 lazy loading)', () => {
  it.each(retroThemes)('writes a render-blocking <link> for a saved $id', ({ id }) => {
    const written = []
    firstPaint(id, { cssVersion: 'abc123', written })
    expect(written).toEqual([
      `<link rel="stylesheet" data-atlas-theme-css="${id}" href="/assets/retro/${id}.css?v=abc123">`,
    ])
  })
  it('appends the <link> when the document is no longer parsing', () => {
    const written = []
    const appended = []
    firstPaint('win95', { cssVersion: 'v1', readyState: 'complete', written, appended })
    expect(written).toEqual([])
    expect(appended).toHaveLength(1)
    expect(appended[0]).toMatchObject({ rel: 'stylesheet', href: '/assets/retro/win95.css?v=v1',
      attributes: { 'data-atlas-theme-css': 'win95' } })
  })
  it.each([
    ['Dark', 'dark', {}],
    ['Light', 'light', {}],
    ['build flag off', 'win95', { retroBuild: 'false' }],
    ['run-time flag off', 'win95', { runtime: 'false' }],
    ['cached run-time flag off', 'win95', { cachedConfig: { features: { retro_themes: false } } }],
    ['no CSS version (flag-off build)', 'win95', { cssVersion: '' }],
    ['unsubstituted CSS version', 'win95', { cssVersion: '%VITE_RETRO_CSS_VERSION%' }],
    ['missing CSS version attribute', 'win95', { cssVersion: null }],
  ])('loads no retro CSS for %s', (_label, saved, options) => {
    const written = []
    const appended = []
    firstPaint(saved, { cssVersion: 'v1', ...options, written, appended })
    expect(written).toEqual([])
    expect(appended).toEqual([])
  })
  it('a failing DOM call never breaks first paint', () => {
    const attributes = {}
    runInNewContext(bootstrap, {
      localStorage: { getItem: key => (key === 'atlas-theme' ? 'beos' : null) },
      document: {
        currentScript: { getAttribute: name => (name === 'data-retro-css' ? 'v1' : 'true') },
        documentElement: { setAttribute(key, value) { attributes[key] = value } },
        readyState: 'loading',
        write() { throw new Error('blocked') },
      },
    })
    expect(attributes).toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'beos' })
  })
})

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
  blocked = false, retroBuild = 'true', cachedConfig = null, cachedConfigRaw = undefined,
  runtime = null, allowed = ALL_ALLOWED_THEMES, cssVersion = null,
  readyState = 'loading', written = [], appended = [], rootStyle = { visibility: '' },
  scheduled = [],
} = {}) {
  const attributes = {}
  const storage = {
    'atlas-theme': saved,
    'chatui-config-cache': cachedConfigRaw === undefined
      ? (cachedConfig === null ? null : JSON.stringify(cachedConfig)) : cachedConfigRaw,
  }
  runInNewContext(bootstrap, {
    setTimeout(fn) { scheduled.push(fn); return scheduled.length },
    clearTimeout() {},
    localStorage: {
      getItem(key) {
        if (blocked) throw new Error('blocked')
        return storage[key] ?? null
      },
    },
    document: {
      currentScript: {
        getAttribute: name => ({
          'data-retro-themes': retroBuild, 'data-retro-runtime': runtime,
          'data-allowed-themes': allowed, 'data-retro-css': cssVersion,
        })[name] ?? null,
      },
      documentElement: { style: rootStyle, setAttribute(key, value) { attributes[key] = value } },
      readyState,
      write: html => written.push(html),
      createElement: () => {
        const listeners = {}
        return {
          attributes: {}, sheet: null, removed: false,
          setAttribute(key, value) { this.attributes[key] = value },
          addEventListener(type, fn) { listeners[type] = fn },
          emit(type) { listeners[type]?.() },
          remove() { this.removed = true },
        }
      },
      head: { appendChild: link => appended.push(link) },
    },
  })
  return attributes
}

const ALL_ALLOWED_THEMES = THEME_REGISTRY.map(theme => theme.id).join(',')

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
  it('stamped flag wins even with malformed cached JSON and saved retro CSS', () => {
    const written = []
    const appended = []
    expect(firstPaint('win95', {
      runtime: 'false', cachedConfigRaw: '{broken', cssVersion: 'v1', written, appended,
    })).toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'light' })
    expect(written).toEqual([])
    expect(appended).toEqual([])
    expect(firstPaint('win95', { runtime: 'true', cachedConfigRaw: '{broken' }))
      .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'win95' })
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
  it.each(retroThemes)('creates a render-blocking DOM <link> for saved $id', ({ id }) => {
    const written = []
    const appended = []
    const rootStyle = { visibility: '' }
    firstPaint(id, { cssVersion: 'abc123', written, appended, rootStyle })
    expect(written).toEqual([]) // No document.write or HTML string injection.
    expect(appended).toHaveLength(1)
    const link = appended[0]
    expect(link).toMatchObject({
      rel: 'stylesheet', href: `/assets/retro/${id}.css?v=abc123`,
      attributes: {
        'data-atlas-theme-css': id,
        'blocking': 'render',
        'data-atlas-theme-css-first-paint': 'true',
      },
    })
    expect(rootStyle.visibility).toBe('hidden')
    link.sheet = { cssRules: [{}] }
    link.emit('load')
    expect(rootStyle.visibility).toBe('')
    expect(link.attributes['data-atlas-theme-css-settled']).toBe('loaded')
  })
  it.each(['error', 'empty-sheet', 'timeout'])('fails closed on %s before first paint', failure => {
    const appended = []
    const scheduled = []
    const rootStyle = { visibility: '' }
    const attributes = firstPaint('windows-xp-luna', {
      cssVersion: 'abc123', appended, scheduled, rootStyle,
    })
    const link = appended[0]
    expect(rootStyle.visibility).toBe('hidden')
    if (failure === 'error') link.emit('error')
    if (failure === 'empty-sheet') {
      link.sheet = { cssRules: [] }
      link.emit('load')
    }
    if (failure === 'timeout') scheduled[0]()
    expect(rootStyle.visibility).toBe('')
    expect(link.removed).toBe(true)
    expect(link.attributes['data-atlas-theme-css-settled']).toBe('failed')
    expect(attributes).toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'light' })
    // An old late event cannot re-enable the revoked/failed stylesheet.
    link.sheet = { cssRules: [{}] }
    link.emit('load')
    expect(attributes['data-atlas-theme']).toBe('light')
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
        currentScript: { getAttribute: name => (
          name === 'data-retro-css' ? 'v1'
            : name === 'data-allowed-themes' ? 'dark,light,beos' : 'true'
        ) },
        documentElement: { setAttribute(key, value) { attributes[key] = value } },
        readyState: 'loading',
        write() { throw new Error('blocked') },
      },
    })
    expect(attributes).toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'light' })
  })
})


describe('administrator theme allowlist at first paint (PR #1175)', () => {
  it('defaults safely to Light/Dark when the server did not stamp a policy', () => {
    expect(firstPaint('win95', { allowed: null })).toEqual({
      'data-theme': 'light', 'data-atlas-theme': 'light',
    })
    expect(firstPaint('crt-phosphor', { allowed: null })).toEqual({
      'data-theme': 'dark', 'data-atlas-theme': 'dark',
    })
  })

  it('allows an explicitly approved theme but never one absent from the allowlist', () => {
    expect(firstPaint('windows-xp-luna', { allowed: 'dark,light,windows-xp-luna' }))
      .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'windows-xp-luna' })
    expect(firstPaint('win95', { allowed: 'dark,light,windows-xp-luna' }))
      .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'light' })
  })

  it('ignores unknown and prototype-related IDs in the administrator policy', () => {
    for (const allowed of ['', 'unknown', '__proto__,constructor', 'win95ish']) {
      expect(firstPaint('win95', { allowed }))
        .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'light' })
    }
    expect(firstPaint('win95', { allowed: 'light, WIN95 , win95' }))
      .toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'win95' })
  })

  it('stamped policy beats a stale cached allowlist and corrupt config JSON', () => {
    const written = []
    expect(firstPaint('win95', {
      allowed: 'dark,light', runtime: 'true', cssVersion: 'abc123', written,
      cachedConfig: { features: { retro_themes: true, allowed_themes: ['win95'] } },
    })).toEqual({ 'data-theme': 'light', 'data-atlas-theme': 'light' })
    expect(written).toEqual([])
    expect(firstPaint('win95', {
      allowed: 'dark,light', runtime: 'true', cachedConfigRaw: '{invalid-json',
    })['data-atlas-theme']).toBe('light')
  })

  it('runtime/build flags still override an administrator allowlist', () => {
    const policy = 'dark,light,win95'
    expect(firstPaint('win95', { allowed: policy, runtime: 'false' })['data-atlas-theme']).toBe('light')
    expect(firstPaint('win95', { allowed: policy, retroBuild: 'false' })['data-atlas-theme']).toBe('light')
  })

  it('never loads CSS for a revoked saved retro theme', () => {
    const written = []
    const appended = []
    firstPaint('win95', { allowed: 'dark,light', cssVersion: 'v1', written, appended })
    expect(written).toEqual([])
    expect(appended).toEqual([])
  })
})

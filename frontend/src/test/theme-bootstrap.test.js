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
function firstPaint(saved, { blocked = false, retroBuild = 'true', cachedConfig = null } = {}) {
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
      currentScript: { getAttribute: name => (name === 'data-retro-themes' ? retroBuild : null) },
      documentElement: { setAttribute(key, value) { attributes[key] = value } },
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
    const tag = '<script src="/theme-bootstrap.js" data-retro-themes="%VITE_FEATURE_RETRO_THEMES%"></script>'
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

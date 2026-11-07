import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { describe, expect, it } from 'vitest'
import { THEME_REGISTRY } from '../themes/themeRegistry'

const bootstrap = readFileSync('public/theme-bootstrap.js', 'utf8')
function firstPaint(saved, blocked = false) {
  const attributes = {}
  runInNewContext(bootstrap, {
    localStorage: { getItem() { if (blocked) throw new Error('blocked'); return saved } },
    document: { documentElement: { setAttribute(key, value) { attributes[key] = value } } },
  })
  return attributes
}

describe('synchronous first-paint theme restoration', () => {
  it.each(THEME_REGISTRY)('restores $id and its $mode base before React', ({ id, mode }) => {
    expect(firstPaint(id)).toEqual({ 'data-theme': mode, 'data-atlas-theme': id })
  })
  it.each([null, '', 'removed-theme', '__proto__', 'constructor', 'https://bad.invalid'])('defaults safely for %s', saved => {
    expect(firstPaint(saved)).toEqual({ 'data-theme': 'dark', 'data-atlas-theme': 'dark' })
  })
  it('survives blocked storage and matches the intentional dark React default', () => {
    expect(firstPaint(null, true)['data-theme']).toBe('dark')
  })
  it('is a blocking head script, not a deferred module', () => {
    const html = readFileSync('index.html', 'utf8')
    expect(html.indexOf('<script src="/theme-bootstrap.js"></script>')).toBeLessThan(html.indexOf('</head>'))
    expect(html).toContain('<script src="/theme-bootstrap.js"></script>')
  })
})
